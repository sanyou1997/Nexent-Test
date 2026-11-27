import io
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import audit, tenancy
from app.database import get_db
from app.deps import get_current_user
from app.models import User, ConductCase, Case, AuditLog
from app.schemas import HandoffIn

router = APIRouter(prefix="/api/conduct", tags=["党风（党风政风监督）"])

CATEGORIES = ["违规吃喝", "公车私用", "收送礼品礼金", "滥发津补贴", "形式主义官僚主义", "其他"]
SOURCES = ["日常监督", "节点监督", "巡视巡察", "信访", "媒体"]
STATUSES = ["待处置", "整改中", "已通报", "已办结"]

# 党风整改预警：整改阶段 / 时限红线
RECTIFY_STAGES = ["待整改", "整改中", "申请销号", "已销号"]
RECTIFY_DUE_DAYS = 60          # 整改期限默认 60 日（可据交办要求调整）
RECTIFY_WARN_DAYS = 7          # 临期预警阈值（≤7 天）

# 流程轨迹动作中文标签
ACTION_LABEL = {
    "conduct_create": "登记四风问题",
    "conduct_update": "修改信息",
    "conduct_expose": "通报曝光",
    "conduct_to_case": "移交案管线索",
    "conduct_rectify": "整改记录",
    "conduct_advance": "状态流转",
    "conduct_rectify_issue": "下达整改",
    "conduct_rectify_report": "报送整改进展",
    "conduct_rectify_apply": "申请销号",
    "conduct_rectify_close": "审核销号",
    "conduct_rectify_reopen": "退回整改",
}


def _content_disposition(inline: bool, name: str) -> str:
    """构造可承载中文文件名的 Content-Disposition（RFC 5987 filename*）。"""
    from urllib.parse import quote
    ascii_name = name.encode("ascii", "ignore").decode() or "export.xlsx"
    disp = "inline" if inline else "attachment"
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


class ConductCreate(BaseModel):
    subject: str = ""
    unit: str = ""
    category: str = ""
    title: str = ""
    content: str = ""
    source: str = "日常监督"
    extras: Optional[dict] = None


class ConductUpdate(BaseModel):
    subject: Optional[str] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    extras: Optional[dict] = None


class ConductRectify(BaseModel):
    req: str = ""       # 整改要求
    due: str = ""       # 整改时限（日期字符串，如 2026-12-31）
    note: str = ""      # 整改落实情况


def _gen_code():
    return f"DF{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:3].upper()}"


def _rect_sla(c: ConductCase) -> dict:
    """整改时限 SLA 引擎：以整改下达日为起点、整改期限为终点做倒计时与预警。

    尚未下达整改 → issued=False；已销号 → 不计入超期；否则按剩余天数判临期/超期。
    """
    if not c.rectify_issued_at or not c.rectify_due_at:
        return {"stage": "整改期限", "issued": False, "deadline": "",
                "days_left": None, "warn": False, "overdue": False, "stage_status": c.rectify_stage}
    dl = c.rectify_due_at
    now = datetime.utcnow()
    days_left = (dl - now).days
    closed = c.rectify_stage == "已销号"
    overdue = (not closed) and days_left < 0
    warn = (not closed) and (not overdue) and days_left <= RECTIFY_WARN_DAYS
    return {"stage": "整改期限", "issued": True,
            "deadline": dl.strftime("%Y-%m-%d"), "days_left": days_left,
            "warn": warn, "overdue": overdue, "stage_status": c.rectify_stage}


