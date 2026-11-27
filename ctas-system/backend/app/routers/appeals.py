"""审理申诉复查（党员对处分决定不服的申诉复查闭环）。

业务依据：《中国共产党党员权利保障条例》《中国共产党纪律检查机关控告申诉工作条例》。
- 党员对处分决定不服，可在规定期限内提出申诉；申诉期间原处分决定不停止执行。
- 受理（30 日）→ 指定复查承办人（原承办人回避）→ 复查（90 日）→ 维持/变更/撤销结论 → 复查决定书 → 送达备案。
- 对复查决定仍不服，可向上一级再申诉（二级终结）。
全部动作为辅助参考，结论由组织依程序作出；系统负责留痕、时限合规提醒与文书初稿。
"""
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit, gongwen, tenancy
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.llm import provider
from app.models import User, Trial, Case, Document, DocumentVersion, AuditLog, Appeal

router = APIRouter(prefix="/api/appeals", tags=["审理申诉复查"])

# —— 时限合规（SLA）——
ACCEPT_DUE_DAYS = 30      # 收到申诉后作出受理决定的时限
REVIEW_DUE_DAYS = 90      # 受理后作出复查决定的时限（控告申诉工作条例：三个月内）
WARN_DAYS = 7             # 临期提醒阈值

APPEAL_STATUS = ["待受理", "复查中", "不予受理", "已维持", "已变更", "已撤销", "已终结"]
CONCLUSIONS = ["维持", "变更", "撤销"]

# 审计 action → 时间轴文案
ACTION_LABEL = {
    "appeal_create": "登记申诉",
    "appeal_accept": "受理审核",
    "appeal_assign": "指定复查承办人（回避落实）",
    "appeal_conclude": "复查结论",
    "appeal_serve": "复查决定书送达",
    "appeal_correct": "纠错执行",
    "appeal_reappeal": "再申诉（二级终结）",
    "appeal_finalize": "复查终结",
}


def _content_disposition(inline: bool, name: str) -> str:
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"appeal_decision\"; filename*=UTF-8''{quote(name)}"


def _terminal(a: Appeal) -> bool:
    """已终结（不予受理 或 已再申诉/终结）不可继续流程。"""
    return a.status == "不予受理" or a.reappealed or bool(a.finalized_at)


def _sla(a: Appeal) -> dict:
    now = datetime.utcnow()
    # 受理决定时限：登记起 30 日（已受理/不受理后不再计算）
    accept_due = a.appeal_at + timedelta(days=ACCEPT_DUE_DAYS)
    accept_overdue = (a.accept_decision not in ("受理", "不予受理")) and accept_due < now
    accept_left = (accept_due - now).days
    accept_needs = (a.accept_decision not in ("受理", "不予受理")) and (0 <= accept_left <= WARN_DAYS)
    # 复查决定时限：受理起 90 日（结论作出后不再计算）
    review_due = (a.accepted_at + timedelta(days=REVIEW_DUE_DAYS)) if a.accepted_at else None
    review_overdue = bool(review_due) and (a.conclusion == "") and review_due < now
    review_left = (review_due - now).days if review_due else 0
    review_needs = bool(review_due) and (a.conclusion == "") and (0 <= review_left <= WARN_DAYS)
    return {
        "accepted": bool(a.accepted_at),
        "accept_due_at": accept_due.strftime("%Y-%m-%d"),
        "accept_overdue": accept_overdue,
        "accept_needs": accept_needs,
        "review_due_at": review_due.strftime("%Y-%m-%d") if review_due else "",
        "review_overdue": review_overdue,
        "review_needs": review_needs,
        "overdue": accept_overdue or review_overdue,
    }


