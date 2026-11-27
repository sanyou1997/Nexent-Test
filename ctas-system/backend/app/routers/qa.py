import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app import audit
from app import nexent_client as nx
from app.database import get_db
from app.deps import get_current_user
from app.llm import provider
from app.models import User
from app.rag import retrieve
from app.schemas import AskIn

router = APIRouter(prefix="/api/qa", tags=["A1 法规问答"])

SYSTEM_PROMPT = (
    "你是纪检监察法规问答助手。严格依据提供的法规条款回答问题。"
    "回答必须完整成句、结构清晰：先给出明确结论，再标注所引用的条款号"
    "（如《中国共产党纪律处分条例》第十条），并可摘录条款关键原文佐证；"
    "如提供的条款不足以回答，必须明确回答'所提供法规条款中未找到依据'，不得编造。"
    "回答使用简体中文，不得只输出词语片段。"
)

# LLM 输出健全性下限：本机 3B 小模型偶发残句（如"警告：一年"），
# 低于该长度视为不完整生成，自动回落离线结构化回答（结论+条款号+原文，全溯源）
_MIN_LLM_ANSWER_LEN = 20

# ---- 离线规则化回答（弱 AI：无 LLM 时的结构化合成，不联网） ----

_QTYPE_HINTS = [
    ("处分种类与档次", ["处分", "警告", "严重警告", "留党察看", "开除党籍", "撤职", "降级"]),
    ("程序与时限", ["程序", "时限", "多长时间", "几天", "几个月", "如何启动", "报批", "复议", "申诉"]),
    ("适用范围", ["适用", "范围", "对象", "是否包括", "监察对象", "党员"]),
    ("从重从轻", ["从重", "从轻", "减轻", "免予", "从宽"]),
]

def _offline_answer(question: str, hits: list[dict]) -> str:
    """基于检索条款 + 问题意图的规则化合成回答（离线可用、零幻觉：所有结论均附原文出处）。"""
    q = question
    intent = next((name for name, kws in _QTYPE_HINTS if any(k in q for k in kws)), "相关规定")
    main = hits[0]
    parts: list[str] = []
    parts.append(f"【{intent}·依据检索】针对您的问题，最直接相关的是《{main['regulation']}》{main['article_no']}：")
    parts.append(f"「{main['content']}」")
    if len(hits) > 1:
        parts.append("\n另请参见以下相关条款：")
        for h in hits[1:4]:
            parts.append(f"· 《{h['regulation']}》{h['article_no']}：{h['content'][:90]}{'…' if len(h['content']) > 90 else ''}")
    parts.append("\n（离线模式说明：当前未联网调用大模型，以上回答由本机检索引擎从法规库中匹配生成，"
                 "全部结论均有原文出处、不做演绎；如需综合分析表述，请联网后使用大模型问答。）")
    return "\n".join(parts)

