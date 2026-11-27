"""识别服务配置与适配：材料 OCR、扫描版 PDF、谈话录音转写统一复用 Nexent 的识别服务。

两种配置来源（「系统管理 → 识别服务」逐项切换）：
- 跟随 Nexent（默认）：每次调用前读取 Nexent 当前租户「文件解析」配置（30 秒缓存，可手动同步刷新）。
  Nexent 的配置接口只接受网页登录，故通过只读数据库连接 NEXENT_DB_DSN 读取
  nexent.document_parser_settings，租户由已配置的北向 API Key 经 nexent.ctas_tenant_of_key() 解析
  （只读账号的建立见 docker/nexent-readonly-role.sql）。读取失败时自动使用下面的手动配置。
- 手动配置：在本系统单独配一份服务地址（同 Nexent 的协议与默认地址）。

本机内置引擎（RapidOCR / faster-whisper）不再出现在界面上；仅当环境变量显式设为 builtin 时
（Windows 桌面单机模式）沿用旧链路。
"""
from __future__ import annotations

import base64
import copy
import importlib.util
import io
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    import httpx2 as httpx  # type: ignore

from app.config import BASE_DIR

log = logging.getLogger("ctas.recognizer")

SETTINGS_FILE = Path(os.getenv("RECOGNITION_SETTINGS_FILE") or (BASE_DIR / "data" / "recognition_settings.json"))
_lock = threading.Lock()

OCR_PROVIDERS = {
    "umi_ocr": "Nexent · Umi-OCR",
    "paddle_ocr": "Nexent · PaddleOCR 通用 OCR",
    "paddle_layout": "Nexent · PP-StructureV3 版面解析",
}
PDF_MODES = {
    "mineru": "扫描版 PDF 整份交 Nexent · MinerU 解析（Nexent 默认）",
    "page_ocr": "文本层抽取 + 扫描页逐页 OCR（使用上面的文字识别服务）",
}
ASR_PROVIDERS = {
    "paraformer": "Nexent · 本地普通话转写（Paraformer-Large）",
    "qwen_asr": "Nexent · Qwen3-ASR",
}
_LEGACY = {"builtin": "本机内置引擎"}
DEFAULT_URLS = {
    "umi_ocr": "http://nexent-umi-ocr:1224",
    "paddle_ocr": "http://host.docker.internal:8080",
    "paddle_layout": "http://host.docker.internal:8081",
    "mineru": "http://host.docker.internal:18001",
    "paraformer": "http://host.docker.internal:18095",
    "qwen_asr": "http://nexent-qwen-asr:8000",
}
QWEN_MODEL = "Qwen/Qwen3-ASR-0.6B"


