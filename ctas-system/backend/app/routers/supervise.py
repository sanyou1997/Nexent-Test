"""大数据监督模型工厂：模型目录（启用/阈值/严重级别配置）。

检测器逻辑见 backend/app/supervise.py 注册表；本路由只负责"工厂化"管理配置。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User, SuperviseModel, Dataset, Alert
from app.supervise import MODELS
from app.audit import write_log
import json
import uuid as _uuid
import pandas as pd
from datetime import datetime as _dt, timedelta as _td

router = APIRouter(prefix="/api/supervise", tags=["大数据监督模型工厂"])

# 预警处置时限（与 routers/alerts.py ALERT_SLA_HOURS 同口径，UTC 计时；
# 本模块不可反向 import alerts.py——alerts.py 已 import app.supervise，会成环）
_PIPE_ALERT_SLA_HOURS = {"高": 24, "中": 72, "低": 168}


def _alert_due_at(level: str):
    h = _PIPE_ALERT_SLA_HOURS.get(level or "", 0)
    return _dt.utcnow() + _td(hours=h) if h else None


class ModelConfigUpdate(BaseModel):
    enabled: bool | None = None
    severity: str | None = None
    threshold: int | None = None
    note: str | None = None


def _model_out(m: SuperviseModel) -> dict:
    spec = MODELS.get(m.key, {})
    return {
        "key": m.key, "name": m.name, "category": m.category,
        "description": m.description, "enabled": m.enabled, "severity": m.severity,
        "threshold": m.threshold, "builtin": m.builtin, "note": m.note,
        "has_detector": m.key in MODELS,
        "kind": m.kind, "config": m.config, "owner_id": m.owner_id,
    }


@router.get("/models")
def list_models(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """模型目录：注册表（内置检测器）与数据库配置合并返回。"""
    cfgs = {m.key: m for m in db.query(SuperviseModel).all()}
    out = []
    # 注册表有的，优先返回库里配置；库里没有则给默认
    for key, spec in MODELS.items():
        if key in cfgs:
            out.append(_model_out(cfgs[key]))
        else:
            out.append({
                "key": key, "name": spec["name"], "category": spec["category"],
                "description": spec["description"], "enabled": True,
                "severity": spec["default_severity"], "threshold": spec["default_threshold"],
                "builtin": True, "note": "", "has_detector": True,
            })
    # 库里有但注册表已移除的（orphan），也返回便于清理
    for key, m in cfgs.items():
        if key not in MODELS:
            out.append(_model_out(m))
    # 用户自建碰撞模型（builtin=False）一律返回
    for m in db.query(SuperviseModel).filter(SuperviseModel.builtin == False).all():
        if m.key not in cfgs:
            out.append(_model_out(m))
    return out


@router.delete("/models/{key}")
def delete_model(key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """删除用户自建模型（内置模型只能停用不能删除）；已产出的预警保留在预警中枢，留痕可追溯。"""
    m = db.query(SuperviseModel).filter(SuperviseModel.key == key).first()
    if not m:
        raise HTTPException(404, "模型不存在")
    if m.builtin:
        raise HTTPException(400, "内置模型不可删除，可在列表中停用")
    if user.role != "admin" and m.owner_id and m.owner_id != user.id:
        raise HTTPException(403, "只能删除本人搭建的模型")
    name = m.name
    db.delete(m)
    db.commit()
    write_log(db, user_id=user.id, username=user.username, action="supervise_model_delete",
              resource_type="supervise_model", resource_id=key, detail={"name": name})
    return {"ok": True}


@router.put("/models/{key}")
def update_model(key: str, data: ModelConfigUpdate,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    m = db.query(SuperviseModel).filter(SuperviseModel.key == key).first()
    if not m:
        raise HTTPException(404, "模型不存在")
    changes = {}
    if data.enabled is not None and data.enabled != m.enabled:
        changes["enabled"] = {"from": m.enabled, "to": data.enabled}
        m.enabled = data.enabled
    if data.severity is not None and data.severity != m.severity:
        changes["severity"] = {"from": m.severity, "to": data.severity}
        m.severity = data.severity
    if data.threshold is not None and data.threshold != m.threshold:
        changes["threshold"] = {"from": m.threshold, "to": data.threshold}
        m.threshold = data.threshold
    if data.note is not None and data.note != m.note:
        changes["note"] = {"from": (m.note or "")[:60], "to": (data.note or "")[:60]}
        m.note = data.note
    if not changes:
        return _model_out(m)
    db.commit()
    write_log(db, user_id=user.id, username=user.username, action="supervise_model_update",
              resource_type="supervise_model", resource_id=key,
              detail={"name": m.name, "changes": changes})
    return _model_out(m)


# ---------------- 用户自建碰撞模型（模型工厂：零代码拖拽建模）----------------
RULE_LABELS = {
    "intersection": "交集命中（同一对象同时出现在两个数据集）",
    "duplicate": "同库重复（同一对象在单数据集重复出现）",
    "threshold": "数值阈值（按关键字段聚合后超阈值）",
    "inclusion": "包含命中（A 中对象出现在 B 的某字段中）",
}


def _dataset_df(db: Session, did: int):
    ds = db.get(Dataset, did)
    if not ds:
        raise HTTPException(404, "数据集不存在")
    rows = json.loads(ds.rows_json or "[]")
    return pd.DataFrame(rows), ds.name


_NAME_COLS = ("姓名", "人员姓名", "公职人员", "名称", "法定代表人", "户主", "领取人", "权利人")


def _labels(df: pd.DataFrame, key_col: str) -> dict:
    """关联键 → 人名（数据集里有姓名类字段时），让预警与廉政档案按「人」而不是证件号呈现。"""
    col = next((c for c in _NAME_COLS if c in df.columns and c != key_col), None)
    if not col:
        return {}
    out = {}
    for k, v in zip(df[key_col].tolist(), df[col].tolist()):
        k, v = str(k).strip(), str(v).strip()
        if k and v and v != "nan" and k not in out:
            out[k] = v
    return out


def mask_key(k: str) -> str:
    """证件号 / 卡号类长数字串脱敏展示（保留前 6 后 4）。"""
    k = str(k)
    return k[:6] + "*" * (len(k) - 10) + k[-4:] if len(k) >= 15 and k[:-1].isdigit() else k


def _collide(df_a: pd.DataFrame, df_b: pd.DataFrame, cfg: dict, names: dict) -> list:
    """按规则对两个数据集做关键字段关联对碰，返回命中列表 [{key, reason}]。"""
    rule = cfg["rule"]
    jk_a = cfg["join_key_a"]
    jk_b = cfg.get("join_key_b") or jk_a
    params = cfg.get("params") or {}
    if jk_a not in df_a.columns:
        raise HTTPException(422, f"数据集A缺少关联字段「{jk_a}」")
    if rule not in ("duplicate", "threshold") and jk_b not in df_b.columns:
        raise HTTPException(422, f"数据集B缺少关联字段「{jk_b}」")
    hits = []
    label_of = _labels(df_a, jk_a)
    if rule == "intersection":
        set_b = set(str(x).strip() for x in df_b[jk_b].tolist() if str(x).strip())
        seen = set()
        for k in (str(x).strip() for x in df_a[jk_a].tolist()):
            if k and k in set_b and k not in seen:
                seen.add(k)
                hits.append({"key": k, "reason": f"对象同时出现在「{names['a']}」与「{names['b']}」"
                                                    f"（关键字段「{jk_a}」交集命中，疑似违规交叉）"})
    elif rule == "duplicate":
        from collections import Counter
        c = Counter(str(x).strip() for x in df_a[jk_a].tolist() if str(x).strip())
        for k, n in c.items():
            if n >= 2:
                hits.append({"key": k, "reason": f"在「{names['a']}」中「{jk_a}」重复出现 {n} 次，建议核查"})
    elif rule == "threshold":
        agg = params.get("agg", "sum")
        field = params.get("agg_field")
        val = float(params.get("value", 0) or 0)
        if not field or field not in df_a.columns:
            raise HTTPException(422, f"聚合字段「{field}」不存在")
        for k, sub in df_a.groupby(df_a[jk_a]):
            kk = str(k).strip()
            if agg == "count":
                v = len(sub)
            else:
                nums = []
                for x in sub[field].tolist():
                    try:
                        nums.append(float(str(x).replace(",", "").replace("%", "")))
                    except ValueError:
                        pass
                v = sum(nums) if agg == "sum" else (sum(nums) / len(nums) if nums else 0)
            if v >= val:
                hits.append({"key": kk, "reason": f"「{jk_a}」={kk} 的 {field}（{agg}）={v:.2f} ≥ 阈值 {val}"})
    elif rule == "inclusion":
        field_b = params.get("field_b")
        if not field_b or field_b not in df_b.columns:
            raise HTTPException(422, f"包含比对字段「{field_b}」不存在")
        blob = " ".join(str(x) for x in df_b[field_b].tolist())
        seen = set()
        for k in (str(x).strip() for x in df_a[jk_a].tolist()):
            if k and k in blob and k not in seen:
                seen.add(k)
                hits.append({"key": k, "reason": f"「{names['a']}」中的「{k}」出现在「{names['b']}」字段「{field_b}」中，建议穿透核查"})
    for h in hits:
        h["label"] = label_of.get(h["key"], "")
    return hits


class ModelBuild(BaseModel):
    name: str
    dataset_a: int
    dataset_b: int
    join_key_a: str
    join_key_b: str = ""
    rule: str                       # intersection / duplicate / threshold / inclusion
    rule_label: str = ""
    params: dict = {}
    severity: str = "中"
    note: str = ""


class PipelineBuild(BaseModel):
    """零代码拖拽建模：画布编译后的算子管线。"""
    name: str
    severity: str = "中"
    note: str = ""
    pipeline: dict                   # {"nodes": [...], "edges": [...]}
    dry_run: bool = False            # True = 只干跑预览，不落库


@router.post("/models/build-pipeline")
def build_pipeline_model(data: PipelineBuild, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """拖拽管线建模：校验拓扑 → 干跑预览（可选）→ 入库；run 时由管线引擎执行。"""
    from app.pipeline import run_pipeline, validate_pipeline, describe_pipeline
    errs = validate_pipeline(data.pipeline)
    if errs:
        raise HTTPException(422, "管线校验未通过：" + "；".join(errs[:6]))
    # 干跑：不落库，直接返回执行预览（保存前确认命中量）
    if data.dry_run:
        res = run_pipeline(db, data.pipeline, collect_steps=True)
        return {"ok": True, "dry_run": True, "model": data.name,
                "total_hits": len(res["hits"]), "steps": res["steps"],
                "sample": [{"key": mask_key(h["key"]), "name": h.get("label", ""), "reason": h["reason"]} for h in res["hits"][:10]]}
    key = f"pipe_{_uuid.uuid4().hex[:12]}"
    m = SuperviseModel(key=key, name=data.name, category="管线类",
                       description=describe_pipeline(data.pipeline), enabled=True,
                       severity=data.severity, builtin=False, kind="pipeline",
                       config={"pipeline": data.pipeline}, owner_id=user.id,
                       note=data.note)
    db.add(m)
    db.commit()
    db.refresh(m)
    write_log(db, user_id=user.id, username=user.username, action="supervise_model_build_pipeline",
              resource_type="supervise_model", resource_id=m.id,
              detail={"name": data.name, "nodes": len(data.pipeline.get("nodes") or [])})
    return _model_out(m)


@router.post("/models/build")
def build_model(data: ModelBuild, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """零代码搭建碰撞模型：选两数据集 + 关联字段 + 比对规则，存入模型仓库。"""
    if data.rule not in RULE_LABELS:
        raise HTTPException(422, f"不支持的规则：{data.rule}")
    da = db.get(Dataset, data.dataset_a)
    db_ = db.get(Dataset, data.dataset_b) if data.rule not in ("duplicate", "threshold") else da
    if not da or not db_:
        raise HTTPException(404, "数据集不存在")
    key = f"user_{_uuid.uuid4().hex[:12]}"
    cfg = {
        "dataset_a": data.dataset_a, "dataset_b": data.dataset_b,
        "join_key_a": data.join_key_a, "join_key_b": data.join_key_b or data.join_key_a,
        "rule": data.rule, "params": data.params,
        "ds_a_name": da.name, "ds_b_name": (db_.name if db_ else da.name),
    }
    m = SuperviseModel(key=key, name=data.name, category="碰撞类",
                       description=RULE_LABELS[data.rule], enabled=True,
                       severity=data.severity, builtin=False, kind="user",
                       config=cfg, owner_id=user.id, note=data.note)
    db.add(m)
    db.commit()
    db.refresh(m)
    write_log(db, user_id=user.id, username=user.username, action="supervise_model_build",
              resource_type="supervise_model", resource_id=m.id,
              detail={"name": data.name, "rule": data.rule})
    return _model_out(m)


@router.post("/models/{key}/dry-run")
def dry_run_model(key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """干跑已入库的管线模型：执行但不写预警，返回逐步行数与命中样本。"""
    m = db.query(SuperviseModel).filter(SuperviseModel.key == key).first()
    if not m or (m.config or {}).get("pipeline") is None:
        raise HTTPException(404, "管线模型不存在（仅拖拽管线支持干跑）")
    from app.pipeline import run_pipeline
    res = run_pipeline(db, m.config["pipeline"], collect_steps=True)
    write_log(db, user_id=user.id, username=user.username, action="supervise_model_dry_run",
              resource_type="supervise_model", resource_id=m.id, detail={"hits": len(res["hits"])})
    return {"ok": True, "model": m.name, "total_hits": len(res["hits"]),
            "steps": res["steps"],
            "sample": [{"key": mask_key(h["key"]), "name": h.get("label", ""), "reason": h["reason"]} for h in res["hits"][:10]]}


@router.post("/models/{key}/run")
def run_user_model(key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """执行用户自建模型：单算子碰撞（旧）或算子管线（新）→ 命中即 upsert 进预警中枢。

    误报反馈闭环：该模型下已被人工判「误报」的对象直接跳过（不新建、不复活），
    让误报反馈真正收敛模型的重复打扰。
    """
    from app import feedback as fb
    m = db.query(SuperviseModel).filter(SuperviseModel.key == key).first()
    if not m or m.kind not in ("user", "pipeline"):
        raise HTTPException(404, "用户模型不存在")
    cfg = m.config or {}
    now = _dt.now()
    upserted = 0
    suppressed = 0
    sample = []
    fk = fb.false_keys(db).get(key, set())

    # ---- 分支一：拖拽管线模型 ----
    if cfg.get("pipeline") is not None:
        from app.pipeline import run_pipeline
        res = run_pipeline(db, cfg["pipeline"])
        hits = res["hits"]
        engine = "pipeline"
    # ---- 分支二：旧版单算子碰撞（完全保留原路径）----
    else:
        df_a, na = _dataset_df(db, cfg["dataset_a"])
        df_b, nb = (df_a, na) if cfg["rule"] in ("duplicate", "threshold") else _dataset_df(db, cfg["dataset_b"])
        hits = _collide(df_a, df_b, cfg, {"a": na, "b": nb})
        engine = cfg.get("rule", "collision")

    now = _dt.now()
    upserted = 0
    sample = []
    for h in hits[:300]:
        if h["key"] in fk:
            suppressed += 1
            continue
        fp = f"user:{key}:{h['key']}"
        existing = db.query(Alert).filter(Alert.fingerprint == fp).first()
        if existing:
            existing.level = m.severity
            existing.detail = h["reason"]
            if h.get("label"):  # 旧版本以证件号作对象，重跑时纠正为人名
                existing.title = f"{m.name}：{h['label']}（{mask_key(h['key'])}）命中"
                existing.ref_type, existing.ref_id = "person", h["label"]
            existing.last_seen = now
            if not existing.due_at or existing.level != m.severity:
                existing.due_at = _alert_due_at(m.severity)
        else:
            who = h.get("label") or mask_key(h["key"])
            a = Alert(fingerprint=fp, level=m.severity, source="数据碰撞",
                      title=f"{m.name}：{who}（{mask_key(h['key'])}）命中" if h.get("label") else f"{m.name}：{who} 命中",
                      detail=h["reason"],
                      # 有人名才按「人员」归档进廉政档案；只有证件号时不冒充人名
                      ref_type="person" if h.get("label") else "dataset", ref_id=h.get("label") or h["key"],
                      status="待处理", last_seen=now, due_at=_alert_due_at(m.severity))
            db.add(a)
        upserted += 1
        if len(sample) < 10:
            sample.append({"key": mask_key(h["key"]), "name": h.get("label", ""), "reason": h["reason"]})
    db.commit()
    write_log(db, user_id=user.id, username=user.username, action="supervise_model_run",
              resource_type="supervise_model", resource_id=m.id,
              detail={"hits": len(hits), "upserted": upserted, "engine": engine,
                      "suppressed": suppressed})
    return {"ok": True, "model": m.name, "total_hits": len(hits), "upserted": upserted,
            "engine": engine, "suppressed": suppressed, "sample": sample}
