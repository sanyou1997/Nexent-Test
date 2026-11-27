"""档案数字化 OCR 入库：扫描件（图片/PDF）OCR 抽取文字后入库，支持检索与编目。

复用既有 OCR 能力：
- 图片：app.routers.ocr.ocr_image_structured
- PDF ：app.rag.pdf_extract.extract_pdf（数字文本抽取 + 扫描页 OCR）

优化增强：
- 编目字段：密级 / 保管期限（贴合纪检档案合规）
- 结构化坐标 boxes_json：支持「原文对照」（点正文行高亮原件）
- 内容哈希 content_hash：重复扫描入库预警
- 列表分页、目录导出 xlsx
"""
import os
import io
import time
import json
import hashlib
from datetime import datetime as _dt
from urllib.parse import quote

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, Response, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.config import BASE_DIR, settings
from app.database import get_db
from app.deps import get_current_user
from app.models import User, Archive
from app import audit
from app.schemas import ArchiveIn, ArchiveOut

router = APIRouter(prefix="/api/archives", tags=["A4 档案数字化OCR入库"])

ARCHIVE_DIR = BASE_DIR / "data" / "archives"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

_IMG = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
_CONTENT = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
    "pdf": "application/pdf",
}
SECRET_LEVELS = ["公开", "内部", "秘密", "机密"]
RETENTIONS = ["永久", "30年", "10年"]
_OCR_DPI = 150  # 原文对照统一渲染 dpi（扫描页与数字文本页一致）


def _content_disposition(inline: bool, name: str) -> str:
    """构造可承载中文文件名的 Content-Disposition（RFC 5987 filename*）。"""
    disp = "inline" if inline else "attachment"
    ascii_name = name.encode("ascii", "ignore").decode() or "download"
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def _out(a: Archive, db: Session, duplicate_of: int | None = None) -> ArchiveOut:
    up = db.get(User, a.uploaded_by) if a.uploaded_by else None
    return ArchiveOut(
        id=a.id, archive_no=a.archive_no, title=a.title, source=a.source,
        category=a.category, ocr_text=a.ocr_text, page_count=a.page_count,
        char_count=a.char_count, status=a.status, file_name=a.file_name,
        file_type=a.file_type, has_file=bool(a.file_path),
        uploaded_by_name=up.real_name if up else "",
        created_at=a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "",
        secret_level=a.secret_level or "内部", retention=a.retention or "30年",
        has_boxes=bool(a.boxes_json), duplicate_of=duplicate_of,
    )


def _gen_no(db: Session) -> str:
    """档号 = DA + UTC 日期 + 当日现存最大序号 + 1。

    历史教训（2026-10-15 实测 500）：不能用 count()+1 —— 删除行会让计数回退，
    把仍然存在的档号重新发出来（如现存 DA...0102 未删、其余行删到计数 101，
    新插入又算成 102 → UNIQUE 冲突整单 500）。取「当日已存在的最大号」递增，
    天然不与现存行碰撞；号段跳变是正确性对美观的必要让步。
    """
    today = _dt.utcnow().strftime("%Y%m%d")
    prefix = f"DA{today}"
    row = (db.query(Archive.archive_no)
           .filter(Archive.archive_no.like(prefix + "%"))
           .order_by(Archive.archive_no.desc())
           .execution_options(tenancy_bypass=True)   # 档号全平台唯一：跨部门取最大号
           .first())
    seq = 1
    if row and row[0]:
        tail = row[0][len(prefix):]
        if tail.isdigit():
            seq = int(tail) + 1
    return f"{prefix}{seq:04d}"


def _norm_box(pts, w, h):
    """四点坐标归一化到 0..1（显示无关）。"""
    if not w or not h:
        return [[0, 0], [0, 0], [0, 0], [0, 0]]
    return [[round(x / w, 4), round(y / h, 4)] for (x, y) in pts]


def _build_boxes_image(data: bytes):
    """图片：抽取结构化框（原始像素坐标），归一化后返回 pages。"""
    from PIL import Image
    from app.routers.ocr import ocr_image_structured
    try:
        im = Image.open(io.BytesIO(data))
        W, H = im.size
    except Exception:
        return None
    boxes = ocr_image_structured(data, preprocess=True, correct=True)
    if not boxes:
        return None
    return {"pages": [{
        "page": 1, "w": W, "h": H,
        "boxes": [{"box": _norm_box(b["box"], W, H), "text": b["text"], "score": b.get("score", 0)}
                  for b in boxes],
    }]}


