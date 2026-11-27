"""Nexent 智能体接入：设置、状态、已发布智能体、流式会话代理、会话台账、存为文书草稿。

所有对 Nexent 的调用都经 CTAS 后端代理：浏览器拿不到 API Key；
会话归属由 nexent_conversations 表登记，续聊 / 查看 / 停止前都校验「是不是本人发起的」。
在案件工作台内发起的首轮对话，可附带案件上下文（基本情况、阅卷摘要、证据与程序校验结论、量纪说理），
附带与否由用户在界面上明确勾选，并写入审计日志。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit
from app import nexent_client as nx
from app.database import SessionLocal, get_db
from app.deps import get_current_user, require_roles
from app.models import Case, Document, DocumentVersion, NexentConversation, Petition, Trial, User

router = APIRouter(prefix="/api/nexent", tags=["nexent"])

CONTEXT_MARK = "【审理人员提问】"


# ----------------------------- 设置与状态 -----------------------------

class SettingsIn(BaseModel):
    enabled: Optional[bool] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    clear_api_key: bool = False
    default_agent: Optional[str] = None
    attach_context_default: Optional[bool] = None
    timeout: Optional[int] = None
    scene_agents: Optional[dict[str, str]] = None


@router.get("/settings")
def get_settings(user: User = Depends(require_roles("admin"))):
    return nx.public_settings()


@router.put("/settings")
def put_settings(body: SettingsIn, user: User = Depends(require_roles("admin")),
                 db: Session = Depends(get_db)):
    if body.timeout is not None and not (30 <= body.timeout <= 3600):
        raise HTTPException(400, "超时时间须在 30–3600 秒之间")
    if body.base_url is not None and not body.base_url.strip():
        raise HTTPException(400, "服务地址不能为空")
    patch = body.model_dump()
    cfg = nx.save_settings(patch)
    audit.write_log(db, user.id, user.username, "nexent_settings", "nexent", "settings", {
        "base_url": cfg.get("base_url"), "enabled": cfg.get("enabled"),
        "default_agent": cfg.get("default_agent"),
        "scene_agents": cfg.get("scene_agents"),
        "api_key_changed": bool(body.api_key) or body.clear_api_key,
    })
    return nx.public_settings(cfg)


class TestIn(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None


@router.post("/settings/test")
def test_settings(body: TestIn, user: User = Depends(require_roles("admin"))):
    """用表单里尚未保存的值（或已保存的值）测试连通性，不落盘。"""
    cfg = nx.load_settings()
    if body.base_url:
        cfg["base_url"] = body.base_url
    if body.api_key:
        cfg["api_key"] = body.api_key.strip()
    out: dict[str, Any] = {"api_root": nx.api_root(cfg), "reachable": nx.health(cfg),
                           "authorized": False, "agents": [], "error": ""}
    if not out["reachable"]:
        out["error"] = "无法访问北向接口 /nb/v1/health，请检查服务地址与网络"
        return out
    if not cfg.get("api_key"):
        out["error"] = "服务可达；尚未填写 API Key，无法列出智能体"
        return out
    try:
        out["agents"] = nx.list_agents(cfg)
        out["authorized"] = True
    except nx.NexentError as e:
        out["error"] = str(e)
    return out


@router.get("/status")
def get_status(refresh: bool = False, user: User = Depends(get_current_user)):
    # 场景 → 智能体映射不含敏感信息，随状态下发，业务角色（非管理员）也能按场景选默认智能体
    return {**nx.status(force=refresh), "scene_agents": nx.load_settings().get("scene_agents") or {}}


def _ready_cfg() -> dict:
    cfg = nx.load_settings()
    if not cfg.get("enabled"):
        raise HTTPException(503, "管理员已停用 Nexent 接入")
    if not cfg.get("api_key"):
        raise HTTPException(503, "尚未配置 Nexent API Key，请管理员在「系统管理 → Nexent 接入」中配置")
    return cfg


@router.get("/agents")
def get_agents(user: User = Depends(get_current_user)):
    cfg = _ready_cfg()
    try:
        return {"items": nx.list_agents(cfg), "default_agent": cfg.get("default_agent") or ""}
    except nx.NexentError as e:
        raise HTTPException(e.status if e.status in (401, 403, 404, 429, 504) else 502, str(e))


# ----------------------------- 案件上下文 -----------------------------

def _verdict_line(label: str, payload: Any) -> str:
    if not payload:
        return ""
    data = payload if isinstance(payload, dict) else {"checks": payload}
    verdict = data.get("verdict") or ""
    problems = []
    for c in data.get("checks") or []:
        if isinstance(c, dict) and c.get("level") in ("error", "warn"):
            name = c.get("point") or c.get("item") or ""
            desc = "，".join(x for x in (c.get("status"), c.get("note")) if x)
            problems.append(f"{name}（{desc}）" if desc else name)
    for g in data.get("gaps") or []:
        problems.append(str(g))
    if not verdict and not problems:
        return ""
    s = f"{label}：{verdict or '已执行'}"
    if problems:
        s += "；待补强/问题：" + "；".join(p for p in problems[:8] if p)
    return s


def build_trial_context(db: Session, trial_id: int) -> dict:
    t = db.get(Trial, trial_id)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    c = db.get(Case, t.case_id) if t.case_id else None
    subject = t.subject or (c.subject if c else "")
    title = t.title or (f"{subject}涉嫌违纪案审理" if subject else "审理事项")
    facts = (t.brief or (c.content if c else "") or "").strip()
    lines = [
        "【案件上下文（由 CTAS 审理辅助系统提供，仅供本次审理辅助使用）】",
        f"审理事项：{title}",
        f"案号：{c.code if c else '（独立建档）'}；当前状态：{t.status or '—'}",
        f"被审查调查人：{subject or '—'}；单位职务：{t.unit or (c.unit if c else '') or '—'}；"
        f"政治面貌：{t.political_status or '—'}；问题性质：{t.problem_type or (c.problem_type if c else '') or '—'}",
    ]
    if t.involved_amount:
        lines.append(f"涉案金额：{t.involved_amount}")
    if facts:
        lines.append("主要问题（登记材料）：" + facts[:1500])
    if t.summary:
        lines.append("阅卷摘要：" + t.summary.strip()[:1500])
    for label, payload in (("证据链校验", t.evidence_check), ("程序合规检查", t.procedure_check)):
        v = _verdict_line(label, payload)
        if v:
            lines.append(v)
    reasoning_text = ""
    try:
        from app.sentencing import build_sentencing_reasoning
        r = build_sentencing_reasoning(t, c, db)
        reasoning_text = (r or {}).get("text") or ""
    except Exception:
        reasoning_text = ""
    if reasoning_text:
        lines.append("量纪说理（CTAS 离线确定性引擎生成，处分档次须经审理组集体审议）：\n" + reasoning_text.strip()[:2500])
    lines.append("说明：以上为系统内已登记信息，事实认定与处理意见以审理组审定为准；如信息不足请明确指出需补充的材料，不要臆测。")
    text = "\n".join(lines)
    return {"trial_id": t.id, "title": title, "subject": subject, "text": text, "chars": len(text)}


def _fmt_day(dt) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "—"


def build_case_context(db: Session, case_id: int) -> dict:
    """案管线索 / 案件：基本情况 + 处置与时限 + 关联信访 + 线索研判画像（规则引擎，离线可审）。"""
    from app.routers.clue import _case_clue_out, _score_case
    c = db.get(Case, case_id)
    if not c:
        raise HTTPException(404, "案件 / 线索不存在")
    title = f"{c.code} {c.subject or ''}{('：' + c.title) if c.title else ''}".strip()
    lines = [
        "【线索 / 案件上下文（由纪检监察业务平台提供，仅供本次研判辅助使用）】",
        f"案号：{c.code}；来源：{c.source or '—'}；状态：{c.status or '—'}；紧急程度：{c.urgency or '—'}",
        f"被反映人：{c.subject or '—'}；单位职务：{c.unit or '—'}；问题类型：{c.problem_type or '—'}",
        f"案由：{c.title or '—'}；立案：{_fmt_day(c.opened_at)}；审查调查时限：{_fmt_day(c.due_at)}",
    ]
    if c.content:
        lines.append("反映内容 / 主要问题：" + c.content.strip()[:1800])
    if c.disposition:
        lines.append(f"处置方式：{c.disposition}；处置依据：{(c.disposition_note or '—')[:400]}")
    if c.meeting_note:
        lines.append("会议结论：" + c.meeting_note.strip()[:400])
    if c.liuzhi_status:
        lines.append(f"留置：{c.liuzhi_status}（{_fmt_day(c.liuzhi_start)} 至 {_fmt_day(c.liuzhi_end)}）")
    if c.linked_petition_id:
        p = db.get(Petition, c.linked_petition_id)
        if p:
            lines.append(f"关联信访件 {p.code}（{p.channel}，{p.status}）：{(p.summary or p.content or '')[:400]}")
    try:
        prof = _case_clue_out(db, c, *_score_case(db, c))
        lines.append(f"线索研判画像（规则引擎）：风险分 {prof['risk_score']}/100；标签：{'、'.join(prof['tags']) or '无'}；"
                     f"依据：{'；'.join(prof['reasons']) or '无'}；建议：{prof['suggestion']}")
        if prof["related"]:
            lines.append("同人关联记录：" + "；".join(
                f"{r['type']} {r.get('code') or r.get('title') or ''}" for r in prof["related"][:8]))
    except Exception:
        pass
    if c.trial_id:
        t = db.get(Trial, c.trial_id)
        if t:
            lines.append(f"已移送审理：审理事项 trial_id={t.id}（{t.status}），可用平台工具 get_trial_context 查看审理情况")
    lines.append("说明：以上为系统内已登记信息与规则引擎结果，处置意见须经集体研究决定；信息不足请明确列出需补充核实的事项，不要臆测。")
    text = "\n".join(lines)
    return {"kind": "case", "id": c.id, "title": title[:80], "subject": c.subject, "text": text, "chars": len(text)}


def build_petition_context(db: Session, pid: int) -> dict:
    """信访件：反映内容 + 已抽取要素 / 分类 + 受理办理状态与时限 + 同一被反映人的其它记录。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "信访件不存在")
    el = p.elements if isinstance(p.elements, dict) else {}
    subject = str(el.get("被反映人") or el.get("subject") or "")
    title = f"信访件 {p.code}" + (f"（被反映人：{subject}）" if subject else "")
    lines = [
        "【信访件上下文（由纪检监察业务平台提供，仅供本次研判辅助使用）】",
        f"编号：{p.code}；渠道：{p.channel or '—'}；业务类别：{p.biz_type or '—'}；状态：{p.status or '—'}；紧急程度：{p.urgency or '—'}",
        f"举报人类型：{p.petitioner_type or ('匿名' if p.petitioner == '匿名' else '—')}；"
        f"受理结论：{p.accept_decision or '未甄别'}；办理期限：{_fmt_day(p.due_at)}；分办去向：{p.transfer_to or '—'}",
        "反映内容：" + (p.content or "").strip()[:2500],
    ]
    if p.summary:
        lines.append("摘要：" + p.summary.strip()[:600])
    if el:
        lines.append("已抽取要素：" + "；".join(f"{k}：{v}" for k, v in el.items() if v not in (None, "", [], {}))[:800])
    if p.labels:
        lines.append("分类标签：" + "、".join(map(str, p.labels)))
    if p.duplicate_of:
        d = db.get(Petition, p.duplicate_of)
        lines.append(f"疑似重复件：{d.code if d else p.duplicate_of}")
    if p.source_case_id:
        c = db.get(Case, p.source_case_id)
        if c:
            lines.append(f"已转案管线索：{c.code}（{c.status}）")
    if subject:
        others = db.query(Case).filter(Case.subject == subject).limit(5).all()
        if others:
            lines.append("同一被反映人的案管记录：" + "；".join(f"{c.code} {c.title}（{c.status}）" for c in others))
    lines.append("说明：以上为登记材料，反映内容未经核实；请区分「材料反映」与「已查实」，信息不足时列出需补充核实的事项。")
    text = "\n".join(lines)
    return {"kind": "petition", "id": p.id, "title": title[:80], "subject": subject, "text": text, "chars": len(text)}


