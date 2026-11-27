"""业务平台 MCP 服务（ctas-tools）：把平台的业务数据查询与确定性引擎开放给 Nexent 智能体。

- 只读 / 预览：不写任何业务数据；量纪说理、证据链、程序合规均为离线确定性计算；
- 鉴权：必须携带服务令牌（CTAS_MCP_TOKEN，在 Nexent 注册 MCP 时填写），否则一律 401；
- 留痕：每次工具调用写入平台审计链（操作人记为 nexent-mcp）；
- 数据范围：服务令牌级。部门令牌（「部门与权限」中生成，注册到该部门对应的 Nexent 租户）只返回该部门
  数据范围内的数据；CTAS_MCP_TOKEN 为平台级令牌（全部部门，单租户部署时使用）。Nexent 调用工具时不携带
  平台用户身份，因此只开放审理 / 研判所需的摘要级数据，不开放原件与附件。

运行：python -m app.mcp_server（容器内由 compose 的 ctas-mcp 服务启动，SSE 端点 /sse）。
"""
from __future__ import annotations

import contextvars
import hmac
import os

import uvicorn
from mcp.server.fastmcp import FastMCP
from sqlalchemy import or_
from starlette.responses import JSONResponse

from app import audit, tenancy
from app.database import SessionLocal, install_tenancy
from app.models import Case, Petition, Trial

install_tenancy()
# 本连接的数据范围（鉴权中间件按令牌设置；None = 平台级令牌，全部部门）
_SCOPE: contextvars.ContextVar = contextvars.ContextVar("ctas_mcp_scope", default=None)


def _session():
    db = SessionLocal()
    tenancy.apply_scope(db, _SCOPE.get())
    return db

TOKEN = os.getenv("CTAS_MCP_TOKEN", "").strip()
PORT = int(os.getenv("CTAS_MCP_PORT", "8097"))
mcp = FastMCP("纪检监察业务平台", host="0.0.0.0", port=PORT)

MAX_TEXT = 3000


def _cut(s: str | None, n: int = MAX_TEXT) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "…（已截断）"


def _log(db, tool: str, detail: dict):
    try:
        sc = _SCOPE.get()
        audit.write_log(db, None, "nexent-mcp", "mcp_tool_call", "mcp", tool,
                        {**detail, "scope": sc.label if sc else "平台级"})
    except Exception:
        pass


# ----------------------------- 案件 / 信访 -----------------------------

@mcp.tool()
def search_cases(keyword: str = "", limit: int = 10) -> list[dict]:
    """按案号、被反映人、单位或案由关键词检索案件，返回案号、被反映人、单位、案由、问题类型、状态。"""
    db = _session()
    try:
        q = db.query(Case)
        if keyword:
            k = f"%{keyword}%"
            q = q.filter(or_(Case.code.like(k), Case.subject.like(k), Case.unit.like(k), Case.title.like(k)))
        rows = q.order_by(Case.id.desc()).limit(max(1, min(limit, 30))).all()
        _log(db, "search_cases", {"keyword": keyword, "hits": len(rows)})
        return [{"code": c.code, "subject": c.subject, "unit": c.unit, "title": c.title,
                 "problem_type": c.problem_type, "status": c.status,
                 "due_at": c.due_at.strftime("%Y-%m-%d") if c.due_at else ""} for c in rows]
    finally:
        db.close()


@mcp.tool()
def get_case(code: str) -> dict:
    """按案号取案件详情：基本信息、主要问题、处置意见与时限。"""
    db = _session()
    try:
        c = db.query(Case).filter(Case.code == code).first()
        _log(db, "get_case", {"code": code, "found": bool(c)})
        if not c:
            return {"error": f"未找到案号 {code}"}
        return {"code": c.code, "source": c.source, "subject": c.subject, "unit": c.unit, "title": c.title,
                "content": _cut(c.content), "problem_type": c.problem_type, "status": c.status,
                "disposition": c.disposition, "disposition_note": _cut(c.disposition_note, 800),
                "opened_at": c.opened_at.strftime("%Y-%m-%d") if c.opened_at else "",
                "due_at": c.due_at.strftime("%Y-%m-%d") if c.due_at else ""}
    finally:
        db.close()


