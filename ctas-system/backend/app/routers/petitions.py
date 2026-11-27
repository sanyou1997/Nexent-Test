import os
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app import audit, tenancy
from app.config import BASE_DIR
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.llm import provider
from app.models import User, Petition, DispositionSuggestion, Case
from app.schemas import HandoffIn, PetitionCreate, PetitionUpdateIn
from app.rag import retrieve
from . import rules

router = APIRouter(prefix="/api/petitions", tags=["A2 信访分办"])

# 材料原件存储目录
MAT_DIR = BASE_DIR / "data" / "petitions"
MAT_DIR.mkdir(parents=True, exist_ok=True)

_IMG = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
_CONTENT = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
    "pdf": "application/pdf",
}


def _content_disposition(inline: bool, name: str) -> str:
    """构造可承载中文文件名的 Content-Disposition（RFC 5987 filename*）。"""
    disp = "inline" if inline else "attachment"
    ascii_name = name.encode("ascii", "ignore").decode() or "download"
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"

EXTRACT_PROMPT = (
    "你是纪检监察信访登记助手。从举报内容中抽取要素，只输出 JSON，字段："
    '{"被反映人":{"姓名":"","单位":"","职务":""},"问题类型":[六项纪律类标签],'
    '"涉及金额":"","时间":"","地点":"","举报分类":"检举控告/申诉/批评建议",'
    '"紧急程度":"高/中/低","紧急原因":""}。信息不存在则留空，不得编造。'
)

SUMMARY_PROMPT = (
    "你是纪检监察信访摘要助手。请为下列信访件编写一段 80-200 字的客观摘要，"
    "要求：①只使用原文已有事实，不得推测、不得添加人名地名之外的信息；"
    "②包含被反映人（姓名/单位/职务，原文有则写）、主要问题、涉及金额与时间（原文有则写）；"
    "③使用规范书面语，不得使用'据说''好像'等不确定表述；④只输出摘要正文，不要小标题。"
)


def _add_workdays(start: datetime, n: int) -> datetime:
    """从 start 起推算 n 个工作日后的日期（跳过周末），用于 15 工作日告知/反馈期限。"""
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _sla(p: Petition) -> dict:
    """计算受理告知 / 办结反馈 / 办理时限 三类 SLA 状态，供前端统一展示临期/逾期。"""
    now = datetime.utcnow()
    decided = p.accept_decision or ""
    in_flow = decided in ("", "受理") and p.status not in ("不予受理", "重复留存")
    res = {
        "petitioner_type": p.petitioner_type or "",
        "accept_decision": decided,
        "needs_notify": False, "notify_overdue": False, "notify_due": "",
        "needs_feedback": False, "feedback_overdue": False, "feedback_due": "",
        "handle_overdue": False, "handle_due": "",
        "supervised_at": p.supervised_at.strftime("%Y-%m-%d %H:%M") if p.supervised_at else "",
    }
    # 受理告知（实名 + 进入办理 + 未告知）：收到之日起 15 个工作日
    if p.petitioner_type == "实名" and in_flow and not p.notified_at:
        res["needs_notify"] = True
        if p.created_at:
            due = _add_workdays(p.created_at, 15)
            res["notify_due"] = due.strftime("%Y-%m-%d")
            if now > due:
                res["notify_overdue"] = True
    # 办结反馈（实名 + 进入办理 + 未反馈/未办结）：办结之日起 15 个工作日
    if p.petitioner_type == "实名" and in_flow and not p.feedback_at and p.status != "已办结":
        res["needs_feedback"] = True
        if p.created_at:
            due = _add_workdays(p.created_at, 15)
            res["feedback_due"] = due.strftime("%Y-%m-%d")
            if now > due:
                res["feedback_overdue"] = True
    # 办理时限（受理起 60 自然日，未办结即超期）：监督追责法治化"督办到位"
    if p.due_at and p.status not in ("已办结", "不予受理", "重复留存"):
        res["handle_due"] = p.due_at.strftime("%Y-%m-%d")
        if now > p.due_at:
            res["handle_overdue"] = True
    return res


