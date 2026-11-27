"""演示数据夹具加载（幂等）：法规全集 + 审理全流程示范案件。

数据来源（scripts/export_demo_fixtures.py 导出，随代码版本化）：
- fixtures/regulations.json：法规库（2026-11-26 按共产党员网 / 新华网 / 人民网官方全文逐条核对；现行有效 19 部，另有未核实 / 历史文本 / 参考资料 10 部，不参与问答与知识库同步）；
- fixtures/showcase.json：审理示范案件（李某已执行报结、赵某已报告、王某独立建档）及其送审材料、文书、人员档案。

启动时由 main.py 调用；只在缺数据时写入，重复启动不会重复导入。全部为虚构演示数据。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as M
from app.llm import embeddings

log = logging.getLogger("ctas.demo_data")
FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    p = FIX / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _coerce(model, row: dict) -> dict:
    """JSON 夹具 → ORM 字段：只保留模型里存在的列，ISO 字符串转 datetime。"""
    cols = {c.name: c for c in model.__table__.columns}
    out = {}
    for k, v in row.items():
        c = cols.get(k)
        if c is None:
            continue
        if v is not None and isinstance(v, str) and _python_type(c) in (dt.datetime, dt.date):
            try:
                v = dt.datetime.fromisoformat(v)
            except ValueError:
                v = None
        out[k] = v
    return out


def _python_type(col):
    try:
        return col.type.python_type
    except NotImplementedError:
        return object


def load_regulations(db: Session, reset: bool = False) -> int:
    """法规库为空时按夹具建库（新部署）。已有法规库是主本，启动时绝不覆盖——
    曾按「条文数少于夹具」整库删除重载，叠加残本去重误删后变成每次重启都用夹具覆盖，
    界面上对法规的修改在重启后丢失（2026-11-26 修正）。确需以夹具覆盖时显式执行：
    python -m app.demo_data --reset-regulations
    """
    data = _load("regulations.json")
    regs, arts = data.get("regulations") or [], data.get("regulation_articles") or []
    if not regs:
        return 0
    if not reset and db.scalar(select(func.count(M.Regulation.id))):
        have = {t for (t,) in db.query(M.Regulation.title).all()}
        missing = [r.get("title") for r in regs if r.get("title") not in have]
        if missing:
            log.info("法规库与夹具不一致（夹具中有 %d 部不在库中：%s…）；如需以夹具覆盖："
                     "python -m app.demo_data --reset-regulations", len(missing), "、".join(missing[:3]))
        return 0
    db.query(M.RegulationArticle).delete()
    db.query(M.Regulation).delete()
    db.flush()
    id_map = {}
    for r in regs:
        old = r.get("id")
        obj = M.Regulation(**{k: v for k, v in _coerce(M.Regulation, r).items() if k != "id"})
        db.add(obj)
        db.flush()
        id_map[old] = obj.id
    for a in arts:
        row = {k: v for k, v in _coerce(M.RegulationArticle, a).items() if k != "id"}
        row["regulation_id"] = id_map.get(a.get("regulation_id"))
        row["tf_vector"] = embeddings.embed_local(f"{a.get('chapter') or ''} {a.get('article_no') or ''} {a.get('content') or ''}")
        db.add(M.RegulationArticle(**row))
    db.commit()
    try:
        from app.rag import retrieve
        retrieve.invalidate_corpus()
    except Exception:
        pass
    return len(arts)


def load_showcase(db: Session) -> int:
    data = _load("showcase.json")
    trials = data.get("trials") or []
    if not trials:
        return 0
    marker = trials[0].get("title") or ""
    if marker and db.scalar(select(func.count(M.Trial.id)).where(M.Trial.title == marker)):
        return 0

    # 用户按用户名映射（夹具里的 created_by 等是来源库的用户 id）
    src_users = {int(k): v for k, v in (data.get("_usernames") or {}).items()}
    by_name = {u.username: u.id for u in db.query(M.User).all()}
    uid = lambda x: by_name.get(src_users.get(x)) if x else None  # noqa: E731

    case_map, doc_map, trial_map = {}, {}, {}
    for c in data.get("cases") or []:
        exists = db.query(M.Case).filter(M.Case.code == c.get("code")).first()
        if exists:
            case_map[c["id"]] = exists.id
            continue
        row = {k: v for k, v in _coerce(M.Case, c).items() if k != "id"}
        for k in ("owner_id",):
            if k in row:
                row[k] = uid(row[k])
        obj = M.Case(**row)
        db.add(obj); db.flush()
        case_map[c["id"]] = obj.id

    for d in data.get("documents") or []:
        row = {k: v for k, v in _coerce(M.Document, d).items() if k != "id"}
        row["created_by"] = uid(row.get("created_by"))
        row["confirmed_by"] = uid(row.get("confirmed_by"))
        row["template_id"] = None  # 模板库按种子重建，id 不对应
        obj = M.Document(**row)
        db.add(obj); db.flush()
        doc_map[d["id"]] = obj.id
    for v in data.get("document_versions") or []:
        if v.get("document_id") not in doc_map:
            continue
        row = {k: val for k, val in _coerce(M.DocumentVersion, v).items() if k != "id"}
        row["document_id"] = doc_map[v["document_id"]]
        row["created_by"] = uid(row.get("created_by"))
        db.add(M.DocumentVersion(**row))

    for t in trials:
        row = {k: v for k, v in _coerce(M.Trial, t).items() if k != "id"}
        row["case_id"] = case_map.get(t.get("case_id"))
        for k in ("report_doc_id", "decision_doc_id", "execution_doc_id"):
            if k in row and row[k]:
                row[k] = doc_map.get(row[k])
        for k in ("created_by", "confirmed_by", "assignee_id"):
            if k in row and row[k]:
                row[k] = uid(row[k])
        obj = M.Trial(**row)
        db.add(obj); db.flush()
        trial_map[t["id"]] = obj.id

    for m in data.get("trial_materials") or []:
        row = {k: v for k, v in _coerce(M.TrialMaterial, m).items() if k != "id"}
        row["trial_id"] = trial_map.get(m.get("trial_id"))
        row["case_id"] = case_map.get(m.get("case_id"))
        row["created_by"] = uid(row.get("created_by"))
        row["file_path"] = ""  # 原件文件不随夹具分发
        db.add(M.TrialMaterial(**row))

    for name, model in (("person_relations", M.PersonRelation), ("person_archives", M.PersonArchive),
                        ("integrity_reply_records", M.IntegrityReplyRecord)):
        for r in data.get(name) or []:
            row = {k: v for k, v in _coerce(model, r).items() if k != "id"}
            for k in ("created_by", "updated_by", "user_id"):
                if k in row and row[k]:
                    row[k] = uid(row[k])
            if "document_id" in row and row["document_id"]:
                row["document_id"] = doc_map.get(row["document_id"])
            db.add(model(**row))
    db.commit()
    return len(trials)


DATASETS = [  # (文件, 数据集名称, 说明)
    ("公职人员名册.xlsx", "公职人员名册（演示）", "碰撞主键：身份证号"),
    ("惠民补贴发放明细.xlsx", "惠民补贴发放明细（演示）", "与名册求交集：公职人员违规领取补贴"),
    ("房产登记信息.xlsx", "房产登记信息（演示）", "与名册求交集：家族资产穿透"),
    ("工商登记_法人及股东.xlsx", "工商登记·法人及股东（演示）", "与名册求交集：违规经商办企业"),
]


def load_datasets(db: Session) -> int:
    """监督模型工厂的演示数据集 + 一个可直接运行的示例碰撞模型（库里已有数据集时不动）。"""
    if db.scalar(select(func.count(M.Dataset.id))):
        return 0
    from app.routers.datasets import _read_df
    ids = {}
    for fname, name, note in DATASETS:
        p = FIX / "datasets" / fname
        if not p.exists():
            continue
        df = _read_df(fname, p.read_bytes()).dropna(how="all")
        cols = [str(c).strip() for c in df.columns]
        recs = [{cols[i]: ("" if str(r.iloc[i]) == "nan" else str(r.iloc[i]).strip()) for i in range(len(cols))}
                for _, r in df.iterrows()]
        ds = M.Dataset(name=name, source_filename=fname, columns=cols, row_count=len(recs),
                       rows_json=json.dumps(recs, ensure_ascii=False), note=note)
        db.add(ds)
        db.flush()
        ids[fname] = ds.id
    a, b = ids.get("公职人员名册.xlsx"), ids.get("惠民补贴发放明细.xlsx")
    if a and b and not db.scalar(select(func.count(M.SuperviseModel.id)).where(M.SuperviseModel.kind == "user")):
        db.add(M.SuperviseModel(
            key="user_demo_subsidy", name="公职人员违规领取惠民补贴（示例）", category="碰撞类",
            description="交集命中（同一对象同时出现在两个数据集）", enabled=True, severity="高",
            builtin=False, kind="user", note="演示：名册 ∩ 补贴明细，按身份证号碰撞，预期命中 5 人",
            config={"dataset_a": a, "dataset_b": b, "join_key_a": "身份证号", "join_key_b": "身份证号",
                    "rule": "intersection", "params": {},
                    "ds_a_name": "公职人员名册（演示）", "ds_b_name": "惠民补贴发放明细（演示）"}))
    db.commit()
    return len(ids)


def run(db: Session) -> dict:
    out = {"regulation_articles": 0, "showcase_trials": 0, "datasets": 0}
    try:
        out["datasets"] = load_datasets(db)
    except Exception as e:
        db.rollback(); log.exception("load datasets failed: %s", e)
    try:
        out["regulation_articles"] = load_regulations(db)
    except Exception as e:  # 夹具问题不应阻断启动
        db.rollback(); log.exception("load regulations failed: %s", e)
    try:
        out["showcase_trials"] = load_showcase(db)
    except Exception as e:
        db.rollback(); log.exception("load showcase failed: %s", e)
    return out


if __name__ == "__main__":
    # 运维显式操作：以夹具覆盖法规库（丢弃界面上对法规的修改；条款 ID 会变化）
    #   docker exec ctas python -m app.demo_data --reset-regulations
    import sys
    from app.database import SessionLocal
    if "--reset-regulations" not in sys.argv:
        raise SystemExit("用法：python -m app.demo_data --reset-regulations（以夹具覆盖法规库，界面上的修改会丢失）")
    _db = SessionLocal()
    try:
        print(json.dumps({"regulation_articles": load_regulations(_db, reset=True)}, ensure_ascii=False))
    finally:
        _db.close()
