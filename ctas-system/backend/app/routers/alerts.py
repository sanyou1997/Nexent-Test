"""智能督办预警中枢：运行监督模型 → 持久化预警 → 清单/确认/统计。

扫描结果按 fingerprint 去重 upsert，重复扫描只更新 last_seen 不重复插入。
监督闭环深耕：预警处置时限 SLA（按级别计时）+ 督办办理期限与逾期自动升级。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    User, Alert, SuperviseModel, SuperviseDispatch,
    Case, ConductCase, InspectionFinding, Inspection, Petition,
)
from app import audit, tenancy
from app import feedback as fb
from app.supervise import run_all
from sqlalchemy import or_

# 复用四路已有 SLA 引擎（做深不堆宽：横向打通，不重复造轮子）
from app.routers.cases import _sla as _case_sla
from app.routers.conductor import _rect_sla
from app.routers.inspections import _finding_sla
from app.routers.petitions import _sla as _petition_sla

router = APIRouter(prefix="/api/alerts", tags=["智能督办预警中枢"])

# ============================================================
# 监督闭环深耕：处置时限 SLA + 督办逾期自动升级
# （计时统一用 UTC，与 models 的 utcnow 默认值同体系；
#   时限自首次发现/下发起算，不因重复扫描顺延）
# ============================================================
ALERT_SLA_HOURS = {"高": 24, "中": 72, "低": 168}        # 预警处置时限（小时）；提示级不设时限
DISPATCH_SLA_DAYS = {"超期": 3, "临期": 7}                 # 督办办理期限（天）
_OPEN_ALERT_STATUS = ("待处理", "已确认", "已分办")         # 未完结预警（超期判定口径）


def _utcnow() -> datetime:
    return datetime.utcnow()


def _alert_sla_state(a: Alert) -> tuple:
    """预警 SLA 状态：(sla_state, days_left)。超期仅对未完结预警判定。"""
    if not a.due_at:
        return "无时限", None
    if a.status not in _OPEN_ALERT_STATUS:
        return "已终结", None
    left = (a.due_at - _utcnow()).total_seconds() / 86400.0
    if left < 0:
        return "超期", left
    if left <= 1:
        return "临期", left
    return "正常", left


def _dispatch_sla(d: SuperviseDispatch) -> tuple:
    """督办 SLA 状态：(sla_state, days_left)。"""
    if d.status == "已闭环":
        return "已完成", None
    if not d.due_at:
        return "无时限", None
    left = (d.due_at - _utcnow()).total_seconds() / 86400.0
    if left < 0:
        return "已逾期", left
    if left <= 1:
        return "临期", left
    return "正常", left


def _escalate_if_overdue(d: SuperviseDispatch, db: Session, user: User) -> bool:
    """逾期自动升级（读时 lazy 触发，幂等）：未结督办超过办理期限 →
    升级计数 +1 并留痕；若源自预警且未销号 → 关联预警级别升一档（低→中→高，封顶）。
    同一逾期周期只升一次（escalated_at >= due_at 即已升级过）。返回是否发生了升级。"""
    if d.status == "已闭环" or not d.due_at:
        return False
    if d.escalated_at is not None and d.escalated_at >= d.due_at:
        return False
    if _utcnow() < d.due_at:
        return False
    d.escalate_count = (d.escalate_count or 0) + 1
    d.escalated_at = _utcnow()
    detail = {"dispatch_id": d.id, "ref": f"{d.ref_type}:{d.ref_id}",
              "urge_to": d.urge_to, "escalate_count": d.escalate_count}
    if d.ref_type == "alert" and d.ref_id:
        la = db.get(Alert, d.ref_id)
        if la and la.status != "已销号" and la.level in ("低", "中"):
            ladder = {"低": "中", "中": "高"}
            old = la.level
            la.level = ladder[old]
            detail["alert_id"] = la.id
            detail["alert_level"] = {"from": old, "to": la.level}
    audit.write_log(db, user_id=user.id, username=user.username,
                    action="dispatch_escalate", resource_type="dispatch",
                    resource_id=str(d.id), detail=detail)
    return True


def _configs(db: Session) -> list:
    rows = db.query(SuperviseModel).all()
    cfgs = []
    for m in rows:
        cfgs.append({"key": m.key, "enabled": m.enabled,
                     "severity": m.severity, "threshold": m.threshold})
    return cfgs


def _alert_out(a: Alert, db: Session, fb_rec: dict | None = None) -> dict:
    ack_user = ""
    if a.acked_by:
        u = db.get(User, a.acked_by)
        ack_user = u.real_name or u.username if u else ""
    sla_state, days_left = _alert_sla_state(a)
    return {
        "id": a.id, "level": a.level, "source": a.source, "title": a.title,
        "detail": a.detail, "ref_type": a.ref_type, "ref_id": a.ref_id,
        "status": a.status, "acked_by": ack_user or "",
        "last_seen": a.last_seen.strftime("%Y-%m-%d %H:%M") if a.last_seen else "",
        "created_at": a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "",
        "due_at": a.due_at.strftime("%Y-%m-%d %H:%M") if a.due_at else "",
        "sla_state": sla_state,
        "days_left": round(days_left, 1) if days_left is not None else None,
        # 误报反馈闭环：模型 key + 人工判定（误报/属实）+ 理由
        "model_key": fb.model_key_of(a.fingerprint),
        "verdict": (fb_rec or {}).get("verdict", ""),
        "feedback_reason": (fb_rec or {}).get("reason", ""),
        "feedback_by": (fb_rec or {}).get("by", ""),
    }


@router.post("/scan")
def scan(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """运行全部已启用模型，upsert 预警条目。

    误报反馈闭环：已被人工判「误报」的模型+对象不再重复产出（新建即跳过，
    已存在的保持「已忽略」不复活），把人工研判真正变成模型的行为修正。
    """
    cfgs = _configs(db)
    raw = run_all(db, cfgs)
    fk = fb.false_keys(db)          # {model_key: set(obj_key)} 误报抑制表
    now = datetime.now()
    upserted = 0
    suppressed = 0
    for r in raw:
        fp = r["fingerprint"]
        mk, ok = fb.model_key_of(fp), fb.obj_key_of(fp)
        hit_false = bool(ok) and ok in fk.get(mk, set())
        # 预警归关联业务对象所在部门（无对象时归经办人部门）；同部门同指纹只保留一条
        owner = tenancy.ref_dept(db, r["ref_type"], r["ref_id"]) or getattr(tenancy.current_scope(db), "dept_id", None)
        existing = (db.query(Alert).filter(Alert.fingerprint == fp, Alert.dept_id == owner)
                    .execution_options(**tenancy.BYPASS).first())
        if existing:
            # 时限自首次发现起算：仅当模型严重级别发生变化时才重算 due_at，
            # 避免重复扫描不断顺延、把 SLA 变成"永不会超期"。
            if existing.level != r["level"]:
                existing.level = r["level"]
                hours = ALERT_SLA_HOURS.get(r["level"], 0)
                existing.due_at = (_utcnow() + timedelta(hours=hours)) if hours else None
            existing.source = r["source"]
            existing.title = r["title"]
            existing.detail = r["detail"]
            existing.last_seen = now
            if hit_false and existing.status == "待处理":
                existing.status = "已忽略"   # 误报判定继续生效，不复活
                suppressed += 1
        else:
            if hit_false:
                suppressed += 1            # 已判误报，不再重复产出打扰
                continue
            hours = ALERT_SLA_HOURS.get(r["level"], 0)
            a = Alert(fingerprint=fp, level=r["level"], source=r["source"],
                      title=r["title"], detail=r["detail"], ref_type=r["ref_type"],
                      ref_id=r["ref_id"], status="待处理", last_seen=now, dept_id=owner,
                      due_at=(_utcnow() + timedelta(hours=hours)) if hours else None)
            db.add(a)
        upserted += 1
    db.commit()
    audit.write_log(db, user_id=user.id, username=user.username, action="alert_scan",
                    resource_type="alert", resource_id="batch",
                    detail={"models": len([c for c in cfgs if c["enabled"]]),
                            "generated": len(raw), "suppressed": suppressed})
    return {"ok": True, "generated": len(raw), "upserted": upserted,
            "suppressed": suppressed}


@router.get("")
def list_alerts(level: str = "", status: str = "", source: str = "",
                sla: str = "", q: str = "", db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    qry = db.query(Alert)
    if level:
        qry = qry.filter(Alert.level == level)
    if status:
        qry = qry.filter(Alert.status == status)
    if source:
        qry = qry.filter(Alert.source == source)
    if q:
        like = f"%{q}%"
        qry = qry.filter((Alert.title.like(like)) | (Alert.detail.like(like)))
    rows = qry.order_by(Alert.level != "高", Alert.last_seen.desc()).all()
    vmap = fb.verdict_map(db, [a.fingerprint for a in rows])   # 批量取反馈，避免 N+1
    out = [_alert_out(a, db, vmap.get(a.fingerprint)) for a in rows]
    if sla:
        out = [o for o in out if o["sla_state"] == sla]
    # 超期预警置顶（stable sort，保留 高级别/最近发现 的既有次序）
    out.sort(key=lambda o: o["sla_state"] != "超期")
    return out


# ============================================================
# 误报反馈闭环：误报/属实判定 → 模型再运行抑制 → 分模型误报率
# ============================================================

class AlertFeedbackIn(BaseModel):
    verdict: str            # 误报 / 属实
    reason: str = ""        # 误报必填（调优依据）；属实可留空


@router.get("/feedback/stats")
def feedback_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """误报反馈统计：总体 + 分模型误报率（模型调优的直接依据）。"""
    models = [{"key": m.key, "name": m.name, "kind": m.kind}
              for m in db.query(SuperviseModel).all()]
    have = {m["key"] for m in models}
    from app.supervise import MODELS
    for key, spec in MODELS.items():      # 注册表有但库无配置的内置模型也纳入
        if key not in have:
            models.append({"key": key, "name": spec["name"], "kind": "builtin"})
    return fb.model_stats(db, models)


@router.get("/feedback")
def feedback_list(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """反馈记录清单（误报分析页展示 / 撤销）。"""
    return fb.feedback_rows(db)


@router.post("/{aid}/feedback")
def alert_feedback(aid: int, body: AlertFeedbackIn, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """对模型预警做人工判定：误报（必填理由）/ 属实。

    联动：误报→预警置「已忽略」且该模型再次运行不再产出该对象；属实→「已确认」。
    """
    a = db.get(Alert, aid)
    if not a:
        raise HTTPException(404, "预警不存在")
    try:
        res = fb.record(db, a, body.verdict, body.reason, user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.write_log(db, user_id=user.id, username=user.username,
                    action="alert_feedback", resource_type="alert",
                    resource_id=str(aid),
                    detail={"verdict": res["verdict"], "model_key": fb.model_key_of(a.fingerprint),
                            "obj_key": fb.obj_key_of(a.fingerprint)})
    by = user.real_name or user.username
    return _alert_out(a, db, {"verdict": res["verdict"], "reason": res["reason"], "by": by})


@router.delete("/{aid}/feedback")
def alert_feedback_revoke(aid: int, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """撤销误报/属实反馈：退回「待处理」，恢复该对象再次被模型产出的资格。"""
    a = db.get(Alert, aid)
    if not a:
        raise HTTPException(404, "预警不存在")
    if not fb.revoke(db, a):
        raise HTTPException(404, "该预警暂无反馈记录")
    audit.write_log(db, user_id=user.id, username=user.username,
                    action="alert_feedback_revoke", resource_type="alert",
                    resource_id=str(aid),
                    detail={"model_key": fb.model_key_of(a.fingerprint)})
    return _alert_out(a, db, {"verdict": "", "reason": "", "by": ""})


@router.post("/{aid}/ack")
def ack(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.get(Alert, aid)
    if not a:
        raise HTTPException(404, "预警不存在")
    a.status = "已确认"
    a.acked_by = user.id
    a.acked_at = datetime.now()
    db.commit()
    return _alert_out(a, db)


@router.post("/{aid}/ignore")
def ignore(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.get(Alert, aid)
    if not a:
        raise HTTPException(404, "预警不存在")
    a.status = "已忽略"
    a.acked_by = user.id
    a.acked_at = datetime.now()
    db.commit()
    return _alert_out(a, db)


class AlertDispatchIn(BaseModel):
    urge_to: str                                 # 分办/承办对象
    note: str = ""


@router.post("/{aid}/dispatch")
def dispatch_alert(aid: int, body: AlertDispatchIn, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """从预警一键分办：生成督办记录并置预警为「已分办」，打通 预警→分办→整改 闭环。"""
    a = db.get(Alert, aid)
    if not a:
        raise HTTPException(404, "预警不存在")
    if a.status in ("已忽略", "已销号"):
        raise HTTPException(400, f"预警状态为「{a.status}」，不可分办")
    d = SuperviseDispatch(
        source=a.source or "数据碰撞", ref_type="alert", ref_id=aid,
        ref_code="", ref_title=a.title, ref_unit=a.ref_id,
        level="超期", urge_to=body.urge_to, note=body.note or a.detail,
        status="已下发", created_by=user.id,
        due_at=_utcnow() + timedelta(days=DISPATCH_SLA_DAYS["超期"]))
    db.add(d)
    a.status = "已分办"
    a.acked_by = user.id
    a.acked_at = datetime.now()
    db.commit(); db.refresh(d)
    audit.write_log(db, user_id=user.id, username=user.username,
                    action="alert_dispatch", resource_type="alert",
                    resource_id=str(aid), detail={"to": body.urge_to, "dispatch_id": d.id})
    return {"alert": _alert_out(a, db), "dispatch": _dispatch_out(d, db)}


@router.post("/{aid}/close")
def close_alert(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """预警销号（人工确认处置完毕）。"""
    a = db.get(Alert, aid)
    if not a:
        raise HTTPException(404, "预警不存在")
    if a.status == "已销号":
        return _alert_out(a, db)
    a.status = "已销号"
    db.commit()
    audit.write_log(db, user_id=user.id, username=user.username,
                    action="alert_close", resource_type="alert",
                    resource_id=str(aid), detail={"title": a.title})
    return _alert_out(a, db)


@router.get("/stats")
def stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Alert).all()
    by_level: dict = {}
    by_source: dict = {}
    by_status: dict = {}
    for a in rows:
        by_level[a.level] = by_level.get(a.level, 0) + 1
        by_source[a.source] = by_source.get(a.source, 0) + 1
        by_status[a.status] = by_status.get(a.status, 0) + 1
    states = [_alert_sla_state(a)[0] for a in rows]
    return {
        "total": len(rows),
        "pending": sum(1 for a in rows if a.status == "待处理"),
        "overdue": sum(1 for s in states if s == "超期"),
        "due_soon": sum(1 for s in states if s == "临期"),
        "by_level": by_level, "by_source": by_source, "by_status": by_status,
    }


# ============================================================
# 督办中枢：四路时限 SLA 横向打通 + 一键督办闭环
# ============================================================

def _owner_name(db: Session, uid: int) -> str:
    if not uid:
        return ""
    u = db.get(User, uid)
    return u.real_name or u.username if u else ""


def _hub_items(db: Session) -> list:
    """聚合案管/党风/巡察/信访四路时限 SLA，归一化为统一预警条目（仅含超期/临期）。"""
    now = datetime.now()
    items: list = []

    # 案管：线索处置(30)/初步核实(60)/审查调查(90) 时限
    for c in db.query(Case).all():
        s = _case_sla(c)
        if s.get("days_left") is None or not (s["overdue"] or s["warn"]):
            continue
        items.append({
            "source": "案管", "ref_type": "case", "ref_id": c.id,
            "ref_code": c.code or "", "ref_title": c.title or c.subject or "",
            "ref_unit": c.unit or "", "owner": _owner_name(db, c.owner_id),
            "stage": s["stage"], "level": "超期" if s["overdue"] else "临期",
            "deadline": s["deadline"], "days_left": s["days_left"], "sla": s,
        })

    # 党风：整改期限 SLA
    for c in db.query(ConductCase).all():
        s = _rect_sla(c)
        if s.get("days_left") is None or not (s["overdue"] or s["warn"]):
            continue
        items.append({
            "source": "党风", "ref_type": "conduct", "ref_id": c.id,
            "ref_code": c.code or "", "ref_title": c.title or c.subject or "",
            "ref_unit": c.unit or "", "owner": _owner_name(db, c.owner_id),
            "stage": "整改期限", "level": "超期" if s["overdue"] else "临期",
            "deadline": s["deadline"], "days_left": s["days_left"], "sla": s,
        })

    # 巡察：问题整改时限 SLA
    ins_map = {i.id: i.code for i in db.query(Inspection).all()}
    for f in db.query(InspectionFinding).all():
        s = _finding_sla(f)
        if s["level"] not in ("overdue", "warn"):
            continue
        items.append({
            "source": "巡察", "ref_type": "inspection", "ref_id": f.id,
            "ref_code": ins_map.get(f.inspection_id, ""), "ref_title": f.content or "",
            "ref_unit": f.category or "", "owner": "",
            "stage": s["stage"], "level": "超期" if s["level"] == "overdue" else "临期",
            "deadline": f.due_at.strftime("%Y-%m-%d") if f.due_at else "",
            "days_left": s.get("days_left"), "sla": s,
        })

    # 信访：受理告知(15工日)/办结反馈(15工日)/办理(60日) 时限
    for p in db.query(Petition).all():
        s = _petition_sla(p)
        overdue = s["notify_overdue"] or s["feedback_overdue"] or s["handle_overdue"]
        cands = []
        for key, label in (("notify_due", "受理告知"), ("feedback_due", "办结反馈"), ("handle_due", "办理")):
            d = s.get(key)
            if d:
                try:
                    cands.append((datetime.strptime(d, "%Y-%m-%d"), label))
                except Exception:
                    pass
        if not cands:
            continue
        if overdue:
            earliest = min(cands, key=lambda x: x[0])
            dl = (earliest[0] - now).days
            items.append({
                "source": "信访", "ref_type": "petition", "ref_id": p.id,
                "ref_code": p.code or "", "ref_title": p.content or p.summary or "",
                "ref_unit": p.petitioner or "", "owner": "",
                "stage": earliest[1], "level": "超期",
                "deadline": earliest[0].strftime("%Y-%m-%d"), "days_left": dl, "sla": s,
            })
        else:
            nearest = min(cands, key=lambda x: x[0])
            dl = (nearest[0] - now).days
            if 0 <= dl <= 7:
                items.append({
                    "source": "信访", "ref_type": "petition", "ref_id": p.id,
                    "ref_code": p.code or "", "ref_title": p.content or p.summary or "",
                    "ref_unit": p.petitioner or "", "owner": "",
                    "stage": nearest[1], "level": "临期",
                    "deadline": nearest[0].strftime("%Y-%m-%d"), "days_left": dl, "sla": s,
                })
    return items


@router.get("/hub")
def hub(source: str = "", level: str = "", q: str = "",
         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """四路时限 SLA 统一预警看板：超期/临期聚合 + 按来源统计 + 是否已督办标记。"""
    items = _hub_items(db)
    # 是否已督办（存在未闭环督办记录）；同时触发逾期自动升级（lazy，幂等）
    open_disp = db.query(SuperviseDispatch).filter(SuperviseDispatch.status != "已闭环").all()
    disp_set = {(d.ref_type, d.ref_id) for d in open_disp}
    escalated_any = False
    for d in open_disp:
        if _escalate_if_overdue(d, db, user):
            escalated_any = True
    if escalated_any:
        db.commit()
    overdue_dispatch = sum(1 for d in open_disp if d.due_at and _utcnow() >= d.due_at)
    escalated_dispatch = sum(1 for d in open_disp if (d.escalate_count or 0) > 0)
    for it in items:
        it["dispatched"] = (it["ref_type"], it["ref_id"]) in disp_set
    # 统计（基于全集，反映中枢全貌）
    by_source: dict = {}
    overdue_n = due_soon_n = 0
    for i in items:
        by_source.setdefault(i["source"], {"overdue": 0, "due_soon": 0})
        if i["level"] == "超期":
            by_source[i["source"]]["overdue"] += 1
            overdue_n += 1
        else:
            by_source[i["source"]]["due_soon"] += 1
            due_soon_n += 1
    # 筛选
    if source:
        items = [i for i in items if i["source"] == source]
    if level:
        items = [i for i in items if i["level"] == level]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in (i["ref_code"] + i["ref_title"]
                                            + i["ref_unit"] + i["owner"]).lower()]
    # 排序：超期优先，其次按剩余天数升序（最紧迫在前）
    rank = {"超期": 0, "临期": 1}

    def _sort_key(i):
        return (rank.get(i["level"], 9), i["days_left"] if i["days_left"] is not None else 999)
    items.sort(key=_sort_key)
    return {
        "items": items, "total": len(items),
        "stats": {"overdue": overdue_n, "due_soon": due_soon_n, "by_source": by_source,
                  "overdue_dispatch": overdue_dispatch, "escalated_dispatch": escalated_dispatch},
    }


class DispatchIn(BaseModel):
    source: str = ""
    ref_type: str = ""
    ref_id: int
    ref_code: str = ""
    ref_title: str = ""
    ref_unit: str = ""
    level: str = ""          # 超期/临期
    urge_to: str = ""
    note: str = ""


def _dispatch_out(d: SuperviseDispatch, db: Session) -> dict:
    creator = db.get(User, d.created_by) if d.created_by else None
    sla_state, days_left = _dispatch_sla(d)
    return {
        "id": d.id, "source": d.source, "ref_type": d.ref_type, "ref_id": d.ref_id,
        "ref_code": d.ref_code, "ref_title": d.ref_title, "ref_unit": d.ref_unit,
        "level": d.level, "urge_to": d.urge_to, "note": d.note, "status": d.status,
        "created_by": creator.real_name or creator.username if creator else "",
        "created_at": d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else "",
        "feedback": d.feedback or "",
        "feedback_at": d.feedback_at.strftime("%Y-%m-%d %H:%M") if d.feedback_at else "",
        "due_at": d.due_at.strftime("%Y-%m-%d %H:%M") if d.due_at else "",
        "sla_state": sla_state,
        "days_left": round(days_left, 1) if days_left is not None else None,
        "escalated": (d.escalate_count or 0) > 0,
        "escalate_count": d.escalate_count or 0,
    }


@router.post("/dispatch")
def dispatch(body: DispatchIn, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """一键督办：对超期/临期事项发起督办，记录责任对象与要求。"""
    if body.level not in ("超期", "临期"):
        raise HTTPException(400, "level 非法（应为 超期/临期）")
    if not body.ref_type or not body.ref_id:
        raise HTTPException(400, "ref_type/ref_id 必填")
    d = SuperviseDispatch(
        source=body.source, ref_type=body.ref_type, ref_id=body.ref_id,
        ref_code=body.ref_code, ref_title=body.ref_title, ref_unit=body.ref_unit,
        level=body.level, urge_to=body.urge_to, note=body.note,
        status="已下发", created_by=user.id,
        due_at=_utcnow() + timedelta(days=DISPATCH_SLA_DAYS.get(body.level, 7)))
    db.add(d)
    db.commit()
    db.refresh(d)
    audit.write_log(db, user_id=user.id, username=user.username,
                    action="supervise_dispatch", resource_type="dispatch",
                    resource_id=str(d.id),
                    detail={"ref": f"{body.ref_type}:{body.ref_id}", "to": body.urge_to})
    return _dispatch_out(d, db)


@router.get("/dispatches")
def list_dispatches(source: str = "", status: str = "", q: str = "",
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """督办记录列表（反馈闭环追溯）。读时触发逾期自动升级（lazy，幂等）。"""
    qry = db.query(SuperviseDispatch)
    if source:
        qry = qry.filter(SuperviseDispatch.source == source)
    if status:
        qry = qry.filter(SuperviseDispatch.status == status)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(SuperviseDispatch.ref_code.like(like),
                             SuperviseDispatch.ref_title.like(like),
                             SuperviseDispatch.urge_to.like(like)))
    rows = qry.order_by(SuperviseDispatch.created_at.desc()).all()
    escalated_any = False
    for d in rows:
        if _escalate_if_overdue(d, db, user):
            escalated_any = True
    if escalated_any:
        db.commit()
    return [_dispatch_out(d, db) for d in rows]


class FeedbackIn(BaseModel):
    feedback: str = ""
    status: str = "已反馈"     # 已反馈 / 已闭环


@router.post("/dispatches/{did}/feedback")
def dispatch_feedback(did: int, body: FeedbackIn, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """督办反馈：承办人反馈整改情况，并将状态推进到 已反馈/已闭环。"""
    if body.status not in ("已反馈", "已闭环"):
        raise HTTPException(400, "status 非法（应为 已反馈/已闭环）")
    d = db.get(SuperviseDispatch, did)
    if not d:
        raise HTTPException(404, "督办记录不存在")
    # 逾期升级留痕先行：即使本次反馈即闭环，也曾逾期的事实必须留在记录与审计链里
    if _escalate_if_overdue(d, db, user):
        pass  # 升级已写入 session，随下方 commit 一并落库
    d.feedback = body.feedback
    d.status = body.status
    d.feedback_at = datetime.now()
    # 闭环联动：督办已闭环且源自预警 → 自动销号对应预警，形成 预警→分办→整改→销号 闭环
    if body.status == "已闭环" and d.ref_type == "alert" and d.ref_id:
        la = db.get(Alert, d.ref_id)
        if la and la.status != "已销号":
            la.status = "已销号"
            audit.write_log(db, user_id=user.id, username=user.username,
                            action="alert_auto_close", resource_type="alert",
                            resource_id=str(d.ref_id), detail={"via_dispatch": d.id})
    db.commit()
    audit.write_log(db, user_id=user.id, username=user.username,
                    action="supervise_feedback", resource_type="dispatch",
                    resource_id=str(d.id), detail={"status": body.status})
    return _dispatch_out(d, db)