def _out(db: Session, c: ConductCase) -> dict:
    owner = db.get(User, c.owner_id) if c.owner_id else None
    creator = db.get(User, c.created_by) if c.created_by else None
    linked = db.get(Case, c.linked_case_id) if c.linked_case_id else None
    return {
        "id": c.id, "code": c.code, "subject": c.subject, "unit": c.unit,
        "category": c.category, "title": c.title, "content": c.content,
        "source": c.source, "status": c.status, "exposure": c.exposure,
        "linked_case_id": c.linked_case_id,
        "linked_case_code": linked.code if linked else "",
        "rectify_req": c.rectify_req or "",
        "rectify_due": c.rectify_due or "",
        "rectify_note": c.rectify_note or "",
        "rectified_at": c.rectified_at.strftime("%Y-%m-%d %H:%M") if c.rectified_at else "",
        "rectify_issued_at": c.rectify_issued_at.strftime("%Y-%m-%d %H:%M") if c.rectify_issued_at else "",
        "rectify_due_at": c.rectify_due_at.strftime("%Y-%m-%d %H:%M") if c.rectify_due_at else "",
        "rectify_stage": c.rectify_stage or "待整改",
        "rectify_report_at": c.rectify_report_at.strftime("%Y-%m-%d %H:%M") if c.rectify_report_at else "",
        "rectify_closed_at": c.rectify_closed_at.strftime("%Y-%m-%d %H:%M") if c.rectify_closed_at else "",
        "rectify_closed_by": c.rectify_closed_by or "",
        "rectify_sla": _rect_sla(c),
        "rectify_progress": (c.extras or {}).get("rectify_progress", []) if isinstance((c.extras or {}), dict) else [],
        "extras": c.extras or {},
        "owner": owner.real_name or owner.username if owner else "",
        "created_by": creator.real_name or creator.username if creator else "",
        "created_at": c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",
    }


@router.get("")
def list_conduct(category: str = "", status: str = "", q: str = "",
                 rectify_stage: str = "", overdue: int = 0,
                 page: int = 1, per_page: int = 10,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(ConductCase)
    if category:
        query = query.filter(ConductCase.category == category)
    if status:
        query = query.filter(ConductCase.status == status)
    if rectify_stage:
        query = query.filter(ConductCase.rectify_stage == rectify_stage)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            ConductCase.subject.like(like), ConductCase.unit.like(like),
            ConductCase.title.like(like), ConductCase.content.like(like),
            ConductCase.code.like(like)))
    items_all = query.order_by(ConductCase.id.desc()).all()
    # 超期筛选：Python 侧按 SLA 命中（与信访/案管一致）
    if overdue:
        items_all = [c for c in items_all if _rect_sla(c)["overdue"]]
    total = len(items_all)
    items = items_all[(page - 1) * per_page: (page - 1) * per_page + per_page]
    return {"items": [_out(db, c) for c in items], "total": total}


@router.get("/export")
def export_xlsx(category: str = "", status: str = "", q: str = "",
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """导出四风问题台账为 xlsx（utf-8，Excel 直接打开）。"""
    import openpyxl
    query = db.query(ConductCase)
    if category:
        query = query.filter(ConductCase.category == category)
    if status:
        query = query.filter(ConductCase.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            ConductCase.subject.like(like), ConductCase.unit.like(like),
            ConductCase.title.like(like), ConductCase.content.like(like),
            ConductCase.code.like(like)))
    rows = query.order_by(ConductCase.id.desc()).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "四风问题台账"
    ws.append(["编号", "涉及人员/单位", "单位", "问题类别", "来源", "状态",
               "是否通报", "整改要求", "整改时限", "整改落实情况", "关联案件", "登记人", "登记时间"])
    for c in rows:
        creator = db.get(User, c.created_by) if c.created_by else None
        linked = db.get(Case, c.linked_case_id) if c.linked_case_id else None
        ws.append([
            c.code, c.subject, c.unit, c.category, c.source, c.status,
            c.exposure, c.rectify_req or "", c.rectify_due or "", c.rectify_note or "",
            linked.code if linked else "",
            creator.real_name or creator.username if creator else "",
            c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    fname = f"四风问题台账_{datetime.now().strftime('%Y%m%d')}.xlsx"
    audit.write_log(db, user.id, user.username, action="conduct_export",
                    resource_type="conduct", resource_id="export", detail={"count": len(rows)})
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(False, fname)},
    )