def _env(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


def _defaults() -> dict:
    return {
        "ocr": {
            "source": _env("CTAS_OCR_SOURCE", "nexent"),
            "provider": _env("CTAS_OCR_PROVIDER", "umi_ocr"),
            "pdf_mode": _env("CTAS_PDF_MODE", "mineru"),
            "fallback_builtin": False,
            "urls": {
                "umi_ocr": _env("CTAS_UMI_OCR_URL", DEFAULT_URLS["umi_ocr"]),
                "paddle_ocr": _env("CTAS_PADDLE_OCR_URL", DEFAULT_URLS["paddle_ocr"]),
                "paddle_layout": _env("CTAS_PADDLE_LAYOUT_URL", DEFAULT_URLS["paddle_layout"]),
                "mineru": _env("CTAS_MINERU_URL", DEFAULT_URLS["mineru"]),
            },
        },
        "asr": {
            "source": _env("CTAS_ASR_SOURCE", "nexent"),
            "provider": _env("CTAS_ASR_PROVIDER", "paraformer"),
            "fallback_builtin": False,
            "urls": {
                "paraformer": _env("CTAS_PARAFORMER_URL", DEFAULT_URLS["paraformer"]),
                "qwen_asr": _env("CTAS_QWEN_ASR_URL", DEFAULT_URLS["qwen_asr"]),
            },
            "qwen_model": _env("CTAS_QWEN_ASR_MODEL", QWEN_MODEL),
        },
    }


def load_settings() -> dict:
    cfg = _defaults()
    try:
        if SETTINGS_FILE.exists():
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for part in ("ocr", "asr"):
                s = saved.get(part) or {}
                for k, v in s.items():
                    if k == "urls" and isinstance(v, dict):
                        cfg[part]["urls"].update({uk: uv for uk, uv in v.items() if uk in cfg[part]["urls"] and uv})
                    elif k in cfg[part] and v is not None:
                        cfg[part][k] = v
    except Exception as e:  # 配置损坏时回到默认值，不影响业务
        log.warning("recognition settings unreadable: %s", e)
    if cfg["ocr"]["provider"] not in OCR_PROVIDERS and cfg["ocr"]["provider"] not in _LEGACY:
        cfg["ocr"]["provider"] = "umi_ocr"
    if cfg["ocr"]["pdf_mode"] not in PDF_MODES:
        cfg["ocr"]["pdf_mode"] = "mineru"
    if cfg["asr"]["provider"] not in ASR_PROVIDERS and cfg["asr"]["provider"] not in _LEGACY:
        cfg["asr"]["provider"] = "paraformer"
    for part in ("ocr", "asr"):
        if cfg[part].get("source") not in ("nexent", "manual"):
            cfg[part]["source"] = "nexent"
    return cfg


def _clean_url(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    if u and not u.startswith(("http://", "https://")):
        u = "http://" + u
    return u


def save_settings(patch: dict) -> dict:
    with _lock:
        cfg = load_settings()
        for part in ("ocr", "asr"):
            p = patch.get(part) or {}
            for k, v in p.items():
                if v is None:
                    continue
                if k == "urls" and isinstance(v, dict):
                    for uk, uv in v.items():
                        if uk in cfg[part]["urls"] and uv:
                            cfg[part]["urls"][uk] = _clean_url(uv)
                elif k in cfg[part]:
                    cfg[part][k] = v
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_settings()


def builtin_available() -> dict:
    return {
        "ocr": importlib.util.find_spec("rapidocr_onnxruntime") is not None,
        "asr": importlib.util.find_spec("faster_whisper") is not None,
    }


def _name(kind: str, p: str) -> str:
    table = {"ocr": OCR_PROVIDERS, "asr": ASR_PROVIDERS}[kind]
    return table.get(p) or _LEGACY.get(p, p)


def ocr_label(cfg: dict | None = None) -> str:
    cfg = cfg or effective()
    p = cfg["ocr"]["provider"]
    if p == "builtin":
        return "PP-OCRv3 · ONNX Runtime · CPU · 离线"
    return f"{_name('ocr', p)}（{cfg['ocr']['urls'].get(p, '')}）"


def asr_label(cfg: dict | None = None) -> str:
    cfg = cfg or effective()
    p = cfg["asr"]["provider"]
    if p == "builtin":
        return "faster-whisper · CPU · 离线"
    return f"{_name('asr', p)}（{cfg['asr']['urls'].get(p, '')}）"


# ======================= 跟随 Nexent：读取租户文件解析配置 =======================

_NX_TTL = 30.0
_nx_cache: dict[str, Any] = {"at": 0.0, "value": None}
_NX_RETIRED = {"pdf": {"baidu_ocr", "tencent_ocr"}, "audio": {"tencent_asr", "volc_asr"}}


def _mask(v: str) -> str:
    return (v[:8] + "…") if v else ""


def nexent_config(force: bool = False) -> dict:
    """读取 Nexent 当前租户的识别服务配置并映射为 CTAS 结构；失败返回 ok=False 与原因。"""
    now = time.time()
    if not force and _nx_cache["value"] is not None and now - _nx_cache["at"] < _NX_TTL:
        return _nx_cache["value"]
    out: dict[str, Any] = {"ok": False, "error": "", "tenant": "", "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    dsn = os.getenv("NEXENT_DB_DSN", "").strip()
    try:
        if not dsn:
            raise RecognitionError("未配置 Nexent 只读数据库连接（NEXENT_DB_DSN），暂按手动配置运行")
        from app import nexent_client
        key = nexent_client.load_settings().get("api_key") or ""
        if not key:
            raise RecognitionError("尚未配置 Nexent API Key，无法确定租户，暂按手动配置运行")
        try:
            import psycopg  # type: ignore
        except ImportError:
            raise RecognitionError("本部署缺少 psycopg，无法读取 Nexent 配置")
        with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT nexent.ctas_tenant_of_key(%s)", (key,))
            tenant = (cur.fetchone() or [None])[0]
            if not tenant:
                raise RecognitionError("Nexent 中找不到该 API Key 对应的租户（Key 可能已撤销）")
            cur.execute("SELECT settings FROM nexent.document_parser_settings WHERE tenant_id=%s AND agent_id=0", (tenant,))
            row = cur.fetchone()
        saved = (row[0] if row else None) or {}
        if isinstance(saved, str):
            saved = json.loads(saved)
        pdf_p = saved.get("pdf_provider", "mineu")
        pdf_p = "mineu" if pdf_p in _NX_RETIRED["pdf"] else pdf_p
        audio_p = saved.get("audio_provider", "paraformer")
        audio_p = "paraformer" if audio_p in _NX_RETIRED["audio"] else audio_p
        urls = {k: (saved.get("local_" + k) or {}).get("url") or DEFAULT_URLS[k]
                for k in ("umi_ocr", "paddle_ocr", "paddle_layout")}
        urls["mineru"] = saved.get("url") or DEFAULT_URLS["mineru"]
        qwen = saved.get("local_qwen_asr") or {}
        out.update(ok=True, tenant=_mask(str(tenant)), raw={"pdf_provider": pdf_p, "audio_provider": audio_p},
                   ocr={"provider": "umi_ocr" if pdf_p == "mineu" else pdf_p,
                        "pdf_mode": "mineru" if pdf_p == "mineu" else "page_ocr", "urls": urls},
                   asr={"provider": audio_p if audio_p in ASR_PROVIDERS else "paraformer",
                        "urls": {"paraformer": (saved.get("asr") or {}).get("url") or DEFAULT_URLS["paraformer"],
                                 "qwen_asr": qwen.get("url") or DEFAULT_URLS["qwen_asr"]},
                        "qwen_model": qwen.get("model") or QWEN_MODEL})
        if out["ocr"]["provider"] not in OCR_PROVIDERS:
            out["ocr"]["provider"] = "umi_ocr"
    except Exception as e:
        out["error"] = str(e) if isinstance(e, RecognitionError) else f"读取 Nexent 配置失败：{type(e).__name__}"
    _nx_cache.update(at=now, value=out)
    return out


def effective() -> dict:
    """实际调用时生效的配置：跟随 Nexent 的部分取 Nexent 当前配置，读取失败回落到手动配置。"""
    cfg = copy.deepcopy(load_settings())
    need = any(cfg[p]["source"] == "nexent" for p in ("ocr", "asr"))
    nx = nexent_config() if need else None
    for part in ("ocr", "asr"):
        if cfg[part]["source"] == "nexent" and nx and nx.get("ok"):
            cfg[part].update(copy.deepcopy(nx[part]))
            cfg[part]["from"] = "nexent"
        else:
            cfg[part]["from"] = "manual" if cfg[part]["source"] == "manual" else "manual_fallback"
    return cfg


def public_settings(force_sync: bool = False) -> dict:
    manual = load_settings()
    nx = nexent_config(force=force_sync)
    eff = effective()
    return {
        **manual,
        "nexent": nx,
        "effective": eff,
        "dsn_configured": bool(os.getenv("NEXENT_DB_DSN", "").strip()),
        "choices": {"ocr": OCR_PROVIDERS, "pdf_mode": PDF_MODES, "asr": ASR_PROVIDERS},
        "labels": {"ocr": ocr_label(eff), "asr": asr_label(eff)},
    }


def _client(timeout: float) -> "httpx.Client":
    # 识别服务都在内网，不走系统代理
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), trust_env=False, follow_redirects=False)


class RecognitionError(RuntimeError):
    pass


# =============================== OCR ===============================

def _to_png_b64(data: bytes) -> tuple[str, int, int]:
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode(), im.size[0], im.size[1]


def _box4(poly: Any) -> list[list[int]] | None:
    try:
        pts = [[int(round(float(p[0]))), int(round(float(p[1])))] for p in poly]
    except Exception:
        return None
    if len(pts) == 4:
        return pts
    if len(pts) > 4:  # 多边形 → 外接矩形
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]]
    return None


