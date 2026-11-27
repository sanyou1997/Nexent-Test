import io
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session
from openpyxl import Workbook

from app import audit
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.models import User, Regulation, RegulationArticle
from app.rag import ingest as rag_ingest
from app.rag.ingest import parse_corpus
from app.llm import embeddings

router = APIRouter(prefix="/api/regulations", tags=["regulations"])

CATEGORIES = ["中央党内法规", "国家法律法规", "纪检监察法规制度", "业务工作规范", "其他"]


def _content_disposition(filename: str) -> str:
    """RFC 5987：中文文件名走 filename*，避免 latin-1 编码报错。"""
    return f"attachment; filename*=UTF-8''{quote(filename)}"


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------
@router.get("/kb-sync")
def kb_sync_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """法规库 ↔ Nexent 知识库 联动状态：按当前条文重算同步指纹，与最近一次同步记录比对。"""
    from app import kb_sync
    return kb_sync.status(db)


@router.get("")
def list_regulations(category: str = "", db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    q = db.query(Regulation)
    if category:
        q = q.filter(Regulation.category == category)
    regs = q.order_by(Regulation.id.desc()).all()
    return [{"id": r.id, "title": r.title, "version": r.version,
             "effective_date": r.effective_date, "status": r.status,
             "source": r.source, "category": r.category or "其他",
             "is_official": bool(getattr(r, "is_official", False)),
             "article_count": db.query(RegulationArticle)
                 .filter(RegulationArticle.regulation_id == r.id).count()}
            for r in regs]


# ---------------------------------------------------------------------------
# 导出（跟随筛选条件，拉全量）
# ---------------------------------------------------------------------------
@router.get("/export")
def export_articles(regulation_id: int | None = None, q: str = "",
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(RegulationArticle)
    if regulation_id:
        query = query.filter(RegulationArticle.regulation_id == regulation_id)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (RegulationArticle.content.like(like)) |
            (RegulationArticle.article_no.like(like)) |
            (RegulationArticle.chapter.like(like)))
    rows = query.order_by(RegulationArticle.id).all()
    reg_ids = {r.regulation_id for r in rows if r.regulation_id}
    reg_map = {r.id: r.title for r in db.query(Regulation).filter(Regulation.id.in_(reg_ids)).all()} if reg_ids else {}

    wb = Workbook()
    ws = wb.active
    ws.title = "法规条款"
    ws.append(["法规", "类别(章节)", "条款", "内容"])
    for r in rows:
        ws.append([reg_map.get(r.regulation_id, ""), r.chapter or "", r.article_no, r.content])
    for col, w in zip("ABCD", (28, 22, 14, 80)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    fname = f"法规条款导出_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": _content_disposition(fname)})


