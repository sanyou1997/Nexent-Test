"""廉政档案画像与廉政意见回复（A7）。

- 人员列表：跨案管/党风/预警聚合出"被监督对象"，给出廉政评级。
- 单人多域档案：基本情况 + 涉及线索/四风/预警 + 廉政评级 + 时间线。
- 廉政意见回复：根据档案一键生成《关于XXX同志廉政情况的回复意见》（A3 文书，LLM 可选）。
全部为辅助参考，结论由组织作出。
"""
from collections import defaultdict
from datetime import datetime
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    User, Case, ConductCase, Alert, Document, DocumentVersion, PersonRelation,
    SuperviseDispatch, InspectionFinding, Inspection, Petition,
    PersonArchive, IntegrityReplyRecord, DocumentTemplate,
)
from app import audit, gongwen
from app.llm import provider
from app.supervise import aggregate_persons
# 复用文书模块的字段别名词典（"从案件/档案带入"智能匹配，不重复造轮子）
from app.routers.documents import _FIELD_ALIASES
# 复用四路已有 SLA 引擎（做深不堆宽：横向打通，不重复造轮子）
from app.routers.cases import _sla as _case_sla
from app.routers.conductor import _rect_sla
from app.routers.inspections import _finding_sla
from app.routers.petitions import _sla as _petition_sla

router = APIRouter(prefix="/api/integrity", tags=["廉政档案画像与廉政意见回复"])


def _persons_index(db: Session) -> dict:
    """{name: {cases, conduct, alerts, domains, units}}。"""
    persons = aggregate_persons(db)  # name -> {cases, conduct, domains}
    idx: dict = {}
    for name, info in persons.items():
        units = set()
        for kc in info["cases"]:
            pass
        # 取案管案件单位
        for c in db.query(Case).filter(Case.subject == name).all():
            if c.unit:
                units.add(c.unit)
        alerts = db.query(Alert).filter(Alert.ref_type == "person", Alert.ref_id == name).all()
        idx[name] = {
            "cases": info["cases"], "conduct": info["conduct"],
            "domains": list(info["domains"]), "units": list(units),
            "alerts": [{"level": a.level, "source": a.source, "title": a.title} for a in alerts],
        }
    # 补充：仅出现在预警(person)但未在 case/conduct 的人
    for a in db.query(Alert).filter(Alert.ref_type == "person").all():
        name = a.ref_id
        if name and name not in idx:
            idx[name] = {"cases": [], "conduct": [], "domains": [], "units": [],
                         "alerts": [{"level": a.level, "source": a.source, "title": a.title}]}
    # 补充：审理独立建档（未关联案管案件）的被审查调查人，同样纳入廉政档案
    from app.models import Trial
    for t in db.query(Trial).filter(Trial.case_id.is_(None), Trial.subject != "").all():
        p = idx.setdefault(t.subject, {"cases": [], "conduct": [], "domains": [], "units": [], "alerts": []})
        p["cases"].append({"id": None, "trial_id": t.id, "code": "审理·独立建档", "title": t.title or "审理事项",
                           "status": t.status or ""})
        if "审理" not in p["domains"]:
            p["domains"].append("审理")
        if t.unit and t.unit not in p["units"]:
            p["units"].append(t.unit)
    return idx


def _rating(info: dict) -> dict:
    n_case = len(info["cases"])
    n_cond = len(info["conduct"])
    n_alert = len(info["alerts"])
    high = sum(1 for a in info["alerts"] if a["level"] == "高")
    score = n_case * 1 + n_cond * 1 + n_alert * 1 + high * 2
    if score == 0:
        rating = "良好"
    elif score <= 2 and high == 0:
        rating = "关注"
    else:
        rating = "重点监督"
    return {"score": score, "rating": rating, "case_count": n_case,
            "conduct_count": n_cond, "alert_count": n_alert, "high_alert": high}


