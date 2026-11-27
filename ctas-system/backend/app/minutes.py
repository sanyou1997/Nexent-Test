"""会议纪要生成：基于转录全文，调用本机/在线大模型产出结构化纪检会议纪要。

- 复用 app.llm.provider.chat_json（自动走离网本机 qwen2.5:3b 或在线云端）；
- 输出结构化 JSON（主题/时间地点/人员/议题/决议/待办/风险点）并渲染为 markdown；
- 小参数本地模型可能输出不稳定，chat_json 已做容错；本模块再兜底为纯文本纪要。
"""
from app.llm import provider

_MAX_CHARS = 8000  # 离线 3B 上下文有限，超长截断（保留尾部，避免截断标题）


def _build_markdown(d: dict) -> str:
    title = (d.get("title") or "未命名会议/谈话").strip() or "未命名会议/谈话"
    tp = (d.get("time_place") or "").strip()
    attendees = d.get("attendees") or []
    topics = d.get("topics") or []
    decisions = d.get("decisions") or []
    todos = d.get("todos") or []
    risks = d.get("risks") or []

    lines = [f"# 会议纪要：{title}", ""]
    if tp:
        lines.append(f"**时间地点**：{tp}")
    if attendees:
        lines.append(f"**参会/谈话人员**：{'、'.join(str(a) for a in attendees if a)}")
    lines.append("")

    if topics:
        lines.append("## 一、议题与要点")
        for i, t in enumerate(topics, 1):
            lines.append(f"{i}. {t}")
        lines.append("")

    if decisions:
        lines.append("## 二、决议事项")
        for i, t in enumerate(decisions, 1):
            lines.append(f"{i}. {t}")
        lines.append("")

    if todos:
        lines.append("## 三、待办任务")
        lines.append("| 任务 | 责任主体 | 时限 |")
        lines.append("| --- | --- | --- |")
        for t in todos:
            if isinstance(t, dict):
                lines.append(f"| {t.get('task','')} | {t.get('owner','')} | {t.get('deadline','')} |")
            else:
                lines.append(f"| {t} | | |")
        lines.append("")

    if risks:
        lines.append("## 四、风险预警点")
        for i, t in enumerate(risks, 1):
            lines.append(f"{i}. {t}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


_SYSTEM = (
    "你是一名纪检监察机关的会议纪要整理助手。请根据下面的谈话/会议转录文本，"
    "整理成结构化会议纪要。仅输出一个 JSON 对象，不要任何解释性文字，字段如下：\n"
    "{\n"
    '  "title": "会议/谈话主题",\n'
    '  "time_place": "时间地点（文本未提及则留空字符串）",\n'
    '  "attendees": ["参会/谈话人员姓名或职务"],\n'
    '  "topics": ["议题与要点（逐条）"],\n'
    '  "decisions": ["决议事项（逐条）"],\n'
    '  "todos": [{"task":"待办任务","owner":"责任主体","deadline":""}],\n'
    '  "risks": ["风险预警点：违纪违规线索、程序瑕疵、履职不力等纪检监察关注点"]\n'
    "}\n"
    "要求：客观准确，不编造未提及内容；人员与待办尽量从原文提取；"
    "risks 聚焦纪检监察关注点（违规吃喝、收送礼品礼金、利益输送、履职不力等）。"
)


def generate_minutes(text: str, lang: str = "zh", user_id=None) -> dict:
    """返回 {"ok","minutes","json","model","error"}。"""
    p = provider.active_profile("minutes")
    model_label = f"Nexent 智能体 · {p['agent'] or '未指定'}"
    if not text or not text.strip():
        return {"ok": False, "minutes": "", "json": {}, "model": model_label,
                "error": "转录文本为空，无法生成纪要"}
    snippet = text.strip()
    if len(snippet) > _MAX_CHARS:
        snippet = snippet[-_MAX_CHARS:]  # 保留尾部，避免截断开头主题

    user_prompt = (
        f"以下是转录文本（语种：{lang}）：\n\n{snippet}\n\n"
        "请按要求整理为结构化会议纪要 JSON。"
    )
    data = provider.chat_json(
        [{"role": "system", "content": _SYSTEM},
         {"role": "user", "content": user_prompt}],
        scene="minutes", user_id=user_id,
    )
    if data is None:
        return {"ok": False, "minutes": "", "json": {}, "model": model_label,
                "error": "纪要智能体当前不可用（请在「系统管理 → Nexent 接入」确认已接入并为「谈话纪要」场景指定智能体）"}

    if not isinstance(data, dict):
        data = {}
    minutes = _build_markdown(data)
    return {"ok": True, "minutes": minutes, "json": data, "model": model_label, "error": ""}