# ---------------------------------------------------------------------------
# 条款列表（服务端 SQL 搜索 + 分页；regulation_id 不传即全库搜索）
# ---------------------------------------------------------------------------
@router.get("/articles")
def list_articles(regulation_id: int | None = None, q: str = "", page: int = 1,
                  per_page: int = 20, required_only: bool = False,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    query = db.query(RegulationArticle)
    if regulation_id:
        query = query.filter(RegulationArticle.regulation_id == regulation_id)
    if required_only:
        req_ids = [r.id for r in db.query(Regulation.id).filter(Regulation.required == True).all()]
        query = query.filter(RegulationArticle.regulation_id.in_(req_ids))
    if q:
        like = f"%{q}%"
        query = query.filter(
            (RegulationArticle.content.like(like)) |
            (RegulationArticle.article_no.like(like)) |
            (RegulationArticle.chapter.like(like)))
    total = query.count()
    items = (query.order_by(RegulationArticle.id)
             .offset((page - 1) * per_page).limit(per_page).all())
    reg_ids = {r.regulation_id for r in items if r.regulation_id}
    reg_objs = {r.id: r for r in db.query(Regulation).filter(Regulation.id.in_(reg_ids)).all()} if reg_ids else {}
    return {
        "items": [{
            "id": r.id,
            "regulation": reg_objs.get(r.regulation_id).title if reg_objs.get(r.regulation_id) else "",
            "chapter": r.chapter, "article_no": r.article_no, "content": r.content,
            "is_official": bool(reg_objs.get(r.regulation_id).is_official) if reg_objs.get(r.regulation_id) else False,
            "required": bool(reg_objs.get(r.regulation_id).required) if reg_objs.get(r.regulation_id) else False,
        } for r in items],
        "total": total, "page": page, "per_page": per_page,
    }


# ---------------------------------------------------------------------------
# 离线零依赖建库：直接粘贴语料（txt/md 格式），不依赖 PDF/MinerU
# ---------------------------------------------------------------------------
@router.post("/create")
def create_from_text(payload: dict, db: Session = Depends(get_db),
                     user: User = Depends(require_roles("admin", "xinfang"))):
    """请求体：{text, source?, official?, category?}。
    复用语料切块规则（首行 法规名|版本|生效日期，## 章，【第X条】内容）。
    """
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "语料文本不能为空")
    source = (payload.get("source") or "在线录入（粘贴语料）").strip()
    official = bool(payload.get("official", False))
    category = payload.get("category") or "其他"
    if category not in CATEGORIES:
        category = "其他"
    try:
        report = rag_ingest.ingest(db, text, source=source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    articles_n = report["article_count"]
    if not articles_n:
        raise HTTPException(400, "未解析到任何条款，请检查语料格式（【第X条】内容）")

    reg = (db.query(Regulation).filter(Regulation.title == report["title"])
           .order_by(Regulation.id.desc()).first())
    if reg:
        reg.is_official = official
        reg.category = category
        db.commit()
        rid = reg.id
    else:
        rid = report.get("regulation_id")
    db.commit()
    from app.rag.retrieve import invalidate_corpus
    invalidate_corpus()
    audit.write_log(db, user.id, user.username, action="regulation_create",
                    resource_type="regulation", resource_id=rid,
                    detail={"articles": articles_n, "official": official, "category": category,
                            "gaps": report.get("gaps", [])})
    resp = {"ok": True, "id": rid, "title": report["title"], "article_count": articles_n}
    if report.get("gaps") or report.get("dups"):
        resp["continuity_warning"] = (
            f"条目不连续：缺 {'、'.join(report['gaps'][:10]) or '无'}"
            f"{'；重号 ' + '、'.join(report['dups'][:5]) if report.get('dups') else ''}；"
            "请核对语料是否完整——数据不全，分析无从谈起")
    return resp


# ---------------------------------------------------------------------------
# 文件上传入库（PDF/扫描件经 MinerU；txt/md 直接解析）
# ---------------------------------------------------------------------------
@router.post("/ingest")
async def ingest_file(file: UploadFile = File(...), official: bool = Form(False),
                      category: str = Form("其他"),
                      source: str = Form(""), db: Session = Depends(get_db),
                      user: User = Depends(require_roles("admin", "xinfang"))):
    """上传法规/业务语料入库：txt/md 直接解析；PDF/扫描件经 MinerU 解析为文本后条款级切块。

    official=true 表示"正式文档录入"：以发布机关原文入库，标记只读（不可编辑）。
    """
    if category not in CATEGORIES:
        category = "其他"
    data = await file.read()
    name = (file.filename or "").lower()
    try:
        if name.endswith(".pdf"):
            from app.rag.mineru_client import parse_pdf_to_text, md_to_corpus
            try:
                md = parse_pdf_to_text(data, file.filename)
            except RuntimeError as e:
                return {"ok": False, "error": f"PDF 解析不可用：{e}"}
            stem = (file.filename or "").rsplit(".", 1)[0]
            text = md_to_corpus(md, stem)
        elif name.endswith((".md", ".txt", "")):
            text = data.decode("utf-8", errors="ignore")
        else:
            return {"ok": False, "error": "仅支持 .txt / .md / .pdf 文件"}
        src = source.strip() or (f"正式文档原件导入｜{file.filename}" if official
                                 else f"用户导入：{file.filename}")
        report = rag_ingest.ingest(db, text, source=src)
        count = report["article_count"]
        if official:
            reg = (db.query(Regulation).filter(Regulation.title == report["title"])
                   .order_by(Regulation.id.desc()).first())
            if reg:
                reg.is_official = True
                reg.category = category
                db.commit()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    audit.write_log(db, user.id, user.username, action="regulation_ingest",
                    resource_type="regulation",
                    detail={"articles": count, "file": file.filename, "official": official,
                            "gaps": report.get("gaps", [])})
    resp = {"ok": True, "article_count": count, "is_official": official}
    if report.get("gaps") or report.get("dups"):
        resp["continuity_warning"] = (
            f"条目不连续：缺 {'、'.join(report['gaps'][:10]) or '无'}"
            f"{'；重号 ' + '、'.join(report['dups'][:5]) if report.get('dups') else ''}；"
            "请核对原文是否完整——数据不全，分析无从谈起")
    return resp