def remote_ocr_image(data: bytes, cfg: dict | None = None) -> list[dict]:
    """调用外部 OCR 服务识别一张图片，返回 [{box:[[x,y]*4], text, score}]（原图坐标）。"""
    cfg = cfg or effective()
    provider = cfg["ocr"]["provider"]
    url = cfg["ocr"]["urls"].get(provider, "")
    b64, w, h = _to_png_b64(data)
    with _client(150) as c:
        if provider == "umi_ocr":
            r = c.post(url + "/api/ocr", json={"base64": b64, "options": {"data.format": "dict"}})
            r.raise_for_status()
            js = r.json()
            if js.get("code") == 101:  # 未识别到文字
                return []
            if js.get("code") != 100 or not isinstance(js.get("data"), list):
                raise RecognitionError(f"Umi-OCR 返回异常：{str(js.get('data'))[:120]}")
            out = []
            for it in js["data"]:
                box = _box4(it.get("box") or [])
                if box and str(it.get("text", "")).strip():
                    out.append({"box": box, "text": str(it["text"]), "score": round(float(it.get("score") or 0), 3)})
            return out
        if provider == "paddle_ocr":
            r = c.post(url + "/ocr", json={"file": b64, "fileType": 1, "visualize": False})
            r.raise_for_status()
            js = r.json()
            if js.get("errorCode") != 0:
                raise RecognitionError(f"PaddleOCR 返回异常：{js.get('errorMsg') or js.get('errorCode')}")
            pages = (js.get("result") or {}).get("ocrResults") or []
            if not pages:
                return []
            pr = pages[0].get("prunedResult") or {}
            texts = pr.get("rec_texts") or []
            scores = pr.get("rec_scores") or [0] * len(texts)
            polys = pr.get("rec_polys") or pr.get("dt_polys") or []
            boxes_xyxy = pr.get("rec_boxes") or []
            out = []
            for i, t in enumerate(texts):
                box = _box4(polys[i]) if i < len(polys) else None
                if box is None and i < len(boxes_xyxy):
                    x1, y1, x2, y2 = [int(v) for v in boxes_xyxy[i]]
                    box = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
                if str(t).strip():
                    out.append({"box": box or [[0, 0], [w, 0], [w, h], [0, h]], "text": str(t),
                                "score": round(float(scores[i] if i < len(scores) else 0), 3)})
            return out
        if provider == "paddle_layout":
            r = c.post(url + "/layout-parsing", json={"file": b64, "fileType": 1, "visualize": False})
            r.raise_for_status()
            js = r.json()
            if js.get("errorCode") != 0:
                raise RecognitionError(f"PP-StructureV3 返回异常：{js.get('errorMsg') or js.get('errorCode')}")
            pages = (js.get("result") or {}).get("layoutParsingResults") or []
            text = ((pages[0].get("markdown") or {}).get("text") if pages else "") or ""
            # 版面解析只返回正文，无逐行坐标：按行给整页框
            return [{"box": [[0, 0], [w, 0], [w, h], [0, h]], "text": ln, "score": 1.0}
                    for ln in text.splitlines() if ln.strip()]
    raise RecognitionError(f"未知的 OCR 服务：{provider}")