def _appeal_out(db: Session, a: Appeal) -> dict:
    t = db.get(Trial, a.trial_id) if a.trial_id else None
    c = db.get(Case, a.case_id) if a.case_id else None
    creator = db.get(User, a.created_by) if a.created_by else None
    reviewer = db.get(User, a.reviewer_id) if a.reviewer_id else None
    return {
        "id": a.id, "code": a.code,
        "trial_id": a.trial_id, "trial_title": t.title if t else "",
        "case_id": a.case_id,
        "case_code": c.code if c else "", "case_subject": c.subject if c else (t.case_subject if t else ""),
        "appellant": a.appellant, "appellant_unit": a.appellant_unit,
        "reason": a.reason, "source": a.source,
        "appeal_at": a.appeal_at.strftime("%Y-%m-%d %H:%M") if a.appeal_at else "",
        "status": a.status,
        "accept_decision": a.accept_decision or "", "accept_reason": a.accept_reason or "",
        "accepted_at": a.accepted_at.strftime("%Y-%m-%d %H:%M") if a.accepted_at else "",
        "reviewer_id": a.reviewer_id, "reviewer_name": reviewer.real_name or reviewer.username if reviewer else "",
        "recusal_note": a.recusal_note or "",
        "review_note": a.review_note or "",
        "conclusion": a.conclusion or "", "conclusion_reason": a.conclusion_reason or "",
        "reviewed_at": a.reviewed_at.strftime("%Y-%m-%d %H:%M") if a.reviewed_at else "",
        "decision_doc_id": a.decision_doc_id,
        "served_at": a.served_at.strftime("%Y-%m-%d %H:%M") if a.served_at else "",
        "served_note": a.served_note or "",
        "reappealed": bool(a.reappealed), "reappeal_note": a.reappeal_note or "",
        "finalized_at": a.finalized_at.strftime("%Y-%m-%d %H:%M") if a.finalized_at else "",
        "correction_note": a.correction_note or "",
        "created_by": a.created_by,
        "created_name": creator.real_name or creator.username if creator else "",
        "created_at": a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "",
        "sla": _sla(a),
    }


class AppealCreate(BaseModel):
    trial_id: int
    appellant: str = ""
    appellant_unit: str = ""
    reason: str = ""
    source: str = "本人书面"
    appeal_date: str = ""     # YYYY-MM-DD，可选；缺省取当前


class AppealAccept(BaseModel):
    decision: str = "受理"     # 受理 / 暂缓受理 / 不予受理
    reason: str = ""


class AppealAssign(BaseModel):
    reviewer_id: int
    recusal_note: str = ""


class AppealConclude(BaseModel):
    conclusion: str           # 维持 / 变更 / 撤销
    reason: str = ""          # 复查结论理由
    review_note: str = ""     # 复核意见


class AppealServe(BaseModel):
    note: str = ""


class AppealCorrect(BaseModel):
    note: str = ""


class AppealReappeal(BaseModel):
    note: str = ""


class AppealFinalize(BaseModel):
    note: str = ""


def _next_code(db: Session) -> str:
    # 编号全平台唯一：跨部门取当年最大号递增（不能按本部门可见记录计数，否则各部门会发出相同编号）
    year = datetime.utcnow().year
    prefix = f"申{year}-"
    seq = 0
    for (code,) in (db.query(Appeal.code).filter(Appeal.code.like(prefix + "%"))
                    .execution_options(**tenancy.BYPASS).all()):
        tail = (code or "")[len(prefix):]
        if tail.isdigit():
            seq = max(seq, int(tail))
    return f"{prefix}{seq + 1:04d}"


