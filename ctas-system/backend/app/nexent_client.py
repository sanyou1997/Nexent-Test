"""Nexent 北向接口接入层（CTAS 作为业务系统，调用 Nexent 已发布的智能体与会话能力）。

接入方式：Nexent 北向 API（/nb/v1/*），Bearer API Key 鉴权。
- 全系统共用一个 Key（管理员在「系统管理 → Nexent 接入」配置），Nexent 侧会话都记在该 Key 对应的账号下；
- 会话归属由 CTAS 自己的 nexent_conversations 表登记，保证每个 CTAS 用户只看得到自己的会话。

配置持久化在 BASE_DIR/data/nexent_settings.json（data/ 不进版本库，Key 不会被提交）；
未配置时回落到环境变量 NEXENT_BASE_URL / NEXENT_API_KEY / NEXENT_DEFAULT_AGENT。
Key 只在服务端使用，任何接口都只返回脱敏后的尾号，不回传明文。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

try:  # openai 3.x 依赖的是 httpx2（API 与 httpx 一致）；两者择一可用即可
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    import httpx2 as httpx  # type: ignore

from app.config import BASE_DIR

SETTINGS_FILE = Path(os.getenv("NEXENT_SETTINGS_FILE") or (BASE_DIR / "data" / "nexent_settings.json"))
DEFAULT_BASE_URL = "http://127.0.0.1:5013"

_lock = threading.Lock()
_status_cache: dict[str, Any] = {"at": 0.0, "value": None}
_STATUS_TTL = 15.0


# ----------------------------- 场景智能体 -----------------------------
# 平台所有大模型调用按「场景」交给一个 Nexent 智能体处理；用哪个模型、挂哪些知识库与工具都在 Nexent 里配，
# 平台不做模型路由。key = 场景，value = (名称, 说明, 默认智能体名)
SCENES: dict[str, tuple[str, str, str]] = {
    "extract": ("要素抽取", "信访要素、报告字段补空、流水表头映射等结构化抽取（JSON）", "ctas_extract"),
    "writing": ("文书起草与润色", "立案决定书、审理报告、申诉决定书、廉政回复、模板文书的起草润色", "ctas_writer"),
    # 文书审查只看文本本身（条款引用已由平台法规库确定性核对），用不带工具的写作智能体，响应快
    "review": ("文书审查", "文书连贯性、逻辑矛盾、前后表述一致性复核", "ctas_writer"),
    "law_qa": ("法规问答", "基于本地法规库的党纪法规问答（逐条出处）", "ctas_law_qa"),
    "minutes": ("谈话纪要", "谈话 / 会议录音转写后的结构化纪要", "ctas_writer"),
    "analysis": ("研判分析", "线索研判、类案比对、审理协审等需要推理的分析", "ctas_analyst"),
}
# 代码里各调用点的 scene 标签 → 场景
SCENE_ALIASES = {
    "extract": "extract", "petition_summary": "extract", "bank": "extract",
    "document": "writing", "assistant": "writing", "education": "writing", "mode_test": "writing",
    "verify": "review", "qa": "law_qa", "minutes": "minutes", "analyze": "analysis", "analysis": "analysis",
}


def scene_of(tag: str) -> str:
    return SCENE_ALIASES.get(tag or "", "writing")


# ----------------------------- 配置 -----------------------------

def _env_defaults() -> dict:
    return {
        "enabled": True,
        "base_url": os.getenv("NEXENT_BASE_URL", DEFAULT_BASE_URL),
        "api_key": os.getenv("NEXENT_API_KEY", ""),
        "default_agent": os.getenv("NEXENT_DEFAULT_AGENT", ""),
        "attach_context_default": True,
        "timeout": int(os.getenv("NEXENT_TIMEOUT", "600") or 600),
        "scene_agents": {k: v[2] for k, v in SCENES.items()},
    }


def load_settings() -> dict:
    cfg = _env_defaults()
    try:
        if SETTINGS_FILE.exists():
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                sa = saved.pop("scene_agents", None)
                cfg.update({k: v for k, v in saved.items() if k in cfg})
                if isinstance(sa, dict):
                    cfg["scene_agents"].update({k: v for k, v in sa.items() if k in SCENES and v is not None})
    except Exception:
        pass
    return cfg


def save_settings(patch: dict) -> dict:
    with _lock:
        cfg = load_settings()
        for k in ("enabled", "base_url", "default_agent", "attach_context_default", "timeout"):
            if k in patch and patch[k] is not None:
                cfg[k] = patch[k]
        if isinstance(patch.get("scene_agents"), dict):
            cfg["scene_agents"].update({k: (v or "") for k, v in patch["scene_agents"].items() if k in SCENES})
        # Key：传非空才更新；clear_api_key=True 显式清空
        if patch.get("clear_api_key"):
            cfg["api_key"] = ""
        elif patch.get("api_key"):
            cfg["api_key"] = str(patch["api_key"]).strip()
        cfg["base_url"] = (cfg.get("base_url") or DEFAULT_BASE_URL).strip()
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    invalidate_status()
    return cfg


def mask_key(key: str) -> str:
    key = key or ""
    if not key:
        return ""
    return "••••" + key[-4:] if len(key) > 4 else "••••"


def public_settings(cfg: dict | None = None) -> dict:
    """给前端看的配置（Key 脱敏）。"""
    cfg = cfg or load_settings()
    return {
        "enabled": bool(cfg.get("enabled")),
        "base_url": cfg.get("base_url") or "",
        "api_root": api_root(cfg),
        "has_api_key": bool(cfg.get("api_key")),
        "api_key_masked": mask_key(cfg.get("api_key", "")),
        "default_agent": cfg.get("default_agent") or "",
        "attach_context_default": bool(cfg.get("attach_context_default", True)),
        "timeout": int(cfg.get("timeout") or 600),
        "scene_agents": cfg.get("scene_agents") or {},
        "scenes": [{"key": k, "name": v[0], "desc": v[1], "default": v[2]} for k, v in SCENES.items()],
    }


def api_root(cfg: dict) -> str:
    """规范化北向接口根地址：允许填 http://host:5013、http://host:3000/api 或完整 …/nb/v1。"""
    base = (cfg.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "http://" + base
    return base if base.endswith("/nb/v1") else base + "/nb/v1"


def is_ready(cfg: dict) -> bool:
    return bool(cfg.get("enabled") and cfg.get("api_key") and cfg.get("base_url"))


def auth_headers(cfg: dict, extra: dict | None = None) -> dict:
    h = {"Authorization": f"Bearer {cfg.get('api_key', '')}"}
    if extra:
        h.update(extra)
    return h


def invalidate_status() -> None:
    _status_cache["at"] = 0.0
    _status_cache["value"] = None


class NexentError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _explain(resp) -> str:
    try:
        detail = resp.json().get("detail")
    except Exception:
        detail = (resp.text or "")[:200]
    if resp.status_code == 401:
        return "Nexent 拒绝了 API Key（401），请检查 Key 是否有效或已被撤销"
    if resp.status_code == 403:
        return f"Nexent 拒绝访问（403）：{detail or '无权限'}"
    if resp.status_code == 404:
        return f"Nexent 未找到资源（404）：{detail or ''}"
    if resp.status_code == 429:
        return "Nexent 请求过于频繁（429），请稍后再试"
    return f"Nexent 返回 {resp.status_code}：{detail or '未知错误'}"


# ----------------------------- 同步调用 -----------------------------

def request(cfg: dict, method: str, path: str, *, timeout: float = 20.0, **kw) -> Any:
    """调用北向接口（非流式），返回 JSON；失败抛 NexentError。"""
    url = api_root(cfg) + path
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as c:
            resp = c.request(method, url, headers=auth_headers(cfg, kw.pop("headers", None)), **kw)
    except httpx.TimeoutException:
        raise NexentError("连接 Nexent 超时，请确认服务地址与网络", 504)
    except httpx.HTTPError as e:
        raise NexentError(f"无法连接 Nexent（{api_root(cfg)}）：{type(e).__name__}", 502)
    if resp.status_code >= 400:
        raise NexentError(_explain(resp), 502 if resp.status_code >= 500 else resp.status_code)
    try:
        return resp.json()
    except Exception:
        return {}


def health(cfg: dict) -> bool:
    try:
        with httpx.Client(timeout=5.0, trust_env=False) as c:
            r = c.get(api_root(cfg) + "/health")
        return r.status_code == 200
    except Exception:
        return False


def normalize_agent(a: dict) -> dict:
    name = a.get("name") or ""
    return {
        "name": name,
        "display_name": a.get("display_name") or name,
        "description": a.get("description") or a.get("business_description") or "",
        "greeting": a.get("greeting_message") or "",
        "author": a.get("author") or "",
        "version_name": a.get("version_name") or "",
        "version_no": a.get("current_version_no"),
        "available": a.get("is_available", True) is not False,
        "unavailable_reasons": a.get("unavailable_reasons") or [],
        "tool_count": len(a.get("tools") or []),
    }


def list_agents(cfg: dict) -> list[dict]:
    data = request(cfg, "GET", "/agents")
    items = data.get("data") if isinstance(data, dict) else data
    agents = [normalize_agent(a) for a in (items or []) if isinstance(a, dict) and a.get("name")]
    # 默认智能体置顶，其余按显示名排序
    default = (cfg.get("default_agent") or "").strip()
    agents.sort(key=lambda a: (a["name"] != default, a["display_name"]))
    return agents


def status(force: bool = False) -> dict:
    """连接状态（顶栏状态灯用），带 15 秒缓存，避免每次刷新页面都打 Nexent。"""
    now = time.time()
    if not force and _status_cache["value"] is not None and now - _status_cache["at"] < _STATUS_TTL:
        return _status_cache["value"]
    cfg = load_settings()
    out = {
        "enabled": bool(cfg.get("enabled")),
        "configured": bool(cfg.get("api_key")),
        "reachable": False,
        "authorized": False,
        "agent_count": 0,
        "default_agent": cfg.get("default_agent") or "",
        "attach_context_default": bool(cfg.get("attach_context_default", True)),
        "error": "",
    }
    if not cfg.get("enabled"):
        out["error"] = "管理员已停用 Nexent 接入"
    elif not cfg.get("api_key"):
        out["reachable"] = health(cfg)
        out["error"] = "尚未配置 Nexent API Key"
    else:
        out["reachable"] = health(cfg)
        if not out["reachable"]:
            out["error"] = f"无法连接 Nexent 北向接口（{api_root(cfg)}）"
        else:
            try:
                out["agent_count"] = len(list_agents(cfg))
                out["authorized"] = True
            except NexentError as e:
                out["error"] = str(e)
    _status_cache.update(at=now, value=out)
    return out


def async_client(cfg: dict):
    t = float(cfg.get("timeout") or 600)
    # trust_env=False：Nexent 是内网服务，不走系统代理（本机若设了 HTTP_PROXY 会把 127.0.0.1 也转发出去）
    return httpx.AsyncClient(timeout=httpx.Timeout(t, connect=10.0), trust_env=False)


# ----------------------------- 同步调用智能体（场景智能体用） -----------------------------

_CITE_MARK = re.compile(r"\[\[[a-zA-Z]?\d+\]\]")


def strip_citations(text: str) -> str:
    """去掉 Nexent 知识库检索留在答案里的引用标记（如 [[a5]]）：平台不渲染引用卡片，留着会原样显示、写进文书。"""
    return _CITE_MARK.sub("", text or "")


def run_agent(cfg: dict, agent_name: str, query: str, *, timeout: float = 300, meta: dict | None = None) -> dict:
    """同步调用一个已发布智能体并取得最终答案。

    返回 {"text", "conversation_id", "error"}；只取主智能体的最终答案（subagent_start 声明的子智能体不计入），
    流里拿不到时以 Nexent 已持久化的会话记录兜底。
    """
    out: dict[str, Any] = {"text": "", "conversation_id": None, "error": ""}
    payload = {"agent_name": agent_name, "query": query, "final_answer_mode": "complete",
               "meta_data": {"source": "ctas", **(meta or {})}}
    sub_ids: set[str] = set()
    answer = ""
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), trust_env=False) as c:
            with c.stream("POST", api_root(cfg) + "/chat/run", json=payload,
                          headers=auth_headers(cfg, {"Content-Type": "application/json"})) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    out["error"] = _explain(resp)
                    return out
                buf = ""
                for chunk in resp.iter_text():
                    buf += chunk.replace("\r\n", "\n")
                    while "\n\n" in buf:
                        raw, buf = buf.split("\n\n", 1)
                        data = "\n".join(ln[5:].lstrip() for ln in raw.splitlines() if ln.startswith("data:"))
                        if not data:
                            continue
                        try:
                            ev = json.loads(data)
                        except Exception:
                            continue
                        et = ev.get("type")
                        if et == "conversation_created":
                            try:
                                out["conversation_id"] = int((ev.get("content") or {}).get("conversation_id"))
                            except Exception:
                                pass
                        elif et == "subagent_start":
                            inv = ev.get("invocation_id")
                            if not inv:
                                try:
                                    inv = json.loads(ev.get("content") or "{}").get("invocation_id")
                                except Exception:
                                    inv = None
                            if inv:
                                sub_ids.add(inv)
                        elif et == "final_answer" and ev.get("invocation_id") not in sub_ids:
                            t = ev.get("content")
                            if isinstance(t, str) and t:
                                answer = t if ev.get("answer_phase") in (None, "complete") else answer + t
                        elif et == "error":
                            out["error"] = str(ev.get("content") or "智能体运行出错")
    except httpx.TimeoutException:
        out["error"] = f"智能体响应超时（>{int(timeout)}s）"
    except httpx.HTTPError as e:
        out["error"] = f"无法连接 Nexent：{type(e).__name__}"
    if not answer.strip() and out["conversation_id"] and not out["error"]:
        try:
            hist = request(cfg, "GET", f"/conversations/{out['conversation_id']}")
            msgs = [m for m in ((hist.get("data") or {}).get("history") or []) if m.get("role") == "assistant"]
            if msgs and isinstance(msgs[-1].get("content"), str):
                answer = msgs[-1]["content"]
        except Exception:
            pass
    out["text"] = strip_citations(answer).strip()
    if not out["text"] and not out["error"]:
        out["error"] = "智能体没有返回内容"
    return out