def _build_boxes_pdf(data: bytes, name: str):
    """PDF：逐页抽取框，统一 ocr_dpi 渲染，坐标归一化。"""
    from app.rag.pdf_extract import extract_pdf
    res = extract_pdf(data, name, return_images=False, ocr_dpi=_OCR_DPI, preview_dpi=_OCR_DPI)
    pages = []
    for p in res.get("pages", []):
        w, h = p.get("width") or 0, p.get("height") or 0
        boxes = [{"box": _norm_box(b["box"], w, h), "text": b.get("text", ""), "score": b.get("score", 0)}
                 for b in (p.get("boxes") or [])]
        pages.append({"page": p.get("page", len(pages) + 1), "w": w, "h": h, "boxes": boxes})
    return {"pages": pages} if pages else None


def _build_boxes(data: bytes, name: str):
    name = (name or "").lower()
    if name.endswith(".pdf"):
        return _build_boxes_pdf(data, name)
    return _build_boxes_image(data)


@router.post("/ingest", response_model=ArchiveOut)
async def ingest(
    file: UploadFile = File(...),
    title: str = Form(""),
    source: str = Form(""),
    category: str = Form("文书档案"),
    secret_level: str = Form("内部"),
    retention: str = Form("30年"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = await file.read()
    if not data:
        raise HTTPException(400, "上传文件为空")
    name = file.filename or ""
    t0 = time.time()
    # 1) 内容哈希 → 重复入库预警
    content_hash = hashlib.sha256(data).hexdigest()
    dup = db.query(Archive).filter(Archive.content_hash == content_hash).first()
    duplicate_of = dup.id if dup else None
    # 2) OCR 文本
    try:
        if name.lower().endswith(".pdf"):
            from app.rag.pdf_extract import extract_pdf
            res = extract_pdf(data, name, return_images=False, ocr_dpi=_OCR_DPI, preview_dpi=_OCR_DPI)
            if res.get("encrypted"):
                raise HTTPException(400, "PDF 已加密，请先解密后再上传")
            text = res.get("text", "") or ""
            pages = len(res.get("pages", []) or [])
        else:
            from app.routers.ocr import ocr_image_bytes
            text = ocr_image_bytes(data, preprocess=True, correct=True)
            pages = 1
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"OCR 解析失败：{e}")
    if not text.strip():
        raise HTTPException(422, "未识别到文字内容，请确认文件为清晰扫描件")
    # 3) 结构化坐标（原文对照）
    boxes = _build_boxes(data, name)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    # 4) 入库。_gen_no 取当日最大号递增，已保证不与现存行碰撞；
    #    重试仅防御并发双写（两个请求同时算出同一号且都未提交时，后提交者撞 UNIQUE）
    arch = None
    for _attempt in range(5):
        no = _gen_no(db)
        rel = ""
        if data:
            safe_no = "".join(c for c in no if c.isalnum())
            fname = f"{safe_no}.{ext}" if ext else safe_no
            disk = ARCHIVE_DIR / fname
            disk.write_bytes(data)
            rel = f"data/archives/{fname}"
        cand = Archive(
            archive_no=no, title=title or name, source=source, category=category,
            secret_level=secret_level if secret_level in SECRET_LEVELS else "内部",
            retention=retention if retention in RETENTIONS else "30年",
            ocr_text=text, page_count=pages, char_count=len(text), status="已入库",
            file_name=name, file_type=ext, file_path=rel, uploaded_by=user.id,
            content_hash=content_hash,
            boxes_json=json.dumps(boxes, ensure_ascii=False) if boxes else "",
            ocr_dpi=_OCR_DPI,
        )
        try:
            db.add(cand)
            db.commit()
            db.refresh(cand)
            arch = cand
            break
        except IntegrityError:
            db.rollback()
            continue
    if arch is None:
        raise HTTPException(500, "档号生成冲突，请稍后重试")
    audit.write_log(db, user.id, user.username, action="archive_ingest",
                    resource_type="archive", resource_id=str(arch.id),
                    detail={"archive_no": arch.archive_no, "chars": len(text), "pages": pages,
                            "has_boxes": bool(boxes), "duplicate_of": duplicate_of,
                            "latency_ms": int((time.time() - t0) * 1000)})
    return _out(arch, db, duplicate_of=duplicate_of)


@router.get("")
def list_archives(
    q: str = "", category: str = "", status: str = "",
    page: int = 1, per_page: int = 20,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    query = db.query(Archive)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Archive.title.like(like)) | (Archive.ocr_text.like(like)) |
            (Archive.source.like(like)) | (Archive.archive_no.like(like))
        )
    if category:
        query = query.filter(Archive.category == category)
    if status:
        query = query.filter(Archive.status == status)
    total = query.count()
    items = (query.order_by(Archive.id.desc())
             .offset((max(page, 1) - 1) * per_page).limit(per_page).all())
    return {"items": [_out(a, db) for a in items], "total": total}