SUBJECT_KINDS = {"trial": "审理事项", "case": "案件 / 线索", "petition": "信访件"}


def build_context(db: Session, kind: str, sid: int) -> dict:
    if kind == "trial":
        ctx = build_trial_context(db, sid)
        return {**ctx, "kind": "trial", "id": sid}
    if kind == "case":
        return build_case_context(db, sid)
    if kind == "petition":
        return build_petition_context(db, sid)
    raise HTTPException(400, f"不支持的业务对象类型：{kind}")


@router.get("/trial-context/{trial_id}")
def preview_trial_context(trial_id: int, user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """给前端预览「将随首轮提问发送给 Nexent 的案件上下文」，做到发送内容可见。"""
    return build_trial_context(db, trial_id)


@router.get("/context/{kind}/{sid}")
def preview_context(kind: str, sid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """预览任一业务对象（审理事项 / 案件线索 / 信访件）将随首轮提问发送的上下文。"""
    return build_context(db, kind, sid)


# ----------------------------- 会话台账 -----------------------------

def _local(dt) -> str:
    """库内按 UTC 存，展示转服务器本地时区。"""
    if not dt:
        return ""
    return dt.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def _conv_out(c: NexentConversation) -> dict:
    return {
        "id": c.id, "conversation_id": c.nexent_conversation_id,
        "agent_name": c.agent_name, "agent_display_name": c.agent_display_name or c.agent_name,
        "trial_id": c.trial_id, "subject_kind": c.subject_kind or ("trial" if c.trial_id else ""),
        "subject_id": c.subject_id or c.trial_id, "title": c.title, "with_context": bool(c.with_context),
        "last_answer": (c.last_answer or "")[:160],
        "created_at": _local(c.created_at),
        "updated_at": _local(c.updated_at),
    }


def _own_conversation(db: Session, user: User, cid: int) -> NexentConversation:
    row = (db.query(NexentConversation)
           .filter(NexentConversation.nexent_conversation_id == cid,
                   NexentConversation.user_id == user.id,
                   NexentConversation.hidden == False)  # noqa: E712
           .first())
    if not row:
        raise HTTPException(404, "会话不存在或不属于当前用户")
    return row


@router.get("/conversations")
def list_conversations(trial_id: Optional[int] = None, subject_kind: str = "", subject_id: Optional[int] = None,
                       limit: int = 50, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(NexentConversation).filter(NexentConversation.user_id == user.id,
                                            NexentConversation.hidden == False)  # noqa: E712
    if trial_id:
        q = q.filter(NexentConversation.trial_id == trial_id)
    elif subject_kind and subject_id:
        if subject_kind == "trial":
            q = q.filter(NexentConversation.trial_id == subject_id)
        else:
            q = q.filter(NexentConversation.subject_kind == subject_kind, NexentConversation.subject_id == subject_id)
    rows = q.order_by(NexentConversation.updated_at.desc()).limit(max(1, min(limit, 200))).all()
    return {"items": [_conv_out(r) for r in rows]}


def _split_context(text: str) -> tuple[str, bool]:
    if CONTEXT_MARK in (text or ""):
        return text.split(CONTEXT_MARK, 1)[1].strip(), True
    return text or "", False


def _assistant_text(content: Any) -> str:
    """Nexent 历史里助手消息可能是纯文本，也可能是消息单元 JSON；尽量取出最终答案。"""
    if isinstance(content, str):
        s = content.strip()
        if s.startswith("[") or s.startswith("{"):
            try:
                return _assistant_text(json.loads(s))
            except Exception:
                return content
        return content
    if isinstance(content, list):
        finals = [u.get("content", "") for u in content
                  if isinstance(u, dict) and u.get("type") == "final_answer"]
        if finals:
            return "".join(str(x) for x in finals)
        return "\n".join(str(u.get("content", "")) for u in content if isinstance(u, dict))
    if isinstance(content, dict):
        return str(content.get("content") or content.get("final_answer") or "")
    return str(content or "")


@router.get("/conversations/{cid}")
def get_conversation(cid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _own_conversation(db, user, cid)
    cfg = _ready_cfg()
    try:
        data = nx.request(cfg, "GET", f"/conversations/{cid}")
    except nx.NexentError as e:
        raise HTTPException(502, str(e))
    payload = data.get("data") if isinstance(data, dict) else {}
    messages = []
    for m in (payload or {}).get("history") or []:
        role = m.get("role") or ""
        if role == "user":
            text, had_ctx = _split_context(m.get("content") or "")
            messages.append({"role": "user", "content": text, "with_context": had_ctx,
                             "files": m.get("minio_files") or []})
        else:
            messages.append({"role": "assistant", "content": _assistant_text(m.get("content")),
                             "files": m.get("minio_files") or []})
    return {"conversation": _conv_out(row), "messages": messages}


@router.delete("/conversations/{cid}")
def hide_conversation(cid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _own_conversation(db, user, cid)
    row.hidden = True
    db.commit()
    return {"ok": True}


@router.get("/chat/stop/{cid}")
def stop_chat(cid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _own_conversation(db, user, cid)
    cfg = _ready_cfg()
    try:
        return nx.request(cfg, "GET", f"/chat/stop/{cid}")
    except nx.NexentError as e:
        raise HTTPException(502, str(e))


# ----------------------------- 附件 -----------------------------

@router.post("/attachments")
async def upload_attachments(files: list[UploadFile] = File(...), user: User = Depends(get_current_user)):
    cfg = _ready_cfg()
    parts = []
    total = 0
    for f in files:
        content = await f.read()
        total += len(content)
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(400, f"附件 {f.filename} 超过 20MB")
        parts.append(("files", (f.filename, content, f.content_type or "application/octet-stream")))
    try:
        async with nx.async_client(cfg) as client:
            resp = await client.post(nx.api_root(cfg) + "/chat/attachments/upload",
                                     headers=nx.auth_headers(cfg), files=parts)
    except Exception as e:
        raise HTTPException(502, f"上传到 Nexent 失败：{type(e).__name__}")
    if resp.status_code >= 400:
        raise HTTPException(502, nx._explain(resp))
    data = resp.json()
    items = data.get("files") or (data.get("data") or {}).get("files") or data.get("data") or []
    return {"files": items, "total_bytes": total}


# ----------------------------- 流式会话 -----------------------------

class ChatIn(BaseModel):
    agent_name: str
    agent_display_name: Optional[str] = ""
    query: str
    conversation_id: Optional[int] = None
    trial_id: Optional[int] = None
    subject_kind: Optional[str] = ""      # trial / case / petition：在哪个业务对象里发起（trial_id 为兼容旧调用）
    subject_id: Optional[int] = None
    with_context: bool = False
    attachments: Optional[list] = None


def _sse(obj: dict) -> bytes:
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def _parse_event(raw: str) -> Optional[dict]:
    data_lines = [ln[5:].lstrip() for ln in raw.splitlines() if ln.startswith("data:")]
    if not data_lines:
        return None
    try:
        return json.loads("\n".join(data_lines))
    except Exception:
        return None



def _record_conversation(cid: int, user_id: int, username: str, meta: dict, answer: str = "", final: bool = False):
    """登记 / 更新会话归属（独立 Session：流式响应期间依赖注入的 Session 已关闭）。"""
    s = SessionLocal()
    try:
        row = (s.query(NexentConversation)
               .filter(NexentConversation.nexent_conversation_id == cid,
                       NexentConversation.user_id == user_id).first())
        if not row:
            owner = s.get(User, user_id)   # 独立 Session 不带请求范围：会话归发起人所在部门
            row = NexentConversation(
                dept_id=owner.dept_id if owner else None,
                nexent_conversation_id=cid, user_id=user_id, username=username,
                agent_name=meta["agent_name"], agent_display_name=meta["agent_display_name"],
                trial_id=meta.get("trial_id"), title=meta["title"], with_context=meta.get("with_context", False),
                subject_kind=meta.get("subject_kind") or "", subject_id=meta.get("subject_id"),
            )
            s.add(row)
        row.updated_at = datetime.utcnow()
        if final and answer:
            row.last_answer = answer[:2000]
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


async def _relay(cfg: dict, payload: dict, state: dict, user_id: int, username: str, meta: dict):
    """调用 Nexent /chat/run 并把 SSE 原样转发；state 中累计 cid 与主智能体最终答案（处理增量/整段/回滚）。"""
    client = nx.async_client(cfg)
    checkpoints: dict[str, int] = {}
    sub_ids: set[str] = set()  # 子智能体调用（subagent_start 声明）；主智能体事件同样带 invocation_id，不能一律排除

    def _is_sub(e: dict) -> bool:
        return bool(e.get("invocation_id")) and e.get("invocation_id") in sub_ids
    try:
        async with client.stream("POST", nx.api_root(cfg) + "/chat/run", json=payload,
                                 headers=nx.auth_headers(cfg, {"Content-Type": "application/json"})) as resp:
            if resp.headers.get("X-Stream-Status") == "conflict":
                yield _sse({"type": "error", "content": "该会话上一轮仍在运行，请稍后再发送"})
                return
            if resp.status_code >= 400:
                await resp.aread()
                yield _sse({"type": "error", "content": nx._explain(resp)})
                return
            if state.get("cid"):
                yield _sse({"type": "ctas_conversation", "content": {"conversation_id": state["cid"], "with_context": meta.get("with_context", False)}})
            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk.replace("\r\n", "\n")
                while "\n\n" in buf:
                    raw, buf = buf.split("\n\n", 1)
                    if not raw.strip():
                        continue
                    ev = _parse_event(raw)
                    if ev is not None:
                        et = ev.get("type")
                        if et == "conversation_created":
                            try:
                                state["cid"] = int((ev.get("content") or {}).get("conversation_id"))
                                _record_conversation(state["cid"], user_id, username, meta)
                                yield _sse({"type": "ctas_conversation", "content": {"conversation_id": state["cid"], "with_context": meta.get("with_context", False)}})
                            except Exception:
                                pass
                            continue
                        if et == "subagent_start":
                            inv = ev.get("invocation_id")
                            if not inv:
                                try:
                                    inv = json.loads(ev.get("content") or "{}").get("invocation_id")
                                except Exception:
                                    inv = None
                            if inv:
                                sub_ids.add(inv)
                        if et == "model_attempt_control" and ev.get("attempt_id") and not _is_sub(ev):
                            if ev.get("phase") == "begin":
                                checkpoints[ev["attempt_id"]] = len(state["answer"])
                            elif ev.get("phase") == "rollback" and ev["attempt_id"] in checkpoints:
                                state["answer"] = state["answer"][:checkpoints.pop(ev["attempt_id"])]
                        if et == "final_answer" and not _is_sub(ev):  # 子智能体答案不计入
                            text = ev.get("content") or ""
                            if not isinstance(text, str):
                                text = str(text.get("content") or text.get("text") or "") if isinstance(text, dict) else ""
                            if ev.get("answer_phase") == "complete":
                                if text:
                                    state["answer"] = text
                            else:
                                state["answer"] += text
                        if et == "error":
                            state["error"] = str(ev.get("content") or "智能体运行出错")
                    yield (raw + "\n\n").encode("utf-8")
            if buf.strip():
                yield (buf + "\n\n").encode("utf-8")
    except Exception as e:  # 连接失败 / 超时 / 被客户端中断
        name = type(e).__name__
        state["error"] = state.get("error") or f"与 Nexent 的连接中断：{name}"
        if "Cancel" not in name:
            yield _sse({"type": "error", "content": state["error"]})
    finally:
        await client.aclose()
        if state.get("cid"):
            _record_conversation(state["cid"], user_id, username, meta, state["answer"], final=True)


@router.post("/chat")
async def chat(body: ChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cfg = _ready_cfg()
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(400, "提问内容不能为空")
    if len(query) > 8000:
        raise HTTPException(400, "单次提问不超过 8000 字")
    if body.conversation_id:
        _own_conversation(db, user, body.conversation_id)
    kind, sid = ("trial", body.trial_id) if body.trial_id else ((body.subject_kind or ""), body.subject_id)
    if kind and kind not in SUBJECT_KINDS:
        raise HTTPException(400, f"不支持的业务对象类型：{kind}")
    if kind and sid and not db.get({"trial": Trial, "case": Case, "petition": Petition}[kind], sid):
        raise HTTPException(404, f"{SUBJECT_KINDS[kind]}不存在")
    if not sid:
        kind = ""
    trial_id = sid if kind == "trial" else None

    send_query = query
    attached_ctx = False
    if kind and body.with_context and not body.conversation_id:
        ctx = build_context(db, kind, sid)
        send_query = f"{ctx['text']}\n\n{CONTEXT_MARK}\n{query}"
        attached_ctx = True

    payload: dict[str, Any] = {
        "agent_name": body.agent_name,
        "query": send_query,
        "final_answer_mode": "delta",
        "meta_data": {"source": "ctas", "ctas_user": user.username,
                      "trial_id": trial_id, "subject_kind": kind, "subject_id": sid, "with_context": attached_ctx},
    }
    if body.conversation_id:
        payload["conversation_id"] = body.conversation_id
    if body.attachments:
        payload["attachments"] = body.attachments

    audit.write_log(db, user.id, user.username, "nexent_chat",
                    kind or "nexent_agent",
                    sid or body.agent_name, {
                        "agent": body.agent_name, "conversation_id": body.conversation_id,
                        "with_context": attached_ctx, "query_chars": len(query),
                        "attachments": len(body.attachments or []),
                    })

    user_id, username = user.id, user.username
    meta = {"agent_name": body.agent_name, "agent_display_name": body.agent_display_name or body.agent_name,
            "trial_id": trial_id, "subject_kind": kind, "subject_id": sid,
            "title": query.replace("\n", " ")[:60], "with_context": attached_ctx}

    async def gen():
        state = {"cid": body.conversation_id, "answer": ""}
        async for chunk in _relay(cfg, payload, state, user_id, username, meta):
            yield chunk
        yield _sse({"type": "ctas_done", "content": {"conversation_id": state["cid"]}})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ----------------------------- 存为文书草稿 -----------------------------

class SaveDocIn(BaseModel):
    title: str
    content: str
    trial_id: Optional[int] = None
    subject_kind: Optional[str] = ""
    subject_id: Optional[int] = None
    agent_name: Optional[str] = ""
    conversation_id: Optional[int] = None


@router.post("/save-document")
def save_document(body: SaveDocIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "内容为空，无法保存")
    case_name = ""
    if body.trial_id:
        t = db.get(Trial, body.trial_id)
        if not t:
            raise HTTPException(404, "审理事项不存在")
        c = db.get(Case, t.case_id) if t.case_id else None
        case_name = c.code if c else (t.subject or "")
    elif body.subject_kind == "case" and body.subject_id:
        c = db.get(Case, body.subject_id)
        case_name = c.code if c else ""
    elif body.subject_kind == "petition" and body.subject_id:
        p = db.get(Petition, body.subject_id)
        case_name = p.code if p else ""
    title = (body.title or "智能体意见").strip()[:150]
    note = f"\n\n——\n本稿由 Nexent 智能体「{body.agent_name or '未知'}」生成，属 AI 初稿，须经审理人员核对原件与法规原文后使用。"
    doc = Document(title=f"{title}（AI初稿）", source="智能体生成", case_name=case_name, content=content + note,
                   status="AI初稿", created_by=user.id)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    audit.write_log(db, user.id, user.username, "nexent_save_doc", "document", doc.id, {
        "trial_id": body.trial_id, "subject_kind": body.subject_kind, "subject_id": body.subject_id,
        "agent": body.agent_name, "conversation_id": body.conversation_id,
    })
    return {"id": doc.id, "title": doc.title}


# ----------------------------- 审理文书库：智能体校对 / 润色 -----------------------------

REVISED_BEGIN, REVISED_END = "<<<修改稿>>>", "<<<结束>>>"
REVIEW_MODES = {
    "proofread": ("校对", "只改正错别字、标点、用词不当和明显语病，不调整结构与表述风格"),
    "polish": ("润色", "在不改变原意的前提下，使表述更规范、严谨、简洁，符合党政机关公文与纪检监察文书的用语习惯"),
}


class DocReviewIn(BaseModel):
    agent_name: str
    agent_display_name: Optional[str] = ""
    mode: str = "proofread"
    instruction: Optional[str] = ""


def _review_prompt(title: str, content: str, mode: str, instruction: str) -> str:
    label, rule = REVIEW_MODES[mode]
    extra = f"\n补充要求：{instruction.strip()}" if instruction and instruction.strip() else ""
    return (
        f"请对下面这份审理文书进行{label}。\n"
        f"要求：{rule}；不得改变事实、数字、金额、日期、人名、单位名称、法规条款与处理结论；"
        f"不得增删事实内容；拿不准的地方保持原文，在修改说明中列出。{extra}\n"
        f"输出格式：先输出修改后的完整全文，放在 {REVISED_BEGIN} 与 {REVISED_END} 之间（不要使用代码块），"
        f"然后另起一段用要点列出主要修改及理由。\n\n"
        f"【文书标题】{title}\n【文书全文】\n{content}"
    )


import re as _re

# 模型偶尔会把起始标记简写成单独一行「<<<」或「<<<修改后全文>>>」，结束标记写成「>>>」；这里都兼容
_REV_START = _re.compile(r"<<<[ \t]*(?:修改稿|修改后全文|修改后的全文)?[ \t]*(?:>>>)?[ \t]*\r?\n")
_REV_END = _re.compile(r"<<<[ \t]*结束[ \t]*>>>|\n[ \t]*>>>[ \t]*(?:\n|$)")


def _extract_revised(answer: str) -> tuple[str, bool]:
    m = _REV_START.search(answer or "")
    if not m:
        return (answer or "").strip(), False
    rest = answer[m.end():]
    e = _REV_END.search(rest)
    body = rest[:e.start()] if e else rest
    return body.strip().strip("`").strip(), bool(e)


@router.post("/documents/{did}/review")
async def review_document(did: int, body: DocReviewIn, user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """调用 Nexent 智能体校对 / 润色文书：结果另存为新版本（待采用），不覆盖当前正文。"""
    cfg = _ready_cfg()
    if body.mode not in REVIEW_MODES:
        raise HTTPException(400, "mode 须为 proofread（校对）或 polish（润色）")
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404, "文书不存在")
    if d.status != "AI初稿":
        raise HTTPException(400, "已定稿文书不可再修改")
    if user.role == "banan" and d.created_by != user.id:
        raise HTTPException(403, "只能处理本人创建的文书")
    content = (d.content or "").strip()
    if not content:
        raise HTTPException(400, "文书正文为空")
    if len(content) > 20000:
        raise HTTPException(400, "文书超过 2 万字，请分段处理")

    label = REVIEW_MODES[body.mode][0]
    agent_display = body.agent_display_name or body.agent_name
    payload = {
        "agent_name": body.agent_name,
        "query": _review_prompt(d.title, content, body.mode, body.instruction or ""),
        "final_answer_mode": "delta",
        "meta_data": {"source": "ctas", "ctas_user": user.username, "document_id": did, "action": f"document_{body.mode}"},
    }
    audit.write_log(db, user.id, user.username, "nexent_doc_review", "document", did,
                    {"agent": body.agent_name, "mode": body.mode, "chars": len(content)})
    user_id, username, title = user.id, user.username, d.title
    meta = {"agent_name": body.agent_name, "agent_display_name": agent_display, "trial_id": None,
            "title": f"文书{label}：{title}"[:60], "with_context": False}

    async def gen():
        state = {"cid": None, "answer": ""}
        async for chunk in _relay(cfg, payload, state, user_id, username, meta):
            yield chunk
        result: dict[str, Any] = {"conversation_id": state["cid"], "saved": False}
        if not state["answer"].strip() and state.get("cid") and not state.get("error"):
            # 兜底：以 Nexent 已持久化的会话记录为准取最终答案
            try:
                hist = nx.request(cfg, "GET", f"/conversations/{state['cid']}")
                msgs = [m for m in ((hist.get("data") or {}).get("history") or []) if m.get("role") == "assistant"]
                if msgs:
                    state["answer"] = _assistant_text(msgs[-1].get("content"))
            except Exception:
                pass
        result["answer"] = state["answer"][:20000]
        revised, structured = _extract_revised(state["answer"])
        if state.get("error") or not revised:
            result["error"] = state.get("error") or "智能体没有返回内容"
        else:
            s = SessionLocal()
            try:
                doc = s.get(Document, did)
                last = (s.query(DocumentVersion).filter(DocumentVersion.document_id == did)
                        .order_by(DocumentVersion.version_no.desc()).first())
                vno = (last.version_no + 1) if last else 2
                note = f"智能体{label}（{agent_display}）· 待采用" + ("" if structured else " · 未按格式返回，保存的是完整回复")
                s.add(DocumentVersion(document_id=did, version_no=vno, content=revised, note=note[:200],
                                      source="智能体", created_by=user_id))
                s.commit()
                result.update(saved=True, version_no=vno, structured=structured, unchanged=(revised == (doc.content or "").strip()))
            except Exception as e:
                s.rollback()
                result["error"] = f"保存新版本失败：{type(e).__name__}"
            finally:
                s.close()
        yield _sse({"type": "ctas_document_version", "content": result})
        yield _sse({"type": "ctas_done", "content": {"conversation_id": state["cid"]}})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
