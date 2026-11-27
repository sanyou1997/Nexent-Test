import io as _io
import uuid
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit, gongwen, tenancy
from app.database import get_db
from app.deps import get_current_user
from app.llm import provider
from app.models import User, Case, Petition, Document, DocumentVersion, AuditLog, Interrogation, Trial

router = APIRouter(prefix="/api/cases", tags=["案管（案件监督管理）"])

# 状态流转顺序（advance 时校验顺序合法性）
STATUS_FLOW = ["待研判", "已分办", "初核中", "已立案", "审查调查中", "已移送审理", "已处置"]
DISPOSITIONS = ["谈话函询", "初步核实", "暂存待查", "予以了结"]
SOURCES = ["信访", "巡视", "审计", "上级交办", "主动发现"]

# —— 案管全流程合规时限（依《规则》《监察法》，自然日）——
DISPOSE_DUE_DAYS = 30   # 线索处置意见：登记后 30 日内提出
CHUHE_DUE_DAYS = 60     # 初步核实：60 日（2 个月）
REVIEW_DUE_DAYS = 90    # 审查调查：90 日（3 个月）
LIUZHI_DUE_DAYS = 90    # 留置：3 个月，可延长一次再 3 个月（法定最长 6 个月）
WARN_DAYS = 7           # 临期阈值（≤7 日提示）
LIUZHI_STATUSES = ["", "已采取", "已延长", "已解除"]

# 审计 action → 时间轴展示文案
ACTION_LABEL = {
    "case_create": "线索登记",
    "case_update": "信息修改",
    "case_dispose": "分办处置",
    "case_advance": "状态流转",
    "case_open": "立案",
    "case_liuzhi": "采取留置",
    "case_liuzhi_extend": "留置延长",
    "case_liuzhi_notify": "留置通知",
    "case_liuzhi_release": "解除留置",
    "case_interrogate": "讯问记录",
    "case_transfer_trial": "移送审理",
}


def _sla(c: Case) -> dict:
    """全流程时限引擎：线索处置(30)/初步核实(60)/审查调查(90) 临期(≤7日)+超期。"""
    now = datetime.now()
    stage = ""
    deadline = None
    if c.status in ("待研判", "已分办"):
        stage = "线索处置"
        deadline = (c.created_at + timedelta(days=DISPOSE_DUE_DAYS)) if c.created_at else None
    elif c.status == "初核中":
        stage = "初步核实"
        base = c.chuhe_at or c.created_at
        deadline = (base + timedelta(days=CHUHE_DUE_DAYS)) if base else None
    elif c.status in ("已立案", "审查调查中", "已移送审理"):
        stage = "审查调查"
        deadline = c.due_at or (
            (c.opened_at + timedelta(days=REVIEW_DUE_DAYS)) if c.opened_at else None)
    if deadline is None:
        return {"stage": stage, "deadline": "", "days_left": None, "warn": False, "overdue": False}
    days_left = (deadline - now).days
    return {
        "stage": stage,
        "deadline": deadline.strftime("%Y-%m-%d"),
        "days_left": days_left,
        "warn": 0 <= days_left <= WARN_DAYS,
        "overdue": days_left < 0,
    }


def _content_disposition(inline: bool, name: str) -> str:
    """HTTP 头只能 latin-1：中文名走 RFC5987 filename*，保留 ASCII filename 兜底。"""
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"case_export\"; filename*=UTF-8''{quote(name)}"
URGENCIES = ["高", "中", "低"]


# ---------------- 请求体 ----------------
class CaseCreate(BaseModel):
    subject: str = ""
    unit: str = ""
    title: str = ""
    content: str = ""
    problem_type: str = ""
    urgency: str = "中"
    source: str = "信访"
    due_at: Optional[str] = None          # ISO 字符串
    linked_petition_id: Optional[int] = None
    from_petition: Optional[int] = None    # 一键从信访转入


class CaseUpdate(BaseModel):
    subject: Optional[str] = None
    unit: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    problem_type: Optional[str] = None
    urgency: Optional[str] = None
    source: Optional[str] = None
    due_at: Optional[str] = None
    owner_id: Optional[int] = None
    extras: Optional[dict] = None


class CaseAdvance(BaseModel):
    status: str
    note: str = ""


