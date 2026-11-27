"""问题线索智能研判画像（A5）：对案管线索/案件做风险评分与关联画像。

纯规则引擎（离线可用）：综合紧急度、超期、长期未处置、同人重复/跨域、关联信访、问题性质等给出
风险分(0-100)与标签、关联人员与记录、处置建议。仅辅助研判，结论由人工作出。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User, Case, ConductCase, Petition, BankStatement
from app.supervise import aggregate_persons

router = APIRouter(prefix="/api/clue", tags=["问题线索智能研判画像"])


def _score_case(db: Session, c: Case):
    now = datetime.now()
    score = 0
    tags = []
    reasons = []
    if c.urgency == "高":
        score += 20; tags.append("紧急"); reasons.append("紧急度高")
    elif c.urgency == "中":
        score += 10; tags.append("较紧急")
    if c.due_at and c.status not in ("已处置", "已了结"):
        days = (c.due_at - now).days
        if days < 0:
            score += 25 if days < -30 else 18
            tags.append("时限超期"); reasons.append(f"审查调查时限已超期 {abs(days)} 天")
        elif days <= 15:
            score += 8; tags.append("时限临期")
    if c.status in ("待研判", "已分办", "初核中") and c.created_at:
        stayed = (now - c.created_at).days
        if stayed >= 60:
            score += 15; tags.append("长期未处置"); reasons.append(f"线索停留 {stayed} 天未推进")
    if c.linked_petition_id:
        p = db.get(Petition, c.linked_petition_id)
        # 同一信访被再次举报（重复信访）提示
        dup = db.query(Petition).filter(Petition.code == (p.code if p else "")).count() if p else 0
        if p and dup > 1:
            score += 10; tags.append("重复举报"); reasons.append("关联信访存在重复举报")
    if c.problem_type and ("犯罪" in c.problem_type or "职务违法" in c.problem_type):
        score += 15; tags.append("涉嫌职务违法犯罪"); reasons.append("问题类型涉职务违法犯罪")
    if c.status == "已立案":
        score += 5; tags.append("已立案")
    # 同人重复 / 跨域
    persons = aggregate_persons(db)
    info = persons.get(c.subject)
    if info:
        total = len(info["cases"]) + len(info["conduct"])
        if total >= 2:
            score += 20; tags.append("同人多次被反映"); reasons.append(f"该人共被反映 {total} 次")
        if len(info["domains"]) >= 2:
            score += 15; tags.append("跨域关联"); reasons.append("涉及多个业务域：" + "、".join(info["domains"]))
    # 关联银行流水异常（敏感摘要含姓名）
    bank_flags = []
    if c.subject:
        for s in db.query(BankStatement).all():
            an = s.analysis or {}
            for a in an.get("anomalies", [])[:200]:
                if c.subject in (a.get("counterparty", "") or "") or c.subject in (a.get("summary", "") or ""):
                    bank_flags.append({"stmt_id": s.id, "type": a.get("type"), "reason": a.get("reason")})
    if bank_flags:
        score += 10; tags.append("关联资金异常"); reasons.append(f"关联银行流水异常 {len(bank_flags)} 条")
    score = min(100, score)
    # 处置建议（规则）：按所处阶段给建议，已立案 / 已处置的不再建议「初核」
    overdue = "时限超期" in tags
    if c.status in ("已处置", "已了结"):
        suggestion = "已处置 / 了结：关注处分执行与回访；如发现新问题线索应另行登记。"
    elif c.status == "已立案":
        level = "高风险" if score >= 70 else ("中风险" if score >= 40 else "常规风险")
        suggestion = (f"{level}·已立案：按审查调查方案推进，"
                      + ("时限已超期，须核实是否办理延期审批并报案管部门督办；" if overdue else "关注审查调查时限；")
                      + "关注关联人员与资金线索，必要时并案。")
    elif score >= 70:
        suggestion = "高风险：建议优先初核/提级办理，必要时合并关联线索并案核查。"
    elif score >= 40:
        suggestion = "中风险：建议按程序初核，关注时限与关联人员。"
    else:
        suggestion = "常规风险：按线索处置流程正常推进。"
    return score, tags, reasons, bank_flags, suggestion


def _case_clue_out(db: Session, c: Case, score, tags, reasons, bank_flags, suggestion) -> dict:
    # 关联同人其它记录
    persons = aggregate_persons(db)
    info = persons.get(c.subject, {})
    related = []
    for kc in info.get("cases", []):
        if kc["id"] != c.id:
            related.append({"type": "案管线索", **kc})
    for cc in info.get("conduct", []):
        related.append({"type": "四风问题", **cc})
    return {
        "id": c.id, "code": c.code, "subject": c.subject, "unit": c.unit,
        "title": c.title, "problem_type": c.problem_type, "status": c.status,
        "urgency": c.urgency, "due_at": c.due_at.strftime("%Y-%m-%d") if c.due_at else "",
        "risk_score": score, "tags": tags, "reasons": reasons,
        "bank_flags": bank_flags, "suggestion": suggestion,
        "related": related,
    }


@router.get("/rank")
def rank(limit: int = 30, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cs = db.query(Case).all()
    scored = []
    for c in cs:
        score, tags, reasons, bank_flags, suggestion = _score_case(db, c)
        scored.append((score, c, tags))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, c, tags in scored[:limit]:
        out.append({"id": c.id, "code": c.code, "subject": c.subject, "title": c.title,
                    "status": c.status, "urgency": c.urgency, "risk_score": score, "tags": tags})
    return out


@router.get("")
def list_clues(source: str = "", status: str = "", urgency: str = "", q: str = "",
               page: int = 1, per_page: int = 20,
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """问题线索研判工作台台账：来源/状态/紧急度/关键词筛选 + 服务端分页，附风险评分（风险优先）。

    研判处置与立案动作复用案管 /api/cases/{cid}/dispose、/open 端点（同一 Case 实体 + 审计链）。
    """
    qry = db.query(Case)
    if source:
        qry = qry.filter(Case.source == source)
    if status:
        qry = qry.filter(Case.status == status)
    if urgency:
        qry = qry.filter(Case.urgency == urgency)
    if q:
        like = f"%{q}%"
        qry = qry.filter((Case.subject.like(like)) | (Case.title.like(like))
                         | (Case.unit.like(like)) | (Case.code.like(like))
                         | (Case.problem_type.like(like)))
    cases = qry.all()
    scored = []
    for c in cases:
        score, tags, reasons, bank_flags, suggestion = _score_case(db, c)
        scored.append((score, c, tags))
    # 风险优先，其次新登记（id 大者优先）
    scored.sort(key=lambda x: (-x[0], -x[1].id))
    total = len(scored)
    page = max(1, page)
    per_page = max(1, min(200, per_page))
    start = (page - 1) * per_page
    page_items = scored[start:start + per_page]
    uids = {c.owner_id for _, c, _ in page_items if c.owner_id}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    items = []
    for score, c, tags in page_items:
        owner = users.get(c.owner_id)
        items.append({
            "id": c.id, "code": c.code, "source": c.source, "subject": c.subject,
            "unit": c.unit, "title": c.title, "problem_type": c.problem_type,
            "urgency": c.urgency, "status": c.status, "disposition": c.disposition,
            "owner": owner.real_name or owner.username if owner else "",
            "due_at": c.due_at.strftime("%Y-%m-%d") if c.due_at else "",
            "created_at": c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",
            "risk_score": score, "tags": tags,
        })
    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.get("/{cid}")
def profile(cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = db.get(Case, cid)
    if not c:
        raise HTTPException(404, "线索/案件不存在")
    score, tags, reasons, bank_flags, suggestion = _score_case(db, c)
    return _case_clue_out(db, c, score, tags, reasons, bank_flags, suggestion)
