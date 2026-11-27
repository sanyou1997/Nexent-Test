"""工作台：按部门开通的功能模块聚合待办与时限（一次请求返回本人该看的全部待办）。

模块 → 待办分区（数据已按部门数据范围过滤）：
- 信访受理 → 信访待处理
- 线索研判 / 案件管理 → 案件办理时限
- 审理台账 → 审理待办、申诉复查
- 预警中枢 → 预警待处置
- 巡察整改 / 党风政风 → 整改督办
- 平台管理员：全部
时限口径沿用各模块既有 SLA（案件 due_at、预警 due_at、整改 due_at / rectify_due_at、审理受理起 30 日）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tenancy
from app.database import get_db
from app.deps import get_current_user
from app.models import Alert, Appeal, Case, ConductCase, InspectionFinding, Petition, Trial, User

router = APIRouter(prefix="/api/workbench", tags=["workbench"])

SECTION_MODULES = [
    ("petitions", {"petitions"}), ("cases", {"cases", "clue"}), ("trials", {"trials"}),
    ("alerts", {"alerts"}), ("rectify", {"inspections", "conduct"}), ("appeals", {"trials"}),
]
SOON = timedelta(days=7)


def _due(dt: datetime | None, now: datetime) -> dict:
    if not dt:
        return {"due_at": "", "overdue": False, "soon": False}
    return {"due_at": dt.strftime("%Y-%m-%d"), "overdue": dt < now, "soon": now <= dt <= now + SOON}


def _rank(item: dict) -> tuple:
    return (not item["overdue"], not item["soon"], item.get("due_at") or "9999")


def _section(key, title, route, items, total) -> dict:
    items.sort(key=_rank)
    return {"key": key, "title": title, "route": route, "total": total,
            "overdue": sum(1 for i in items if i["overdue"]), "soon": sum(1 for i in items if i["soon"]),
            "items": items[:6]}


def _petitions(db, now):
    q = db.query(Petition).filter(Petition.status.in_(["待登记", "已抽取", "已建议"]))
    rows = q.order_by(Petition.created_at.desc()).limit(200).all()
    items = [{"id": p.id, "title": f"{p.code} · {(p.summary or p.content or '')[:30]}",
              "sub": f"{p.channel or ''} · {p.petitioner or '匿名'}", "tag": p.status,
              "link": f"/petitions?open={p.id}", **_due(getattr(p, "due_at", None), now)} for p in rows]
    return _section("petitions", "信访待处理", "/petitions", items, q.count())


def _cases(db, now):
    q = db.query(Case).filter(Case.status.in_(["待研判", "已分办", "初核中", "已立案"]))
    rows = q.all()
    items = [{"id": c.id, "title": f"{c.code} · {c.subject or ''} {c.title or ''}".strip(),
              "sub": f"{c.unit or ''} · {c.problem_type or ''}", "tag": c.status,
              "link": f"/cases?open={c.id}", **_due(c.due_at, now)} for c in rows]
    return _section("cases", "案件办理时限", "/cases", items, len(rows))


def _trials(db, now):
    from app.routers.trials import _sla
    q = db.query(Trial).filter(~Trial.status.in_(["不予受理"]))
    items = []
    total = 0
    for t in q.all():
        if t.status == "已执行" and getattr(t, "execution_closed", 0):
            continue
        total += 1
        s = _sla(t)
        reason = "待受理" if not t.accept_decision else ("待报结" if t.status == "已执行" else t.status)
        items.append({"id": t.id, "title": t.title or f"{t.subject or ''}审理事项", "sub": t.subject or "",
                      "tag": reason, "link": f"/trials?open={t.id}",
                      "due_at": s.get("due_at", ""), "overdue": bool(s.get("overdue")),
                      "soon": bool(s.get("needs_attention")) and not s.get("overdue")})
    return _section("trials", "审理待办", "/trials", items, total)


def _alerts(db, now):
    q = db.query(Alert).filter(Alert.status == "待处理")
    rows = q.order_by(Alert.due_at.asc().nullslast()).limit(200).all()
    items = [{"id": a.id, "title": a.title or "预警", "sub": f"{a.source or ''} · {a.level or ''}级",
              "tag": a.level or "", "link": "/alerts", **_due(a.due_at, now)} for a in rows]
    return _section("alerts", "预警待处置", "/alerts", items, q.count())


def _rectify(db, now):
    items = []
    fq = db.query(InspectionFinding).filter(InspectionFinding.fix_status != "已销号")
    for f in fq.limit(200).all():
        items.append({"id": f"if{f.id}", "title": (f.content or "巡察问题")[:36], "sub": f"巡察整改 · {f.category or ''}",
                      "tag": f.fix_status, "link": "/inspections", **_due(f.due_at, now)})
    cq = db.query(ConductCase).filter(ConductCase.status.in_(["待处置", "整改中"]))
    for c in cq.limit(200).all():
        items.append({"id": f"cc{c.id}", "title": f"{c.code} · {c.title or ''}", "sub": f"党风整改 · {c.unit or ''}",
                      "tag": c.status, "link": "/conduct", **_due(c.rectify_due_at, now)})
    return _section("rectify", "整改督办", "/inspections", items, fq.count() + cq.count())


def _appeals(db, now):
    q = db.query(Appeal).filter(Appeal.status.in_(["待受理", "复查中"]))
    rows = q.all()
    items = []
    for a in rows:
        due = (a.accepted_at + timedelta(days=90)) if a.accepted_at else None
        items.append({"id": a.id, "title": f"{a.code} · {a.appellant or ''}申诉", "sub": a.appellant_unit or "",
                      "tag": a.status, "link": "/trials?view=appeal", **_due(due, now)})
    return _section("appeals", "申诉复查", "/trials?view=appeal", items, len(rows))


BUILDERS = {"petitions": _petitions, "cases": _cases, "trials": _trials,
            "alerts": _alerts, "rectify": _rectify, "appeals": _appeals}


@router.get("")
def workbench(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    now = datetime.utcnow()
    scope = tenancy.current_scope(db)
    keys = [k for k, mods in SECTION_MODULES if scope is None or scope.modules is None or mods & scope.modules]
    sections = []
    for k in keys:
        try:
            sections.append(BUILDERS[k](db, now))
        except Exception as e:  # 单个分区出错不拖垮整个工作台
            sections.append({"key": k, "title": k, "route": "/", "total": 0, "overdue": 0, "soon": 0,
                             "items": [], "error": type(e).__name__})
    return {"role": user.role, "sections": sections,
            "totals": {"pending": sum(s["total"] for s in sections),
                       "overdue": sum(s["overdue"] for s in sections),
                       "soon": sum(s["soon"] for s in sections)}}