def _petition_out(db: Session, p: Petition) -> dict:
    sugg = db.query(DispositionSuggestion).filter(
        DispositionSuggestion.petition_id == p.id).order_by(DispositionSuggestion.id.desc()).first()
    creator = db.get(User, p.created_by) if p.created_by else None
    return {
        "id": p.id, "code": p.code, "content": p.content, "petitioner": p.petitioner,
        "channel": p.channel, "status": p.status, "elements": p.elements,
        "labels": p.labels, "urgency": p.urgency, "duplicate_of": p.duplicate_of,
        "created_by": creator.username if creator else "",
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else "",
        "biz_type": getattr(p, "biz_type", "") or "",
        "summary": getattr(p, "summary", "") or "",
        "summary_mode": getattr(p, "summary_mode", "") or "",
        "has_material": bool(getattr(p, "file_path", "") or ""),
        "file_name": getattr(p, "file_name", "") or "",
        "file_type": getattr(p, "file_type", "") or "",
        "petitioner_type": p.petitioner_type or "",
        "contact": p.contact or "",
        "accept_decision": p.accept_decision or "",
        "accept_reason": p.accept_reason or "",
        "notified_at": p.notified_at.strftime("%Y-%m-%d %H:%M") if p.notified_at else "",
        "due_at": p.due_at.strftime("%Y-%m-%d") if p.due_at else "",
        "feedback_at": p.feedback_at.strftime("%Y-%m-%d %H:%M") if p.feedback_at else "",
        "feedback_note": p.feedback_note or "",
        "transfer_to": p.transfer_to or "",
        "source_case_id": p.source_case_id or None,
        "supervised_at": p.supervised_at.strftime("%Y-%m-%d %H:%M") if p.supervised_at else "",
        "supervise_note": p.supervise_note or "",
        "sla": _sla(p),
        "suggestion": None if not sugg else {
            "id": sugg.id, "dept": sugg.dept, "reason": sugg.reason,
            "rule_hits": sugg.rule_hits, "status": sugg.status,
            "confirmed_at": sugg.confirmed_at.strftime("%Y-%m-%d %H:%M") if sugg.confirmed_at else "",
        },
    }


@router.get("")
def list_petitions(q: str = "", status: str = "", urgency: str = "",
                   biz_type: str = "", accept: str = "", overdue: int = 0,
                   page: int = 1, per_page: int = 10,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """信访件清单：关键词搜索 + 状态/紧急度/业务类别/受理决定筛选 + 超期筛选 + 服务端分页。"""
    query = db.query(Petition)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Petition.code.like(like)) | (Petition.content.like(like)) |
            (Petition.summary.like(like)) | (Petition.petitioner.like(like))
        )
    if status:
        query = query.filter(Petition.status == status)
    if urgency:
        query = query.filter(Petition.urgency == urgency)
    if biz_type:
        query = query.filter(Petition.biz_type == biz_type)
    if accept:
        query = query.filter(Petition.accept_decision == accept)
    total = query.count()
    ps = (query.order_by(Petition.id.desc())
          .offset((max(page, 1) - 1) * max(per_page, 1)).limit(max(per_page, 1)).all())
    if not ps:
        return {"items": [], "total": total}
    # 超期筛选：在 Python 侧按 SLA 命中（避免 SQL 层重复实现工作日计算）
    if overdue:
        ps = [p for p in ps if any((_sla(p)[k] for k in ("notify_overdue", "feedback_overdue", "handle_overdue")))]
        total = len(ps)  # 超期筛选后按命中集重新计总数，保证分页准确
    # 批量预取：每条举报的最新分办建议 + 创建者，避免 N+1
    pids = [p.id for p in ps]
    uids = {p.created_by for p in ps if p.created_by}
    suggs = (db.query(DispositionSuggestion)
             .filter(DispositionSuggestion.petition_id.in_(pids))
             .order_by(DispositionSuggestion.id.desc()).all())
    latest_sugg = {}
    for s in suggs:
        if s.petition_id not in latest_sugg:  # id desc 排序后首条即最新
            latest_sugg[s.petition_id] = s
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    return {"items": [_petition_out_fast(p, latest_sugg.get(p.id), users.get(p.created_by)) for p in ps],
            "total": total}