def mineru_pdf_pages(pdf_bytes: bytes, page_count: int, cfg: dict | None = None, deadline: int = 900) -> list[str]:
    """整份 PDF 交 MinerU 解析，返回逐页文本（协议同 Nexent 文件解析 · MinerU）。"""
    cfg = cfg or effective()
    url = cfg["ocr"]["urls"]["mineru"]
    form = {"backend": "pipeline", "lang_list": "ch", "parse_method": "auto",
            "table_enable": "true", "formula_enable": "false",
            "return_content_list": "true", "return_md": "false"}
    with _client(60) as c:
        r = c.post(url + "/tasks", data=form, files={"files": ("document.pdf", pdf_bytes, "application/pdf")})
        r.raise_for_status()
        task = r.json()["task_id"]
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            s = c.get(f"{url}/tasks/{task}")
            s.raise_for_status()
            status = s.json().get("status")
            if status == "completed":
                res = c.get(f"{url}/tasks/{task}/result")
                res.raise_for_status()
                blocks = res.json()["results"]["document"]["content_list"]
                if isinstance(blocks, str):
                    blocks = json.loads(blocks)
                pages: list[list[str]] = [[] for _ in range(page_count)]
                for b in blocks:
                    idx = b.get("page_idx")
                    if type(idx) is not int or not 0 <= idx < page_count:
                        continue
                    for key in ("table_caption", "table_body", "table_footnote", "text", "list_items",
                                "img_caption", "img_footnote", "code_body"):
                        v = b.get(key)
                        if isinstance(v, list):
                            v = "\n".join(x for x in v if isinstance(x, str))
                        if isinstance(v, str) and v.strip():
                            pages[idx].append(v.strip())
                return ["\n".join(p) for p in pages]
            if status in ("failed", "cancelled", "canceled"):
                raise RecognitionError("MinerU 任务失败")
            time.sleep(1)
    raise RecognitionError("MinerU 解析超时")


