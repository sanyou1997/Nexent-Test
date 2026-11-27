import io
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import audit, tenancy
from app.database import get_db
from app.deps import get_current_user
from app.models import User, Inspection, InspectionFinding, Case, ConductCase, Petition, AuditLog

router = APIRouter(prefix="/api/inspections", tags=["巡察（巡视巡察监督）"])

INSPECT_STATUS = ["准备中", "进驻中", "反馈中", "整改中", "已结束"]
TRANSFER_TO = ["案管", "党风", "信访", "未移交"]
FIX_STATUS = ["待整改", "整改中", "已销号"]
FIX_EFFECT = ["未评估", "好", "中", "差"]     # 整改成效评估（成果运用）
WARN_DAYS = 7                                # 临期预警阈值（天）

# 流程轨迹动作中文标签
ACTION_LABEL = {
    "inspection_create": "创建巡察任务",
    "inspection_advance": "任务状态流转",
    "inspection_finding_add": "录入发现问题",
    "inspection_to_case": "移交案管线索",
    "inspection_to_conduct": "移交党风问题",
    "inspection_to_petition": "移交信访件",
    "inspection_finding_fix": "问题整改",
}


def _content_disposition(inline: bool, name: str) -> str:
    """构造可承载中文文件名的 Content-Disposition（RFC 5987 filename*）。"""
    from urllib.parse import quote
    ascii_name = name.encode("ascii", "ignore").decode() or "export.xlsx"
    disp = "inline" if inline else "attachment"
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


class InspectCreate(BaseModel):
    round_name: str = ""
    target: str = ""
    group_name: str = ""
    start_date: str = ""
    end_date: str = ""


class FindingCreate(BaseModel):
    content: str = ""
    category: str = ""
    severity: str = "中"


class FindingTransfer(BaseModel):
    transfer_to: str                 # 案管 / 党风 / 信访
    subject: str = ""
    unit: str = ""
    title: str = ""
    content: str = ""
    dept_id: int | None = None       # 接收部门（不填自动确定）


class FindingFix(BaseModel):
    fix_status: str = ""      # 待整改/整改中/已销号（可空，仅填整改记录时不改状态）
    req: str = ""            # 整改要求
    due: str = ""            # 整改时限（日期字符串，如 2026-12-31）
    note: str = ""           # 整改落实情况
    fix_effect: str = ""      # 整改成效：未评估/好/中/差（可空）


def _gen_code(prefix: str):
    return f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:3].upper()}"


def _insp_out(db: Session, ins: Inspection) -> dict:
    creator = db.get(User, ins.created_by) if ins.created_by else None
    n = db.query(InspectionFinding).filter(InspectionFinding.inspection_id == ins.id).count()
    transferred = db.query(InspectionFinding).filter(
        InspectionFinding.inspection_id == ins.id,
        InspectionFinding.transfer_to != "未移交",
        InspectionFinding.transfer_to != "").count()
    return {
        "id": ins.id, "code": ins.code, "round_name": ins.round_name,
        "target": ins.target, "group_name": ins.group_name,
        "start_date": ins.start_date, "end_date": ins.end_date,
        "status": ins.status, "finding_count": n, "transferred": transferred,
        "created_by": creator.real_name or creator.username if creator else "",
        "created_at": ins.created_at.strftime("%Y-%m-%d %H:%M") if ins.created_at else "",
    }


def _finding_sla(f: InspectionFinding) -> dict:
    """整改时限 SLA：已销号=ok；有 due_at 且未销号=计算剩余天数/临期/超期；无期限=不计时。"""
    if f.fix_status == "已销号":
        return {"level": "ok", "stage": "已销号"}
    if not f.due_at:
        return {"level": "none", "stage": "未设时限"}
    days_left = (f.due_at - datetime.utcnow()).days
    if days_left < 0:
        return {"level": "overdue", "stage": "超期未整改", "days_left": days_left, "overdue_days": -days_left}
    if days_left <= WARN_DAYS:
        return {"level": "warn", "stage": "临期", "days_left": days_left}
    return {"level": "ok", "stage": "整改中", "days_left": days_left}