class CaseDispose(BaseModel):
    disposition: str
    note: str = ""                 # 研判意见 / 处置依据
    meeting_note: str = ""         # 会议结论
    owner_id: Optional[int] = None # 研判时即分办到人
    due_at: Optional[str] = None   # 研判时定办结时限（ISO 日期）


class CaseOpen(BaseModel):
    # 立案决定书要素（可直接用默认值，由系统套模板生成初稿）
    basis: str = ""          # 立案依据（如：初核发现涉嫌XX）
    measure: str = ""        # 拟采取措施（如：留置/谈话）


# ---------------- 输出 ----------------
def _case_out(db: Session, c: Case) -> dict:
    creator = db.get(User, c.created_by) if c.created_by else None
    owner = db.get(User, c.owner_id) if c.owner_id else None
    pet = db.get(Petition, c.linked_petition_id) if c.linked_petition_id else None
    # 关联文书：立案决定书 + 案由（案号）相同的其他文书
    if c.opened_doc_id or c.code:
        conds = []
        if c.opened_doc_id:
            conds.append(Document.id == c.opened_doc_id)
        if c.code:
            conds.append(Document.case_name == c.code)
        from sqlalchemy import or_
        docs = db.query(Document).filter(or_(*conds)).order_by(Document.id.desc()).all()
    else:
        docs = []
    return {
        "id": c.id, "code": c.code, "source": c.source, "subject": c.subject,
        "unit": c.unit, "title": c.title, "content": c.content,
        "problem_type": c.problem_type, "urgency": c.urgency, "status": c.status,
        "disposition": c.disposition, "disposition_note": c.disposition_note,
        "meeting_note": c.meeting_note,
        "linked_petition_id": c.linked_petition_id,
        "linked_petition_code": pet.code if pet else "",
        "opened_doc_id": c.opened_doc_id,
        "opened_at": c.opened_at.strftime("%Y-%m-%d") if c.opened_at else "",
        "due_at": c.due_at.strftime("%Y-%m-%d") if c.due_at else "",
        "extras": c.extras or {},
        "owner": owner.real_name or owner.username if owner else "",
        "created_by": creator.real_name or creator.username if creator else "",
        "created_at": c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",
        "documents": [{"id": d.id, "title": d.title, "status": d.status} for d in docs],
        # —— 案管全流程合规字段 ——
        "chuhe_at": c.chuhe_at.strftime("%Y-%m-%d") if c.chuhe_at else "",
        "liuzhi_status": c.liuzhi_status or "",
        "liuzhi_approved_by": c.liuzhi_approved_by or "",
        "liuzhi_start": c.liuzhi_start.strftime("%Y-%m-%d") if c.liuzhi_start else "",
        "liuzhi_end": c.liuzhi_end.strftime("%Y-%m-%d") if c.liuzhi_end else "",
        "liuzhi_extended": c.liuzhi_extended,
        "liuzhi_release_at": c.liuzhi_release_at.strftime("%Y-%m-%d") if c.liuzhi_release_at else "",
        "liuzhi_notify_at": c.liuzhi_notify_at.strftime("%Y-%m-%d %H:%M") if c.liuzhi_notify_at else "",
        "liuzhi_notify_note": c.liuzhi_notify_note or "",
        "trial_id": c.trial_id,
        "transferred_at": c.transferred_at.strftime("%Y-%m-%d") if c.transferred_at else "",
        "sla": _sla(c),
        "liuzhi_sla": _liuzhi_sla(c),
    }


def _liuzhi_sla(c: Case) -> Optional[dict]:
    """留置期限倒计时（已采取/已延长）：含延长，超 6 个月法定上限红线。"""
    if c.liuzhi_status not in ("已采取", "已延长") or not c.liuzhi_end:
        return None
    days_left = (c.liuzhi_end - datetime.now()).days
    return {
        "days_left": days_left,
        "warn": 0 <= days_left <= WARN_DAYS,
        "overdue": days_left < 0,
        "extended": c.liuzhi_extended,
    }


def _gen_code():
    return f"AJ{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:3].upper()}"