# =============================== ASR ===============================

def _segments_out(raw: list[dict], speakers: int) -> list[dict]:
    segs = [{"start": round(float(s.get("start", 0)), 2), "end": round(float(s.get("end", 0)), 2),
             "text": str(s.get("text", "")).strip()} for s in raw if str(s.get("text", "")).strip()]
    if speakers and speakers >= 2:
        from app.asr_worker import _diarize_pause
        return _diarize_pause(segs, speakers)
    return [{**s, "speaker": 0} for s in segs]


def remote_transcribe(data: bytes, filename: str, *, segments: bool, language: str,
                      speakers: int, timeout: int, cfg: dict | None = None) -> dict:
    """调用外部 ASR 服务，返回与本机引擎一致的结构：{text, duration_s, language, diarization, speakers, [segments]}。"""
    cfg = cfg or effective()
    provider = cfg["asr"]["provider"]
    url = cfg["asr"]["urls"].get(provider, "")
    name = os.path.basename(filename or "audio.webm") or "audio.webm"
    duration = 0.0
    raw: list[dict] = []
    text = ""
    with _client(max(60, timeout)) as c:
        if provider == "paraformer":
            r = c.post(url + "/v1/jobs", files={"file": (name, data, "application/octet-stream")})
            r.raise_for_status()
            job = r.json()["id"]
            end = time.monotonic() + timeout
            while True:
                s = c.get(f"{url}/v1/jobs/{job}")
                s.raise_for_status()
                js_job = s.json()
                st = js_job.get("status")
                if st in ("completed", "done"):
                    break
                if st == "failed":
                    raise RecognitionError(f"转写服务处理失败：{js_job.get('error') or '请核对音频或查看服务 worker.log'}")
                if st not in ("queued", "running"):
                    raise RecognitionError(f"转写服务返回未知状态：{st}")
                if time.monotonic() > end:
                    raise RecognitionError(f"转写超时（>{timeout}s）")
                time.sleep(0.8)
            res = c.get(f"{url}/v1/jobs/{job}/files/json")
            res.raise_for_status()
            js = res.json()
            duration = float(js.get("duration_seconds") or 0)
            raw = js.get("segments") or []
            text = js.get("text") or "".join(str(x.get("text", "")) for x in raw)
        elif provider == "qwen_asr":
            r = c.post(url + "/v1/audio/transcriptions",
                       data={"model": cfg["asr"]["qwen_model"], "response_format": "json"},
                       files={"file": (name, data, "application/octet-stream")})
            r.raise_for_status()
            js = r.json()
            text = str(js.get("text") or "")
            duration = float(js.get("duration") or 0)
            raw = js.get("segments") or ([{"start": 0, "end": duration, "text": text}] if text else [])
        else:
            raise RecognitionError(f"未知的语音识别服务：{provider}")
    try:
        from app.asr_engine import to_simplified
        text = to_simplified(text)
        raw = [{**s, "text": to_simplified(str(s.get("text", "")))} for s in raw]
    except Exception:
        pass
    out = {
        "text": text,
        "duration_s": round(duration or (float(raw[-1].get("end", 0)) if raw else 0), 1),
        "language": language or "zh",
        "diarization": "heuristic" if (segments and speakers >= 2) else "none",
        "speakers": max(1, speakers) if speakers >= 2 else 1,
        "engine": asr_label(cfg),
    }
    if segments:
        out["segments"] = _segments_out(raw, speakers)
    return out