@router.get("/list")
def list_persons(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    idx = _persons_index(db)
    # 人工维护档案（单位/职务/职级）优先于聚合单位展示
    names = list(idx.keys())
    arch_map = {}
    if names:
        for a in db.query(PersonArchive).filter(PersonArchive.person.in_(names)).all():
            arch_map[a.person] = a
    out = []
    for name, info in idx.items():
        r = _rating(info)
        a = arch_map.get(name)
        units = info["units"] or ([a.unit] if a and a.unit else [])
        out.append({
            "name": name, "units": units, "domains": info["domains"],
            "post": (a.post if a else "") or "", "rank": (a.rank if a else "") or "",
            "rating": r["rating"], "score": r["score"],
            "case_count": r["case_count"], "conduct_count": r["conduct_count"],
            "alert_count": r["alert_count"],
        })
    out.sort(key=lambda x: -x["score"])
    return out


@router.get("/{person}")
def profile(person: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    idx = _persons_index(db)
    if person not in idx:
        raise HTTPException(404, "未找到该人员廉政档案")
    info = idx[person]
    r = _rating(info)
    # 时间线（合并线索登记/四风/预警）
    timeline = []
    for kc in info["cases"]:
        timeline.append({"date": "", "type": "案管线索", "text": f"{kc['code']} {kc['title']}（{kc['status']}）"})
    for cc in info["conduct"]:
        timeline.append({"date": "", "type": "四风问题", "text": f"{cc['title']}（{cc['category']}/{cc['status']}）"})
    for a in info["alerts"]:
        timeline.append({"date": "", "type": "预警", "text": a["title"]})
    return {
        "name": person, "units": info["units"], "domains": info["domains"],
        "rating": r, "timeline": timeline,
        "cases": info["cases"], "conduct": info["conduct"], "alerts": info["alerts"],
        "archive": _archive_out(
            db.query(PersonArchive).filter(PersonArchive.person == person).first(), person),
        "disclaimer": "廉政档案由系统自动聚合业务数据生成，仅供组织参考，不作为定性结论。",
    }


# ---------------- 廉政档案人工维护资料（组织掌握情况） ----------------
class ArchiveIn(BaseModel):
    unit: str = ""
    post: str = ""
    rank: str = ""
    org_notes: str = ""
    personal_report: str = ""
    remark: str = ""


def _archive_out(a, person: str) -> dict:
    if not a:
        return {"person": person, "unit": "", "post": "", "rank": "",
                "org_notes": "", "personal_report": "", "remark": "",
                "exists": False, "updated_at": ""}
    return {"person": a.person, "unit": a.unit or "", "post": a.post or "",
            "rank": a.rank or "", "org_notes": a.org_notes or "",
            "personal_report": a.personal_report or "", "remark": a.remark or "",
            "exists": True,
            "updated_at": a.updated_at.strftime("%Y-%m-%d %H:%M") if a.updated_at else ""}


@router.get("/{person}/archive")
def get_archive(person: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """读取人工维护的档案资料（单位/职务/职级、组织掌握情况、个人事项报告要点等）。"""
    a = db.query(PersonArchive).filter(PersonArchive.person == person).first()
    return _archive_out(a, person)


@router.put("/{person}/archive")
def put_archive(person: str, data: ArchiveIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """维护人工档案资料（可编辑、更新留痕；与系统聚合数据分区呈现）。"""
    a = db.query(PersonArchive).filter(PersonArchive.person == person).first()
    if not a:
        a = PersonArchive(person=person)
        db.add(a)
    a.unit = (data.unit or "")[:120]
    a.post = (data.post or "")[:80]
    a.rank = (data.rank or "")[:40]
    a.org_notes = (data.org_notes or "")[:4000]
    a.personal_report = (data.personal_report or "")[:4000]
    a.remark = (data.remark or "")[:2000]
    a.updated_by = user.id
    db.commit()
    db.refresh(a)
    audit.write_log(db, user_id=user.id, username=user.username,
                    action="integrity_archive_update",
                    resource_type="person_archive", resource_id=a.id,
                    detail={"person": person})
    return _archive_out(a, person)


# ---------------- 多维画像雷达与年度趋势 ----------------
_DIMS = ["线索", "四风", "预警", "超期", "督办", "关系风险"]


@router.get("/{person}/portrait")
def portrait(person: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """多维画像：六维原始值 + 归一化评分 + 年度趋势（让风险结构一目了然）。"""
    cases = db.query(Case).filter(Case.subject == person).all()
    conduct = db.query(ConductCase).filter(ConductCase.subject == person).all()
    alerts = db.query(Alert).filter(Alert.ref_type == "person", Alert.ref_id == person).all()
    sup = _person_supervision(db, person)
    overdue = int(sup["summary"]["overdue"])
    open_dispatch = int(sup["summary"]["open_dispatch"])

    # 关系风险：正向 + 反向关联人中的"危险主体"（有预警或跨业务域出现）
    rel_names = {r.related_name for r in
                 db.query(PersonRelation).filter(PersonRelation.person == person).all()}
    rel_names |= {r.person for r in
                  db.query(PersonRelation).filter(PersonRelation.related_name == person).all()}
    rel_names.discard(person)
    persons_idx = aggregate_persons(db)
    alert_names = {a.ref_id for a in
                   db.query(Alert).filter(Alert.ref_type == "person").all() if a.ref_id}
    danger_rel = sorted(n for n in rel_names
                        if n in alert_names or len(persons_idx.get(n, {}).get("domains", [])) >= 2)

    raw = {"线索": len(cases), "四风": len(conduct), "预警": len(alerts),
           "超期": overdue, "督办": open_dispatch, "关系风险": len(danger_rel)}
    mx = max(raw.values()) if raw else 0
    scores = {k: (round(100 * v / mx) if mx else 0) for k, v in raw.items()}
    weighted = (raw["线索"] + raw["四风"] + raw["预警"] * 2 +
                raw["超期"] * 3 + raw["督办"] * 2 + raw["关系风险"])
    level = "高" if weighted >= 8 else ("中" if weighted >= 3 else "低")

    trend: dict = {}

    def bump(dt, key):
        if not dt:
            return
        row = trend.setdefault(dt.year, {"year": str(dt.year), "线索": 0, "四风": 0, "预警": 0})
        row[key] += 1

    for c in cases:
        bump(c.created_at, "线索")
    for cc in conduct:
        bump(cc.created_at, "四风")
    for a in alerts:
        bump(a.created_at, "预警")

    return {"person": person, "dims": _DIMS, "raw": raw, "scores": scores,
            "weighted_score": weighted, "risk_level": level,
            "danger_relations": danger_rel,
            "trend": [trend[y] for y in sorted(trend.keys())],
            "disclaimer": "画像由系统业务数据自动聚合生成，仅供组织参考，不作为定性结论。"}


# ---------------- 廉政意见出具记录 ----------------
def _reply_record_out(r: IntegrityReplyRecord, db: Session) -> dict:
    d = db.get(Document, r.document_id) if r.document_id else None
    u = db.get(User, r.created_by) if r.created_by else None
    return {"id": r.id, "person": r.person, "purpose": r.purpose or "",
            "rating": r.rating or "", "document_id": r.document_id,
            "document_title": d.title if d else "", "template_id": r.template_id,
            "note": r.note or "",
            "created_by": (u.real_name or u.username) if u else "",
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""}


@router.get("/{person}/replies")
def list_replies(person: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """某人的廉政意见出具记录（时间 / 用途 / 评级 / 关联文书）。"""
    rows = (db.query(IntegrityReplyRecord)
            .filter(IntegrityReplyRecord.person == person)
            .order_by(IntegrityReplyRecord.id.desc()).all())
    return [_reply_record_out(r, db) for r in rows]


class ReplyReq(BaseModel):
    purpose: str = "干部选拔任用"   # 干部选拔任用/评优评先/其他
    extra: str = ""
    template_id: int | None = None  # 复用文书模板库中的廉政意见模板（可选）


# 廉政意见文书的字段别名词典（模板字段 → 档案要素）
_REPLY_ALIASES = {
    "person": ["被审查人", "被反映人", "姓名", "对象", "人员", "当事人"],
    "purpose": ["用途", "事由", "任用情形"],
    "rating": ["廉政评级", "评级", "廉政结论"],
    "issue": ["核查情况", "核查结果"],
    "conclusion": ["结论", "回复意见", "意见"],
    "case_count": ["线索数", "问题线索数"],
    "conduct_count": ["四风数", "四风问题数"],
    "alert_count": ["预警数", "监督预警数"],
    "high_alert": ["高风险数", "高风险预警数"],
    "extra": ["备注", "补充"],
    "date": ["日期", "出具日期", "时间"],
    "post": ["职务"],
    "rank": ["职级"],
}


def _fill_reply_template(tpl: DocumentTemplate, canonical: dict) -> str:
    """按别名字典把档案要素填入廉政意见模板，未命中项留空由后续统一标记。"""
    content = tpl.template
    for f in (tpl.fields or []):
        key = (f.get("key") or "").strip()
        if not key:
            continue
        val = ""
        for canon, aliases in _FIELD_ALIASES.items():
            if key in aliases or any(a in key for a in aliases):
                val = str(canonical.get(canon, "") or "")
                break
        if not val:
            for canon, aliases in _REPLY_ALIASES.items():
                if key in aliases or any(a in key for a in aliases):
                    val = str(canonical.get(canon, "") or "")
                    break
        if not val:
            val = str(canonical.get(key, "") or "")
        content = content.replace("{{" + key + "}}", val)
    # 未带入的占位符统一标记，避免残留花括号直接进正文
    return re.sub(r"\{\{[^{}\n]+\}\}", "〔待补充〕", content)


@router.post("/{person}/reply")
def reply(person: str, data: ReplyReq, db: Session = Depends(get_db),
          user: User = Depends(get_current_user)):
    """生成《关于XXX同志廉政情况的回复意见》（A3 文书，LLM 可选，可复用模板）。"""
    idx = _persons_index(db)
    info = idx.get(person, {"cases": [], "conduct": [], "alerts": [], "units": []})
    archive = db.query(PersonArchive).filter(PersonArchive.person == person).first()
    units = "、".join(info["units"]) or (archive.unit if archive and archive.unit else "") or "（未登记单位）"
    r = _rating(info)
    has_issue = r["rating"] != "良好"
    if has_issue:
        issue_text = (f"经核查，该同志涉及问题线索 {r['case_count']} 条、四风问题 {r['conduct_count']} 条、"
                      f"监督预警 {r['alert_count']} 条（其中高风险 {r['high_alert']} 条）。"
                      f"建议暂缓使用或结合具体情形审慎研判。")
        conclusion = "存在可能影响使用的问题，建议暂缓"
    else:
        issue_text = "经核查，目前未收到该同志相关问题线索反映，无立案及处分记录。"
        conclusion = "暂未发现影响使用的问题"

    canonical = {
        "person": person, "subject": person, "unit": units,
        "purpose": data.purpose, "rating": r["rating"],
        "issue": issue_text, "conclusion": conclusion,
        "case_count": str(r["case_count"]), "conduct_count": str(r["conduct_count"]),
        "alert_count": str(r["alert_count"]), "high_alert": str(r["high_alert"]),
        "extra": data.extra, "date": datetime.utcnow().strftime("%Y年%m月%d日"),
        "post": archive.post if archive else "", "rank": archive.rank if archive else "",
    }
    tpl = db.get(DocumentTemplate, data.template_id) if data.template_id else None
    used_template = tpl.id if tpl else None
    if tpl:
        content = _fill_reply_template(tpl, canonical)
    else:
        content = (
            f"关于{person}同志廉政情况的回复意见\n\n"
            f"{units}{person}同志，因{data.purpose}需出具廉政意见。\n\n"
            f"一、基本情况\n{units}{person}同志。\n\n"
            f"二、核查情况\n{issue_text}\n\n"
            f"三、回复意见\n{conclusion}。\n\n"
            f"（本意见由系统根据业务数据自动聚合生成，仅供组织参考。）\n"
            f"中共XX纪委\n{data.extra}\n"
        )
    content = gongwen.format_document(content)
    gen = provider.chat(
        [{"role": "system", "content": "你是纪委廉政意见文书助手，请在不增加事实的前提下规范表述，输出完整正文。"},
         {"role": "user", "content": content}],
        scene="document", user_id=user.id)
    # 事实保全：润色结果必须保留对象姓名与廉政评级，否则弃用回到规则版本
    if gen and len(gen) > 50 and person in gen and r["rating"] in gen:
        content = gongwen.format_document(gen)

    d = Document(template_id=used_template, source="流程生成", title=f"关于{person}同志廉政情况的回复意见（AI初稿）",
                 case_name=person, content=content, status="AI初稿", created_by=user.id)
    db.add(d)
    db.flush()
    db.add(DocumentVersion(document_id=d.id, version_no=1, content=content,
                           note="廉政意见回复 AI 生成初稿", created_by=user.id))
    db.add(IntegrityReplyRecord(
        person=person, purpose=data.purpose, rating=r["rating"],
        document_id=d.id, template_id=used_template,
        note="复用模板" if used_template else "默认格式", created_by=user.id))
    db.commit()
    audit.write_log(db, user_id=user.id, username=user.username, action="integrity_reply",
                    resource_type="document", resource_id=d.id,
                    detail={"person": person, "template_id": used_template})
    return {"id": d.id, "title": d.title, "status": d.status, "content": content,
            "rating": r["rating"], "template_used": bool(used_template)}


# ---------------- 人物专属时限预警与督办（与智能督办预警中枢打通） ----------------
def _person_supervision(db: Session, name: str) -> dict:
    """汇总某人名下跨四路的时限 SLA、模型预警与督办闭环记录，形成可追溯监督画像。"""
    cases = db.query(Case).filter(Case.subject == name).all()
    conduct = db.query(ConductCase).filter(ConductCase.subject == name).all()

    def sla_of(kind: str, obj):
        if kind == "case":
            s = _case_sla(obj)
        elif kind == "conduct":
            s = _rect_sla(obj)
        else:
            return {"stage": "", "deadline": "", "days_left": None, "warn": False, "overdue": False}
        return {
            "stage": s.get("stage", ""), "deadline": s.get("deadline", ""),
            "days_left": s.get("days_left"), "warn": s.get("warn", False),
            "overdue": s.get("overdue", False),
        }

    case_rows, conduct_rows = [], []
    overdue = due_soon = 0
    for c in cases:
        s = sla_of("case", c)
        if s["overdue"]:
            overdue += 1
        elif s["warn"]:
            due_soon += 1
        case_rows.append({"id": c.id, "code": c.code, "title": c.title, "status": c.status,
                          "unit": c.unit or "", "owner": _owner_name(db, c.owner_id), "sla": s})
    for cc in conduct:
        s = sla_of("conduct", cc)
        if s["overdue"]:
            overdue += 1
        elif s["warn"]:
            due_soon += 1
        conduct_rows.append({"id": cc.id, "code": cc.code, "title": cc.title, "category": cc.category,
                             "status": cc.status, "unit": cc.unit or "",
                             "owner": _owner_name(db, cc.owner_id), "sla": s})

    # 模型预警（person 类）
    alerts = db.query(Alert).filter(Alert.ref_type == "person", Alert.ref_id == name).all()
    alert_rows = [{"id": a.id, "level": a.level, "source": a.source, "title": a.title,
                   "status": a.status} for a in alerts]

    # 督办闭环：本罪案/党风事项被下发的督办记录（按 ref 穿透）+ 督办对象点名到本人
    case_ids = {c.id for c in cases}
    conduct_ids = {cc.id for cc in conduct}
    disps = db.query(SuperviseDispatch).all()
    disp_rows = []
    for d in disps:
        related = (d.ref_type == "case" and d.ref_id in case_ids) or \
                  (d.ref_type == "conduct" and d.ref_id in conduct_ids) or \
                  (d.urge_to and name and d.urge_to == name)
        if not related:
            continue
        disp_rows.append({
            "id": d.id, "source": d.source, "ref_type": d.ref_type, "ref_code": d.ref_code,
            "ref_title": d.ref_title, "level": d.level, "status": d.status,
            "urge_to": d.urge_to, "note": d.note, "feedback": d.feedback,
            "created_at": d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else "",
        })
    open_dispatch = sum(1 for d in disp_rows if d["status"] != "已闭环")
    return {
        "name": name,
        "cases": case_rows, "conduct": conduct_rows,
        "alerts": alert_rows, "dispatches": disp_rows,
        "summary": {
            "case_total": len(case_rows), "conduct_total": len(conduct_rows),
            "overdue": overdue, "due_soon": due_soon,
            "alert_count": len(alert_rows), "open_dispatch": open_dispatch,
        },
    }


def _owner_name(db: Session, uid: int) -> str:
    if not uid:
        return ""
    u = db.get(User, uid)
    return (u.real_name or u.username) if u else ""


@router.get("/{person}/supervision")
def person_supervision(person: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """某人跨四路时限 SLA + 模型预警 + 督办闭环 的统一监督画像（与督办中枢联动）。"""
    return _person_supervision(db, person)


# ---------------- 人物关系图谱 ----------------
class RelationCreate(BaseModel):
    person: str
    related_name: str
    relation_type: str = "其他"      # 家庭成员/亲属/同事/特定关系人/利益关联/其他
    source: str = "手工录入"
    note: str = ""
    bidirectional: bool = False      # 双向关系（如配偶）：自动生成反向边


@router.post("/relations")
def add_relation(data: RelationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """新增一条人物关系（廉政档案关系图谱的边）；双向关系自动补反向边。"""
    if not data.person or not data.related_name:
        raise HTTPException(422, "主体与关联人不能为空")
    person = data.person.strip()
    related = data.related_name.strip()
    if person == related:
        raise HTTPException(422, "主体与关联人不能相同")
    # 查重：同一对主体之间同一关系类型只保留一条（双向视为等价）
    dup = db.query(PersonRelation).filter(
        PersonRelation.person == person, PersonRelation.related_name == related,
        PersonRelation.relation_type == data.relation_type).first()
    if dup:
        raise HTTPException(409, "该关系已存在")
    r = PersonRelation(person=person, related_name=related,
                       relation_type=data.relation_type, source=data.source, note=data.note,
                       bidirectional=bool(data.bidirectional), created_by=user.id)
    db.add(r)
    db.flush()
    if data.bidirectional:
        rev = db.query(PersonRelation).filter(
            PersonRelation.person == related, PersonRelation.related_name == person,
            PersonRelation.relation_type == data.relation_type).first()
        if not rev:
            db.add(PersonRelation(
                person=related, related_name=person,
                relation_type=data.relation_type, source=data.source,
                note="（双向关系·系统自动生成）", bidirectional=True, created_by=user.id))
    db.commit()
    db.refresh(r)
    audit.write_log(db, user_id=user.id, username=user.username, action="integrity_relation_add",
                    resource_type="person_relation", resource_id=r.id,
                    detail={"person": person, "bidirectional": bool(data.bidirectional)})
    return {"id": r.id, "person": r.person, "related_name": r.related_name,
            "relation_type": r.relation_type, "source": r.source, "note": r.note,
            "bidirectional": bool(r.bidirectional)}


def _rel_out(r: PersonRelation, direction: str) -> dict:
    return {"id": r.id, "person": r.person, "related_name": r.related_name,
            "relation_type": r.relation_type, "source": r.source, "note": r.note,
            "bidirectional": bool(r.bidirectional), "direction": direction}


@router.get("/{person}/relations")
def list_relations(person: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """某人的关系清单：含正向关系与反向关系（他人把本人录为关联人）。"""
    rows = []
    for r in db.query(PersonRelation).filter(PersonRelation.person == person).all():
        rows.append(_rel_out(r, "正向"))
    for r in db.query(PersonRelation).filter(PersonRelation.related_name == person).all():
        if r.person != person:  # 避免自环重复
            rows.append(_rel_out(r, "反向"))
    return rows


@router.delete("/relations/{rid}")
def del_relation(rid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(PersonRelation, rid)
    if not r:
        raise HTTPException(404, "关系不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.get("/{person}/graph")
def relation_graph(person: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """构建人物关系图谱（力导向结构，供前端渲染）：节点=人，边=关系。

    含 2 跳关联（关联人的关系也纳入）；节点携带实时监督足迹（超期数/未结督办数），
    对有预警/跨域/超期/被督办的主体标红（危险主体），并在 danger_related 给出危险关联人清单。"""
    # 风险人员集合：有 person 预警 或 跨业务域出现
    risk_names = set()
    for a in db.query(Alert).filter(Alert.ref_type == "person").all():
        if a.ref_id:
            risk_names.add(a.ref_id)
    persons_idx = aggregate_persons(db)
    for name, info in persons_idx.items():
        if len(info["domains"]) >= 2:
            risk_names.add(name)

    # 预计算：案件/党风按 subject 分组 + 全部督办记录（避免图中每节点重复全表扫描）
    from collections import defaultdict
    cases_by_subj = defaultdict(list)
    for c in db.query(Case).all():
        if c.subject:
            cases_by_subj[c.subject].append(c)
    conduct_by_subj = defaultdict(list)
    for cc in db.query(ConductCase).all():
        if cc.subject:
            conduct_by_subj[cc.subject].append(cc)
    all_disps = db.query(SuperviseDispatch).all()

    def footprint(name: str):
        cs = cases_by_subj.get(name, [])
        ccs = conduct_by_subj.get(name, [])
        overdue = 0
        for c in cs:
            if _case_sla(c).get("overdue"):
                overdue += 1
        for cc in ccs:
            if _rect_sla(cc).get("overdue"):
                overdue += 1
        case_ids = {c.id for c in cs}
        conduct_ids = {cc.id for cc in ccs}
        od = 0
        for d in all_disps:
            related = (d.ref_type == "case" and d.ref_id in case_ids) or \
                      (d.ref_type == "conduct" and d.ref_id in conduct_ids) or \
                      (d.urge_to and d.urge_to == name)
            if related and d.status != "已闭环":
                od += 1
        return overdue, od

    def node(name: str, ntype: str):
        overdue, od = footprint(name)
        risk = (name in risk_names) or overdue > 0 or od > 0
        return {"id": name, "label": name, "type": ntype,
                "risk": risk, "domains": list(persons_idx.get(name, {}).get("domains", [])),
                "sla_overdue": overdue, "open_dispatch": od, "badge": overdue + od}

    nodes, edges = [], []
    seen_nodes, seen_edges = set(), set()
    nodes.append(node(person, "focal"))
    seen_nodes.add(person)

    danger_related = []

    def add_rels(p: str, depth: int):
        # 正向边（p → 关联人）与反向边（他人把 p 录为关联人）一并纳入
        edges_of = [(r, r.related_name, r.relation_type, "正向")
                    for r in db.query(PersonRelation).filter(PersonRelation.person == p).all()]
        edges_of += [(r, r.person, r.relation_type, "反向")
                     for r in db.query(PersonRelation).filter(PersonRelation.related_name == p).all()
                     if r.person != p]
        for r, rel, rtype, direction in edges_of:
            label = rtype if direction == "正向" else f"{rtype}·反"
            if rel not in seen_nodes:
                rnode = node(rel, "related")
                nodes.append(rnode)
                seen_nodes.add(rel)
                if rnode["risk"] and rel != person:
                    danger_related.append({"name": rel, "relation_type": label,
                                           "sla_overdue": rnode["sla_overdue"],
                                           "open_dispatch": rnode["open_dispatch"]})
            ek = (p, rel)
            if ek not in seen_edges:
                edges.append({"source": p, "target": rel, "label": label, "type": rtype})
                seen_edges.add(ek)
            if depth < 2 and rel != person:
                add_rels(rel, depth + 1)  # 2 跳

    add_rels(person, 1)
    return {"focal": person, "nodes": nodes, "edges": edges, "danger_related": danger_related,
            "disclaimer": "关系图谱由手工维护与业务数据聚合生成，仅供组织参考，关联不等于违纪；红点表示该人/关联人名下存在未结时限预警或督办。"}

