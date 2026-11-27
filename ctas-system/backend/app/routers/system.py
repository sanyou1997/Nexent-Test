"""运行模式自检：如实暴露各引擎是「离线本机」还是「云端 API」。

用途：前端据此显示真实角标，避免空喊「完全离网」。识别类（OCR/PDF/ASR）
默认即离线；文本生成类（LLM）在「在线·云端」与「离网·本机」之间由用户在界面上切换，
一次都不能虚标——服务没起来、Key 没配，接口必须如实返回不可用。
"""
import importlib.util as _util
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.models import User
from app.audit import write_log
from app import nexent_client as nx

router = APIRouter(prefix="/api/system", tags=["系统运行模式"])


def _installed(mod: str) -> bool:
    return _util.find_spec(mod) is not None


def _llm_mode() -> dict:
    """文本生成能力：全部由 Nexent 场景智能体承担（模型在 Nexent 里按智能体配置）。"""
    cfg = nx.load_settings()
    st = nx.status()
    agents = cfg.get("scene_agents") or {}
    ready = bool(st.get("authorized"))
    return {
        "mode": "nexent" if ready else "disabled",
        "label": "Nexent 场景智能体",
        "ready": ready, "reachable": bool(st.get("reachable")),
        "scene_agents": {nx.SCENES[k][0]: v for k, v in agents.items() if k in nx.SCENES},
        "note": ("生成、抽取、审查、法规问答、纪要、研判均交由 Nexent 智能体处理；模型在 Nexent 中按智能体配置"
                 if ready else f"Nexent 暂不可用：{st.get('error') or '未配置'}；相关功能已降级为规则 / 模板 / 纯检索"),
    }


def _embedding_mode() -> dict:
    key = bool(settings.EMBEDDING_API_KEY)
    base = (settings.EMBEDDING_BASE_URL or "").lower()
    if not key:
        return {"mode": "local", "engine": "本地字符 n-gram TF-IDF",
                "note": "未配置 Embedding Key，RAG 检索走本地 TF-IDF，完全离线"}
    if "localhost" in base or "127.0.0.1" in base:
        return {"mode": "local", "engine": settings.EMBEDDING_MODEL,
                "note": "指向本机向量服务，可离网"}
    return {"mode": "cloud", "engine": settings.EMBEDDING_MODEL,
            "base_url": settings.EMBEDDING_BASE_URL,
            "note": "指向公有云向量服务，需联网"}


@router.get("/runtime")
def runtime(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tesseract = _installed("rapidocr_onnxruntime")
    cv2_ok = _installed("cv2")
    pdfium = _installed("pypdfium2")
    asr = _installed("faster_whisper") or _installed("whisper")
    mineru = _installed("mineru")
    from app import recognizer
    eff = recognizer.effective()
    return {
        "llm": _llm_mode(),
        "embedding": _embedding_mode(),
        "ocr": {
            "available": True,
            "engine": recognizer.ocr_label(eff),
            "offline": True,
            "note": "图片与扫描页 OCR 复用 Nexent 识别服务（系统管理 → 识别服务）",
        },
        "pdf": {
            "available": bool(pdfium),
            "engine": ("pypdfium2 文本抽取 + " + ("Nexent · MinerU" if eff["ocr"]["pdf_mode"] == "mineru"
                                                 else recognizer.ocr_label(eff))) if pdfium else "未安装",
            "offline": True,
            "note": "数字文本页本机抽取；扫描页交 Nexent 识别服务",
        },
        "asr": {
            "available": True,
            "engine": recognizer.asr_label(eff),
            "offline": True,
            "note": "谈话录音转写复用 Nexent 识别服务（系统管理 → 识别服务）",
        },
        "rag": {
            "mode": _embedding_mode()["mode"],
            "offline": _embedding_mode()["mode"] == "local",
        },
        "mineru": {"available": bool(mineru),
                   "note": "可选：PDF 语料入库的版面解析；缺失时不影响 OCR/检索"},
        "offline_capable": True,
        "summary": "识别类（OCR / PDF / 录音）复用 Nexent 识别服务；文本生成类交由 Nexent 场景智能体，"
                   "部署到内网后只需把 Nexent 的模型接口换成本地模型，业务平台无需改动",
    }


class SceneTestIn(BaseModel):
    scene: str = "writing"


@router.get("/llm-mode", summary="文本生成能力概况（Nexent 场景智能体）")
def get_model_mode(user: User = Depends(get_current_user)):
    return _llm_mode()


@router.post("/llm-mode/test", summary="用指定场景的 Nexent 智能体真实生成一次，验证端到端效果")
def test_model_generation(body: SceneTestIn = SceneTestIn(), db: Session = Depends(get_db),
                          user: User = Depends(require_roles("admin"))):
    from app.llm import provider
    if body.scene not in nx.SCENES:
        raise HTTPException(400, "未知场景")
    prof = provider.active_profile(body.scene)
    t0 = time.time()
    text = provider.chat([{"role": "user", "content": "这是连通性测试，请只回复两个字：可用"}],
                         scene=body.scene, user_id=user.id, timeout=120)
    ms = int((time.time() - t0) * 1000)
    write_log(db, user_id=user.id, username=user.username, action="scene_agent_test",
              resource_type="system", resource_id=body.scene, detail={"ok": bool(text), "agent": prof["agent"], "latency_ms": ms})
    if not text:
        return {"ok": False, "scene": body.scene, "agent": prof["agent"], "latency_ms": ms,
                "reason": "未返回结果（Nexent 未接入 / 场景未指定智能体 / 智能体出错），详见审计与调用记录"}
    return {"ok": True, "scene": body.scene, "agent": prof["agent"], "latency_ms": ms, "sample": text.strip()[:80]}
