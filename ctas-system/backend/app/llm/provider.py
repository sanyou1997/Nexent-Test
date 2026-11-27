"""大模型调用入口：全部交给 Nexent「场景智能体」。

平台内所有生成 / 抽取 / 审查类调用都经过这里：
- 调用方照旧传 scene 标签（qa / extract / document / verify / minutes …），
  这里映射到 6 个场景（见 nexent_client.SCENES），再找到系统管理里为该场景指定的 Nexent 智能体；
- 用哪个模型、挂哪些知识库与工具，全部在 Nexent 里按智能体配置——平台不做模型路由；
- 任一环节不可用（未配置 Key / 场景未指定智能体 / 智能体出错）都返回 None，调用方走既有降级路径
  （纯检索 / 规则抽取 / 模板填充），降级原因写入 llm_calls.note，做到能力诚实可见。
"""
from __future__ import annotations

import json
import re
import time

from app import nexent_client as nx
from app.database import SessionLocal
from app.models import LLMCall

_DEFAULT_TIMEOUT = {"extract": 180, "writing": 300, "review": 300, "law_qa": 300, "minutes": 300, "analysis": 600}


def llm_available(scene: str = "writing") -> bool:
    """Nexent 已接入、该场景已指定智能体即视为可用（实际成败以调用结果为准）。"""
    cfg = nx.load_settings()
    return nx.is_ready(cfg) and bool((cfg.get("scene_agents") or {}).get(nx.scene_of(scene) if scene not in nx.SCENES else scene))


def active_profile(scene: str = "writing") -> dict:
    cfg = nx.load_settings()
    key = nx.scene_of(scene) if scene not in nx.SCENES else scene
    return {"mode": "nexent", "scene": key, "agent": (cfg.get("scene_agents") or {}).get(key, ""),
            "ready": nx.is_ready(cfg)}


def _compose(messages: list[dict], json_mode: bool) -> str:
    """把 OpenAI 风格的 messages 合成为一条给智能体的任务消息。"""
    sys_parts = [m["content"] for m in messages if m.get("role") == "system" and m.get("content")]
    other = [m for m in messages if m.get("role") != "system"]
    lines = []
    if sys_parts:
        lines.append("【任务说明】\n" + "\n".join(sys_parts))
    if len(other) == 1:
        lines.append("【输入】\n" + (other[0].get("content") or ""))
    else:
        for m in other:
            role = "用户" if m.get("role") == "user" else "助手"
            lines.append(f"【{role}】\n{m.get('content') or ''}")
    if json_mode:
        lines.append("【输出要求】只输出一个合法的 JSON 对象，不要代码块，不要任何解释文字。")
    return "\n\n".join(lines)


def _record(user_id, scene, agent, ok, t0, conversation_id=None, note=""):
    try:
        db = SessionLocal()
        try:
            db.add(LLMCall(user_id=user_id, scene=scene, provider="nexent", model=(agent or "")[:50],
                           ok=ok, latency_ms=int((time.time() - t0) * 1000),
                           conversation_id=conversation_id, note=(note or "")[:200]))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass  # 审计记录尽力而为，绝不影响业务调用


def chat(messages: list[dict], json_mode: bool = False, scene: str = "qa",
         user_id=None, timeout: int | None = None) -> str | None:
    """返回最终答案文本；None 表示不可用或失败，调用方走降级路径。"""
    t0 = time.time()
    key = nx.scene_of(scene) if scene not in nx.SCENES else scene
    cfg = nx.load_settings()
    agent = (cfg.get("scene_agents") or {}).get(key, "")
    if not nx.is_ready(cfg):
        _record(user_id, scene, agent, False, t0, note="Nexent 未接入或已停用")
        return None
    if not agent:
        _record(user_id, scene, "", False, t0, note=f"场景「{nx.SCENES[key][0]}」未指定智能体")
        return None
    r = nx.run_agent(cfg, agent, _compose(messages, json_mode),
                     timeout=timeout or _DEFAULT_TIMEOUT.get(key, 300),
                     meta={"scene": scene, "user_id": user_id})
    ok = bool(r["text"]) and not r["error"]
    _record(user_id, scene, agent, ok, t0, r.get("conversation_id"), r.get("error") or "")
    # Nexent 检索类工具会在答案里插入 [[a1]] 形式的引用标记（前端会话里渲染为角标），
    # 平台把答案写进文书 / 摘要等正文时去掉，避免残留符号。
    return _CITE.sub("", r["text"]).strip() if ok else None


_CITE = re.compile(r"\[\[[a-zA-Z]?\d+\]\]")


_FENCE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```")


def chat_json(messages: list[dict], scene: str = "extract", user_id=None, timeout: int | None = None) -> dict | None:
    text = chat(messages, json_mode=True, scene=scene, user_id=user_id, timeout=timeout)
    if text is None:
        return None
    m = _FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    s, e = text.find("{"), text.rfind("}")
    if 0 <= s < e:
        try:
            return json.loads(text[s:e + 1])
        except json.JSONDecodeError:
            return None
    return None