@router.get("/stats")
def petitions_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """信访数据统计：状态 / 业务类别 / 问题类别 / 紧急程度 / 渠道 / 近 6 月趋势。"""
    ps = db.query(Petition).all()
    total = len(ps)
    by_status: dict = {}
    by_biz: dict = {}
    by_urgency: dict = {}
    by_channel: dict = {}
    by_category: dict = {}
    for p in ps:
        by_status[p.status] = by_status.get(p.status, 0) + 1
        if p.biz_type:
            by_biz[p.biz_type] = by_biz.get(p.biz_type, 0) + 1
        by_urgency[p.urgency or "中"] = by_urgency.get(p.urgency or "中", 0) + 1
        if p.channel:
            by_channel[p.channel] = by_channel.get(p.channel, 0) + 1
        for lab in (p.labels or []):
            by_category[lab] = by_category.get(lab, 0) + 1
    # 近 6 个月趋势（按创建月份）
    from collections import Counter, defaultdict
    month_counter: Counter = Counter()
    for p in ps:
        if p.created_at:
            month_counter[p.created_at.strftime("%Y-%m")] += 1
    months = []
    from datetime import datetime
    ym = datetime.now().strftime("%Y-%m")
    y, m = int(ym[:4]), int(ym[5:7])
    for _ in range(6):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()
    monthly = [{"month": mo, "count": month_counter.get(mo, 0)} for mo in months]
    # —— 效能指标：受理法治化 / 办理法治化 / 监督追责法治化 ——
    by_accept: dict = {}
    realname_total = 0
    notified_total = 0
    feedback_total = 0
    transferred_total = 0
    overdue_total = 0
    unresolved_total = 0  # 实名受理且进入办理但未办结反馈
    for p in ps:
        dec = p.accept_decision or "待甄别"
        by_accept[dec] = by_accept.get(dec, 0) + 1
        if p.petitioner_type == "实名":
            realname_total += 1
        if p.notified_at:
            notified_total += 1
        if p.feedback_at:
            feedback_total += 1
        if p.source_case_id:
            transferred_total += 1
        sla = _sla(p)
        if sla["notify_overdue"] or sla["feedback_overdue"] or sla["handle_overdue"]:
            overdue_total += 1
        if (p.petitioner_type == "实名" and dec in ("", "受理")
                and p.status not in ("不予受理", "重复留存", "已办结") and not p.feedback_at):
            unresolved_total += 1
    return {
        "total": total,
        "by_status": by_status,
        "by_biz_type": by_biz,
        "by_urgency": by_urgency,
        "by_channel": by_channel,
        "by_category": by_category,
        "monthly": monthly,
        "extracted": sum(1 for p in ps if p.status in ("已抽取", "已建议", "已分办")),
        "with_summary": sum(1 for p in ps if p.summary),
        "by_accept": by_accept,
        "realname_total": realname_total,
        "notified_total": notified_total,
        "feedback_total": feedback_total,
        "transferred_total": transferred_total,
        "overdue_total": overdue_total,
        "unresolved_total": unresolved_total,
    }