@router.get("/stats")
def conduct_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cs = db.query(ConductCase).all()
    by_cat: dict = {}
    by_status: dict = {}
    by_source: dict = {}
    by_rectify_stage: dict = {}
    exposed = 0
    rectify_active = 0       # 整改中 + 申请销号（在整改）
    rectify_due_soon = 0     # 临期 ≤7 天
    rectify_overdue = 0      # 超期
    rectify_closed = 0       # 已销号
    for c in cs:
        by_cat[c.category] = by_cat.get(c.category, 0) + 1
        by_status[c.status] = by_status.get(c.status, 0) + 1
        by_source[c.source] = by_source.get(c.source, 0) + 1
        if c.exposure == "是":
            exposed += 1
        st = c.rectify_stage or "待整改"
        by_rectify_stage[st] = by_rectify_stage.get(st, 0) + 1
        sla = _rect_sla(c)
        if st in ("整改中", "申请销号"):
            rectify_active += 1
        if st == "已销号":
            rectify_closed += 1
        if sla["overdue"]:
            rectify_overdue += 1
        elif sla["warn"]:
            rectify_due_soon += 1
    transferred = sum(1 for c in cs if c.linked_case_id)
    rectify_total = sum(1 for c in cs if c.rectify_issued_at)  # 已下达整改的事项
    rectify_rate = round(rectify_closed / rectify_total * 100) if rectify_total else 0
    return {"total": len(cs), "by_cat": by_cat, "by_status": by_status,
            "by_source": by_source, "exposed": exposed, "transferred": transferred,
            "by_rectify_stage": by_rectify_stage,
            "rectify_active": rectify_active, "rectify_due_soon": rectify_due_soon,
            "rectify_overdue": rectify_overdue, "rectify_closed": rectify_closed,
            "rectify_total": rectify_total, "rectify_rate": rectify_rate}


