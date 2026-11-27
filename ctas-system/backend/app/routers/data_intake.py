"""数据接入完整度总览——让「数据是否全了」全局可见（用户 #1 原则）。

银行流水自带完整性质检（跳行/缺失率/评分）；档案/转录/数据集有落库记录可量化；
OCR/ASR 为实时调用、不留存批次，故用审计链反查当日调用次数，使"数据全不全"跨渠道统一呈现。
"""
from datetime import datetime, date

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    User, BankStatement, Archive, Transcript, Dataset, AuditLog,
)

router = APIRouter(prefix="/api/data-intake", tags=["数据接入"])


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # ---- 银行流水：自带完整性质检 ----
    banks = db.query(BankStatement).all()
    bank_by_level = {"高": 0, "中": 0, "低": 0}
    bank_scores = [b.completeness_score for b in banks if b.completeness_score is not None]
    for b in banks:
        lv = b.completeness_level or "中"
        bank_by_level[lv] = bank_by_level.get(lv, 0) + 1
    bank_avg = round(sum(bank_scores) / len(bank_scores), 3) if bank_scores else None
    bank_latest = max((b.created_at for b in banks if b.created_at), default=None)

    # ---- 档案数字化：OCR 落地情况即完整度代理 ----
    archives = db.query(Archive).all()
    arch_ocr_done = sum(1 for a in archives if (a.ocr_text or "").strip())
    arch_pages = sum((a.page_count or 0) for a in archives)
    arch_chars = sum((a.char_count or 0) for a in archives)
    arch_latest = max((a.created_at for a in archives if a.created_at), default=None)

    # ---- 转录：状态分布 ----
    trans = db.query(Transcript).all()
    trans_by_status = {}
    for t in trans:
        trans_by_status[t.status] = trans_by_status.get(t.status, 0) + 1
    trans_latest = max((t.created_at for t in trans if t.created_at), default=None)

    # ---- 数据集：行数总量 ----
    datasets = db.query(Dataset).all()
    ds_rows = sum((d.row_count or 0) for d in datasets)
    ds_latest = max((d.created_at for d in datasets if d.created_at), default=None)

    # ---- OCR / ASR：实时调用，用审计链反查当日次数 ----
    today = date.today()
    def _audit_count(action: str) -> int:
        return db.query(func.count(AuditLog.id)).filter(
            AuditLog.action == action,
            func.date(AuditLog.created_at) == today,
        ).scalar() or 0
    ocr_today = _audit_count("ocr")
    asr_today = _audit_count("asr")

    channels = [
        {
            "key": "bank", "name": "银行流水", "persisted": True,
            "count": len(banks),
            "latest_at": bank_latest.strftime("%Y-%m-%d %H:%M") if bank_latest else None,
            "completeness": {
                "by_level": bank_by_level,
                "avg_score": bank_avg,
                "low_count": bank_by_level.get("低", 0),
            },
            "note": "每批次含完整性质检：跳行原因 / 字段缺失率 / 完整度评分",
        },
        {
            "key": "archive", "name": "档案数字化", "persisted": True,
            "count": len(archives),
            "latest_at": arch_latest.strftime("%Y-%m-%d %H:%M") if arch_latest else None,
            "completeness": {
                "ocr_done": arch_ocr_done,
                "ocr_missing": len(archives) - arch_ocr_done,
                "total_pages": arch_pages,
                "total_chars": arch_chars,
            },
            "note": "OCR 文本落地即完整度代理；未识别文本=该份不可检索",
        },
        {
            "key": "transcript", "name": "会议/谈话转录", "persisted": True,
            "count": len(trans),
            "latest_at": trans_latest.strftime("%Y-%m-%d %H:%M") if trans_latest else None,
            "completeness": {"by_status": trans_by_status},
            "note": "失败/转录中状态需重跑，否则该份内容缺失",
        },
        {
            "key": "dataset", "name": "监督数据集", "persisted": True,
            "count": len(datasets),
            "latest_at": ds_latest.strftime("%Y-%m-%d %H:%M") if ds_latest else None,
            "completeness": {"total_rows": ds_rows},
            "note": "对碰分析的原料；行数与字段完整性决定模型可用",
        },
        {
            "key": "ocr", "name": "OCR 实时识别", "persisted": False,
            "count": ocr_today,
            "completeness": {"scope": "今日调用次数（来自审计链）"},
            "note": "实时调用不留存批次，仅记审计；需落库请走「档案数字化」",
        },
        {
            "key": "asr", "name": "语音实时转写", "persisted": False,
            "count": asr_today,
            "completeness": {"scope": "今日调用次数（来自审计链）"},
            "note": "实时调用不留存批次，仅记审计；需留存请走「转录」",
        },
    ]

    return {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "channels": channels,
        "summary": {
            "persisted_total": len(banks) + len(archives) + len(trans) + len(datasets),
            "low_completeness_batches": bank_by_level.get("低", 0) + (len(archives) - arch_ocr_done),
        },
    }