# =============================== 测试 ===============================

def _sample_image() -> bytes:
    """生成一张含已知文字的测试图（纯 ASCII，容器内无中文字体也能渲染）。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (560, 140), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=56)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    d.text((24, 36), "CTAS OCR 2026", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_service(kind: str, provider: str, url: str = "", model: str = "") -> dict:
    """连通性 + 样例测试（不落盘）。kind: ocr / pdf / asr。"""
    cfg = load_settings()
    if not provider and kind in ("ocr", "asr"):
        # 未指定服务时测试「实际生效」的配置（跟随 Nexent 时即 Nexent 当前选择）
        eff = effective()
        cfg[kind].update(eff[kind])
        provider = eff[kind].get("provider", "")
    url = _clean_url(url) if url else ""
    t0 = time.time()
    try:
        if kind == "ocr":
            if provider == "builtin":
                if not builtin_available()["ocr"]:
                    return {"ok": False, "message": "本部署未安装内置 OCR 引擎（rapidocr-onnxruntime）"}
                from app.routers.ocr import _get_engine
                res, _ = _get_engine()(_sample_image())
                text = " ".join(str(r[1]) for r in (res or []))
            else:
                cfg["ocr"]["provider"] = provider
                if url:
                    cfg["ocr"]["urls"][provider] = url
                text = " ".join(i["text"] for i in remote_ocr_image(_sample_image(), cfg))
            ok = "2026" in text
            return {"ok": ok, "message": f"样例识别结果：{text or '（空）'}" + ("" if ok else "（未识别出预期文字）"),
                    "latency_ms": int((time.time() - t0) * 1000)}
        if kind == "pdf":
            u = url or cfg["ocr"]["urls"]["mineru"]
            with _client(8) as c:
                js = c.get(u + "/health").json()
            ok = js.get("status") == "healthy"
            return {"ok": ok, "message": f"MinerU {js.get('version', '')} · 状态 {js.get('status')} · 排队 {js.get('queued_tasks', 0)}",
                    "latency_ms": int((time.time() - t0) * 1000)}
        if kind == "asr":
            if provider == "builtin":
                ok = builtin_available()["asr"]
                return {"ok": ok, "message": "本机 faster-whisper 已安装" if ok else "本部署未安装 faster-whisper"}
            u = url or cfg["asr"]["urls"][provider]
            with _client(8) as c:
                if provider == "paraformer":
                    js = c.get(u + "/health").json()
                    ok = js.get("status") == "ready"
                    return {"ok": ok, "message": f"状态 {js.get('status')} · 模型 {'、'.join(js.get('models') or [])} · {js.get('runtime', '')}",
                            "latency_ms": int((time.time() - t0) * 1000)}
                js = c.get(u + "/v1/models").json()
                ids = [m.get("id") for m in js.get("data") or []]
                want = model or cfg["asr"]["qwen_model"]
                ok = want in ids
                return {"ok": ok, "message": f"可用模型：{'、'.join(ids) or '无'}" + ("" if ok else f"（未找到 {want}）"),
                        "latency_ms": int((time.time() - t0) * 1000)}
        return {"ok": False, "message": "未知的测试类型"}
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"无法连接服务：{type(e).__name__}（{url or '已保存地址'}）"}
    except Exception as e:
        return {"ok": False, "message": f"测试失败：{e}"}