def _petition_out_fast(p: Petition, sugg, creator) -> dict:
    return {
        "id": p.id, "code": p.code, "content": p.content, "petitioner": p.petitioner,
        "channel": p.channel, "status": p.status, "elements": p.elements,
        "labels": p.labels, "urgency": p.urgency, "duplicate_of": p.duplicate_of,
        "created_by": creator.username if creator else "",
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else "",
        "biz_type": getattr(p, "biz_type", "") or "",
        "summary": getattr(p, "summary", "") or "",
        "summary_mode": getattr(p, "summary_mode", "") or "",
        "has_material": bool(getattr(p, "file_path", "") or ""),
        "file_name": getattr(p, "file_name", "") or "",
        "file_type": getattr(p, "file_type", "") or "",
        "petitioner_type": p.petitioner_type or "",
        "contact": p.contact or "",
        "accept_decision": p.accept_decision or "",
        "accept_reason": p.accept_reason or "",
        "notified_at": p.notified_at.strftime("%Y-%m-%d %H:%M") if p.notified_at else "",
        "due_at": p.due_at.strftime("%Y-%m-%d") if p.due_at else "",
        "feedback_at": p.feedback_at.strftime("%Y-%m-%d %H:%M") if p.feedback_at else "",
        "feedback_note": p.feedback_note or "",
        "transfer_to": p.transfer_to or "",
        "source_case_id": p.source_case_id or None,
        "supervised_at": p.supervised_at.strftime("%Y-%m-%d %H:%M") if p.supervised_at else "",
        "supervise_note": p.supervise_note or "",
        "sla": _sla(p),
        "suggestion": None if not sugg else {
            "id": sugg.id, "dept": sugg.dept, "reason": sugg.reason,
            "rule_hits": sugg.rule_hits, "status": sugg.status,
            "confirmed_at": sugg.confirmed_at.strftime("%Y-%m-%d %H:%M") if sugg.confirmed_at else "",
        },
    }


@router.post("")
def create_petition(data: PetitionCreate, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    # 加 4 位随机后缀，避免同秒并发登记撞 unique 约束
    code = f"XF{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"
    from app.llm.embeddings import embed_local
    # 实名/匿名：未显式填写时按举报人推断（非"匿名"即视为实名）
    ptype = data.petitioner_type or ("匿名" if (data.petitioner or "匿名") == "匿名" else "实名")
    p = Petition(code=code, content=data.content, petitioner=data.petitioner,
                 channel=data.channel, tf_vector=embed_local(data.content),
                 petitioner_type=ptype, contact=data.contact or "",
                 created_by=user.id)
    db.add(p)
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_create",
                    resource_type="petition", resource_id=p.id, detail={"code": code})
    return _petition_out(db, p)


@router.post("/parse")
async def parse_material(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """信访材料识别：图片走本机 OCR，PDF 走 MinerU/文本抽取 → 返回纯文本。
    识别结果仅用于填充登记内容，须人工核对。"""
    data = await file.read()
    name = (file.filename or "").lower()
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(413, "材料超过 30MB 限制")
    text = ""
    if name.endswith(".pdf"):
        from app.rag.pdf_extract import extract_pdf
        try:
            res = extract_pdf(data, file.filename)
        except Exception as e:
            raise HTTPException(422, f"PDF 解析失败：{e}")
        if res.get("encrypted"):
            # 真正加密时才提示解密，避免误报
            raise HTTPException(400, "PDF 已加密，请先解密/去除密码后再上传")
        text = res.get("text", "")
        if not text.strip():
            raise HTTPException(400, "未识别到文字：可能是纯图片扫描件且清晰度不足，请确认材料清晰度后重试")
        return {"text": text.strip(), "chars": len(text.strip()), "filename": file.filename,
                "pages": res.get("pages", []), "engine": res.get("engine", ""),
                "note": res.get("note", "")}
    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="ignore")
    else:
        from app.routers.ocr import ocr_image_bytes
        text = ocr_image_bytes(data)
    if not text.strip():
        raise HTTPException(400, "未识别到文字，请确认材料清晰度")
    return {"text": text.strip(), "chars": len(text.strip()), "filename": file.filename}