@mcp.tool()
def get_petition(code: str) -> dict:
    """按信访编号取信访件：反映内容、已抽取要素、分类标签、紧急程度与办理状态。"""
    db = _session()
    try:
        p = db.query(Petition).filter(Petition.code == code).first()
        _log(db, "get_petition", {"code": code, "found": bool(p)})
        if not p:
            return {"error": f"未找到信访件 {code}"}
        return {"code": p.code, "channel": p.channel, "status": p.status, "urgency": p.urgency,
                "content": _cut(p.content), "summary": p.summary, "elements": p.elements, "labels": p.labels,
                "biz_type": p.biz_type}
    finally:
        db.close()


# ----------------------------- 审理 -----------------------------

@mcp.tool()
def search_trials(keyword: str = "", limit: int = 10) -> list[dict]:
    """按事项名称或被审查调查人检索审理事项，返回 trial_id、事项、被审查调查人、状态。"""
    db = _session()
    try:
        q = db.query(Trial)
        if keyword:
            k = f"%{keyword}%"
            q = q.filter(or_(Trial.title.like(k), Trial.subject.like(k)))
        rows = q.order_by(Trial.id.desc()).limit(max(1, min(limit, 30))).all()
        _log(db, "search_trials", {"keyword": keyword, "hits": len(rows)})
        return [{"trial_id": t.id, "title": t.title, "subject": t.subject, "status": t.status,
                 "problem_type": t.problem_type} for t in rows]
    finally:
        db.close()


@mcp.tool()
def get_trial_context(trial_id: int) -> dict:
    """取审理事项的结构化案情摘要：基本情况、主要问题、阅卷摘要、证据与程序校验结论、量纪说理稿。"""
    from app.routers.nexent import build_trial_context
    db = _session()
    try:
        try:
            ctx = build_trial_context(db, trial_id)
        except Exception:
            return {"error": f"未找到审理事项 {trial_id}"}
        _log(db, "get_trial_context", {"trial_id": trial_id, "chars": ctx["chars"]})
        return {"trial_id": trial_id, "title": ctx["title"], "text": _cut(ctx["text"], 6000)}
    finally:
        db.close()


@mcp.tool()
def sentencing_reasoning(trial_id: int) -> dict:
    """运行量纪说理引擎（离线确定性）：定性、适用条款、党纪 / 政务处分参考区间、从轻从重情节与说理稿。"""
    from app.sentencing import build_sentencing_reasoning
    db = _session()
    try:
        t = db.get(Trial, trial_id)
        if not t:
            return {"error": f"未找到审理事项 {trial_id}"}
        c = db.get(Case, t.case_id) if t.case_id else None
        r = build_sentencing_reasoning(t, c, db) or {}
        _log(db, "sentencing_reasoning", {"trial_id": trial_id})
        return {"category": r.get("category"), "party_range": r.get("tier_range"), "gov_range": r.get("gov_tier_range"),
                "light": r.get("light"), "heavy": r.get("heavy"), "text": _cut(r.get("text"), 5000),
                "note": "处分档次须经审理组集体审议，系统不替代人工结论"}
    finally:
        db.close()


@mcp.tool()
def evidence_and_procedure_check(trial_id: int) -> dict:
    """重新计算（不落库）证据链完整性校验与程序合规检查，返回结论与问题清单。"""
    from app.routers.trials import _evidence_chain, _procedure_check
    db = _session()
    try:
        t = db.get(Trial, trial_id)
        if not t:
            return {"error": f"未找到审理事项 {trial_id}"}
        c = db.get(Case, t.case_id) if t.case_id else None
        ev, pr = _evidence_chain(t, c), _procedure_check(t, c)
        _log(db, "evidence_and_procedure_check", {"trial_id": trial_id})

        def brief(x):
            return {"verdict": x.get("verdict"), "gaps": x.get("gaps") or [],
                    "issues": [{k: i.get(k) for k in ("point", "item", "status", "level", "note") if i.get(k)}
                               for i in (x.get("checks") or []) if i.get("level") in ("error", "warn")]}
        return {"evidence": brief(ev), "procedure": brief(pr)}
    finally:
        db.close()


