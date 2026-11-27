"""识别服务设置：文字识别（OCR）/ 扫描版 PDF / 语音转写（ASR）选用 Nexent 的识别服务或自行配置。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit, recognizer
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.models import User

router = APIRouter(prefix="/api/recognition", tags=["recognition"])


class OcrIn(BaseModel):
    source: Optional[str] = None
    provider: Optional[str] = None
    pdf_mode: Optional[str] = None
    fallback_builtin: Optional[bool] = None
    urls: Optional[dict[str, str]] = None


class AsrIn(BaseModel):
    source: Optional[str] = None
    provider: Optional[str] = None
    fallback_builtin: Optional[bool] = None
    urls: Optional[dict[str, str]] = None
    qwen_model: Optional[str] = None


class SettingsIn(BaseModel):
    ocr: Optional[OcrIn] = None
    asr: Optional[AsrIn] = None


@router.get("/status")
def status(user: User = Depends(get_current_user)):
    eff = recognizer.effective()
    return {"ocr": {"provider": eff["ocr"]["provider"], "label": recognizer.ocr_label(eff),
                    "pdf_mode": eff["ocr"]["pdf_mode"], "from": eff["ocr"]["from"]},
            "asr": {"provider": eff["asr"]["provider"], "label": recognizer.asr_label(eff), "from": eff["asr"]["from"]}}


@router.get("/settings")
def get_settings(user: User = Depends(require_roles("admin"))):
    return recognizer.public_settings()


@router.post("/sync")
def sync(user: User = Depends(require_roles("admin"))):
    """立即重新读取 Nexent 当前租户的识别服务配置（跳过 30 秒缓存）。"""
    return recognizer.public_settings(force_sync=True)


@router.put("/settings")
def put_settings(body: SettingsIn, user: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    patch = body.model_dump(exclude_none=True)
    o, a = patch.get("ocr") or {}, patch.get("asr") or {}
    for part in (o, a):
        if part.get("source") and part["source"] not in ("nexent", "manual"):
            raise HTTPException(400, "配置来源只能是 nexent 或 manual")
    if o.get("provider") and o["provider"] not in recognizer.OCR_PROVIDERS:
        raise HTTPException(400, "未知的文字识别服务")
    if o.get("pdf_mode") and o["pdf_mode"] not in recognizer.PDF_MODES:
        raise HTTPException(400, "未知的 PDF 解析方式")
    if a.get("provider") and a["provider"] not in recognizer.ASR_PROVIDERS:
        raise HTTPException(400, "未知的语音识别服务")
    for part in (o, a):
        for k, v in (part.get("urls") or {}).items():
            if v and not v.strip().lower().startswith(("http://", "https://")) and "://" in v:
                raise HTTPException(400, f"{k} 地址只支持 http/https")
    recognizer.save_settings(patch)
    s = recognizer.public_settings()
    audit.write_log(db, user.id, user.username, "recognition_settings", "system", "recognition", {
        "ocr_source": s["ocr"]["source"], "ocr": s["ocr"]["provider"], "pdf_mode": s["ocr"]["pdf_mode"],
        "asr_source": s["asr"]["source"], "asr": s["asr"]["provider"],
    })
    return s


class TestIn(BaseModel):
    kind: str            # ocr / pdf / asr
    provider: str = ""
    url: str = ""
    model: str = ""


@router.post("/test")
def test(body: TestIn, user: User = Depends(require_roles("admin"))):
    if body.kind not in ("ocr", "pdf", "asr"):
        raise HTTPException(400, "kind 须为 ocr / pdf / asr")
    return recognizer.test_service(body.kind, body.provider, body.url, body.model)