@router.post("/{pid}/material")
async def upload_material(pid: int, file: UploadFile = File(...),
                          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """留存信访材料原件（PDF / 图片），供办理时回溯核对。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    data = await file.read()
    if not data:
        raise HTTPException(400, "上传文件为空")
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(413, "材料超过 30MB 限制")
    name = file.filename or ""
    ext = (name.rsplit(".", 1)[-1] or "bin").lower()
    sub = MAT_DIR / str(pid)
    sub.mkdir(parents=True, exist_ok=True)
    disk = sub / f"{uuid.uuid4().hex[:10]}.{ext}"
    disk.write_bytes(data)
    p.file_path = str(disk.relative_to(BASE_DIR)).replace("\\", "/")
    p.file_name = name
    p.file_type = ext
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_material_upload",
                    resource_type="petition", resource_id=pid, detail={"file": name, "size": len(data)})
    return _petition_out(db, p)


@router.get("/{pid}/material")
def get_material(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """回传材料原件：图片/PDF 内联预览，其余作为附件下载。"""
    p = db.get(Petition, pid)
    if not p or not p.file_path:
        raise HTTPException(404, "材料原件不存在")
    disk = BASE_DIR / p.file_path
    if not disk.exists():
        raise HTTPException(404, "材料原件文件已丢失")
    ctype = _CONTENT.get(p.file_type or "", "application/octet-stream")
    inline = (p.file_type or "") in _IMG or (p.file_type or "") == "pdf"
    return FileResponse(
        str(disk), media_type=ctype, filename=p.file_name or disk.name,
        headers={"Content-Disposition": _content_disposition(inline, p.file_name or disk.name)},
    )


@router.get("/export")
def export_xlsx(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """导出信访台账为 xlsx（utf-8，Excel 直接打开）。"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "信访台账"
    ws.append(["编号", "业务类别", "问题分类", "摘要", "紧急程度", "状态",
               "举报人", "渠道", "建议承办部门", "分办状态", "登记人", "登记时间"])
    rows = db.query(Petition).order_by(Petition.id.desc()).all()
    for p in rows:
        sugg = db.query(DispositionSuggestion).filter(
            DispositionSuggestion.petition_id == p.id).order_by(DispositionSuggestion.id.desc()).first()
        creator = db.get(User, p.created_by) if p.created_by else None
        ws.append([
            p.code, p.biz_type or "", "、".join(p.labels or []), p.summary or "",
            p.urgency or "中", p.status, p.petitioner or "", p.channel or "",
            sugg.dept if sugg else "", sugg.status if sugg else "",
            creator.username if creator else "",
            p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else "",
        ])
    import io as _io
    buf = _io.BytesIO()
    wb.save(buf)
    fname = f"信访台账_{datetime.now().strftime('%Y%m%d')}.xlsx"
    audit.write_log(db, user.id, user.username, action="petition_export",
                    resource_type="petition", resource_id="export", detail={"count": len(rows)})
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(False, fname)},
    )


@router.get("/{pid}")
def get_petition(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """单件详情（工作台待办 / 深链接 /petitions?open=<id> 直达办理）。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "信访件不存在")
    return _petition_out(db, p)


@router.post("/{pid}/classify")
def classify(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """信访件分类：业务类别（检举控告/申诉/批评建议）+ 问题类别（六项纪律/职务违法犯罪）。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    res = rules.rule_classify(p.content)
    p.labels = res["categories"]
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_classify",
                    resource_type="petition", resource_id=pid,
                    detail={"biz": res["biz_type"], "cats": res["categories"]})
    return res


def _make_summary(p: Petition, user_id) -> tuple[str, str]:
    """摘要：场景智能体在规则摘要基础上改写（不增加事实）；智能体不可用时用规则摘要。"""
    base = rules.rule_summary(p.content)
    gen = provider.chat(
        [{"role": "system", "content": SUMMARY_PROMPT},
         {"role": "user", "content": f"举报原文：\n{p.content}\n\n规则摘要（供参考，不得增加事实）：\n{base}"}],
        scene="petition_summary", user_id=user_id)
    return (gen.strip(), "llm") if gen else (base, "rules")