# ---------------- 接口 ----------------
def _filtered_query(db: Session, source: str, status: str, urgency: str,
                    owner: int, q: str):
    qry = db.query(Case)
    if source:
        qry = qry.filter(Case.source == source)
    if status:
        qry = qry.filter(Case.status == status)
    if urgency:
        qry = qry.filter(Case.urgency == urgency)
    if owner:
        qry = qry.filter(Case.owner_id == owner)
    if q:
        like = f"%{q}%"
        qry = qry.filter(
            (Case.subject.like(like)) | (Case.title.like(like))
            | (Case.unit.like(like)) | (Case.code.like(like))
            | (Case.content.like(like)) | (Case.problem_type.like(like)))
    return qry


@router.get("")
def list_cases(
    source: str = "", status: str = "", urgency: str = "", owner: int = 0, q: str = "",
    overdue: int = 0, page: int = 1, per_page: int = 20,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """线索台账：支持来源/状态/紧急度/承办人/关键词/超期筛选 + 服务端分页。"""
    qry = _filtered_query(db, source, status, urgency, owner, q)
    total = qry.count()
    page = max(1, page)
    per_page = max(1, min(200, per_page))
    cases = qry.order_by(Case.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
    uids = {c.owner_id for c in cases if c.owner_id} | {c.created_by for c in cases if c.created_by}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    items = []
    for c in cases:
        o = _case_out(db, c)
        o["owner"] = (users.get(c.owner_id).real_name or users.get(c.owner_id).username) if c.owner_id and users.get(c.owner_id) else ""
        items.append(o)
    if overdue:   # 超期筛选：在 Python 侧按 SLA 命中（与信访一致）
        items = [o for o in items if o["sla"]["overdue"]]
        total = len(items)
    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.post("")
def create_case(data: CaseCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    # 一键从信访转入：复用其要素
    if data.from_petition:
        p = db.get(Petition, data.from_petition)
        if not p:
            raise HTTPException(404, "关联信访件不存在")
        el = p.elements if isinstance(p.elements, dict) else {}
        bf = el.get("被反映人") or {}
        data.subject = data.subject or (bf.get("姓名", "") if isinstance(bf, dict) else "")
        data.unit = data.unit or (bf.get("单位", "") if isinstance(bf, dict) else "")
        data.unit = data.unit or (bf.get("职务", "") if isinstance(bf, dict) else data.unit)
        data.content = data.content or p.content
        data.problem_type = data.problem_type or (
            "/".join(p.labels) if p.labels else "")
        data.source = "信访"
        data.linked_petition_id = p.id
    if data.urgency not in URGENCIES:
        data.urgency = "中"
    if data.source not in SOURCES:
        data.source = "信访"
    due = None
    if data.due_at:
        try:
            due = datetime.fromisoformat(data.due_at)
        except Exception:
            due = None
    c = Case(code=_gen_code(), source=data.source, subject=data.subject, unit=data.unit,
             title=data.title, content=data.content, problem_type=data.problem_type,
             urgency=data.urgency, linked_petition_id=data.linked_petition_id,
             due_at=due, owner_id=user.id, created_by=user.id)
    db.add(c)
    if data.from_petition:
        db.flush()
        tenancy.link_handoff(db, p, c, "信访转线索", user)
    db.commit()
    audit.write_log(db, user.id, user.username, action="case_create",
                    resource_type="case", resource_id=c.id,
                    detail={"source": c.source, "from_petition": data.from_petition})
    return _case_out(db, c)


@router.get("/export")
def export_xlsx(source: str = "", status: str = "", urgency: str = "", q: str = "",
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """导出线索台账为 xlsx（与页面筛选条件一致）。"""
    import openpyxl
    rows = _filtered_query(db, source, status, urgency, 0, q).order_by(Case.id.desc()).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "线索台账"
    ws.append(["案号", "被反映人", "单位/职务", "案由", "问题类型", "来源", "紧急度",
               "状态", "处置方式", "处置依据", "会议结论", "关联信访", "立案时间",
               "时限", "承办人", "登记时间"])
    for c in rows:
        pet = db.get(Petition, c.linked_petition_id) if c.linked_petition_id else None
        owner = db.get(User, c.owner_id) if c.owner_id else None
        ws.append([
            c.code, c.subject or "", c.unit or "", c.title or "", c.problem_type or "",
            c.source or "", c.urgency or "中", c.status or "", c.disposition or "",
            c.disposition_note or "", c.meeting_note or "",
            pet.code if pet else "", c.opened_at.strftime("%Y-%m-%d") if c.opened_at else "",
            c.due_at.strftime("%Y-%m-%d") if c.due_at else "",
            (owner.real_name or owner.username) if owner else "",
            c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",
        ])
    buf = _io.BytesIO()
    wb.save(buf)
    fname = f"线索台账_{datetime.now().strftime('%Y%m%d')}.xlsx"
    audit.write_log(db, user.id, user.username, action="case_export",
                    resource_type="case", resource_id="export", detail={"count": len(rows)})
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(False, fname)},
    )


@router.get("/stats")
def cases_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """案管监督合规看板：全流程时限超期/临期 + 留置合规 + 移送审理 + 时限合规率。"""
    cs = db.query(Case).all()
    by_source: dict = {}
    by_type: dict = {}
    by_status: dict = {}
    overdue = 0
    due_soon = 0
    active_total = 0
    liuzhi_active = 0
    liuzhi_overdue = 0
    liuzhi_extended = 0
    transferred = 0
    now = datetime.now()
    for c in cs:
        by_source[c.source] = by_source.get(c.source, 0) + 1
        if c.problem_type:
            for t in c.problem_type.split("/"):
                t = t.strip()
                if t:
                    by_type[t] = by_type.get(t, 0) + 1
        by_status[c.status] = by_status.get(c.status, 0) + 1
        s = _sla(c)
        if s["stage"]:   # 在办（受时限约束）
            active_total += 1
            if s["overdue"]:
                overdue += 1
            elif s["warn"]:
                due_soon += 1
        if c.liuzhi_status in ("已采取", "已延长"):
            liuzhi_active += 1
            if c.liuzhi_extended:
                liuzhi_extended += 1
            if c.liuzhi_end and c.liuzhi_end < now:
                liuzhi_overdue += 1
        if c.trial_id:
            transferred += 1
    opened = sum(1 for c in cs if c.status in ("已立案", "审查调查中", "初核中"))
    done = sum(1 for c in cs if c.status in ("已处置", "已了结", "已移送审理"))
    compliance_rate = round((active_total - overdue) / active_total * 100, 1) if active_total else 0.0
    return {
        "total": len(cs), "by_source": by_source, "by_type": by_type,
        "by_status": by_status, "overdue": overdue, "due_soon": due_soon,
        "active_total": active_total,
        "liuzhi_active": liuzhi_active, "liuzhi_overdue": liuzhi_overdue,
        "liuzhi_extended": liuzhi_extended, "transferred": transferred,
        "opened": opened, "done": done, "compliance_rate": compliance_rate,
    }


@router.get("/{cid}")
def get_case(cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    return _case_out(db, c)


@router.get("/{cid}/timeline")
def case_timeline(cid: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """流程轨迹：把该线索的审计记录（登记/流转/处置/立案/修改）串成时间轴。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    logs = (db.query(AuditLog)
            .filter(AuditLog.resource_type == "case", AuditLog.resource_id == str(cid))
            .order_by(AuditLog.id.asc()).all())
    items = []
    for lg in logs:
        d = lg.detail if isinstance(lg.detail, dict) else {}
        label = ACTION_LABEL.get(lg.action, lg.action)
        desc = ""
        if lg.action == "case_advance":
            desc = f"状态流转至「{d.get('to', '')}」"
        elif lg.action == "case_dispose":
            desc = f"处置方式：{d.get('disposition', '')}"
        elif lg.action == "case_open":
            desc = f"立案，生成立案决定书（文书 #{d.get('doc_id', '')}，{d.get('mode', '')}）"
        elif lg.action == "case_create":
            src = d.get("source", "")
            desc = f"{src}来源线索登记" + ("（由信访件转入）" if d.get("from_petition") else "")
        elif lg.action == "case_liuzhi":
            desc = f"采取留置措施（审批：{d.get('approved_by', '')}，期限至 {d.get('end', '')}）"
        elif lg.action == "case_liuzhi_extend":
            desc = f"留置延长一次（新届满 {d.get('end', '')}）"
        elif lg.action == "case_liuzhi_notify":
            desc = f"留置后通知单位/家属：{d.get('note', '')}"
        elif lg.action == "case_liuzhi_release":
            desc = f"解除留置：{d.get('note', '')}"
        elif lg.action == "case_interrogate":
            desc = f"讯问记录（被讯问人 {d.get('interrogated', '')}，{d.get('duration', '')} 小时）"
        elif lg.action == "case_transfer_trial":
            desc = f"移送审理（审理事项 #{d.get('trial_id', '')}）"
        elif lg.action == "case_update":
            desc = "修改字段：" + "、".join(d.get("changed", []) or [])
        if d.get("note"):
            desc = f"{desc}｜备注：{d['note']}"
        items.append({
            "id": lg.id, "action": lg.action, "label": label, "desc": desc,
            "username": lg.username,
            "created_at": lg.created_at.strftime("%Y-%m-%d %H:%M") if lg.created_at else "",
        })
    return {"case_id": cid, "code": c.code, "status": c.status, "items": items}


@router.get("/{cid}/doc")
def case_doc(cid: int, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """立案决定书正文（用于案管抽屉内直接查看，不跳页面）。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    if not c.opened_doc_id:
        raise HTTPException(404, "尚未立案，暂无立案决定书")
    d = db.get(Document, c.opened_doc_id)
    if not d:
        raise HTTPException(404, "立案决定书已不存在")
    return {
        "id": d.id, "title": d.title, "status": d.status, "content": d.content or "",
        "case_code": c.code, "subject": c.subject or "",
        "opened_at": c.opened_at.strftime("%Y-%m-%d") if c.opened_at else "",
    }


@router.get("/{cid}/doc/export")
def case_doc_export(cid: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """立案决定书下载（txt，UTF-8）。"""
    data = case_doc(cid, db, user)
    fname = f"{data['case_code']}_立案决定书.txt"
    return Response(
        content=data["content"].encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(False, fname)},
    )


@router.put("/{cid}")
def update_case(cid: int, data: CaseUpdate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    changed = []
    for f in ("subject", "unit", "title", "content", "problem_type", "urgency",
             "source", "owner_id", "extras"):
        v = getattr(data, f)
        if v is not None:
            if f == "urgency" and v not in URGENCIES:
                continue
            setattr(c, f, v)
            changed.append(f)
    if data.due_at is not None:
        try:
            c.due_at = datetime.fromisoformat(data.due_at) if data.due_at else None
        except Exception:
            pass
        changed.append("due_at")
    db.commit()
    audit.write_log(db, user.id, user.username, action="case_update",
                    resource_type="case", resource_id=cid, detail={"changed": changed})
    return _case_out(db, c)


# 立案及之后的阶段（线索处置、立案操作不再适用）
FILED_STAGES = ("已立案", "审查调查中", "已移送审理", "已处置", "已了结")


@router.post("/{cid}/dispose")
def dispose(cid: int, data: CaseDispose, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """分办处置：记录处置方式、依据与会议结论。予以了结 → 状态置已了结。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    if data.disposition not in DISPOSITIONS:
        raise HTTPException(400, "处置方式应为：谈话函询/初步核实/暂存待查/予以了结")
    if c.status in FILED_STAGES:
        raise HTTPException(400, f"该案件当前为「{c.status}」，线索处置（谈话函询 / 初步核实等）只适用于立案前")
    old = c.status
    c.disposition = data.disposition
    c.disposition_note = data.note
    c.meeting_note = data.meeting_note
    if data.owner_id is not None:
        c.owner_id = data.owner_id
    if data.due_at is not None:
        try:
            c.due_at = datetime.fromisoformat(data.due_at) if data.due_at else None
        except Exception:
            pass
    if data.disposition == "予以了结":
        c.status = "已了结"
    elif c.status == "待研判":
        c.status = "已分办"
    db.commit()
    audit.write_log(db, user.id, user.username, action="case_dispose",
                    resource_type="case", resource_id=cid,
                    detail={"disposition": data.disposition, "from": old, "to": c.status,
                            "owner_id": data.owner_id, "due_at": data.due_at,
                            "note": data.meeting_note or ""})
    return _case_out(db, c)


@router.post("/{cid}/advance")
def advance(cid: int, data: CaseAdvance, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """状态流转：待研判→已分办→初核中→已立案→已处置。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    if data.status not in STATUS_FLOW:
        raise HTTPException(400, "非法状态")
    # 允许跳到已处置/已了结，但须经过已立案之前阶段（顺序校验）
    cur = STATUS_FLOW.index(c.status) if c.status in STATUS_FLOW else -1
    nxt = STATUS_FLOW.index(data.status)
    if nxt < cur:
        raise HTTPException(400, "状态不可回退")
    old = c.status
    c.status = data.status
    if data.status == "初核中" and not c.chuhe_at:
        c.chuhe_at = datetime.utcnow()   # 初核时限 SLA 起点
    if data.note and c.meeting_note:
        c.meeting_note = c.meeting_note + "\n" + data.note
    elif data.note:
        c.meeting_note = data.note
    db.commit()
    audit.write_log(db, user.id, user.username, action="case_advance",
                    resource_type="case", resource_id=cid,
                    detail={"from": old, "to": data.status, "note": data.note or ""})
    return _case_out(db, c)


@router.post("/{cid}/open")
def open_case(cid: int, data: CaseOpen, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """立案：状态置已立案，登记立案时间，并调用 A3 文书生成「立案决定书」初稿。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    if c.status in FILED_STAGES:
        raise HTTPException(400, f"该案件当前为「{c.status}」，已立案，不能重复立案")
    c.status = "已立案"
    c.opened_at = datetime.utcnow()
    if not c.due_at:
        c.due_at = c.opened_at + timedelta(days=REVIEW_DUE_DAYS)  # 审查调查 90 日
    # 事务边界：先把状态改动落盘再调模型。
    # 本地模型单次调用可能耗时数秒至数十秒，若此时仍持有 SQLite 写锁，
    # 其它连接（含 provider 记录调用日志的连接）会直接报 database is locked。
    db.commit()

    # 生成立案决定书初稿（复用 A3 规范化能力）
    title = f"关于对{c.subject or '×××'}同志立案审查调查的决定"
    raw = (
        f"{title}\n\n"
        f"根据《中国共产党纪律检查机关监督执纪工作规则》等有关规定，"
        f"经研究，决定对{c.unit or ''}{c.subject or '×××'}同志涉嫌"
        f"{c.problem_type or '违纪违法'}问题立案审查调查。\n\n"
        f"立案依据：{data.basis or '经初步核实，发现存在涉嫌违纪违法问题线索。'}\n\n"
        f"审查调查措施：{data.measure or '依规依纪依法采取相应审查调查措施。'}\n\n"
        f"本决定自印发之日起执行。\n"
    )
    content = gongwen.format_document(raw)
    mode = "template+rules"
    gen = provider.chat(
        [{"role": "system", "content": "你是纪检监察文书助手，请在不增加事实的前提下规范表述，输出完整文书正文。"},
         {"role": "user", "content": content}],
        scene="document", user_id=user.id)
    if gen and len(gen) > 50:
        # 事实保全：立案决定书中的被审查调查人姓名属关键事实，
        # 模型润色若把它改掉或漏掉，宁可弃用润色结果，不用错稿。
        if c.subject and c.subject not in gen:
            mode = "template+rules"
        else:
            content = gen
            mode = "template+llm"
    content = gongwen.format_document(content)

    d = Document(template_id=None, source="流程生成", title=f"{c.code}——{title}（AI初稿）",
                 case_name=c.code, content=content, status="AI初稿", created_by=user.id)
    db.add(d)
    db.flush()
    db.add(DocumentVersion(document_id=d.id, version_no=1, content=content,
                           note="立案决定书 AI 生成初稿", created_by=user.id))
    c.opened_doc_id = d.id
    db.commit()
    audit.write_log(db, user.id, user.username, action="case_open",
                    resource_type="case", resource_id=cid,
                    detail={"doc_id": d.id, "mode": mode})
    out = _case_out(db, c)
    out["document"] = {"id": d.id, "title": d.title, "status": d.status, "content": content}
    return out


# ===================== 案管全流程合规：留置 / 讯问 / 移送审理 =====================

class LiuzhiTake(BaseModel):
    approved_by: str = ""
    start: Optional[str] = None     # ISO；默认 now
    note: str = ""


class LiuzhiExtend(BaseModel):
    note: str = ""


class LiuzhiNotify(BaseModel):
    notified_at: Optional[str] = None
    note: str = ""


class LiuzhiRelease(BaseModel):
    note: str = ""


class InterrogationCreate(BaseModel):
    interrogated: str = ""
    interrogator: str = ""
    at: Optional[str] = None
    location: str = ""
    duration_hours: float = 0.0
    rights_informed: bool = False
    rest_meal_ok: bool = False
    note: str = ""


class TransferTrial(BaseModel):
    title: str = ""
    summary: str = ""
    dept_id: int | None = None   # 承办审理的部门（不填自动确定）


def _interro_issues(it: Interrogation) -> list:
    issues = []
    if not it.rights_informed:
        issues.append("未记录权利告知")
    if not it.rest_meal_ok:
        issues.append("未保障饮食与休息")
    if it.duration_hours and it.duration_hours > 12:
        issues.append("单次讯问超过12小时（须保障必要休息）")
    return issues


@router.post("/{cid}/liuzhi")
def take_liuzhi(cid: int, data: LiuzhiTake, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """采取留置措施：仅已立案/审查调查中案件；期限默认 3 个月；记录审批单位。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    if c.status not in ("已立案", "审查调查中"):
        raise HTTPException(400, "仅已立案/审查调查中案件可采取留置")
    if c.liuzhi_status in ("已采取", "已延长"):
        raise HTTPException(400, "该案件已采取留置，请勿重复采取")
    start = datetime.fromisoformat(data.start) if data.start else datetime.utcnow()
    end = start + timedelta(days=LIUZHI_DUE_DAYS)
    c.liuzhi_status = "已采取"
    c.liuzhi_approved_by = data.approved_by
    c.liuzhi_start = start
    c.liuzhi_end = end
    c.liuzhi_extended = False
    c.liuzhi_release_at = None
    db.commit()
    audit.write_log(db, user.id, user.username, action="case_liuzhi",
                    resource_type="case", resource_id=cid,
                    detail={"approved_by": data.approved_by, "start": start.strftime("%Y-%m-%d"),
                            "end": end.strftime("%Y-%m-%d"), "note": data.note})
    return _case_out(db, c)


@router.post("/{cid}/liuzhi/extend")
def extend_liuzhi(cid: int, data: LiuzhiExtend, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """留置延长一次：自原届满日再延长 3 个月（法定最长 6 个月，不可二次延长）。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    if c.liuzhi_status != "已采取":
        raise HTTPException(400, "须先采取留置方可延长")
    if c.liuzhi_extended:
        raise HTTPException(400, "留置已延长一次，不得超过法定最长期限（6 个月）")
    base = c.liuzhi_end or (c.liuzhi_start + timedelta(days=LIUZHI_DUE_DAYS))
    new_end = base + timedelta(days=LIUZHI_DUE_DAYS)
    c.liuzhi_end = new_end
    c.liuzhi_extended = True
    c.liuzhi_status = "已延长"
    db.commit()
    audit.write_log(db, user.id, user.username, action="case_liuzhi_extend",
                    resource_type="case", resource_id=cid,
                    detail={"end": new_end.strftime("%Y-%m-%d"), "note": data.note})
    return _case_out(db, c)


@router.post("/{cid}/liuzhi/notify")
def notify_liuzhi(cid: int, data: LiuzhiNotify, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """留置后 24 小时内通知被留置人所在单位/家属（合规留痕）。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    if c.liuzhi_status not in ("已采取", "已延长"):
        raise HTTPException(400, "未采取留置，无需通知")
    c.liuzhi_notify_at = datetime.fromisoformat(data.notified_at) if data.notified_at else datetime.utcnow()
    c.liuzhi_notify_note = data.note
    db.commit()
    audit.write_log(db, user.id, user.username, action="case_liuzhi_notify",
                    resource_type="case", resource_id=cid, detail={"note": data.note})
    return _case_out(db, c)


@router.post("/{cid}/liuzhi/release")
def release_liuzhi(cid: int, data: LiuzhiRelease, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """解除留置（审查调查结束或变更措施时）。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    if c.liuzhi_status not in ("已采取", "已延长"):
        raise HTTPException(400, "未采取留置，无需解除")
    c.liuzhi_status = "已解除"
    c.liuzhi_release_at = datetime.utcnow()
    db.commit()
    audit.write_log(db, user.id, user.username, action="case_liuzhi_release",
                    resource_type="case", resource_id=cid, detail={"note": data.note})
    return _case_out(db, c)


@router.post("/{cid}/interrogations")
def add_interrogation(cid: int, data: InterrogationCreate, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """讯问记录（合规台账）：立案后方可讯问；自动校验权利告知/饮食休息/单次时长。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    if c.status not in ("已立案", "审查调查中", "已移送审理"):
        raise HTTPException(400, "讯问须立案后进行")
    at = datetime.fromisoformat(data.at) if data.at else datetime.utcnow()
    it = Interrogation(case_id=cid, interrogated=data.interrogated, interrogator=data.interrogator,
                       at=at, location=data.location, duration_hours=data.duration_hours,
                       rights_informed=data.rights_informed, rest_meal_ok=data.rest_meal_ok,
                       note=data.note, created_by=user.id)
    db.add(it)
    db.commit()
    issues = _interro_issues(it)
    audit.write_log(db, user.id, user.username, action="case_interrogate",
                    resource_type="case", resource_id=cid,
                    detail={"interrogated": data.interrogated, "duration": data.duration_hours,
                            "issues": issues, "note": data.note})
    return {
        "id": it.id, "case_id": cid, "interrogated": it.interrogated,
        "interrogator": it.interrogator, "at": at.strftime("%Y-%m-%d %H:%M"),
        "location": it.location, "duration_hours": it.duration_hours,
        "rights_informed": it.rights_informed, "rest_meal_ok": it.rest_meal_ok,
        "note": it.note, "compliance_issues": issues,
    }


@router.get("/{cid}/interrogations")
def list_interrogations(cid: int, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """讯问台账：列出本案全部讯问记录及累计时长、合规问题。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    its = db.query(Interrogation).filter(Interrogation.case_id == cid).order_by(
        Interrogation.id.desc()).all()
    items = []
    total_duration = 0.0
    for it in its:
        total_duration += it.duration_hours or 0.0
        items.append({
            "id": it.id, "interrogated": it.interrogated, "interrogator": it.interrogator,
            "at": it.at.strftime("%Y-%m-%d %H:%M") if it.at else "",
            "location": it.location, "duration_hours": it.duration_hours,
            "rights_informed": it.rights_informed, "rest_meal_ok": it.rest_meal_ok,
            "note": it.note, "compliance_issues": _interro_issues(it),
        })
    return {"items": items, "total": len(items), "total_duration": round(total_duration, 1)}


@router.delete("/{cid}/interrogations/{iid}")
def del_interrogation(cid: int, iid: int, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    it = db.get(Interrogation, iid)
    if not it or it.case_id != cid:
        raise HTTPException(404, "讯问记录不存在")
    db.delete(it)
    db.commit()
    return {"ok": True}


@router.post("/{cid}/transfer-trial")
def transfer_to_trial(cid: int, data: TransferTrial, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """移送审理：审查调查结束 → 创建关联审理事项（Trial），案件置已移送审理并阻断再处置。"""
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    if c.status not in ("已立案", "审查调查中"):
        raise HTTPException(400, "须已立案/审查调查中方可移送审理")
    if c.trial_id:
        raise HTTPException(400, f"该案已移送审理（trial_id={c.trial_id}）")
    target = tenancy.route_to(db, user, ("trials",), data.dept_id)
    t = Trial(case_id=c.id,
              title=data.title or f"{c.subject or '×××'}违纪违法案审理",
              summary=data.summary, status="待阅卷", created_by=user.id, dept_id=target)
    db.add(t)
    db.flush()
    tenancy.link_handoff(db, c, t, "移送审理", user)
    c.trial_id = t.id
    c.transferred_at = datetime.utcnow()
    c.status = "已移送审理"
    db.commit()
    audit.write_log(db, user.id, user.username, action="case_transfer_trial",
                    resource_type="case", resource_id=cid,
                    detail={"trial_id": t.id, "dept": tenancy.dept_name(db, target)})
    out = _case_out(db, c)
    out["trial"] = {"id": t.id, "title": t.title, "status": t.status, "dept_name": tenancy.dept_name(db, target)}
    return out