@router.get("/stats/summary")
def stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Archive).all()
    by_cat: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_secret: dict[str, int] = {}
    for a in rows:
        by_cat[a.category] = by_cat.get(a.category, 0) + 1
        by_status[a.status] = by_status.get(a.status, 0) + 1
        by_secret[a.secret_level] = by_secret.get(a.secret_level, 0) + 1
    return {
        "total": len(rows), "by_category": by_cat, "by_status": by_status,
        "by_secret": by_secret,
        "total_chars": sum(a.char_count for a in rows),
        "total_pages": sum(a.page_count for a in rows),
    }


@router.get("/export")
def export_xlsx(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """导出档案目录为 xlsx（utf-8，Excel 直接打开）。"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "档案目录"
    headers = ["档号", "标题", "来源", "类别", "密级", "保管期限", "状态",
               "页数", "字数", "入库人", "入库时间"]
    ws.append(headers)
    rows = db.query(Archive).order_by(Archive.id.desc()).all()
    for a in rows:
        up = db.get(User, a.uploaded_by) if a.uploaded_by else None
        ws.append([
            a.archive_no, a.title, a.source, a.category, a.secret_level or "内部",
            a.retention or "30年", a.status, a.page_count, a.char_count,
            up.real_name if up else "",
            a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"档案目录_{_dt.now().strftime('%Y%m%d')}.xlsx"
    audit.write_log(db, user.id, user.username, action="archive_export",
                    resource_type="archive", resource_id="export",
                    detail={"count": len(rows)})
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(False, fname)},
    )


@router.get("/{aid}", response_model=ArchiveOut)
def detail(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.get(Archive, aid)
    if not a:
        raise HTTPException(404, "档案不存在")
    return _out(a, db)


@router.get("/{aid}/boxes")
def boxes(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """返回结构化坐标（原文对照用），避免列表响应携带大体积 JSON。"""
    a = db.get(Archive, aid)
    if not a or not a.boxes_json:
        raise HTTPException(404, "无坐标数据")
    return json.loads(a.boxes_json)


@router.get("/{aid}/file")
def download_file(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """回传扫描原件：图片内联预览，其余作为附件下载。"""
    a = db.get(Archive, aid)
    if not a or not a.file_path:
        raise HTTPException(404, "原件不存在")
    disk = BASE_DIR / a.file_path
    if not disk.exists():
        raise HTTPException(404, "原件文件已丢失")
    ctype = _CONTENT.get(a.file_type, "application/octet-stream")
    inline = a.file_type in _IMG
    return FileResponse(
        str(disk), media_type=ctype,
        filename=a.file_name or disk.name,
        headers={"Content-Disposition": _content_disposition(inline, a.file_name or disk.name)},
    )


@router.get("/{aid}/page/{n}")
def page_image(aid: int, n: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """PDF 原文对照：按 ocr_dpi 渲染第 n 页为 PNG。"""
    a = db.get(Archive, aid)
    if not a or a.file_type != "pdf" or not a.file_path:
        raise HTTPException(404, "非 PDF 档案或无原件")
    disk = BASE_DIR / a.file_path
    if not disk.exists():
        raise HTTPException(404, "原件文件已丢失")
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(str(disk))
        if n < 1 or n > len(pdf):
            raise HTTPException(404, "页码越界")
        dpi = a.ocr_dpi or _OCR_DPI
        pil = pdf[n - 1].render(scale=dpi / 72.0).to_pil()
        pdf.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"渲染失败：{e}")
    buf = io.BytesIO()
    pil.save(buf, "PNG")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")


@router.put("/{aid}", response_model=ArchiveOut)
def update(aid: int, body: ArchiveIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.get(Archive, aid)
    if not a:
        raise HTTPException(404, "档案不存在")
    if body.title is not None:
        a.title = body.title
    if body.source is not None:
        a.source = body.source
    if body.category is not None:
        a.category = body.category
    if body.status is not None:
        a.status = body.status
    if body.secret_level is not None:
        a.secret_level = body.secret_level if body.secret_level in SECRET_LEVELS else a.secret_level
    if body.retention is not None:
        a.retention = body.retention if body.retention in RETENTIONS else a.retention
    db.commit()
    db.refresh(a)
    audit.write_log(db, user.id, user.username, action="archive_update",
                    resource_type="archive", resource_id=str(a.id),
                    detail={"archive_no": a.archive_no})
    return _out(a, db)