@router.post("/{pid}/summarize")
def summarize(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """信访件摘要编写：离线按信息密度摘取关键句；配置大模型时由其润色（不改变事实）。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    base, mode = _make_summary(p, user.id)
    p.summary, p.summary_mode = base, mode
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_summarize",
                    resource_type="petition", resource_id=pid, detail={"mode": mode, "chars": len(base)})
    return {"summary": base, "mode": mode}


@router.post("/{pid}/extract")
def extract(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    elements = provider.chat_json(
        [{"role": "system", "content": EXTRACT_PROMPT},
         {"role": "user", "content": p.content}],
        scene="extract", user_id=user.id)
    mode = "llm"
    if not elements:
        elements = rules.rule_extract(p.content)
        mode = "rules"
    urgency, urgency_hits = rules.rule_urgency(p.content)
    if elements.get("紧急程度") not in ("高", "中", "低"):
        elements["紧急程度"] = urgency
    if elements.get("紧急原因") and not urgency_hits:
        urgency_hits = [{"label": str(elements.get("紧急原因")), "keyword": "LLM判定"}]
    p.elements = elements
    p.labels = elements.get("问题类型") or []
    p.urgency = elements.get("紧急程度") or "中"
    p.status = "已抽取"
    # 同步产出分类与摘要，便于"一键整理"
    cls = rules.rule_classify(p.content)
    p.biz_type = cls["biz_type"]
    if not p.labels:
        p.labels = cls["categories"]
    # 「一键整理」承诺产出摘要：未有摘要或只有规则摘要时，同样交给场景智能体生成（失败回落规则）
    if not p.summary or p.summary_mode != "llm":
        p.summary, p.summary_mode = _make_summary(p, user.id)
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_extract",
                    resource_type="petition", resource_id=pid,
                    detail={"mode": mode, "urgency_hits": urgency_hits})
    return {"mode": mode, "elements": elements, "urgency_hits": urgency_hits,
            "classify": cls, "summary": p.summary}


@router.post("/{pid}/suggest")
def suggest(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = db.get(Petition, pid)
    if not p or not p.elements:
        raise HTTPException(400, "请先完成要素抽取")
    elements = p.elements if isinstance(p.elements, dict) else {}
    rule, hits = rules.rule_disposition(elements)
    sugg = DispositionSuggestion(
        petition_id=p.id, dept=rule["dept"], reason=rule["reason"], rule_hits=hits)
    db.add(sugg)
    db.flush()
    p.status = "已建议"
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_suggest",
                    resource_type="petition", resource_id=pid,
                    detail={"dept": rule["dept"], "rule": rule["name"]})
    return {"dept": rule["dept"], "reason": rule["reason"], "rule_hits": hits}


@router.post("/{pid}/confirm")
def confirm(pid: int, db: Session = Depends(get_db),
            user: User = Depends(require_roles("xinfang", "admin"))):
    """人工确认分办 —— AI 只到建议为止，流转必须人工确认（核心红线）。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    sugg = db.query(DispositionSuggestion).filter(
        DispositionSuggestion.petition_id == pid,
        DispositionSuggestion.status == "待确认").order_by(DispositionSuggestion.id.desc()).first()
    if not sugg:
        raise HTTPException(400, "无可确认的分办建议")
    sugg.status = "已确认"
    sugg.confirmed_by = user.id
    sugg.confirmed_at = datetime.utcnow()
    p.status = "已分办"
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_confirm",
                    resource_type="petition", resource_id=pid,
                    detail={"dept": sugg.dept})
    return _petition_out(db, p)