@router.post("")
def create_conduct(data: ConductCreate, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    if data.category not in CATEGORIES:
        data.category = "其他"
    if data.source not in SOURCES:
        data.source = "日常监督"
    c = ConductCase(code=_gen_code(), subject=data.subject, unit=data.unit,
                    category=data.category, title=data.title, content=data.content,
                    source=data.source, owner_id=user.id, created_by=user.id,
                    extras=data.extras or {})
    db.add(c)
    db.commit()
    audit.write_log(db, user.id, user.username, action="conduct_create",
                    resource_type="conduct", resource_id=c.id, detail={"category": c.category})
    return _out(db, c)


@router.get("/{cid}/timeline")
def conduct_timeline(cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """流程轨迹：把该四风问题的审计记录（登记/修改/通报/移交/整改）串成时间轴。"""
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    logs = (db.query(AuditLog)
            .filter(AuditLog.resource_type == "conduct", AuditLog.resource_id == str(cid))
            .order_by(AuditLog.id.asc()).all())
    items = []
    for lg in logs:
        d = lg.detail if isinstance(lg.detail, dict) else {}
        label = ACTION_LABEL.get(lg.action, lg.action)
        desc = ""
        if lg.action == "conduct_create":
            desc = f"{d.get('category', '')}来源问题登记"
        elif lg.action == "conduct_update":
            desc = "修改字段：" + "、".join(d.get("changed", []) or [])
        elif lg.action == "conduct_expose":
            desc = f"通报曝光状态：{d.get('exposure', '')}"
        elif lg.action == "conduct_to_case":
            desc = f"移交案管线索（案件 {d.get('case_code', '')}）"
        elif lg.action == "conduct_rectify":
            desc = f"整改记录：{d.get('summary', '')}"
        elif lg.action == "conduct_rectify_issue":
            desc = f"下达整改：期限 {d.get('due', '')}（{d.get('days', '')} 日）"
        elif lg.action == "conduct_rectify_report":
            desc = f"报送进展：{d.get('note', '')}"
        elif lg.action == "conduct_rectify_apply":
            desc = "整改完成，申请销号"
        elif lg.action == "conduct_rectify_close":
            desc = f"审核销号（{d.get('by', '')}）"
        elif lg.action == "conduct_rectify_reopen":
            desc = "审核未通过，退回继续整改"
        elif lg.action == "conduct_advance":
            desc = f"状态流转至「{d.get('to', '')}」"
        if d.get("note"):
            desc = f"{desc}｜备注：{d['note']}"
        items.append({
            "id": lg.id, "action": lg.action, "label": label, "desc": desc,
            "username": lg.username,
            "created_at": lg.created_at.strftime("%Y-%m-%d %H:%M") if lg.created_at else "",
        })
    return {"conduct_id": cid, "code": c.code, "status": c.status, "items": items}


@router.post("/{cid}/rectify")
def rectify(cid: int, data: ConductRectify, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """整改记录留痕：填写整改要求/时限/落实情况；填了落实情况即视为整改办结。"""
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    c.rectify_req = data.req
    c.rectify_due = data.due
    c.rectify_note = data.note
    if data.note and c.status in ("待处置", "整改中", "已通报"):
        c.status = "已办结"
        c.rectified_at = datetime.utcnow()
    db.commit()
    summary = f"要求：{data.req or '—'}；时限：{data.due or '—'}；落实：{data.note or '—'}"
    audit.write_log(db, user.id, user.username, action="conduct_rectify",
                    resource_type="conduct", resource_id=cid,
                    detail={"summary": summary, "to_status": c.status})
    return _out(db, c)


@router.get("/{cid}")
def get_conduct(cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    return _out(db, c)


# ===================== 党风整改预警闭环（做深做透） =====================
class ConductRectifyIssue(BaseModel):
    req: str = ""                 # 整改要求
    due: str = ""                 # 整改期限日期（YYYY-MM-DD）；缺省按 due_days 推算
    due_days: int = RECTIFY_DUE_DAYS
    note: str = ""                # 首期整改进展（可选）


class ConductRectifyReport(BaseModel):
    note: str = ""                # 整改进展描述


@router.post("/{cid}/rectify-issue")
def rectify_issue(cid: int, data: ConductRectifyIssue, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """下达整改：设定整改要求与期限，启动时限 SLA 倒计时；重复下达拦截。"""
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    if c.rectify_issued_at and c.rectify_stage != "已销号":
        raise HTTPException(400, "已下达整改，不可重复下达")
    now = datetime.utcnow()
    if data.due:
        try:
            due_at = datetime.strptime(data.due, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "整改期限格式应为 YYYY-MM-DD")
    else:
        due_at = now + timedelta(days=data.due_days or RECTIFY_DUE_DAYS)
    c.rectify_req = data.req
    c.rectify_due = due_at.strftime("%Y-%m-%d")
    c.rectify_due_at = due_at
    c.rectify_issued_at = now
    c.rectify_stage = "整改中"
    if c.status in ("待处置", "已通报"):
        c.status = "整改中"
    if data.note:
        c.rectify_note = data.note
        ext = dict(c.extras or {})
        prog = list(ext.get("rectify_progress", []) or [])
        prog.append({"at": now.strftime("%Y-%m-%d %H:%M"), "by": user.real_name or user.username, "note": data.note})
        ext["rectify_progress"] = prog
        c.extras = ext
        c.rectify_report_at = now
    db.commit()
    audit.write_log(db, user.id, user.username, action="conduct_rectify_issue",
                    resource_type="conduct", resource_id=cid,
                    detail={"due": c.rectify_due, "days": (due_at - now).days})
    return _out(db, c)


@router.post("/{cid}/rectify-report")
def rectify_report(cid: int, data: ConductRectifyReport, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """报送整改进展：追加进展历史，更新最近报送时间（阶段保持整改中）。"""
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    if not c.rectify_issued_at:
        raise HTTPException(400, "尚未下达整改，请先下达整改")
    if not data.note:
        raise HTTPException(400, "请填写整改进展")
    now = datetime.utcnow()
    c.rectify_note = data.note
    c.rectify_report_at = now
    ext = dict(c.extras or {})
    prog = list(ext.get("rectify_progress", []) or [])
    prog.append({"at": now.strftime("%Y-%m-%d %H:%M"), "by": user.real_name or user.username, "note": data.note})
    ext["rectify_progress"] = prog
    c.extras = ext
    if c.rectify_stage == "待整改":
        c.rectify_stage = "整改中"
    db.commit()
    audit.write_log(db, user.id, user.username, action="conduct_rectify_report",
                    resource_type="conduct", resource_id=cid, detail={"note": data.note})
    return _out(db, c)


@router.post("/{cid}/rectify-apply")
def rectify_apply(cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """申请销号：整改完成，申请审核销号（须处于整改中）。"""
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    if c.rectify_stage != "整改中":
        raise HTTPException(400, "仅整改中事项可申请销号")
    c.rectify_stage = "申请销号"
    db.commit()
    audit.write_log(db, user.id, user.username, action="conduct_rectify_apply",
                    resource_type="conduct", resource_id=cid, detail={})
    return _out(db, c)


@router.post("/{cid}/rectify-close")
def rectify_close(cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """审核销号：确认整改到位，销号归档（须先申请销号；越级/重复拦截）。"""
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    if c.rectify_stage == "已销号":
        raise HTTPException(400, "已销号，不可重复销号")
    if c.rectify_stage != "申请销号":
        raise HTTPException(400, "未申请销号，不能直接审核销号")
    now = datetime.utcnow()
    c.rectify_stage = "已销号"
    c.status = "已办结"
    c.rectified_at = now
    c.rectify_closed_at = now
    c.rectify_closed_by = user.real_name or user.username
    db.commit()
    audit.write_log(db, user.id, user.username, action="conduct_rectify_close",
                    resource_type="conduct", resource_id=cid, detail={"by": c.rectify_closed_by})
    return _out(db, c)


@router.post("/{cid}/rectify-reopen")
def rectify_reopen(cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """退回整改：审核不通过，退回继续整改（须处于申请销号）。"""
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    if c.rectify_stage != "申请销号":
        raise HTTPException(400, "仅申请销号事项可退回")
    c.rectify_stage = "整改中"
    db.commit()
    audit.write_log(db, user.id, user.username, action="conduct_rectify_reopen",
                    resource_type="conduct", resource_id=cid, detail={})
    return _out(db, c)


@router.put("/{cid}")
def update_conduct(cid: int, data: ConductUpdate, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    changed = []
    for f in ("subject", "unit", "category", "title", "content", "source", "status", "extras"):
        v = getattr(data, f)
        if v is not None:
            setattr(c, f, v)
            changed.append(f)
    db.commit()
    audit.write_log(db, user.id, user.username, action="conduct_update",
                    resource_type="conduct", resource_id=cid, detail={"changed": changed})
    return _out(db, c)


@router.post("/{cid}/expose")
def expose(cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """通报曝光：切换是否通报（生成脱敏通报建议由 A3 文书处理，此处仅标记状态）。"""
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    c.exposure = "是" if c.exposure != "是" else "否"
    if c.exposure == "是" and c.status in ("待处置", "整改中"):
        c.status = "已通报"
    db.commit()
    audit.write_log(db, user.id, user.username, action="conduct_expose",
                    resource_type="conduct", resource_id=cid, detail={"exposure": c.exposure})
    return _out(db, c)


@router.post("/{cid}/to-case")
def to_case(cid: int, body: HandoffIn | None = None, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """一键移交案管：将四风问题转为案件线索（Case），形成监督闭环。"""
    c = db.get(ConductCase, cid)
    if not c:
        raise HTTPException(404, "党风问题不存在")
    if c.linked_case_id:
        raise HTTPException(400, "已转线索，不可重复移交")
    case = Case(code=f"AJ{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:3].upper()}",
                source="党风", subject=c.subject, unit=c.unit, title=c.title,
                content=c.content, problem_type=c.category, status="待研判",
                created_by=user.id, owner_id=user.id,
                dept_id=tenancy.route_to(db, user, tenancy.CLUE_DESK, body.dept_id if body else None))
    db.add(case)
    db.flush()
    tenancy.link_handoff(db, c, case, "党风转线索", user)
    c.linked_case_id = case.id
    if c.status == "待处置":
        c.status = "整改中"
    db.commit()
    audit.write_log(db, user.id, user.username, action="conduct_to_case",
                    resource_type="conduct", resource_id=cid,
                    detail={"case_id": case.id, "case_code": case.code})
    out = _out(db, c)
    out["case"] = {"id": case.id, "code": case.code}
    return out