@router.post("")
def create_appeal(data: AppealCreate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """登记申诉：关联原处分决定（已定稿的审理事项）。"""
    t = db.get(Trial, data.trial_id)
    if not t:
        raise HTTPException(404, "关联审理事项不存在")
    if t.status not in ("已定稿",):
        raise HTTPException(400, "仅已定稿（处分决定已作出）的审理事项可登记申诉复查")
    # 重复申诉红线：同一处分决定已有在办/已出结论申诉的，不得重复登记（应走再申诉）
    existing = db.query(Appeal).filter(Appeal.trial_id == t.id).all()
    for e in existing:
        if e.status in ("待受理", "复查中", "已维持", "已变更", "已撤销"):
            raise HTTPException(409, "该处分决定已有在办申诉复查，请勿重复登记（应走再申诉程序）")
        if e.reappealed or e.finalized_at:
            raise HTTPException(409, "该处分决定申诉复查已二级终结，不得重复登记")
        # 仅当既往为"不予受理"时，允许重新登记申诉
    c = db.get(Case, t.case_id) if t.case_id else None
    try:
        appeal_at = datetime.strptime(data.appeal_date, "%Y-%m-%d") if data.appeal_date else datetime.utcnow()
    except ValueError:
        raise HTTPException(400, "appeal_date 格式应为 YYYY-MM-DD")
    a = Appeal(
        code=_next_code(db), trial_id=t.id, case_id=t.case_id,
        appellant=data.appellant or (c.subject if c else ""),
        appellant_unit=data.appellant_unit,
        reason=data.reason or "（未填写申诉理由）", source=data.source,
        appeal_at=appeal_at, status="待受理", created_by=user.id,
    )
    db.add(a)
    db.commit()
    audit.write_log(db, user.id, user.username, action="appeal_create",
                    resource_type="appeal", resource_id=a.id, detail={"trial_id": t.id})
    return _appeal_out(db, a)


@router.get("")
def list_appeals(status: str = "", q: str = "", overdue: bool = False,
                 page: int = 1, per_page: int = 20,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Appeal)
    if status:
        query = query.filter(Appeal.status == status)
    if q:
        like = f"%{q}%"
        query = (query.join(Case, Case.id == Appeal.case_id, isouter=True)
                 .join(Trial, Trial.id == Appeal.trial_id, isouter=True)
                 .filter((Appeal.code.like(like)) | (Appeal.appellant.like(like))
                         | (Case.code.like(like)) | (Case.subject.like(like))
                         | (Trial.title.like(like))))
    all_a = query.order_by(Appeal.id.desc()).all()
    if overdue:
        all_a = [a for a in all_a if _sla(a)["overdue"]]
    total = len(all_a)
    page = max(1, page); per_page = max(1, min(200, per_page))
    items = [_appeal_out(db, a) for a in all_a[(page - 1) * per_page: page * per_page]]
    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.get("/reviewers")
def list_reviewers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """复查承办人候选（已登录即可查，供前端指派下拉；回避由后端校验）。"""
    q = db.query(User).filter(User.is_active.is_(True))
    if user.role != "admin":   # 复查承办人限本部门账号
        q = q.filter(User.dept_id == user.dept_id)
    return [{"id": u.id, "name": u.real_name or u.username, "role": u.role} for u in q.all()]


@router.get("/stats")
def appeals_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.query(Appeal).all()
    by_status: dict = {}
    for x in a:
        by_status[x.status] = by_status.get(x.status, 0) + 1
    concluded = [x for x in a if x.conclusion]
    by_conclusion: dict = {}
    for x in concluded:
        by_conclusion[x.conclusion] = by_conclusion.get(x.conclusion, 0) + 1
    overdue = sum(1 for x in a if _sla(x)["overdue"])
    correction = sum(1 for x in a if x.conclusion in ("变更", "撤销"))
    reappealed = sum(1 for x in a if x.reappealed)
    return {
        "total": len(a), "by_status": by_status,
        "accepted_total": sum(1 for x in a if x.accept_decision == "受理"),
        "not_accepted_total": sum(1 for x in a if x.accept_decision == ""),
        "concluded_total": len(concluded),
        "by_conclusion": by_conclusion,
        "correction_total": correction,            # 变更+撤销（纠错率分子）
        "overdue_total": overdue,
        "reappealed_total": reappealed,
        "finalized_total": sum(1 for x in a if x.finalized_at),
    }


@router.get("/{aid}")
def get_appeal(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    return _appeal_out(db, a)


@router.get("/{aid}/timeline")
def appeal_timeline(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    logs = (db.query(AuditLog)
            .filter(AuditLog.resource_type == "appeal", AuditLog.resource_id == str(aid))
            .order_by(AuditLog.id.asc()).all())
    items = []
    for lg in logs:
        d = lg.detail if isinstance(lg.detail, dict) else {}
        label = ACTION_LABEL.get(lg.action, lg.action)
        desc = ""
        if lg.action == "appeal_create":
            desc = f"关联审理事项 #{d.get('trial_id', '')}；申诉人：{a.appellant}"
        elif lg.action == "appeal_accept":
            desc = f"决定：{d.get('decision', '')}" + (f"；{d.get('reason', '')}" if d.get('reason') else "")
        elif lg.action == "appeal_assign":
            desc = f"复查承办人 #{d.get('reviewer_id', '')}" + (f"；回避：{d.get('recusal_note', '')}" if d.get('recusal_note') else "")
        elif lg.action == "appeal_conclude":
            desc = f"结论：{d.get('conclusion', '')}" + (f"；{d.get('reason', '')}" if d.get('reason') else "")
        elif lg.action == "appeal_reappeal":
            desc = "申诉人提出再申诉，进入二级终结"
        elif lg.action == "appeal_finalize":
            desc = "复查终结"
        items.append({
            "id": lg.id, "action": lg.action, "label": label, "desc": desc,
            "username": lg.username,
            "created_at": lg.created_at.strftime("%Y-%m-%d %H:%M") if lg.created_at else "",
        })
    return {"appeal_id": aid, "status": a.status, "items": items}


@router.post("/{aid}/accept")
def accept_appeal(aid: int, data: AppealAccept, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """受理审核：决定受理 / 不予受理 / 暂缓受理。受理起启动复查 90 日时限。"""
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    if _terminal(a):
        raise HTTPException(400, "该申诉复查已终结，不可再受理")
    if data.decision not in ("受理", "暂缓受理", "不予受理"):
        raise HTTPException(400, "decision 须为 受理 / 暂缓受理 / 不予受理")
    if a.accept_decision == "不予受理":
        raise HTTPException(400, "已决定不予受理，不可重复受理")
    a.accept_decision = data.decision
    a.accept_reason = data.reason or ""
    if data.decision == "受理":
        a.accepted_at = datetime.utcnow()
        a.status = "复查中"
    elif data.decision == "不予受理":
        a.accepted_at = None
        a.status = "不予受理"
    else:  # 暂缓受理
        a.accepted_at = None
        a.status = "待受理"
    db.commit()
    audit.write_log(db, user.id, user.username, action="appeal_accept",
                    resource_type="appeal", resource_id=aid,
                    detail={"decision": data.decision, "reason": data.reason or ""})
    return _appeal_out(db, a)


@router.post("/{aid}/assign")
def assign_appeal(aid: int, data: AppealAssign, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """指定复查承办人（落实回避）：复查承办人不得为原审理事项承办人。"""
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    if _terminal(a):
        raise HTTPException(400, "该申诉复查已终结，不可指派")
    if a.status != "复查中":
        raise HTTPException(400, "须先受理，再指定复查承办人")
    t = db.get(Trial, a.trial_id)
    if t and t.created_by and t.created_by == data.reviewer_id:
        raise HTTPException(400, "复查承办人不得为原审理事项承办人（应回避）")
    reviewer = db.get(User, data.reviewer_id)
    if not reviewer:
        raise HTTPException(404, "复查承办人不存在")
    a.reviewer_id = data.reviewer_id
    a.recusal_note = data.recusal_note or ""
    db.commit()
    audit.write_log(db, user.id, user.username, action="appeal_assign",
                    resource_type="appeal", resource_id=aid,
                    detail={"reviewer_id": data.reviewer_id, "recusal_note": data.recusal_note or ""})
    return _appeal_out(db, a)


@router.post("/{aid}/conclude")
def conclude_appeal(aid: int, data: AppealConclude, db: Session = Depends(get_db),
                    user: User = Depends(require_roles("shenli", "admin"))):
    """复查结论（须审理室/管理员）：维持 / 变更 / 撤销，并生成复查决定书初稿。"""
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    if _terminal(a):
        raise HTTPException(400, "该申诉复查已终结，不可再作结论")
    if a.status != "复查中":
        raise HTTPException(400, "须先受理并指定复查承办人，再作复查结论")
    if not a.reviewer_id:
        raise HTTPException(400, "须先指定复查承办人并落实回避，再作复查结论")
    if data.conclusion not in CONCLUSIONS:
        raise HTTPException(400, "conclusion 须为 维持 / 变更 / 撤销")
    t = db.get(Trial, a.trial_id)
    c = db.get(Case, a.case_id) if a.case_id else None
    conclusion_text = {
        "维持": "维持原处分决定。",
        "变更": "变更原处分决定（变更后的处分档次详见复查决定书正文，应写明具体处分）。",
        "撤销": "撤销原处分决定，恢复申诉人相关党员权利/公职待遇，并按有关规定恢复名誉；原处分错误造成的影响应予消除。",
    }[data.conclusion]
    raw = (
        f"关于{a.appellant or (c.subject if c else '×')}同志申诉的复查决定书\n\n"
        f"一、基本情况\n"
        f"申诉人：{a.appellant or (c.subject if c else '×')}（{a.appellant_unit or '单位/职务未填'}）\n"
        f"原处分决定：{c.code if c else ''} {t.title if t else ''}，经审理定稿"
        f"{('（' + (t.confirmed_at.strftime('%Y-%m-%d') if t and t.confirmed_at else '—') + '）') if t else ''}。\n"
        f"申诉理由（要点）：{a.reason}\n\n"
        f"二、复查情况\n复查组调阅原案卷材料，经集体复核认为：{data.review_note or '（详见复核报告）'}。\n\n"
        f"三、复查结论\n经复查，决定：{conclusion_text}\n\n"
        f"四、权利救济\n如不服本复查决定，可自收到本决定书之日起三十日内向上一级主管机关提出再申诉。"
        f"申诉期间，原处分决定不停止执行。\n\n"
        f"（本决定书由系统根据复查情况自动生成初稿，须经集体审议后印发。）\n\n"
        f"中共XX纪律检查委员会（XX监察委员会）\n{datetime.utcnow().strftime('%Y年%m月%d日')}"
    )
    content = gongwen.format_document(raw)
    gen = provider.chat(
        [{"role": "system", "content": "你是申诉复查文书助手，请规范复查决定书表述，不增加事实。"},
         {"role": "user", "content": content}],
        scene="document", user_id=user.id)
    if gen and len(gen) > 50:
        content = gongwen.format_document(gen)
    d = Document(template_id=None, source="流程生成", title=f"{a.code}——{a.appellant or (c.subject if c else '')}申诉复查决定书（AI初稿）",
                 case_name=c.code if c else "", content=content, status="AI初稿", created_by=user.id)
    db.add(d)
    db.flush()
    db.add(DocumentVersion(document_id=d.id, version_no=1, content=content,
                           note="复查决定书 AI 生成初稿", created_by=user.id))
    a.conclusion = data.conclusion
    a.conclusion_reason = data.reason or ""
    a.review_note = data.review_note or ""
    a.decision_doc_id = d.id
    a.reviewed_at = datetime.utcnow()
    a.status = f"已{data.conclusion}"
    db.commit()
    audit.write_log(db, user.id, user.username, action="appeal_conclude",
                    resource_type="appeal", resource_id=aid,
                    detail={"conclusion": data.conclusion, "reason": data.reason or "", "doc_id": d.id})
    out = _appeal_out(db, a)
    out["document"] = {"id": d.id, "title": d.title, "content": content}
    return out


@router.post("/{aid}/serve")
def serve_appeal(aid: int, data: AppealServe, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """复查决定书送达 + 报上级备案（结论作出后须送达申诉人）。"""
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    if a.conclusion == "":
        raise HTTPException(400, "须先作出复查结论，再送达")
    if _terminal(a):
        raise HTTPException(400, "该申诉复查已终结，不可重复送达")
    a.served_at = datetime.utcnow()
    a.served_note = data.note or ""
    db.commit()
    audit.write_log(db, user.id, user.username, action="appeal_serve",
                    resource_type="appeal", resource_id=aid, detail={"note": data.note or ""})
    return _appeal_out(db, a)


@router.post("/{aid}/correct")
def correct_appeal(aid: int, data: AppealCorrect, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """纠错执行（变更/撤销时）：恢复党员权利/公职待遇、恢复名誉等留痕。"""
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    if a.conclusion not in ("变更", "撤销"):
        raise HTTPException(400, "仅变更/撤销结论须纠错执行")
    a.correction_note = data.note or ""
    db.commit()
    audit.write_log(db, user.id, user.username, action="appeal_correct",
                    resource_type="appeal", resource_id=aid, detail={"note": data.note or ""})
    return _appeal_out(db, a)


@router.post("/{aid}/reappeal")
def reappeal_appeal(aid: int, data: AppealReappeal, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """再申诉（二级终结）：申诉人对复查决定仍不服，向上一级提出再申诉，进入二级终结。"""
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    if a.conclusion == "":
        raise HTTPException(400, "须先作出复查结论，方可再申诉")
    if a.reappealed or a.finalized_at:
        raise HTTPException(400, "该申诉复查已终结")
    a.reappealed = True
    a.reappeal_note = data.note or ""
    a.status = "已终结"
    a.finalized_at = datetime.utcnow()
    db.commit()
    audit.write_log(db, user.id, user.username, action="appeal_reappeal",
                    resource_type="appeal", resource_id=aid, detail={"note": data.note or ""})
    return _appeal_out(db, a)


@router.post("/{aid}/finalize")
def finalize_appeal(aid: int, data: AppealFinalize, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """复查终结（维持且无再申诉 / 纠错执行完毕）：作出复查结论后，申诉人未再申诉的，予以终结。"""
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    if a.conclusion == "":
        raise HTTPException(400, "须先作出复查结论，方可终结")
    if a.reappealed or a.finalized_at:
        raise HTTPException(400, "该申诉复查已终结")
    a.status = "已终结"
    a.finalized_at = datetime.utcnow()
    db.commit()
    audit.write_log(db, user.id, user.username, action="appeal_finalize",
                    resource_type="appeal", resource_id=aid, detail={"note": data.note or ""})
    return _appeal_out(db, a)


@router.get("/{aid}/decision")
def appeal_decision_view(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """查看复查决定书正文（只读）。"""
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    if not a.decision_doc_id:
        raise HTTPException(404, "尚未生成复查决定书")
    d = db.get(Document, a.decision_doc_id)
    if not d:
        raise HTTPException(404, "复查决定书已不存在")
    c = db.get(Case, a.case_id)
    return {"id": d.id, "title": d.title, "content": d.content or "",
            "case_code": c.code if c else "", "status": d.status}


@router.get("/{aid}/decision/export")
def appeal_decision_export(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """复查决定书下载（txt，UTF-8）。"""
    a = db.get(Appeal, aid)
    if not a:
        raise HTTPException(404, "申诉复查事项不存在")
    if not a.decision_doc_id:
        raise HTTPException(404, "尚未生成复查决定书")
    d = db.get(Document, a.decision_doc_id)
    if not d:
        raise HTTPException(404, "复查决定书已不存在")
    c = db.get(Case, a.case_id)
    fname = f"{a.code}_复查决定书.txt"
    return Response(
        content=(d.content or "").encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(False, fname)},
    )