def _finding_out(f: InspectionFinding) -> dict:
    return {
        "id": f.id, "inspection_id": f.inspection_id, "content": f.content,
        "category": f.category, "severity": f.severity, "transfer_to": f.transfer_to,
        "transfer_ref_id": f.transfer_ref_id, "fix_status": f.fix_status,
        "due_at": f.due_at.strftime("%Y-%m-%d") if f.due_at else "",
        "fix_req": f.fix_req or "", "fix_note": f.fix_note or "",
        "fix_at": f.fix_at.strftime("%Y-%m-%d %H:%M") if f.fix_at else "",
        "fix_effect": f.fix_effect or "未评估",
        "sla": _finding_sla(f),
        "created_at": f.created_at.strftime("%Y-%m-%d %H:%M") if f.created_at else "",
    }


@router.get("")
def list_inspections(status: str = "", q: str = "",
                    page: int = 1, per_page: int = 10,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Inspection)
    if status:
        query = query.filter(Inspection.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Inspection.target.like(like), Inspection.round_name.like(like),
            Inspection.group_name.like(like), Inspection.code.like(like)))
    total = query.count()
    items = query.order_by(Inspection.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return {"items": [_insp_out(db, i) for i in items], "total": total}


@router.get("/findings")
def list_findings(category: str = "", severity: str = "", fix_status: str = "",
                  overdue: int = 0, q: str = "", page: int = 1, per_page: int = 10,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """巡察问题库：跨巡察任务的全量问题清单，支持类别/程度/整改状态/超期筛选。"""
    query = db.query(InspectionFinding)
    if category:
        query = query.filter(InspectionFinding.category == category)
    if severity:
        query = query.filter(InspectionFinding.severity == severity)
    if fix_status:
        query = query.filter(InspectionFinding.fix_status == fix_status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(InspectionFinding.content.like(like), InspectionFinding.category.like(like)))
    items = query.order_by(InspectionFinding.id.desc()).all()
    out = [_finding_out(f) for f in items]
    if overdue:
        out = [o for o in out if o["sla"]["level"] == "overdue"]
    total = len(out)
    out = out[(page - 1) * per_page: (page - 1) * per_page + per_page]
    return {"items": out, "total": total}


@router.get("/export")
def export_xlsx(status: str = "", q: str = "",
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """导出巡察台账为 xlsx（utf-8，Excel 直接打开）。"""
    import openpyxl
    query = db.query(Inspection)
    if status:
        query = query.filter(Inspection.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Inspection.target.like(like), Inspection.round_name.like(like),
            Inspection.group_name.like(like), Inspection.code.like(like)))
    rows = query.order_by(Inspection.id.desc()).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "巡察台账"
    ws.append(["巡字编号", "轮次", "巡察对象", "巡察组", "开始", "结束", "状态",
               "发现问题数", "已移交数", "登记人", "登记时间"])
    for i in rows:
        o = _insp_out(db, i)
        ws.append([
            i.code, i.round_name, i.target, i.group_name, i.start_date, i.end_date, i.status,
            o["finding_count"], o["transferred"], o["created_by"],
            i.created_at.strftime("%Y-%m-%d %H:%M") if i.created_at else "",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    fname = f"巡察台账_{datetime.now().strftime('%Y%m%d')}.xlsx"
    audit.write_log(db, user.id, user.username, action="inspection_export",
                    resource_type="inspection", resource_id="export", detail={"count": len(rows)})
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(False, fname)},
    )


@router.get("/stats")
def inspect_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """巡察成果运用看板：问题总量、整改进度、时限预警、移交去向、整改成效分布。"""
    ins = db.query(Inspection).all()
    findings = db.query(InspectionFinding).all()
    by_status: dict = {}
    for i in ins:
        by_status[i.status] = by_status.get(i.status, 0) + 1
    by_fix: dict = {}
    by_sev: dict = {}
    by_effect: dict = {}
    by_transfer: dict = {}
    overdue = 0
    due_soon = 0
    unsealed = 0
    sealed = 0
    for f in findings:
        by_fix[f.fix_status] = by_fix.get(f.fix_status, 0) + 1
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        eff = f.fix_effect or "未评估"
        by_effect[eff] = by_effect.get(eff, 0) + 1
        if f.transfer_to and f.transfer_to != "未移交":
            by_transfer[f.transfer_to] = by_transfer.get(f.transfer_to, 0) + 1
        if f.fix_status != "已销号":
            unsealed += 1
        else:
            sealed += 1
        sla = _finding_sla(f)
        if sla["level"] == "overdue":
            overdue += 1
        elif sla["level"] == "warn":
            due_soon += 1
    sealed_rate = round(sealed / len(findings) * 100, 1) if findings else 0
    return {"inspection_total": len(ins), "by_status": by_status,
            "finding_total": len(findings), "by_fix": by_fix, "by_sev": by_sev,
            "by_fix_effect": by_effect, "by_transfer": by_transfer,
            "transferred": sum(1 for f in findings if f.transfer_to and f.transfer_to != "未移交"),
            "finding_overdue": overdue, "finding_due_soon": due_soon,
            "unsealed": unsealed, "sealed": sealed, "sealed_rate": sealed_rate}


@router.get("/common-problems")
def common_problems(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """共性问题分析（巡察成果运用核心）：按问题类别聚合，识别跨单位反复出现的高频问题。"""
    insps = {i.id: i for i in db.query(Inspection).all()}
    findings = db.query(InspectionFinding).all()
    agg: dict = {}
    for f in findings:
        cat = f.category or "未分类"
        a = agg.setdefault(cat, {"category": cat, "count": 0, "units": set(),
                                 "inspections": set(), "unsealed": 0, "sealed": 0, "sev_high": 0})
        a["count"] += 1
        if f.severity == "高":
            a["sev_high"] += 1
        if f.fix_status == "已销号":
            a["sealed"] += 1
        else:
            a["unsealed"] += 1
        insp = insps.get(f.inspection_id)
        if insp:
            a["units"].add(insp.target)
            a["inspections"].add(insp.id)
    rows = []
    for cat, a in agg.items():
        unit_n = len(a["units"])
        rows.append({
            "category": cat, "count": a["count"], "unit_count": unit_n,
            "inspection_count": len(a["inspections"]), "unsealed": a["unsealed"],
            "sealed": a["sealed"],
            "sealed_rate": round(a["sealed"] / a["count"] * 100, 1) if a["count"] else 0,
            "sev_high": a["sev_high"],
            # 共性问题判定：出现在 ≥2 个不同被巡察单位，或同类问题累计 ≥3 次
            "is_common": unit_n >= 2 or a["count"] >= 3,
        })
    rows.sort(key=lambda x: (-x["count"]))
    return {"items": rows, "common_count": sum(1 for r in rows if r["is_common"]),
            "total": len(rows)}


@router.post("")
def create_inspection(data: InspectCreate, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    ins = Inspection(code=_gen_code("XUN"), round_name=data.round_name,
                     target=data.target, group_name=data.group_name,
                     start_date=data.start_date, end_date=data.end_date,
                     created_by=user.id)
    db.add(ins)
    db.commit()
    audit.write_log(db, user.id, user.username, action="inspection_create",
                    resource_type="inspection", resource_id=ins.id,
                    detail={"target": data.target})
    return _insp_out(db, ins)


@router.get("/{iid}")
def get_inspection(iid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ins = db.get(Inspection, iid)
    if not ins:
        raise HTTPException(404, "巡察任务不存在")
    findings = db.query(InspectionFinding).filter(
        InspectionFinding.inspection_id == iid).order_by(InspectionFinding.id.desc()).all()
    out = _insp_out(db, ins)
    out["findings"] = [_finding_out(f) for f in findings]
    return out


class InspectAdvance(BaseModel):
    status: str
    note: str = ""


@router.post("/{iid}/advance")
def advance_inspection(iid: int, data: InspectAdvance, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """任务状态流转（准备中→进驻中→反馈中→整改中→已结束），可填备注，记入审计链。"""
    ins = db.get(Inspection, iid)
    if not ins:
        raise HTTPException(404, "巡察任务不存在")
    if data.status not in INSPECT_STATUS:
        raise HTTPException(400, "status 应为 " + "/".join(INSPECT_STATUS))
    old = ins.status
    ins.status = data.status
    db.commit()
    audit.write_log(db, user.id, user.username, action="inspection_advance",
                    resource_type="inspection", resource_id=iid,
                    detail={"from": old, "to": data.status, "note": data.note})
    return _insp_out(db, ins)


@router.get("/{iid}/timeline")
def inspection_timeline(iid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """流程轨迹：把该巡察任务的审计记录（创建/流转/录入/移交）串成时间轴。"""
    ins = db.get(Inspection, iid)
    if not ins:
        raise HTTPException(404, "巡察任务不存在")
    logs = (db.query(AuditLog)
            .filter(AuditLog.resource_type == "inspection", AuditLog.resource_id == str(iid))
            .order_by(AuditLog.id.asc()).all())
    items = []
    for lg in logs:
        d = lg.detail if isinstance(lg.detail, dict) else {}
        label = ACTION_LABEL.get(lg.action, lg.action)
        desc = ""
        if lg.action == "inspection_create":
            desc = f"创建巡察任务，对象：{d.get('target', '')}"
        elif lg.action == "inspection_advance":
            desc = f"状态流转：{d.get('from', '')} → {d.get('to', '')}"
        elif lg.action == "inspection_finding_add":
            desc = "录入发现问题"
        elif lg.action.startswith("inspection_to_"):
            desc = f"问题移交去向：{d.get('to', '')}"
        elif lg.action == "inspection_finding_fix":
            desc = f"问题整改：{d.get('summary', '')}"
        if d.get("note"):
            desc = f"{desc}｜备注：{d['note']}"
        items.append({
            "id": lg.id, "action": lg.action, "label": label, "desc": desc,
            "username": lg.username,
            "created_at": lg.created_at.strftime("%Y-%m-%d %H:%M") if lg.created_at else "",
        })
    return {"inspection_id": iid, "code": ins.code, "status": ins.status, "items": items}


@router.post("/{iid}/findings")
def add_finding(iid: int, data: FindingCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    ins = db.get(Inspection, iid)
    if not ins:
        raise HTTPException(404, "巡察任务不存在")
    f = InspectionFinding(inspection_id=iid, content=data.content,
                          category=data.category, severity=data.severity,
                          transfer_to="未移交", fix_status="待整改",
                          created_by=user.id)
    db.add(f)
    db.commit()
    audit.write_log(db, user.id, user.username, action="inspection_finding_add",
                    resource_type="inspection", resource_id=iid,
                    detail={"finding_id": f.id})
    return _finding_out(f)


@router.get("/findings/{fid}")
def get_finding(fid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    f = db.get(InspectionFinding, fid)
    if not f:
        raise HTTPException(404, "问题不存在")
    return _finding_out(f)


@router.get("/findings/{fid}/trace")
def trace_finding(fid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """移交成效追溯（成果运用闭环）：返回该巡察问题移交去向关联对象的当前状态。"""
    f = db.get(InspectionFinding, fid)
    if not f:
        raise HTTPException(404, "问题不存在")
    if not f.transfer_to or f.transfer_to == "未移交" or not f.transfer_ref_id:
        return {"transferred": False, "finding": _finding_out(f)}
    ref_id = f.transfer_ref_id
    status = ""
    code = ""
    if f.transfer_to == "案管":
        c = db.get(Case, ref_id)
        status = c.status if c else "已不存在"
        code = c.code if c else ""
    elif f.transfer_to == "党风":
        c = db.get(ConductCase, ref_id)
        status = c.status if c else "已不存在"
        code = c.code if c else ""
    else:  # 信访
        p = db.get(Petition, ref_id)
        status = p.status if p else "已不存在"
        code = p.code if p else ""
    return {"transferred": True, "to": f.transfer_to, "ref_id": ref_id,
            "ref_code": code, "ref_status": status, "finding": _finding_out(f)}


@router.post("/findings/{fid}/transfer")
def transfer_finding(fid: int, data: FindingTransfer, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """一键移交：将巡察问题转案管线索 / 党风问题 / 信访件，建立去向关联（成果运用闭环）。"""
    f = db.get(InspectionFinding, fid)
    if not f:
        raise HTTPException(404, "问题不存在")
    if data.transfer_to not in TRANSFER_TO or data.transfer_to == "未移交":
        raise HTTPException(400, "transfer_to 应为 案管/党风/信访")
    if f.transfer_to and f.transfer_to != "未移交":
        raise HTTPException(400, "已移交，不可重复")
    ref_id = None
    mods = {"案管": tenancy.CLUE_DESK, "党风": ("conduct",), "信访": ("petitions",)}[data.transfer_to]
    target = tenancy.route_to(db, user, mods, data.dept_id)
    if data.transfer_to == "案管":
        obj = Case(code=_gen_code("AJ"), source="巡视", subject=data.subject,
                   unit=data.unit, title=data.title or f.content[:50],
                   content=data.content or f.content, problem_type=f.category,
                   status="待研判", created_by=user.id, owner_id=user.id, dept_id=target)
        action = "inspection_to_case"
    elif data.transfer_to == "党风":
        obj = ConductCase(code=_gen_code("DF"), subject=data.subject, unit=data.unit,
                          category=f.category or "其他", title=data.title or f.content[:50],
                          content=data.content or f.content, source="巡视巡察",
                          created_by=user.id, owner_id=user.id, dept_id=target)
        action = "inspection_to_conduct"
    else:  # 信访
        obj = Petition(code=_gen_code("XF"), content=data.content or f.content,
                       petitioner=data.subject or "巡察移交", channel="巡视巡察",
                       created_by=user.id, dept_id=target)
        action = "inspection_to_petition"
    db.add(obj)
    db.flush()
    tenancy.link_handoff(db, f, obj, "巡察移交", user)
    ref_id = obj.id
    f.transfer_to = data.transfer_to
    f.transfer_ref_id = ref_id
    db.commit()
    audit.write_log(db, user.id, user.username, action=action,
                    resource_type="inspection", resource_id=f.inspection_id,
                    detail={"finding_id": fid, "to": data.transfer_to, "ref_id": ref_id,
                            "dept": tenancy.dept_name(db, target)})
    out = _finding_out(f)
    out["ref_id"] = ref_id
    return out


@router.post("/findings/{fid}/fix")
def fix_finding(fid: int, data: FindingFix, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    f = db.get(InspectionFinding, fid)
    if not f:
        raise HTTPException(404, "问题不存在")
    if data.fix_status and data.fix_status not in FIX_STATUS:
        raise HTTPException(400, "整改状态应为 待整改/整改中/已销号")
    if data.fix_status:
        f.fix_status = data.fix_status
    if data.fix_effect:
        if data.fix_effect not in FIX_EFFECT:
            raise HTTPException(400, "整改成效应为 未评估/好/中/差")
        f.fix_effect = data.fix_effect
    if data.req:
        f.fix_req = data.req
    if data.due:
        try:
            f.due_at = datetime.strptime(data.due, "%Y-%m-%d")
        except Exception:
            pass
    if data.note:
        f.fix_note = data.note
    if data.fix_status == "已销号" or data.note:
        f.fix_at = datetime.utcnow()
    db.commit()
    summary = f"要求：{data.req or '—'}；时限：{data.due or '—'}；落实：{data.note or '—'}；成效：{f.fix_effect or '未评估'}"
    audit.write_log(db, user.id, user.username, action="inspection_finding_fix",
                    resource_type="inspection", resource_id=f.inspection_id,
                    detail={"fix": data.fix_status or f.fix_status, "summary": summary})
    return _finding_out(f)