@router.post("/{pid}/reject")
def reject(pid: int, reason: str = "", db: Session = Depends(get_db),
           user: User = Depends(require_roles("xinfang", "admin"))):
    """人工驳回分办建议 —— AI 建议可不被采纳，是否采纳由人决定。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    sugg = db.query(DispositionSuggestion).filter(
        DispositionSuggestion.petition_id == pid,
        DispositionSuggestion.status == "待确认").order_by(DispositionSuggestion.id.desc()).first()
    if not sugg:
        raise HTTPException(400, "无可驳回的分办建议")
    sugg.status = "已驳回"
    sugg.confirmed_by = user.id
    sugg.confirmed_at = datetime.utcnow()
    sugg.reason = f"{sugg.reason or ''}\n【人工驳回】{reason}".strip()
    p.status = "已抽取"
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_reject",
                    resource_type="petition", resource_id=pid,
                    detail={"dept": sugg.dept, "reason": reason})
    return _petition_out(db, p)


class ScreenIn(BaseModel):
    decision: str                 # 受理 / 不予受理 / 重复留存
    reason: str = ""             # 不予受理原因 / 重复留存说明
    duplicate_of: int | None = None


class FeedbackIn(BaseModel):
    note: str = ""               # 办结反馈内容


class SuperviseIn(BaseModel):
    note: str = ""               # 督办意见


@router.post("/{pid}/screen")
def screen(pid: int, body: ScreenIn, db: Session = Depends(get_db),
           user: User = Depends(require_roles("xinfang", "admin"))):
    """研判甄别：受理 / 不予受理 / 重复留存（受理法治化"分清性质、明确管辖"）。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    if body.decision not in ("受理", "不予受理", "重复留存"):
        raise HTTPException(400, "甄别结论应为：受理 / 不予受理 / 重复留存")
    p.accept_decision = body.decision
    p.accept_reason = body.reason
    if body.decision == "不予受理":
        p.status = "不予受理"
    elif body.decision == "重复留存":
        p.status = "重复留存"
        if body.duplicate_of:
            p.duplicate_of = body.duplicate_of
    else:  # 受理：默认办理期限 60 自然日（监督追责法治化"督办到位"）
        if not p.due_at and p.created_at:
            p.due_at = p.created_at + timedelta(days=60)
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_screen",
                    resource_type="petition", resource_id=pid,
                    detail={"decision": body.decision, "reason": body.reason})
    return _petition_out(db, p)


@router.post("/{pid}/notify")
def notify(pid: int, db: Session = Depends(get_db),
           user: User = Depends(require_roles("xinfang", "admin"))):
    """受理告知：实名检举控告收到之日起 15 个工作日内书面告知（受理法治化核心）。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    if p.petitioner_type != "实名":
        raise HTTPException(400, "仅实名举报须作受理告知")
    if p.accept_decision == "不予受理":
        raise HTTPException(400, "不予受理件无需告知")
    if p.notified_at:
        raise HTTPException(400, "该件已作受理告知")
    # SLA 校验：超 15 工作日仍可补登，但回传 overdue 供前端提示
    was_overdue = False
    if p.created_at and datetime.utcnow() > _add_workdays(p.created_at, 15):
        was_overdue = True
    p.notified_at = datetime.utcnow()
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_notify",
                    resource_type="petition", resource_id=pid,
                    detail={"was_overdue": was_overdue})
    return {**_petition_out(db, p), "was_overdue": was_overdue}


@router.post("/{pid}/feedback")
def feedback(pid: int, body: FeedbackIn, db: Session = Depends(get_db),
             user: User = Depends(require_roles("xinfang", "admin"))):
    """办结反馈：实名检举控告办结之日起 15 个工作日内向举报人反馈（办理法治化）。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    if p.petitioner_type != "实名":
        raise HTTPException(400, "仅实名举报须作办结反馈")
    if p.accept_decision == "不予受理":
        raise HTTPException(400, "不予受理件无需反馈")
    if p.status == "已办结":
        raise HTTPException(400, "该件已办结反馈")
    was_overdue = False
    if p.created_at and datetime.utcnow() > _add_workdays(p.created_at, 15):
        was_overdue = True
    p.feedback_at = datetime.utcnow()
    p.feedback_note = body.note
    p.status = "已办结"
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_feedback",
                    resource_type="petition", resource_id=pid,
                    detail={"was_overdue": was_overdue, "note": body.note})
    return {**_petition_out(db, p), "was_overdue": was_overdue}