@router.post("/ask")
def ask(data: AskIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not data.question.strip():
        return {"question": "", "answer": "请输入问题。", "mode": "retrieval", "citations": []}

    result = retrieve.retrieve(db, data.question)
    hits = result["hits"]
    weak = result["mode"] in ("refused", "empty")

    gen = None
    if provider.llm_available("qa"):
        gen = provider.chat([{"role": "user", "content": _agent_query(data.question, hits, weak)}],
                            scene="qa", user_id=user.id)
    if gen and len(gen.strip()) >= _MIN_LLM_ANSWER_LEN:
        answer, mode = gen, "llm"
    elif weak:
        # 拒答机制：智能体不可用且本地检索相关性低于阈值 → 不生成、不猜测
        answer, mode = REFUSED_ANSWER, "refused"
    else:
        # 智能体不可用 / 输出不完整 → 离线结构化合成（结论+条款号+原文，全溯源）
        answer, mode = _offline_answer(data.question, hits), "retrieval"

    audit.write_log(db, user.id, user.username, action="qa_ask",
                    detail={"question": data.question, "mode": mode,
                            "top_citations": [h["article_no"] for h in hits[:3]]})
    return {"question": data.question, "answer": answer, "mode": mode, "citations": hits}

REFUSED_ANSWER = ("知识库中未找到与该问题足够相关的法规依据，为避免误导，本助手不予回答。"
                  "建议：①更换问法或使用规范纪法术语；②联系法规部门人工核实。")


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


def _agent_query(question: str, hits: list[dict], weak: bool) -> str:
    """给「党纪法规问答」智能体的任务：平台本地检索结果只作候选线索；智能体在 Nexent 里用同源知识库做
    语义检索，并按条号取原文核对后作答（本地稀疏检索对口语化问法召回弱，不能由它决定拒答）。"""
    ctx = "\n\n".join(f"《{h['regulation']}》{h['chapter']} {h['article_no']}：{h['content']}" for h in hits)
    hint = ("平台本地检索的候选条款相关度较低，仅供参考，请以知识库检索结果为准" if weak
            else "平台法规库检索到的候选条款（可结合知识库检索补充，引用前请核对原文）")
    return f"{SYSTEM_PROMPT}\n\n问题：{question}\n\n{hint}：\n{ctx or '（无）'}"


@router.post("/ask-stream")
def ask_stream(data: AskIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """流式问答：先推平台法规库的检索条款（即时可见、可溯源），再转发「党纪法规问答」场景智能体的流式作答
    （智能体在 Nexent 里检索同源知识库并按条号取原文核对），最后给出定稿答案；任一环节不可用自动降级为离线合成。"""
    from app.routers.nexent import _relay

    question = data.question.strip()
    result = retrieve.retrieve(db, question) if question else {"mode": "empty", "hits": []}
    hits = result["hits"]
    weak = result["mode"] in ("refused", "empty")
    cfg = nx.load_settings()
    agent = (cfg.get("scene_agents") or {}).get("law_qa", "")
    use_agent = bool(question) and nx.is_ready(cfg) and bool(agent)
    refused = weak and not use_agent
    uid, uname = user.id, user.username
    audit.write_log(db, uid, uname, action="qa_ask",
                    detail={"question": question, "stream": True, "agent": agent if use_agent else "",
                            "top_citations": [h["article_no"] for h in hits[:3]]})

    async def gen():
        yield _sse({"type": "ctas_citations", "content": {"hits": hits, "refused": refused, "weak": weak}})
        if refused:
            yield _sse({"type": "ctas_qa_done", "content": {"answer": REFUSED_ANSWER, "mode": "refused"}})
            return
        if not use_agent:
            yield _sse({"type": "ctas_qa_done", "content": {"answer": _offline_answer(question, hits), "mode": "retrieval",
                                                             "reason": "Nexent 未接入或「法规问答」场景未指定智能体"}})
            return
        payload = {"agent_name": agent, "final_answer_mode": "delta", "query": _agent_query(question, hits, weak),
                   "meta_data": {"source": "ctas", "ctas_user": uname, "action": "law_qa"}}
        meta = {"agent_name": agent, "agent_display_name": "党纪法规问答", "trial_id": None,
                "title": f"法规问答：{question}"[:60], "with_context": False}
        state = {"cid": None, "answer": ""}
        t0 = time.time()
        async for chunk in _relay(cfg, payload, state, uid, uname, meta):
            yield chunk
        answer = provider._CITE.sub("", state["answer"]).strip()
        ok = len(answer) >= _MIN_LLM_ANSWER_LEN and not state.get("error")
        provider._record(uid, "qa", agent, ok, t0, state.get("cid"), state.get("error") or "")
        if ok:
            yield _sse({"type": "ctas_qa_done", "content": {"answer": answer, "mode": "llm", "conversation_id": state["cid"]}})
        elif weak:
            yield _sse({"type": "ctas_qa_done", "content": {"answer": REFUSED_ANSWER, "mode": "refused",
                                                             "reason": state.get("error") or "智能体未返回有效回答"}})
        else:
            yield _sse({"type": "ctas_qa_done", "content": {"answer": _offline_answer(question, hits), "mode": "retrieval",
                                                             "reason": state.get("error") or "智能体未返回有效回答"}})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/status")
def status(user: User = Depends(get_current_user)):
    return {"llm_available": provider.llm_available("qa")}