# ----------------------------- 法规与类案 -----------------------------

@mcp.tool()
def check_legal_citations(text: str) -> dict:
    """从文书文本中抽取法规引用（《法规名》第X条），逐条核对平台法规库是否存在该条款及原文。"""
    from app.refcheck import check_refs, extract_refs
    db = _session()
    try:
        refs = extract_refs(text or "")
        result = check_refs(db, refs) if refs else []
        _log(db, "check_legal_citations", {"refs": len(refs)})
        return {"citations": result[:30], "total": len(result)}
    finally:
        db.close()


@mcp.tool()
def get_regulation_article(law: str, article_no: str) -> dict:
    """按法规名称（全称或简称）和条号（如「第九十七条」或「97」）精确取平台法规库中的条文原文、所属章节与版本。"""
    from app.models import Regulation, RegulationArticle
    from app.refcheck import check_refs
    db = _session()
    try:
        no = article_no.strip()
        r = (check_refs(db, [{"law": law.strip("《》 "), "article_no": no}]) or [{}])[0]
        _log(db, "get_regulation_article", {"law": law[:40], "article_no": no, "status": r.get("status")})
        aid = r.get("matched_article_id")
        if not aid:
            return {"found": False, "law": law, "article_no": no, "reason": r.get("reason") or "平台法规库未检索到该条款"}
        a = db.get(RegulationArticle, aid)
        reg = db.get(Regulation, a.regulation_id)
        return {"found": True, "law": reg.title, "version": reg.version, "status": reg.status,
                "chapter": a.chapter, "article_no": a.article_no, "content": a.content}
    finally:
        db.close()



@mcp.tool()
def search_similar_reports(query: str, top_k: int = 5) -> dict:
    """在历史审计 / 巡察 / 审理报告库中检索类似问题，返回报告标题、单位、问题类别、金额与命中片段。"""
    from app.routers.historical_reports import search_reports
    from app.schemas import HistoricalReportSearchIn
    db = _session()
    try:
        r = search_reports(HistoricalReportSearchIn(q=query, top_k=max(1, min(top_k, 10))), db=db, user=None)
        _log(db, "search_similar_reports", {"query": query[:60], "hits": len(r.get("hits") or [])})
        keep = ("id", "title", "report_type", "year", "region", "audited_org", "problem_category", "severity", "amount",
                "findings", "legal_refs", "score", "snippets")
        return {"hits": [{k: h.get(k) for k in keep if k in h} for h in (r.get("hits") or [])]}
    finally:
        db.close()


# ----------------------------- 启动 -----------------------------

class TokenAuth:
    """纯 ASGI 鉴权（不用 BaseHTTPMiddleware：它会缓冲 / 打断 SSE 长连接）。
    Nexent 把注册时填写的令牌原样放进 Authorization 头，带不带 Bearer 前缀都接受。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("path") == "/healthz":
            return await JSONResponse({"ok": True})(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        got = headers.get(b"authorization", b"").decode("latin-1").removeprefix("Bearer ").strip()
        if TOKEN and hmac.compare_digest(got.encode(), TOKEN.encode()):
            dept_scope = None                      # 平台级令牌
        else:
            db = SessionLocal()
            try:
                dept_scope = tenancy.scope_for_mcp_token(db, got)
            finally:
                db.close()
            if dept_scope is None:
                return await JSONResponse({"detail": "unauthorized"}, status_code=401)(scope, receive, send)
        # 工具调用在 SSE 连接的会话任务里执行，会继承这里设置的上下文
        _SCOPE.set(dept_scope)
        return await self.app(scope, receive, send)


def create_app():
    return TokenAuth(mcp.sse_app())


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("CTAS_MCP_TOKEN 未设置，拒绝以无鉴权方式启动 MCP 服务")
    uvicorn.run(create_app(), host="0.0.0.0", port=PORT, proxy_headers=True)