@router.post("/{pid}/supervise")
def supervise(pid: int, body: SuperviseIn, db: Session = Depends(get_db),
              user: User = Depends(require_roles("xinfang", "admin"))):
    """督办：对推诿拖延、超期未办件发函督办（监督追责法治化"督办到位"）。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    p.supervised_at = datetime.utcnow()
    p.supervise_note = body.note
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_supervise",
                    resource_type="petition", resource_id=pid, detail={"note": body.note})
    return _petition_out(db, p)


@router.post("/{pid}/transfer-case")
def transfer_case(pid: int, body: HandoffIn | None = None, db: Session = Depends(get_db),
                  user: User = Depends(require_roles("xinfang", "admin"))):
    """转案管线索：高价值检举控告一键转入案管（问题线索），复用同一 Case 实体，全链路可溯。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    if p.source_case_id:
        raise HTTPException(400, "该信访件已转线索（案号关联见详情）")
    el = p.elements if isinstance(p.elements, dict) else {}
    person = el.get("被反映人", {}) if isinstance(el, dict) else {}
    subject = (person.get("姓名") if isinstance(person, dict) else "") or (p.petitioner if p.petitioner != "匿名" else "")
    unit = (person.get("单位") if isinstance(person, dict) else "") or (person.get("职务") if isinstance(person, dict) else "")
    title = p.summary or (p.content[:80] if p.content else "")
    code = f"AJ{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:3].upper()}"
    target = tenancy.route_to(db, user, tenancy.CLUE_DESK, body.dept_id if body else None)
    c = Case(code=code, source="信访", subject=subject, unit=unit, title=title,
             content=p.content, problem_type="、".join(p.labels or []), urgency=p.urgency or "中",
             linked_petition_id=p.id, created_by=user.id, dept_id=target)
    db.add(c)
    db.flush()
    tenancy.link_handoff(db, p, c, "信访转线索", user)
    p.source_case_id = c.id
    p.transfer_to = "案管线索"
    if p.status not in ("不予受理", "重复留存", "已办结"):
        p.status = "已分办"
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_transfer_case",
                    resource_type="petition", resource_id=pid,
                    detail={"case_id": c.id, "case_code": c.code, "dept": tenancy.dept_name(db, target)})
    return {**_petition_out(db, p), "case_id": c.id, "case_code": c.code, "dept_name": tenancy.dept_name(db, target)}


@router.put("/{pid}")
def update(pid: int, body: PetitionUpdateIn, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """人工修订摘要/分类（辅助结果的留痕修订）。"""
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    changed = []
    if body.summary is not None:
        p.summary = body.summary
        p.summary_mode = "manual"
        changed.append("summary")
    if body.biz_type:
        p.biz_type = body.biz_type
        changed.append("biz_type")
    if body.labels is not None:
        p.labels = body.labels
        changed.append("labels")
    db.commit()
    audit.write_log(db, user.id, user.username, action="petition_update",
                    resource_type="petition", resource_id=pid, detail={"changed": changed})
    return _petition_out(db, p)


@router.post("/{pid}/check-duplicate")
def check_duplicate(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = db.get(Petition, pid)
    if not p:
        raise HTTPException(404, "举报不存在")
    others = db.query(Petition).filter(Petition.id != pid).all()
    sims = []
    from app.llm.embeddings import embed_local, cosine_sparse
    qv = p.tf_vector or embed_local(p.content)
    if not p.tf_vector:
        # 旧数据补全向量，下次查重可直接复用
        p.tf_vector = qv
        db.commit()
    for o in others:
        ov = o.tf_vector or embed_local(o.content)
        score = cosine_sparse(qv, ov)
        if score > 0.25:
            sims.append({"id": o.id, "code": o.code, "score": round(score, 3),
                         "excerpt": o.content[:60]})
    sims.sort(key=lambda x: -x["score"])
    if sims:
        p.duplicate_of = sims[0]["id"]
        db.commit()
    audit.write_log(db, user.id, user.username, action="petition_dup_check",
                    resource_type="petition", resource_id=pid,
                    detail={"similar_count": len(sims)})
    return {"similar": sims[:5], "hint": "疑似重复举报，合并办理须由人工决定" if sims else "未发现相似举报"}
