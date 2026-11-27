"""CTAS 历史报告库：批量导入历史审计/调查/巡视报告（Word/PDF）→ 自动识别建档
→ 类案参考 + 多维度统计。

端点一览：
- POST /api/historical-reports/batch-import  批量导入（多文件，逐文件回执）
- GET  /api/historical-reports                列表（多维筛选：类型/年份/地区/类别/状态/关键词）
- GET  /api/historical-reports/{id}           详情
- PUT  /api/historical-reports/{id}           人工补录（修正自动识别字段）
- DELETE /api/historical-reports/{id}         删除
- POST /api/historical-reports/search         类案参考检索（TF-IDF 相似度 + finding 级命中片段）
- GET  /api/historical-reports/similar/{id}   与指定报告相似的历史报告
- POST /api/historical-reports/reference      类案引用留痕（形成审计链）
- GET  /api/historical-reports/references     最近引用记录
- GET  /api/historical-reports/quality-report 数据完整性质检报告（缺失统计/待补录/重复）
- GET  /api/historical-reports/stats/dimensions 多维度统计（时间/类型/地区/类别/金额/处理结果）
- GET  /api/historical-reports/export         导出 xlsx（明细 + 统计页）
- GET  /api/historical-reports/batches        导入批次列表
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import time
from collections import Counter
from datetime import datetime as _dt

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

from app import audit
from app.config import BASE_DIR
from app.database import get_db
from app.deps import get_current_user
from app.llm import embeddings
from app.models import User, HistoricalReport, ImportBatch, ReportFinding, ReportReference
from app.refcheck import check_refs
from app.report_extract import (
    REPORT_TYPES, AUDIT_TYPES, PROBLEM_CATEGORIES, SEVERITIES,
    build_record, extract_text, split_findings,
)
from app.schemas import (HistoricalReportIn, HistoricalReportOut, HistoricalReportSearchIn,
                          HistoricalReportBatchDeleteIn, HistoricalReportReferenceIn)

router = APIRouter(prefix="/api/historical-reports", tags=["H1 历史报告库"])

REPORT_DIR = BASE_DIR / "data" / "historical_reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

_ALLOWED_EXT = {"docx", "pdf", "txt"}
_MAX_FILE_MB = 30

# 状态机：自动识别入库 → 置信度不足转「待补录」→ 人工复核「已复核」
_REVIEW_THRESHOLD = 0.75


def _content_disposition(inline: bool, name: str) -> str:
    from urllib.parse import quote
    disp = "inline" if inline else "attachment"
    ascii_name = name.encode("ascii", "ignore").decode() or "download"
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def _out(r: HistoricalReport, db: Session) -> HistoricalReportOut:
    up = db.get(User, r.uploaded_by) if r.uploaded_by else None
    return HistoricalReportOut(
        id=r.id, report_no=r.report_no or "", title=r.title or "",
        report_type=r.report_type or "", report_date=r.report_date or "",
        year=r.year or 0, region=r.region or "", audited_org=r.audited_org or "",
        audit_type=r.audit_type or "", problem_category=r.problem_category or "",
        severity=r.severity or "", amount=r.amount or 0.0, subject=r.subject or "",
        findings=(r.findings or "")[:4000], conclusion=(r.conclusion or "")[:2000],
        legal_refs=r.legal_refs or [], tags=r.tags or [],
        char_count=r.char_count or 0, page_count=r.page_count or 0,
        file_name=r.file_name or "", file_type=r.file_type or "",
        has_file=bool(r.file_path),         extract_confidence=r.extract_confidence or 0.0,
        missing_fields=r.missing_fields or [], status=r.status or "已入库",
        near_duplicate_of=r.near_duplicate_of or 0,
        source=r.source or "", uploaded_by_name=(up.real_name or up.username) if up else "",
        created_at=r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
    )


def _gen_batch_no(db: Session) -> str:
    day = _dt.now().strftime("%Y%m%d")
    prefix = f"IMP{day}"
    n = db.query(ImportBatch).filter(ImportBatch.batch_no.like(f"{prefix}%")).count()
    return f"{prefix}{n + 1:03d}"


def _index_text(r: HistoricalReport) -> str:
    """类案检索用的索引文本：标题 + 关键字段 + 全文前段（保证问题特征可召回）。"""
    return " ".join([
        r.title or "", r.report_type or "", r.audited_org or "", r.region or "",
        r.problem_category or "", r.subject or "", " ".join(r.tags or []),
        (r.findings or "")[:1500], (r.conclusion or "")[:600],
        (r.content or "")[:4000],
    ])


# ================================================================ 批量导入

@router.post("/batch-import")
async def batch_import(
    files: list[UploadFile] = File(...),
    use_llm: bool = Form(True),
    source: str = Form("批量导入"),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量导入历史报告：Word(.docx) / PDF / TXT。

    每个文件独立处理，单个文件失败不影响其余；逐文件回执说明
    「成功 / 重复 / 失败 / 待补录」及原因。
    """
    if not files:
        raise HTTPException(400, "未选择文件")
    batch_no = _gen_batch_no(db)
    t0 = time.time()
    results: list[dict] = []
    ok = dup = failed = need_review = 0

    for f in files:
        name = f.filename or "未命名"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        item = {"file": name, "status": "失败", "report_id": None, "note": "", "missing": []}
        try:
            if ext not in _ALLOWED_EXT:
                raise ValueError(f"不支持的格式 .{ext}（仅支持 docx/pdf/txt）")
            data = await f.read()
            if not data:
                raise ValueError("文件为空")
            if len(data) > _MAX_FILE_MB * 1024 * 1024:
                raise ValueError(f"文件超过 {_MAX_FILE_MB}MB 上限")

            # 1) 全文抽取
            ext_res = extract_text(data, name)
            text = ext_res["text"]

            # 2) 内容哈希去重
            import hashlib
            ch = hashlib.sha256(data).hexdigest()
            exist = db.query(HistoricalReport).filter(HistoricalReport.content_hash == ch).first()
            if exist:
                dup += 1
                item.update(status="重复", report_id=exist.id,
                            note=f"与已有报告《{exist.title}》内容完全一致")
                results.append(item)
                continue

            # 3) 结构化识别（规则优先 + LLM 补空）
            rec = build_record(text, name, use_llm=use_llm, user_id=user.id)
            fields = rec["fields"]
            conf = rec["confidence"]
            missing = rec["missing"]

            # 3.1 近似重复：文号相同（内容哈希不同）→ 提示但仍入库，标为待复核
            #      注意：不进 missing_fields（那是"字段缺失"清单，语义不同）
            near_dup_of = None
            no_key = (fields.get("report_no") or "").strip()
            if no_key:
                prev = db.query(HistoricalReport).filter(
                    HistoricalReport.report_no == no_key).first()
                if prev:
                    near_dup_of = prev.id

            # 4) 落盘原件
            safe = re.sub(r"[^\w\u4e00-\u9fff.-]", "_", name)[:80]
            disk_name = f"{_dt.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}_{safe}"
            disk = REPORT_DIR / disk_name
            disk.write_bytes(data)
            rel = f"data/historical_reports/{disk_name}"

            # 5) 入库
            rpt = HistoricalReport(
                report_no=fields.get("report_no", ""),
                title=fields.get("title", "")[:300],
                report_type=fields.get("report_type", ""),
                report_date=fields.get("report_date", ""),
                year=fields.get("year", 0) or 0,
                region=fields.get("region", ""),
                audited_org=fields.get("audited_org", "")[:150],
                audit_type=fields.get("audit_type", ""),
                problem_category=fields.get("problem_category", ""),
                severity=fields.get("severity", ""),
                amount=fields.get("amount", 0.0) or 0.0,
                subject=fields.get("subject", "")[:200],
                findings=fields.get("findings", ""),
                conclusion=fields.get("conclusion", ""),
                legal_refs=fields.get("legal_refs", []) or [],
                tags=fields.get("tags", []) or [],
                content=text,
                char_count=len(text),
                page_count=ext_res.get("page_count", 0) or 0,
                file_name=name, file_type=ext, file_path=rel,
                content_hash=ch,
                extract_confidence=conf,
                missing_fields=missing,
                status="待补录" if (conf < _REVIEW_THRESHOLD or missing) else "已入库",
                near_duplicate_of=near_dup_of or 0,
                source=source or "批量导入",
                uploaded_by=user.id,
            )
            rpt.tf_vector = embeddings.embed_local(_index_text(rpt))
            db.add(rpt)
            db.flush()

            # 5.1 问题条目拆条（支撑条目级统计）
            for f in split_findings(text):
                db.add(ReportFinding(
                    report_id=rpt.id, seq=f.get("seq", ""), title=f.get("title", "")[:200],
                    content=f.get("content", ""), amount=f.get("amount", 0.0),
                    problem_type=f.get("problem_type", ""), severity=f.get("severity", ""),
                    legal_refs=f.get("legal_refs", []) or [],
                ))

            # 5.2 法规引用有效性校验（错条/错法名要显式暴露，不能默默带过）
            ref_check = check_refs(db, rpt.legal_refs or [])
            bad_refs = [c for c in ref_check if c.get("status") == "bad"]
            if bad_refs:
                missing = list(rpt.missing_fields or [])
                missing.append("legal_refs")
                rpt.missing_fields = missing
                if rpt.status == "已入库":
                    rpt.status = "待补录"
            db.commit()
            db.refresh(rpt)

            ok += 1
            if rpt.status == "待补录":
                need_review += 1
            note_txt = f"{rec['engine_note']}；置信度 {conf:.0%}"
            if missing:
                note_txt += f"；待补录字段：{'、'.join(missing)}"
            if near_dup_of:
                note_txt += f"；疑似重复（与 #{near_dup_of} 文号相同），请人工确认"
            if bad_refs:
                note_txt += f"；法规引用存疑 {len(bad_refs)} 处：" + \
                    "；".join(f"《{b['law']}》{b['article_no']}（{b['reason']}）" for b in bad_refs[:3])
            item.update(status="成功", report_id=rpt.id, note=note_txt, missing=missing)
        except Exception as e:
            failed += 1
            item.update(status="失败", note=str(e)[:300])
        results.append(item)

    batch = ImportBatch(
        batch_no=batch_no, total=len(files), ok=ok, duplicated=dup,
        failed=failed, need_review=need_review,
        detail_json=json.dumps(results, ensure_ascii=False), note=note,
        created_by=user.id,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    audit.write_log(db, user.id, user.username, action="historical_batch_import",
                    resource_type="historical_report", resource_id=batch_no,
                    detail={"total": len(files), "ok": ok, "dup": dup, "failed": failed,
                            "need_review": need_review, "use_llm": use_llm,
                            "latency_ms": int((time.time() - t0) * 1000)})
    return {"batch_no": batch_no, "total": len(files), "ok": ok, "duplicated": dup,
            "failed": failed, "need_review": need_review, "results": results,
            "batch_id": batch.id}


# ================================================================ 列表 / 详情

@router.get("")
def list_reports(
    q: str = "", report_type: str = "", year: int = 0, region: str = "",
    problem_category: str = "", audit_type: str = "", severity: str = "",
    status: str = "", page: int = 1, per_page: int = 20,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """多维筛选列表。q 命中标题/单位/问题发现/标签。"""
    query = db.query(HistoricalReport)
    if report_type:
        query = query.filter(HistoricalReport.report_type == report_type)
    if year:
        query = query.filter(HistoricalReport.year == year)
    if region:
        query = query.filter(HistoricalReport.region.like(f"%{region}%"))
    if problem_category:
        query = query.filter(HistoricalReport.problem_category == problem_category)
    if audit_type:
        query = query.filter(HistoricalReport.audit_type == audit_type)
    if severity:
        query = query.filter(HistoricalReport.severity == severity)
    if status:
        query = query.filter(HistoricalReport.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            HistoricalReport.title.like(like),
            HistoricalReport.audited_org.like(like),
            HistoricalReport.findings.like(like),
            HistoricalReport.subject.like(like),
            HistoricalReport.report_no.like(like),
        ))
    total = query.count()
    rows = (query.order_by(HistoricalReport.year.desc(), HistoricalReport.id.desc())
            .offset(max(0, (page - 1) * per_page)).limit(min(per_page, 100)).all())
    return {"total": total, "page": page, "per_page": per_page,
            "items": [_out(r, db) for r in rows]}


@router.get("/quality-report")
def quality_report(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """数据完整性质检：字段缺失分布 + 待补录队列 + 重复与低置信清单。

    这是「数据全了才有分析」的门禁——分析前先看这份报告。
    """
    rows = db.query(HistoricalReport).all()
    total = len(rows)
    field_names = {
        "report_type": "报告类型", "report_date": "报告日期", "region": "地区",
        "audited_org": "被审计单位", "audit_type": "审计类型",
        "problem_category": "问题类别", "severity": "严重程度",
        "amount": "涉及金额", "report_no": "报告文号", "subject": "被审计对象",
        "findings": "问题发现", "conclusion": "处理意见",
    }
    missing_counter = Counter()
    for r in rows:
        for k in (r.missing_fields or []):
            missing_counter[k] += 1
    # 实际为空也算缺失（历史导入或人工删改后）
    actual_missing = Counter()
    for r in rows:
        for k, label in field_names.items():
            v = getattr(r, k, None)
            empty = v in (None, "", 0, 0.0, [], {}) if k != "amount" else v in (None, 0, 0.0)
            if empty:
                actual_missing[k] += 1
    need_review = [r for r in rows if r.status == "待补录"]
    low_conf = [r for r in rows if (r.extract_confidence or 0) < _REVIEW_THRESHOLD]
    hash_counter = Counter(r.content_hash for r in rows if r.content_hash)
    dups = [h for h, c in hash_counter.items() if c > 1]
    # 近似重复：导入时已标记 near_duplicate_of；再补扫一次存量数据
    near_dup: list[dict] = []
    for r in rows:
        if r.near_duplicate_of:
            prev = next((x for x in rows if x.id == r.near_duplicate_of), None)
            near_dup.append({
                "id": r.id, "title": r.title, "duplicate_of": r.near_duplicate_of,
                "duplicate_title": prev.title if prev else "",
                "reason": "文号相同",
            })
    seen_key: dict[tuple, int] = {}
    for r in rows:
        if r.near_duplicate_of:
            continue
        key = (r.report_no or "", (r.title or "").strip(), r.year or 0, (r.audited_org or "").strip())
        if not (key[0] or key[1]):
            continue
        k = (key[0], key[2]) if key[0] else (key[1], key[2], key[3])
        if k in seen_key:
            prev = next((x for x in rows if x.id == seen_key[k]), None)
            near_dup.append({
                "id": r.id, "title": r.title, "duplicate_of": seen_key[k],
                "duplicate_title": prev.title if prev else "",
                "reason": "文号相同" if key[0] else "标题+年度+单位相同",
            })
        else:
            seen_key[k] = r.id
    avg_conf = round(sum(r.extract_confidence or 0 for r in rows) / total, 3) if total else 0.0
    # 法规引用有效性（区分「错条」（bad）与「法规未入库」（missing））
    bad_refs: list[dict] = []
    missing_laws: list[dict] = []
    refs_checked = 0
    for r in rows:
        refs = r.legal_refs or []
        if not refs:
            continue
        refs_checked += len(refs)
        for c in check_refs(db, refs):
            if c.get("status") == "bad":
                bad_refs.append({**c, "report_id": r.id, "report_title": r.title})
            elif c.get("status") == "missing":
                missing_laws.append({**c, "report_id": r.id, "report_title": r.title})
    return {
        "total": total,
        "avg_confidence": avg_conf,
        "field_coverage": [
            {"field": k, "label": label,
             "missing": actual_missing.get(k, 0),
             "coverage": round(1 - actual_missing.get(k, 0) / total, 3) if total else 0.0}
            for k, label in field_names.items()
        ],
        "need_review_count": len(need_review),
        "need_review": [{"id": r.id, "title": r.title, "confidence": r.extract_confidence,
                         "missing": r.missing_fields or []} for r in need_review[:50]],
        "low_confidence_count": len(low_conf),
        "duplicate_groups": len(dups),
        "near_duplicates": len(near_dup),
        "near_duplicate_items": near_dup[:30],
        "by_status": dict(Counter(r.status or "已入库" for r in rows)),
        "char_total": sum(r.char_count or 0 for r in rows),
        "refs_checked": refs_checked,
        "bad_refs_count": len(bad_refs),
        "bad_refs": bad_refs[:30],
        "uncollected_laws_count": len(missing_laws),
        "uncollected_laws": missing_laws[:30],
    }


@router.get("/stats/dimensions")
def stats_dimensions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """多维度统计：时间 / 类型 / 地区 / 问题类别 / 审计类型 / 严重程度 / 金额区间 / 处理结果。"""
    rows = db.query(HistoricalReport).all()
    by_year = Counter()
    by_type = Counter()
    by_region = Counter()
    by_problem = Counter()
    by_audit = Counter()
    by_severity = Counter()
    by_status = Counter()
    amount_by_year: dict[int, float] = {}
    amount_total = 0.0
    amount_buckets = Counter()
    conclusion_kinds = Counter()

    for r in rows:
        y = r.year or 0
        if y:
            by_year[y] += 1
            amount_by_year[y] = round(amount_by_year.get(y, 0.0) + (r.amount or 0.0), 2)
        for c, key in ((by_type, r.report_type), (by_region, r.region),
                       (by_problem, r.problem_category), (by_audit, r.audit_type),
                       (by_severity, r.severity), (by_status, r.status)):
            if key:
                c[key] += 1
        amt = r.amount or 0.0
        amount_total = round(amount_total + amt, 2)
        if amt <= 0:
            amount_buckets["未提取金额"] += 1
        elif amt < 10:
            amount_buckets["10万以下"] += 1
        elif amt < 100:
            amount_buckets["10-100万"] += 1
        elif amt < 500:
            amount_buckets["100-500万"] += 1
        else:
            amount_buckets["500万以上"] += 1
        # 处理结果归类
        ctext = (r.conclusion or "") + (r.findings or "")
        if any(k in ctext for k in ("移送", "立案", "司法", "刑事")):
            conclusion_kinds["移送处理"] += 1
        elif any(k in ctext for k in ("处分", "问责", "诫勉", "提醒", "约谈")):
            conclusion_kinds["问责处理"] += 1
        elif any(k in ctext for k in ("整改", "建议", "完善", "健全")):
            conclusion_kinds["整改建议"] += 1
        elif r.conclusion:
            conclusion_kinds["其他处理"] += 1

    years = sorted(by_year.keys())
    from app.regions import REGIONS
    # 地区固定按测试地区 10 区划顺序输出（口径稳定，不随数量波动）
    by_region_ordered = [{"key": x, "count": by_region.get(x, 0)} for x in REGIONS]
    return {
        "total": len(rows),
        "by_year": [{"key": y, "count": by_year[y], "amount": amount_by_year.get(y, 0.0)} for y in years],
        "by_type": [{"key": k, "count": v} for k, v in by_type.most_common()],
        "by_region": by_region_ordered,
        "by_problem": [{"key": k, "count": v} for k, v in by_problem.most_common()],
        "by_audit_type": [{"key": k, "count": v} for k, v in by_audit.most_common()],
        "by_severity": [{"key": k, "count": v} for k, v in by_severity.most_common()],
        "by_status": [{"key": k, "count": v} for k, v in by_status.most_common()],
        "by_amount_bucket": [{"key": k, "count": v} for k, v in amount_buckets.items()],
        "by_conclusion_kind": [{"key": k, "count": v} for k, v in conclusion_kinds.most_common()],
        "amount_total": amount_total,
        "avg_amount": round(amount_total / len([r for r in rows if (r.amount or 0) > 0]), 2)
        if any((r.amount or 0) > 0 for r in rows) else 0.0,
    }


@router.get("/findings/analysis")
def findings_analysis(
    top: int = 12,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """问题条目级分析：把报告拆到条目粒度后统计。

    回答「哪类问题最常出现、涉及金额多大、集中在哪些单位/年份」——
    这是报告级统计给不出的颗粒度。
    """
    rows = (db.query(ReportFinding).join(
        HistoricalReport, ReportFinding.report_id == HistoricalReport.id).all())
    if not rows:
        return {"total": 0, "by_type": [], "by_year": [], "by_org": [],
                "by_severity": [], "top_findings": [], "amount_total": 0.0}
    by_type: Counter = Counter()
    by_year: Counter = Counter()
    by_org: Counter = Counter()
    by_sev: Counter = Counter()
    amount_total = 0.0
    for f in rows:
        if f.problem_type:
            by_type[f.problem_type] += 1
        if f.severity:
            by_sev[f.severity] += 1
        amount_total = round(amount_total + (f.amount or 0.0), 2)
        rep = db.get(HistoricalReport, f.report_id)
        if rep:
            if rep.year:
                by_year[rep.year] += 1
            if rep.audited_org:
                by_org[rep.audited_org] += 1
    top_rows = sorted(rows, key=lambda x: -(x.amount or 0))[:top]
    return {
        "total": len(rows),
        "amount_total": amount_total,
        "by_type": [{"key": k, "count": v} for k, v in by_type.most_common()],
        "by_year": [{"key": k, "count": v} for k, v in sorted(by_year.items())],
        "by_org": [{"key": k, "count": v} for k, v in by_org.most_common(15)],
        "by_severity": [{"key": k, "count": v} for k, v in by_sev.most_common()],
        "top_findings": [{
            "id": f.id, "report_id": f.report_id, "seq": f.seq, "title": f.title,
            "content": (f.content or "")[:200], "amount": f.amount,
            "problem_type": f.problem_type, "severity": f.severity,
        } for f in top_rows],
    }


@router.get("/refs/validate")
def validate_refs(
    report_id: int = 0,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """法规引用有效性校验：报告里援引的条款在法规库中是否真实存在。

    report_id=0 时校验全部报告（用于质检看板）。
    """
    if report_id:
        reps = [r for r in [db.get(HistoricalReport, report_id)] if r]
    else:
        reps = db.query(HistoricalReport).all()
    all_bad: list[dict] = []
    uncollected: list[dict] = []
    checked = 0
    for r in reps:
        refs = r.legal_refs or []
        if not refs:
            continue
        checked += len(refs)
        for c in check_refs(db, refs):
            if c.get("status") == "bad":
                all_bad.append({**c, "report_id": r.id, "report_title": r.title})
            elif c.get("status") == "missing":
                uncollected.append({**c, "report_id": r.id, "report_title": r.title})
    return {
        "reports": len(reps), "refs_checked": checked,
        "bad_count": len(all_bad), "bad_refs": all_bad[:100],
        "uncollected_count": len(uncollected), "uncollected_laws": uncollected[:100],
    }


@router.get("/meta/options")
def meta_options(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """筛选项字典（含库内实际取值 + 预置分类 + 测试地区 10 个地区口径）。"""
    from app.regions import REGIONS
    rows = db.query(HistoricalReport).all()
    years = sorted({r.year for r in rows if r.year}, reverse=True)
    # 地区按标准 10 区划顺序输出（库里出现过的排前面，其余保留占位）
    used = {r.region for r in rows if r.region}
    regions = [x for x in REGIONS if x in used] + [x for x in REGIONS if x not in used]
    return {
        "report_types": REPORT_TYPES,
        "audit_types": AUDIT_TYPES,
        "problem_categories": PROBLEM_CATEGORIES,
        "severities": SEVERITIES,
        "years": years,
        "regions": regions,
        "all_regions": REGIONS,
        "actual_types": sorted({r.report_type for r in rows if r.report_type}),
    }


# ================================================================ 类案参考

def _corpus(db: Session) -> tuple[list[dict], dict]:
    rows = db.query(HistoricalReport).all()
    snapshot = [{
        "id": r.id, "title": r.title, "report_no": r.report_no,
        "report_type": r.report_type, "year": r.year, "region": r.region,
        "audited_org": r.audited_org, "problem_category": r.problem_category,
        "severity": r.severity, "amount": r.amount, "subject": r.subject,
        "findings": (r.findings or "")[:600], "conclusion": (r.conclusion or "")[:400],
        "tags": r.tags or [], "legal_refs": r.legal_refs or [],
        "tf_vector": r.tf_vector or {}, "status": r.status,
        "extract_confidence": r.extract_confidence,
    } for r in rows]
    n = len(snapshot) or 1
    df = Counter()
    for s in snapshot:
        for t in (s["tf_vector"] or {}):
            df[t] += 1
    idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
    return snapshot, idf


def _match_snippets(db: Session, report_ids: list[int], q: str, limit: int = 3) -> dict[int, list[dict]]:
    """为命中报告抽取精确命中查询词的问题片段（finding 级）。

    为什么需要：报告级相似度只能告诉你「哪份报告可能相关」，审计人员真正要
    看的是「报告里哪一段和我要写的问题对口」。这里在 ReportFinding 条目中按
    查询词命中强度排序取前 limit 条；报告未拆条时回退 findings 分句匹配。
    返回 {report_id: [snippet...]}，仅含有命中的报告。
    """
    if not q.strip() or not report_ids:
        return {}
    terms = set(embeddings.tokenize(q))
    out: dict[int, list[dict]] = {}
    findings = (db.query(ReportFinding)
                .filter(ReportFinding.report_id.in_(report_ids)).all())
    for f in findings:
        text = f"{f.title or ''}。{f.content or ''}"
        # 「审计评价」类总体客套段落参考价值低，不作为命中片段
        if text.lstrip().startswith(("审计评价", "审计认为", "总体评价", "审计结果表明")):
            continue
        score = sum(1 for t in terms if t in text)
        if q in text:
            score += 5
        if score <= 0:
            continue
        out.setdefault(f.report_id, []).append({
            "finding_id": f.id, "seq": f.seq, "problem_type": f.problem_type,
            "content": (f.content or "")[:300], "score": score,
        })
    # 未拆条的报告：回退用 findings 文本分句匹配，保证老数据也有片段
    for rid in set(report_ids) - set(out.keys()):
        r = db.get(HistoricalReport, rid)
        if not r or not r.findings:
            continue
        for sent in re.split(r"[。；;\n]", r.findings):
            sent = sent.strip()
            if len(sent) < 8 or sent.startswith(("审计评价", "审计认为", "总体评价", "审计结果表明")):
                continue
            score = sum(1 for t in terms if t in sent)
            if q in sent:
                score += 5
            if score > 0:
                out.setdefault(rid, []).append({
                    "finding_id": 0, "seq": "", "problem_type": r.problem_category,
                    "content": sent[:300], "score": score,
                })
    for rid in out:
        out[rid].sort(key=lambda x: -x["score"])
        out[rid] = out[rid][:limit]
    return out


@router.post("/search")
def search_reports(
    body: HistoricalReportSearchIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """类案参考检索：TF-IDF 相似度 + 多维过滤。

    空 query 时返回最新入库（用于浏览）。
    """
    q = body.q
    top_k = body.top_k
    report_type = body.report_type
    year = body.year
    region = body.region
    problem_category = body.problem_category
    snapshot, idf = _corpus(db)
    cand = snapshot
    if report_type:
        cand = [s for s in cand if s["report_type"] == report_type]
    if year:
        cand = [s for s in cand if s["year"] == year]
    if region:
        cand = [s for s in cand if region in (s["region"] or "")]
    if problem_category:
        cand = [s for s in cand if s["problem_category"] == problem_category]

    if not q.strip():
        hits = [{"id": s["id"], "title": s["title"], "score": 0.0, **s} for s in cand[:top_k]]
        return {"mode": "browse", "hits": hits, "total": len(cand)}

    q_vec = embeddings.embed_local(q)
    scored = []
    for s in cand:
        sim = embeddings.cosine_sparse(q_vec, s["tf_vector"] or {}, idf)
        # 结构性加权：同地区/同类别轻微加权，提升"像不像"的直觉一致性
        if region and s["region"] == region:
            sim += 0.05
        if problem_category and s["problem_category"] == problem_category:
            sim += 0.05
        scored.append({**s, "score": round(sim, 4)})
    scored.sort(key=lambda x: -x["score"])
    hits = scored[:max(1, min(top_k, 30))]
    hit_ids = [h["id"] for h in hits]
    # finding 级命中片段：让用户直接看到「报告里哪一段对口」
    snippets = _match_snippets(db, hit_ids, q)
    # 被引热度：被引用次数越多 = 被越多审计人员认可，优先看
    cite_counts = dict(
        db.query(ReportReference.report_id, func.count(ReportReference.id))
        .filter(ReportReference.report_id.in_(hit_ids))
        .group_by(ReportReference.report_id).all()
    ) if hit_ids else {}
    for h in hits:
        h["snippets"] = snippets.get(h["id"], [])
        h["cite_count"] = cite_counts.get(h["id"], 0)
    return {"mode": "search", "hits": hits, "total": len(cand), "query": q,
            "terms": sorted(set(embeddings.tokenize(q)))}


@router.get("/similar/{rid}")
def similar_reports(rid: int, top_k: int = 6,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """与指定报告最相似的历史报告（以该报告全文为查询）。"""
    src = db.get(HistoricalReport, rid)
    if not src:
        raise HTTPException(404, "报告不存在")
    snapshot, idf = _corpus(db)
    q_vec = src.tf_vector or embeddings.embed_local(_index_text(src))
    scored = []
    for s in snapshot:
        if s["id"] == rid:
            continue
        scored.append({**s, "score": round(embeddings.cosine_sparse(q_vec, s["tf_vector"] or {}, idf), 4)})
    scored.sort(key=lambda x: -x["score"])
    return {"source": {"id": src.id, "title": src.title}, "hits": scored[:max(1, min(top_k, 20))]}


@router.post("/reference")
def create_reference(
    body: HistoricalReportReferenceIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """类案引用留痕：记录「谁、何时、引用了哪份报告/哪条问题」，形成审计链。

    与「引用并复制」按钮配套：复制表述供复用前先落一条痕迹，既满足留痕要求，
    也让被引次数成为报告参考价值的热度指标。
    """
    r = db.get(HistoricalReport, body.report_id)
    if not r:
        raise HTTPException(404, "报告不存在")
    if body.finding_id:
        f = db.get(ReportFinding, body.finding_id)
        if not f or f.report_id != r.id:
            raise HTTPException(400, "问题条目不属于该报告")
    ref = ReportReference(
        report_id=r.id,
        finding_id=body.finding_id or None,
        query=(body.query or "")[:200],
        note=(body.note or "")[:200],
        user_id=user.id,
    )
    db.add(ref)
    db.commit()
    db.refresh(ref)
    audit.write_log(db, user.id, user.username, action="historical_reference",
                    resource_type="historical_report", resource_id=str(r.id),
                    detail={"finding_id": ref.finding_id, "query": ref.query})
    return {"id": ref.id, "report_id": r.id, "finding_id": ref.finding_id,
            "cite_count": _cite_count(db, r.id),
            "created_at": ref.created_at.isoformat() if ref.created_at else ""}


def _cite_count(db: Session, report_id: int) -> int:
    return (db.query(func.count(ReportReference.id))
            .filter(ReportReference.report_id == report_id).scalar() or 0)


@router.get("/references")
def list_references(limit: int = 50,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """最近引用记录（审计链可见）：报告标题 + 条目摘要 + 引用人 + 时间。"""
    rows = (db.query(ReportReference)
            .join(HistoricalReport, ReportReference.report_id == HistoricalReport.id)
            .order_by(ReportReference.id.desc())
            .limit(max(1, min(limit, 200))).all())
    out = []
    for ref in rows:
        r = db.get(HistoricalReport, ref.report_id)
        u = db.get(User, ref.user_id) if ref.user_id else None
        snippet = ""
        if ref.finding_id:
            f = db.get(ReportFinding, ref.finding_id)
            if f:
                snippet = (f.content or "")[:120]
        if not snippet and r:
            snippet = (r.title or "")[:120]
        out.append({
            "id": ref.id, "report_id": ref.report_id,
            "report_title": (r.title if r else "（报告已删除）"),
            "finding_id": ref.finding_id or 0, "snippet": snippet,
            "query": ref.query or "", "note": ref.note or "",
            "username": (u.username if u else "—"),
            "created_at": ref.created_at.strftime("%Y-%m-%d %H:%M") if ref.created_at else "",
        })
    return {"items": out, "total": len(out)}


# ================================================================ 详情 / 编辑 / 删除

# ================================================================ 批次与导出

@router.get("/batches")
def list_batches(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (db.query(ImportBatch).order_by(ImportBatch.id.desc()).limit(50).all())
    out = []
    for b in rows:
        up = db.get(User, b.created_by) if b.created_by else None
        out.append({
            "id": b.id, "batch_no": b.batch_no, "total": b.total, "ok": b.ok,
            "duplicated": b.duplicated, "failed": b.failed, "need_review": b.need_review,
            "note": b.note or "",
            "operator": (up.real_name or up.username) if up else "",
            "created_at": b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else "",
            "detail": json.loads(b.detail_json) if b.detail_json else [],
        })
    return {"items": out}


@router.get("/export")
def export_xlsx(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """导出报告明细 + 多维统计（两个工作表）。"""
    import openpyxl
    rows = db.query(HistoricalReport).order_by(HistoricalReport.year.desc()).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "报告明细"
    ws.append(["报告文号", "标题", "报告类型", "报告日期", "年度", "地区", "被审计单位",
               "审计类型", "问题类别", "严重程度", "涉及金额(万元)", "被审计对象",
               "问题发现", "处理意见", "标签", "状态", "识别置信度", "入库时间"])
    for r in rows:
        ws.append([
            r.report_no, r.title, r.report_type, r.report_date, r.year, r.region,
            r.audited_org, r.audit_type, r.problem_category, r.severity, r.amount,
            r.subject, (r.findings or "")[:500], (r.conclusion or "")[:300],
            "、".join(r.tags or []), r.status,
            round(r.extract_confidence or 0, 2),
            r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        ])
    ws2 = wb.create_sheet("多维统计")
    ws2.append(["维度", "取值", "数量"])
    for dim, key in (("年度", "year"), ("报告类型", "report_type"), ("地区", "region"),
                     ("问题类别", "problem_category"), ("审计类型", "audit_type"),
                     ("严重程度", "severity"), ("状态", "status")):
        c = Counter(getattr(r, key) for r in rows if getattr(r, key, 0))
        for k, v in c.most_common():
            ws2.append([dim, k, v])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"历史报告库_{_dt.now().strftime('%Y%m%d')}.xlsx"
    audit.write_log(db, user.id, user.username, action="historical_export",
                    resource_type="historical_report", resource_id="export",
                    detail={"count": len(rows)})
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(False, fname)},
    )


# ================================================================ 屡查屡犯 / 整改跟踪

@router.get("/recurring")
def recurring_analysis(
    min_reports: int = 2,
    min_repeat: int = 2,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """屡查屡犯 / 整改跟踪分析（纯从已建档数据派生，无需新增录入）。

    核心：同一被审计单位在 >=min_reports 份不同报告中，某一问题类别
    （finding.problem_type，缺失时回退 report.problem_category）跨不同报告出现
    >=min_repeat 次，即判定「屡查屡犯」——同一问题反复出现 = 整改不力信号。
    这是报告级统计给不出的深度（报告级类别不同，但条目级老毛病一样）。
    """
    reports = db.query(HistoricalReport).all()
    if not reports:
        return {"total_orgs": 0, "audited_multi": 0, "recurring_orgs": 0,
                "threshold": {"min_reports": min_reports, "min_repeat": min_repeat},
                "items": [], "by_region": []}
    fmap: dict[int, list] = {}
    for f in db.query(ReportFinding).all():
        fmap.setdefault(f.report_id, []).append(f)

    org_groups: dict[str, list] = {}
    for r in reports:
        org_groups.setdefault(r.audited_org or "（未标注单位）", []).append(r)

    items: list[dict] = []
    for org, reps in org_groups.items():
        type_reports: dict[str, dict] = {}  # eff_type -> {reports:set, findings:int}
        finding_count = 0
        for r in reps:
            for f in fmap.get(r.id, []):
                finding_count += 1
                t = (f.problem_type or "").strip() or (r.problem_category or "").strip()
                if not t:
                    continue
                d = type_reports.setdefault(t, {"reports": set(), "findings": 0})
                d["reports"].add(r.id)
                d["findings"] += 1
        repeated = [
            {"type": t, "times": len(d["reports"]), "reports": sorted(d["reports"]),
             "findings": d["findings"]}
            for t, d in type_reports.items() if len(d["reports"]) >= min_repeat
        ]
        repeated.sort(key=lambda x: (-x["times"], -x["findings"]))
        if len(reps) >= min_reports and repeated:
            items.append({
                "org": org, "region": reps[0].region or "", "report_count": len(reps),
                "finding_count": finding_count,
                "repeated_category_count": len(repeated),
                "max_repeat": max(x["times"] for x in repeated),
                "repeated_categories": repeated,
                "reports": [{"rid": r.id, "year": r.year or 0, "title": r.title,
                             "problem_category": r.problem_category or ""}
                            for r in sorted(reps, key=lambda x: -(x.year or 0))],
            })
    items.sort(key=lambda x: (-x["repeated_category_count"], -x["report_count"], -x["max_repeat"]))

    region_map: dict[str, dict] = {}
    for it in items:
        key = it["region"] or "（未标注）"
        rg = region_map.setdefault(key, {"org_count": 0, "recurring_org_count": 0})
        rg["org_count"] += 1
        rg["recurring_org_count"] += 1
    by_region = [{"region": k, **v} for k, v in
                 sorted(region_map.items(), key=lambda kv: -kv[1]["recurring_org_count"])]
    audited_multi = sum(1 for reps in org_groups.values() if len(reps) >= min_reports)
    return {
        "total_orgs": len(org_groups),
        "audited_multi": audited_multi,
        "recurring_org_count": len(items),
        "threshold": {"min_reports": min_reports, "min_repeat": min_repeat},
        "items": items,
        "by_region": by_region,
    }


@router.get("/recurring/org")
def recurring_org(
    org: str = "",
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """单单位整改时间线（证据链）：历年审计报告 + 重复出现的问题类别高亮。

    审计人员据此写「整改不力」说明：同一问题在哪些年份被反复指出。
    """
    if not (org or "").strip():
        raise HTTPException(400, "缺少单位参数 org")
    reps = db.query(HistoricalReport).filter(HistoricalReport.audited_org == org).all()
    if not reps:
        raise HTTPException(404, "未找到该单位的报告")
    rids = [r.id for r in reps]
    fmap: dict[int, list] = {}
    for f in db.query(ReportFinding).filter(ReportFinding.report_id.in_(rids)).all():
        fmap.setdefault(f.report_id, []).append(f)
    type_reports: dict[str, set] = {}
    for r in reps:
        for f in fmap.get(r.id, []):
            t = (f.problem_type or "").strip() or (r.problem_category or "").strip()
            if t:
                type_reports.setdefault(t, set()).add(r.id)
    repeated_types = {t for t, s in type_reports.items() if len(s) >= 2}
    timeline = []
    for r in sorted(reps, key=lambda x: -(x.year or 0)):
        fs = fmap.get(r.id, [])
        cats = sorted({(f.problem_type or "").strip() or (r.problem_category or "").strip()
                       for f in fs if ((f.problem_type or "").strip() or (r.problem_category or "").strip())})
        timeline.append({
            "rid": r.id, "year": r.year or 0, "title": r.title,
            "report_type": r.report_type or "", "problem_category": r.problem_category or "",
            "finding_count": len(fs), "categories": cats,
        })
    return {"org": org, "region": reps[0].region or "", "report_count": len(reps),
            "repeated_types": sorted(repeated_types), "timeline": timeline}


@router.get("/{rid}", response_model=HistoricalReportOut)
def detail(rid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(HistoricalReport, rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    return _out(r, db)


@router.get("/{rid}/findings")
def report_findings(rid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """单份报告的问题条目（拆条结果）。"""
    r = db.get(HistoricalReport, rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    rows = db.query(ReportFinding).filter(ReportFinding.report_id == rid).order_by(ReportFinding.id).all()
    return {"report_id": rid, "title": r.title, "items": [{
        "id": f.id, "seq": f.seq, "title": f.title, "content": f.content,
        "amount": f.amount, "problem_type": f.problem_type, "severity": f.severity,
        "legal_refs": f.legal_refs or [],
    } for f in rows]}


@router.get("/{rid}/content")
def full_content(rid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(HistoricalReport, rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    return {"id": r.id, "title": r.title, "content": r.content or "",
            "legal_refs": r.legal_refs or []}


@router.put("/{rid}", response_model=HistoricalReportOut)
def update(rid: int, body: HistoricalReportIn, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """人工补录 / 修正自动识别字段。修正后重新计算缺失清单与状态。"""
    r = db.get(HistoricalReport, rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    data = body.model_dump(exclude_none=True)
    for k, v in data.items():
        if hasattr(r, k):
            setattr(r, k, v)
    if data.get("report_date"):
        try:
            r.year = int(str(data["report_date"])[:4])
        except Exception:
            pass
    # 重算缺失：关键字段（title/report_type/report_date/region/audited_org/findings）
    key_fields = ["title", "report_type", "report_date", "region", "audited_org", "findings"]
    r.missing_fields = [k for k in key_fields if not getattr(r, k, "")]
    if r.missing_fields:
        r.status = "待补录"
    elif r.status == "待补录":
        r.status = "已复核"
    r.extract_confidence = 1.0 if not r.missing_fields else (r.extract_confidence or 0.0)
    r.tf_vector = embeddings.embed_local(_index_text(r))
    db.commit()
    db.refresh(r)
    audit.write_log(db, user.id, user.username, action="historical_update",
                    resource_type="historical_report", resource_id=str(r.id),
                    detail={"fields": list(data.keys())})
    return _out(r, db)


@router.post("/batch-delete")
def batch_delete(
    body: HistoricalReportBatchDeleteIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """批量删除历史报告（需输入当前账号与密码二次确认）。

    安全设计：
    - 必须校验「当前登录用户」的口令，防止误操作与他人冒用；
    - 口令错误一律不执行任何删除（返回 401）；
    - 审计日志记录删除数量与目标 ID，便于追溯。
    """
    if not body.ids:
        raise HTTPException(400, "未选择要删除的报告")
    if len(body.ids) > 500:
        raise HTTPException(400, "单次删除不得超过 500 条")
    if not (body.username or "").strip() or not body.password:
        raise HTTPException(400, "请输入账号与密码以确认删除")

    from app.security import verify_password
    # 账号必须与当前登录用户一致（只允许用自己的口令删自己的操作）
    if body.username.strip() != (user.username or ""):
        raise HTTPException(401, "账号与当前登录账号不一致")
    if not verify_password(body.password, user.password_hash or ""):
        raise HTTPException(401, "密码错误，删除未执行")

    ids = list({int(i) for i in body.ids})
    rows = db.query(HistoricalReport).filter(HistoricalReport.id.in_(ids)).all()
    exist_ids = [r.id for r in rows]
    deleted = 0
    for r in rows:
        if r.file_path:
            try:
                p = BASE_DIR / r.file_path
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        db.delete(r)
    db.commit()
    # 关联的问题条目一并清理
    try:
        db.query(ReportFinding).filter(ReportFinding.report_id.in_(exist_ids)).delete(
            synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()

    audit.write_log(db, user.id, user.username, action="historical_batch_delete",
                    resource_type="historical_report", resource_id="batch",
                    detail={"requested": ids, "deleted": len(exist_ids),
                            "not_found": [i for i in ids if i not in exist_ids]})
    return {"ok": True, "deleted": len(exist_ids), "not_found": len(ids) - len(exist_ids),
            "ids": exist_ids}


@router.delete("/{rid}")
def delete(rid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(HistoricalReport, rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    if r.file_path:
        try:
            p = BASE_DIR / r.file_path
            if p.exists():
                p.unlink()
        except Exception:
            pass
    db.delete(r)
    db.commit()
    audit.write_log(db, user.id, user.username, action="historical_delete",
                    resource_type="historical_report", resource_id=str(rid), detail={})
    return {"ok": True}


