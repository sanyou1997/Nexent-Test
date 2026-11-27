"""工作台统计接口：Dashboard 数据源。

性能：状态/紧急程度分布用 SQL GROUP BY + COUNT，不再全表加载 Petition 列表；
    总数、定稿数、已确认数全部走 COUNT 查询，旧版仅用于时间线/总计的 AuditLog 用 limit。
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    Petition, Document, AuditLog, RegulationArticle, DispositionSuggestion,
    Case, Trial, HistoricalReport, BankStatement, Archive, Dataset, PersonArchive,
)

router = APIRouter(prefix="/api/stats", tags=["Stats"])


@router.get("")
def stats(db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 1) Petition：总数、按 status 分组、按 urgency 分组（GROUP BY 替代 Python Counter）
    petition_total = db.scalar(select(func.count(Petition.id)))
    petition_by_status = {
        (row[0] or ""): row[1]
        for row in db.execute(
            select(Petition.status, func.count(Petition.id))
            .group_by(Petition.status)
        ).all()
    }
    petition_by_urgency = {
        (row[0] or ""): row[1]
        for row in db.execute(
            select(Petition.urgency, func.count(Petition.id))
            .group_by(Petition.urgency)
        ).all()
    }

    # 2) Document：总数 + 已定稿数
    doc_total = db.scalar(select(func.count(Document.id)))
    doc_final = db.scalar(
        select(func.count(Document.id)).where(Document.status == "已定稿"))

    # 3) DispositionSuggestion 已确认数
    confirmed = db.scalar(
        select(func.count(DispositionSuggestion.id))
        .where(DispositionSuggestion.status == "已确认"))

    # 4) 法规条款总数 + 审计日志总数
    article_total = db.scalar(select(func.count(RegulationArticle.id)))
    audit_total = db.scalar(select(func.count(AuditLog.id)))

    # 5) 最近 8 条审计日志（仅用于时间线，limit 控制）
    logs = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(8).all()

    return {
        "petition_total": petition_total or 0,
        "petition_by_status": petition_by_status,
        "petition_by_urgency": petition_by_urgency,
        "doc_total": doc_total or 0,
        "doc_final": doc_final or 0,
        "confirmed_count": confirmed or 0,
        "article_total": article_total or 0,
        "audit_total": audit_total or 0,
        "recent_logs": [
            {
                "id": l.id, "username": l.username, "action": l.action,
                "resource_type": l.resource_type, "detail": l.detail,
                "created_at": l.created_at.isoformat() + "Z" if l.created_at else "",
            } for l in logs
        ],
    }


def _blank(*cols):
    """字段为空（NULL 或空串）的 OR 条件。"""
    conds = []
    for c in cols:
        conds.append(c == "")
        conds.append(c.is_(None))
    return or_(*conds)


@router.get("/integrity-overview")
def integrity_overview(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """全局数据完整性总览：跨模块回答「数据是否全了」（Dashboard 数据完整性卡）。

    口径原则（诚实优先，呼应「数据全了才有分析」）：
    - 每个模块只报**该模块自己定义的完整度**，全部基于现有字段与已有质检，不发明指标；
    - 无数据的模块 score=None，不计入总分——空模块既不拉低也不虚增整体健康度；
    - issues 的口径写进 issue_label，看到数字就知道缺什么；link 直接跳到补数据的页面。
    """
    def _score(total: int, issues: int):
        if not total:
            return None
        return max(0, round((total - min(issues, total)) / total * 100))

    modules = []

    # 1) 信访登记：登记要素(elements)与分类标签(labels)全无 → 无法分办/无法统计
    p_rows = db.query(Petition.elements, Petition.labels).all()
    p_total = len(p_rows)
    p_issues = sum(1 for el, lb in p_rows if not el and not lb)
    modules.append({"key": "petition", "name": "信访登记", "total": p_total,
                    "issues": p_issues, "issue_label": "登记要素/分类标签缺失",
                    "score": _score(p_total, p_issues), "link": "/petitions"})

    # 2) 案管案件：被反映人/单位/反映内容/问题类型 必填未齐
    c_total = db.scalar(select(func.count(Case.id))) or 0
    c_issues = db.scalar(select(func.count(Case.id)).where(
        _blank(Case.subject, Case.unit, Case.content, Case.problem_type))) or 0
    modules.append({"key": "case", "name": "案管案件", "total": c_total,
                    "issues": c_issues, "issue_label": "必填字段缺失",
                    "score": _score(c_total, c_issues), "link": "/cases"})

    # 3) 审理：报告产出完整度（未生成审理报告 = 审理数据未闭环）
    t_total = db.scalar(select(func.count(Trial.id))) or 0
    t_issues = db.scalar(select(func.count(Trial.id)).where(
        or_(Trial.report_doc_id.is_(None), Trial.report_doc_id == 0))) or 0
    modules.append({"key": "trial", "name": "审理报告", "total": t_total,
                    "issues": t_issues, "issue_label": "未生成审理报告",
                    "score": _score(t_total, t_issues), "link": "/trials"})

    # 4) 历史报告库：待补录（置信度不足或字段缺失，人工补录后出队）
    h_total = db.scalar(select(func.count(HistoricalReport.id))) or 0
    h_issues = db.scalar(select(func.count(HistoricalReport.id)).where(
        HistoricalReport.status == "待补录")) or 0
    modules.append({"key": "historical", "name": "历史报告库", "total": h_total,
                    "issues": h_issues, "issue_label": "待补录",
                    "score": _score(h_total, h_issues), "link": "/historical-reports"})

    # 5) 银行流水：完整度评级「低」（表头/列映射需人工兜底）
    b_total = db.scalar(select(func.count(BankStatement.id))) or 0
    b_issues = db.scalar(select(func.count(BankStatement.id)).where(
        BankStatement.completeness_level == "低")) or 0
    modules.append({"key": "bank", "name": "银行流水", "total": b_total,
                    "issues": b_issues, "issue_label": "完整度低（需人工映射/补数）",
                    "score": _score(b_total, b_issues), "link": "/bank"})

    # 6) 档案数字化：无 OCR 文本（扫了没识别）+ 内容哈希重复（重复入库）
    a_total = db.scalar(select(func.count(Archive.id))) or 0
    a_no_ocr = db.scalar(select(func.count(Archive.id)).where(
        _blank(Archive.ocr_text))) or 0
    dup_groups = db.execute(
        select(Archive.content_hash, func.count(Archive.id))
        .where(Archive.content_hash != "")
        .group_by(Archive.content_hash)
        .having(func.count(Archive.id) > 1)
    ).all()
    a_dup = sum(n for _, n in dup_groups)
    modules.append({"key": "archive", "name": "档案数字化", "total": a_total,
                    "issues": a_no_ocr + a_dup,
                    "issue_label": f"无OCR文本 {a_no_ocr} / 重复档 {a_dup}",
                    "score": _score(a_total, a_no_ocr + a_dup), "link": "/archives"})

    # 7) 文书库：正文为空（模板/草稿建了没内容）
    d_total = db.scalar(select(func.count(Document.id))) or 0
    d_issues = db.scalar(select(func.count(Document.id)).where(
        _blank(Document.content))) or 0
    modules.append({"key": "document", "name": "文书库", "total": d_total,
                    "issues": d_issues, "issue_label": "正文为空",
                    "score": _score(d_total, d_issues), "link": "/documents"})

    # 8) 监督数据集：零行数据集（接了文件没解析出行）
    ds_total = db.scalar(select(func.count(Dataset.id))) or 0
    ds_issues = db.scalar(select(func.count(Dataset.id)).where(
        or_(Dataset.row_count == 0, Dataset.row_count.is_(None)))) or 0
    modules.append({"key": "dataset", "name": "监督数据集", "total": ds_total,
                    "issues": ds_issues, "issue_label": "零行数据集",
                    "score": _score(ds_total, ds_issues), "link": "/supervise"})

    # 9) 廉政档案：人工档案的单位/职务缺失（聚合档有数据但缺组织信息）
    pa_total = db.scalar(select(func.count(PersonArchive.id))) or 0
    pa_issues = db.scalar(select(func.count(PersonArchive.id)).where(
        _blank(PersonArchive.unit, PersonArchive.post))) or 0
    modules.append({"key": "integrity", "name": "廉政档案", "total": pa_total,
                    "issues": pa_issues, "issue_label": "单位/职务缺失",
                    "score": _score(pa_total, pa_issues), "link": "/integrity"})

    scored = [m["score"] for m in modules if m["score"] is not None]
    return {
        "overall": round(sum(scored) / len(scored)) if scored else None,
        "scored_modules": len(scored),
        "modules": modules,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
