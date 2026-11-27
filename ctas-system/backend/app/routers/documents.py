from datetime import datetime
import io
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session
from app import audit, gongwen
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.llm import provider
from app.models import User, Document, DocumentTemplate, DocumentVersion, Case, ConductCase
from app.schemas import DocumentGenerateIn, DocumentUpdateIn

router = APIRouter(prefix="/api/documents", tags=["A3 文书生成"])

POLISH_PROMPT = (
    "你是纪检监察文书助手。以下是一份按模板填充的文书初稿，请在保持事实要素不变的前提下，"
    "规范表述、补足公文语言，输出完整文书正文。保持原有层级结构（一、（一）等），"
    "不得增加模板之外的事实性内容，不得输出任何解释性文字。"
)

_DATE_RE = re.compile(r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?$")
_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
RULE_TYPES = ("text", "date", "amount", "number")

# 字段名 → 案件要素的别名词典（用于"从案件带入"智能匹配）
_FIELD_ALIASES = {
    "subject": ["被审查人", "被反映人", "被调查人", "审查对象", "调查对象", "核查对象",
                "姓名", "对象", "当事人", "人员", "涉案人员"],
    "unit": ["单位", "所在单位", "工作单位", "所在部门", "所在地区"],
    "title": ["案由", "问题类别", "标题", "事项", "问题", "案件名称", "事由", "问题概要"],
    "code": ["编号", "线索编号", "案件编号", "文号"],
    "source": ["线索来源", "来源", "案件来源"],
    "category": ["类别", "问题类型", "违纪类型"],
    "status": ["状态", "办理状态"],
    "created_at": ["登记时间", "受理时间", "立案时间"],
}


def _auto_rules(keys) -> dict:
    """按字段名智能推断校验规则（类型/必填/提示），可人工覆盖。"""
    rules = {}
    for k in keys:
        r = {"required": False, "type": "text", "max_len": 0, "hint": ""}
        if any(s in k for s in ("日期", "时间", "期限", "年限")):
            r["type"] = "date"; r["hint"] = "格式：2024-01-31"
        elif any(s in k for s in ("金额", "数额", "款项", "赃款", "违法所得", "价值", "钱款")):
            r["type"] = "amount"; r["hint"] = "格式：123456.78"
        elif any(s in k for s in ("数量", "人数", "条数", "次数")):
            r["type"] = "number"; r["hint"] = "格式：整数"
        if any(s in k for s in ("被审查人", "被反映人", "被调查人", "审查对象",
                                "调查对象", "核查对象", "姓名", "案由", "单位")):
            r["required"] = True
        rules[k] = r
    return rules


def _validate_fields(tpl: DocumentTemplate, fields: dict) -> dict:
    """按 field_rules 校验填写内容：返回缺失必填项 + 格式错误清单。"""
    rules = tpl.field_rules or {}
    fields = fields or {}
    missing, invalid = [], []
    for f in (tpl.fields or []):
        key = f.get("key") or ""
        rule = rules.get(key) or {}
        label = f.get("label") or key
        val = str(fields.get(key) or "").strip()
        if not val:
            if rule.get("required"):
                missing.append({"key": key, "label": label})
            continue
        typ = rule.get("type") or "text"
        if typ == "date" and not _DATE_RE.match(val):
            invalid.append({"key": key, "label": label,
                            "message": f"{label}：日期格式不正确（应为 2024-01-31）"})
        elif typ == "amount" and not _NUM_RE.match(val):
            invalid.append({"key": key, "label": label, "message": f"{label}：金额应为数字（可含小数）"})
        elif typ == "number" and not _NUM_RE.match(val):
            invalid.append({"key": key, "label": label, "message": f"{label}：应为数字"})
        ml = int(rule.get("max_len") or 0)
        if ml and len(val) > ml:
            invalid.append({"key": key, "label": label, "message": f"{label}：超长（≤{ml} 字）"})
    return {"missing": missing, "invalid": invalid, "ok": not missing and not invalid}


def _doc_out(db: Session, d: Document) -> dict:
    tpl = db.get(DocumentTemplate, d.template_id) if d.template_id else None
    creator = db.get(User, d.created_by) if d.created_by else None
    confirmer = db.get(User, d.confirmed_by) if d.confirmed_by else None
    versions = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == d.id).order_by(DocumentVersion.version_no).all()
    return {
        "id": d.id, "title": d.title, "case_name": d.case_name, "content": d.content,
        "status": d.status, "template_name": tpl.name if tpl else "",
        "created_by": creator.real_name or creator.username if creator else "",
        "confirmed_by": confirmer.real_name or confirmer.username if confirmer else "",
        "confirmed_at": d.confirmed_at.strftime("%Y-%m-%d %H:%M") if d.confirmed_at else "",
        "created_at": d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else "",
        "source": d.source or ("模板生成" if d.template_id else "流程生成"),
        "versions": [{"version_no": v.version_no, "note": v.note, "source": v.source or ("人工" if v.version_no > 1 else "系统"),
                      "content": v.content,
                      "created_at": v.created_at.strftime("%Y-%m-%d %H:%M") if v.created_at else ""}
                     for v in versions],
    }


def _tpl_out(t: DocumentTemplate, doc_count: int | None = None) -> dict:
    return {
        "id": t.id, "name": t.name, "doc_type": t.doc_type,
        "template": t.template, "fields": t.fields or [],
        "source": t.source or "自定义", "field_rules": t.field_rules or {},
        "pinned": bool(t.pinned),
        "doc_count": doc_count if doc_count is not None else -1,
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "",
    }


def _extract_fields(raw: str) -> list:
    """从模板文本提取 {{字段}} 占位符，生成字段清单 [{key,label,placeholder}]。"""
    keys = list(dict.fromkeys(k.strip() for k in re.findall(r"\{\{([^{}\n]+)\}\}", raw) if k.strip()))
    return [{"key": k, "label": k, "placeholder": f"请填写{k}"} for k in keys]


@router.get("/templates")
def list_templates(q: str = "", doc_type: str = "", db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """模板库列表（含字段清单与校验规则，供文书校验页「模板库」使用）。

    支持按名称/类型搜索；置顶模板优先，其余按 id 正序。"""
    query = db.query(DocumentTemplate)
    if q:
        like = f"%{q}%"
        query = query.filter((DocumentTemplate.name.like(like)) |
                             (DocumentTemplate.doc_type.like(like)))
    if doc_type:
        query = query.filter(DocumentTemplate.doc_type == doc_type)
    tpls = query.all()
    # 批量统计每个模板已生成的文书数（避免逐模板 count）
    tids = [t.id for t in tpls]
    counts: dict = {}
    if tids:
        for tid, n in (db.query(Document.template_id, sa_func.count(Document.id))
                       .filter(Document.template_id.in_(tids))
                       .group_by(Document.template_id).all()):
            counts[tid] = n
    out = [_tpl_out(t, counts.get(t.id, 0)) for t in tpls]
    out.sort(key=lambda x: (not x["pinned"], x["id"]))
    return out


class TemplateIn(BaseModel):
    name: str
    doc_type: str = "自定义"
    template: str


@router.post("/templates")
def create_template(data: TemplateIn, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """从文本新增模板（{{字段}} 占位符自动解析为字段清单）。"""
    raw = (data.template or "").replace("\r\n", "\n").strip()
    if not raw:
        raise HTTPException(400, "模板内容不能为空")
    if len(raw) > 20000:
        raise HTTPException(400, "模板内容过长（≤20000 字）")
    name = (data.name or "").strip()[:60] or "未命名模板"
    keys = [f["key"] for f in _extract_fields(raw)]
    tpl = DocumentTemplate(name=name, doc_type=(data.doc_type or "自定义")[:30],
                           template=raw, fields=_extract_fields(raw),
                           source="自定义", field_rules=_auto_rules(keys))
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    audit.write_log(db, user.id, user.username, action="template_create",
                    resource_type="template", resource_id=tpl.id,
                    detail={"name": name, "fields": len(tpl.fields or [])})
    return _tpl_out(tpl)


class TemplateUpdateIn(BaseModel):
    name: str | None = None
    doc_type: str | None = None
    template: str | None = None


@router.put("/templates/{tid}")
def update_template(tid: int, data: TemplateUpdateIn, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """编辑模板（改内容时自动重算字段清单）。"""
    tpl = db.get(DocumentTemplate, tid)
    if not tpl:
        raise HTTPException(404, "模板不存在")
    if data.name is not None:
        tpl.name = (data.name or "").strip()[:60] or tpl.name
    if data.doc_type is not None:
        tpl.doc_type = (data.doc_type or "自定义")[:30]
    if data.template is not None:
        raw = (data.template or "").replace("\r\n", "\n").strip()
        if not raw:
            raise HTTPException(400, "模板内容不能为空")
        tpl.template = raw
        tpl.fields = _extract_fields(raw)
        # 字段变化时同步校验规则：老字段保留原规则，新字段补默认规则，删掉的字段一并清理
        old_rules = tpl.field_rules or {}
        new_rules = _auto_rules([f["key"] for f in tpl.fields])
        new_rules.update({k: v for k, v in old_rules.items() if k in new_rules})
        tpl.field_rules = new_rules
    db.commit()
    audit.write_log(db, user.id, user.username, action="template_update",
                    resource_type="template", resource_id=tid, detail={"name": tpl.name})
    return _tpl_out(tpl)


class TemplateRenderIn(BaseModel):
    fields: dict = {}


@router.post("/templates/{tid}/render")
def render_template(tid: int, data: TemplateRenderIn, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """按字段填写渲染模板文本（不生成正式文书，供校验页「使用模板」预填编辑器）。"""
    tpl = db.get(DocumentTemplate, tid)
    if not tpl:
        raise HTTPException(404, "模板不存在")
    content = tpl.template
    for k, v in (data.fields or {}).items():
        content = content.replace("{{" + k + "}}", (v or "").strip())
    # 未填写的占位符统一标记，避免残留占位符直接进正文
    content = re.sub(r"\{\{[^{}\n]+\}\}", "〔待补充〕", content)
    audit.write_log(db, user.id, user.username, action="template_render",
                    resource_type="template", resource_id=tid,
                    detail={"name": tpl.name, "fields": len(data.fields or {})})
    return {"content": content, "template_name": tpl.name,
            "pending": content.count("〔待补充〕")}


@router.post("/templates/upload")
async def upload_template(
    file: UploadFile = File(...),
    name: str = Form(""),
    doc_type: str = Form("自定义"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "shenli", "banan", "xinfang")),
):
    """上传自定义模板（.docx 或 .txt），自动解析 {{占位符}} 生成字段清单。"""
    fname = (file.filename or "").lower()
    data = await file.read()
    if not data:
        raise HTTPException(400, "上传文件为空")
    try:
        if fname.endswith(".docx"):
            from docx import Document as Docx
            d = Docx(io.BytesIO(data))
            parts = [p.text for p in d.paragraphs if p.text.strip()]
            for tbl in d.tables:  # 表格内的占位符也提取
                for row in tbl.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            parts.append(cell.text.strip())
            raw = "\n".join(parts)
        elif fname.endswith((".txt", ".md")):
            raw = data.decode("utf-8", errors="ignore")
        else:
            raise HTTPException(400, "仅支持 .docx / .txt 模板文件")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"模板解析失败：{e}")

    raw = raw.replace("\r\n", "\n")
    keys = list(dict.fromkeys(re.findall(r"\{\{([^{}\n]+)\}\}", raw)))
    if not keys:
        raise HTTPException(400, "模板中未发现 {{字段}} 占位符，请用 {{字段名}} 标记可填写内容")
    fields = [{"key": k.strip(), "label": k.strip(),
               "placeholder": f"请填写{k.strip()}"} for k in keys]
    tpl_name = (name or re.sub(r"\.[^.]+$", "", file.filename or "自定义模板"))[:60]
    tpl = DocumentTemplate(name=tpl_name, doc_type=doc_type or "自定义",
                           template=raw, fields=fields,
                           source="上传", field_rules=_auto_rules([k.strip() for k in keys]))
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    audit.write_log(db, user.id, user.username, action="template_upload",
                    resource_type="template", resource_id=tpl.id,
                    detail={"name": tpl_name, "fields": len(keys)})
    return {"id": tpl.id, "name": tpl.name, "doc_type": tpl.doc_type, "fields": fields}


@router.delete("/templates/{tid}")
def delete_template(tid: int, db: Session = Depends(get_db),
                    user: User = Depends(require_roles("admin"))):
    tpl = db.get(DocumentTemplate, tid)
    if not tpl:
        raise HTTPException(404, "模板不存在")
    if db.query(Document).filter(Document.template_id == tid).count():
        raise HTTPException(400, "该模板已生成过文书，不可删除")
    db.delete(tpl)
    db.commit()
    audit.write_log(db, user.id, user.username, action="template_delete",
                    resource_type="template", resource_id=tid)
    return {"ok": True}


# ---------------- 字段校验规则配置与生成前预检 ----------------
class TemplateRulesIn(BaseModel):
    rules: dict = {}
    auto: bool = False


@router.put("/templates/{tid}/rules")
def set_template_rules(tid: int, data: TemplateRulesIn, db: Session = Depends(get_db),
                       user: User = Depends(require_roles("admin", "shenli", "banan"))):
    """配置模板字段校验规则（必填/类型/长度）；auto=true 时按字段名智能重推。"""
    tpl = db.get(DocumentTemplate, tid)
    if not tpl:
        raise HTTPException(404, "模板不存在")
    keys = [f["key"] for f in (tpl.fields or []) if f.get("key")]
    if data.auto:
        tpl.field_rules = _auto_rules(keys)
    else:
        rules = {}
        for k, v in (data.rules or {}).items():
            if k not in keys:
                continue
            typ = (v or {}).get("type") or "text"
            if typ not in RULE_TYPES:
                typ = "text"
            rules[k] = {
                "required": bool((v or {}).get("required", False)),
                "type": typ,
                "max_len": max(0, int((v or {}).get("max_len") or 0)),
                "hint": str((v or {}).get("hint") or "")[:60],
            }
        tpl.field_rules = rules
    db.commit()
    audit.write_log(db, user.id, user.username, action="template_rules",
                    resource_type="template", resource_id=tid,
                    detail={"auto": data.auto, "rules": len(tpl.field_rules or {})})
    return _tpl_out(tpl)


class FieldCheckIn(BaseModel):
    fields: dict = {}


@router.post("/templates/{tid}/check-fields")
def check_fields(tid: int, data: FieldCheckIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """生成前预检：返回缺失必填项与格式错误清单（让"数据是否填全"显式可见）。"""
    tpl = db.get(DocumentTemplate, tid)
    if not tpl:
        raise HTTPException(404, "模板不存在")
    return _validate_fields(tpl, data.fields or {})


# ---------------- 模板复制与置顶 ----------------
@router.post("/templates/{tid}/clone")
def clone_template(tid: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """复制模板（字段清单与校验规则一并复制），便于在既有模板基础上改造新版。"""
    src = db.get(DocumentTemplate, tid)
    if not src:
        raise HTTPException(404, "模板不存在")
    copy = DocumentTemplate(
        name=f"{src.name}（副本）"[:60], doc_type=src.doc_type,
        template=src.template, fields=[dict(f) for f in (src.fields or [])],
        source=src.source or "自定义",
        field_rules={k: dict(v) for k, v in (src.field_rules or {}).items()},
        pinned=False,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    audit.write_log(db, user.id, user.username, action="template_clone",
                    resource_type="template", resource_id=copy.id,
                    detail={"from": tid, "name": copy.name})
    return _tpl_out(copy)


@router.post("/templates/{tid}/pin")
def pin_template(tid: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """切换常用置顶（置顶模板在模板库中排在最前）。"""
    tpl = db.get(DocumentTemplate, tid)
    if not tpl:
        raise HTTPException(404, "模板不存在")
    tpl.pinned = not bool(tpl.pinned)
    db.commit()
    audit.write_log(db, user.id, user.username, action="template_pin",
                    resource_type="template", resource_id=tid, detail={"pinned": tpl.pinned})
    return {"id": tpl.id, "pinned": bool(tpl.pinned)}


# ---------------- 案件要素联动：从案管/党风带入 ----------------
@router.get("/case-sources")
def case_sources(q: str = "", db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """可带入的案件要素来源清单（案管案件 + 党风政风问题事项）。"""
    out = []
    cq = db.query(Case)
    if q:
        like = f"%{q}%"
        cq = cq.filter((Case.title.like(like)) | (Case.subject.like(like)) | (Case.code.like(like)))
    for c in cq.order_by(Case.id.desc()).limit(50).all():
        out.append({"case_type": "case", "id": c.id, "code": c.code or "",
                    "title": c.title or "", "subject": c.subject or "",
                    "unit": c.unit or "", "category": "案管线索",
                    "status": c.status or ""})
    hq = db.query(ConductCase)
    if q:
        like = f"%{q}%"
        hq = hq.filter((ConductCase.title.like(like)) | (ConductCase.subject.like(like)) |
                       (ConductCase.code.like(like)))
    for h in hq.order_by(ConductCase.id.desc()).limit(50).all():
        out.append({"case_type": "conduct", "id": h.id, "code": h.code or "",
                    "title": h.title or "", "subject": h.subject or "",
                    "unit": h.unit or "", "category": h.category or "",
                    "status": h.status or ""})
    return out


@router.get("/case-sources/{case_type}/{case_id}/fields")
def case_source_fields(case_type: str, case_id: int, template_id: int = 0,
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """按模板字段把案件要素映射为可带入的填写值（别名匹配，未命中返回空）。"""
    if case_type == "case":
        obj = db.get(Case, case_id)
    elif case_type == "conduct":
        obj = db.get(ConductCase, case_id)
    else:
        raise HTTPException(400, "case_type 仅支持 case / conduct")
    if not obj:
        raise HTTPException(404, "案件/事项不存在")
    canonical = {
        "subject": getattr(obj, "subject", "") or "",
        "unit": getattr(obj, "unit", "") or "",
        "title": getattr(obj, "title", "") or "",
        "code": getattr(obj, "code", "") or "",
        "source": getattr(obj, "source", "") or "",
        "category": getattr(obj, "category", "") or "",
        "status": getattr(obj, "status", "") or "",
        "created_at": obj.created_at.strftime("%Y-%m-%d") if getattr(obj, "created_at", None) else "",
    }
    values, matched = {}, []
    if template_id:
        tpl = db.get(DocumentTemplate, template_id)
        if tpl:
            for f in (tpl.fields or []):
                key = (f.get("key") or "").strip()
                if not key:
                    continue
                val = ""
                for canon, aliases in _FIELD_ALIASES.items():
                    if key in aliases or any(a in key for a in aliases):
                        val = canonical.get(canon, "")
                        break
                values[key] = val
                if val:
                    matched.append(key)
    return {"case_type": case_type, "case_id": case_id, "values": values,
            "matched": matched, "source_label": f"{obj.code or ''} {obj.title or ''}".strip()}


@router.get("")
def list_docs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # 行级权限演示：办案人员/审理室仅见本人相关，管理员与信访室全局可见
    q = db.query(Document).order_by(Document.id.desc())
    if user.role in ("banan", "shenli"):
        q = q.filter(Document.created_by == user.id)
    docs = q.all()
    if not docs:
        return []
    # 批量预取模板/创建者/确认者/版本，消除 N+1
    tids = {d.template_id for d in docs if d.template_id}
    uids = {d.created_by for d in docs if d.created_by} | {d.confirmed_by for d in docs if d.confirmed_by}
    dids = [d.id for d in docs]
    tpls = {t.id: t for t in db.query(DocumentTemplate).filter(DocumentTemplate.id.in_(tids)).all()} if tids else {}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    vers = (db.query(DocumentVersion).filter(DocumentVersion.document_id.in_(dids))
            .order_by(DocumentVersion.document_id, DocumentVersion.version_no).all())
    vers_by_doc = {}
    for v in vers:
        vers_by_doc.setdefault(v.document_id, []).append(v)
    out = []
    for d in docs:
        out.append(_doc_out_fast(d, tpls.get(d.template_id), users.get(d.created_by),
                                 users.get(d.confirmed_by), vers_by_doc.get(d.id, [])))
    return out


@router.get("/lite")
def list_docs_lite(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """轻量列表：仅返回 id / title / content，供文书校验页「从已生成文书导入」下拉使用。"""
    q = db.query(Document).order_by(Document.id.desc())
    if user.role in ("banan", "shenli"):
        q = q.filter(Document.created_by == user.id)
    return [{"id": d.id, "title": d.title, "content": d.content} for d in q.all()]


def _doc_out_fast(d: Document, tpl, creator, confirmer, versions: list) -> dict:
    return {
        "id": d.id, "title": d.title, "case_name": d.case_name, "content": d.content,
        "status": d.status, "template_name": tpl.name if tpl else "",
        "created_by": creator.real_name or creator.username if creator else "",
        "confirmed_by": confirmer.real_name or confirmer.username if confirmer else "",
        "confirmed_at": d.confirmed_at.strftime("%Y-%m-%d %H:%M") if d.confirmed_at else "",
        "created_at": d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else "",
        "source": d.source or ("模板生成" if d.template_id else "流程生成"),
        "versions": [{"version_no": v.version_no, "note": v.note, "source": v.source or ("人工" if v.version_no > 1 else "系统"),
                      "content": v.content,
                      "created_at": v.created_at.strftime("%Y-%m-%d %H:%M") if v.created_at else ""}
                     for v in versions],
    }


@router.post("/generate")
def generate(data: DocumentGenerateIn, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    tpl = db.get(DocumentTemplate, data.template_id)
    if not tpl:
        raise HTTPException(404, "模板不存在")
    # 填参预检：把缺失必填项与格式错误显式回传（不再静默填〔待补充〕）
    field_check = _validate_fields(tpl, data.fields or {})
    warnings = [f"缺失必填项：{m['label']}" for m in field_check["missing"]]
    warnings += [i["message"] for i in field_check["invalid"]]
    content = tpl.template
    for k, v in data.fields.items():
        content = content.replace("{{" + k + "}}", v or "〔待补充〕")
    # 未填写的占位符统一标记，避免残留在正式文书里
    content = re.sub(r"\{\{[^}]+\}\}", "〔待补充〕", content)
    title = f"{data.case_name or '某某'}——{tpl.name}（AI初稿）"

    mode = "template+rules"
    llm_rejected = False
    missing: list[str] = []
    pending_before = content.count("〔待补充〕")   # 待补充标记数（润色前后必须一致）
    gen = provider.chat(
        [{"role": "system", "content": POLISH_PROMPT},
         {"role": "user", "content": content}],
        scene="document", user_id=user.id)
    if gen and len(gen) > 50:
        # 事实保全：模型润色只能改表述，不得改掉或丢掉承办人填报的关键要素
        # （日期、金额、单位、人名等）。这些是文书中的事实，一旦被模型改写，
        # 对纪检监察文书而言属于实质性风险，因此宁可弃用润色结果回到模板版本。
        missing = [str(v).strip() for v in data.fields.values()
                   if v and str(v).strip() and str(v).strip() not in gen]
        # 另需保全「待补充」标记：模型不得自行编内容把空填了——
        # 留白让承办人核实，远好过机器编一段看起来完整却无出处的话。
        if missing or gen.count("〔待补充〕") < pending_before:
            llm_rejected = True
        else:
            content = gen
            mode = "template+llm"
    # 无论是否联网，统一应用公文格式规范化（离线弱 AI 能力）
    content = gongwen.format_document(content)

    d = Document(template_id=tpl.id, source="模板生成", title=title, case_name=data.case_name,
                  content=content, status="AI初稿", created_by=user.id)
    db.add(d)
    db.flush()
    db.add(DocumentVersion(document_id=d.id, version_no=1, content=content,
                           note="AI 生成初稿", created_by=user.id))
    db.commit()
    audit.write_log(db, user.id, user.username, action="document_generate",
                    resource_type="document", resource_id=d.id,
                    detail={"template": tpl.name, "mode": mode,
                            "llm_rejected": llm_rejected,
                            "llm_missing_fields": missing if llm_rejected else [],
                            "field_missing": [m["key"] for m in field_check["missing"]],
                            "field_invalid": [i["key"] for i in field_check["invalid"]]})
    out = _doc_out(db, d)
    out["mode"] = mode
    # 智能润色改动了承办人填报的事实要素时弃用润色稿（事实保全），把原因回传给前端展示
    out["llm_rejected"] = llm_rejected
    out["llm_missing_fields"] = missing if llm_rejected else []
    out["warnings"] = warnings
    out["field_check"] = field_check
    return out


@router.get("/{did}/docx")
def export_docx(did: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """导出公文格式 Word（标题二号小标宋居中 / 正文三号仿宋 / 固定行距）。"""
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404, "文书不存在")
    try:
        payload = gongwen.to_docx(d.content)
    except Exception as e:
        raise HTTPException(500, f"Word 导出失败：{e}")
    fname = re.sub(r"[\\/:*?\"<>|]", "_", d.title)[:60]
    audit.write_log(db, user.id, user.username, action="document_export_docx",
                    resource_type="document", resource_id=did)
    return Response(
        payload,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}.docx"},
    )


@router.put("/{did}")
def update_doc(did: int, data: DocumentUpdateIn, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404, "文书不存在")
    if d.status != "AI初稿":
        raise HTTPException(400, "已定稿文书不可修改")
    if user.role == "banan" and d.created_by != user.id:
        raise HTTPException(403, "只能修改本人创建的文书")
    last = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == did).order_by(
        DocumentVersion.version_no.desc()).first()
    d.content = data.content
    db.add(DocumentVersion(document_id=did, version_no=(last.version_no + 1) if last else 2,
                           content=data.content, note=data.note or "人工修改", source="人工", created_by=user.id))
    db.commit()
    audit.write_log(db, user.id, user.username, action="document_edit",
                    resource_type="document", resource_id=did, detail={"note": data.note})
    return _doc_out(db, d)


@router.post("/{did}/confirm")
def confirm_doc(did: int, db: Session = Depends(get_db),
                 user: User = Depends(require_roles("shenli", "admin"))):
    """定稿须审理室/管理员确认 —— 责任链：AI初稿 → 承办人修改 → 审理确认。"""
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404, "文书不存在")
    d.status = "已定稿"
    d.confirmed_by = user.id
    d.confirmed_at = datetime.utcnow()
    db.commit()
    audit.write_log(db, user.id, user.username, action="document_confirm",
                    resource_type="document", resource_id=did)
    return _doc_out(db, d)


@router.post("/{did}/check")
def check_doc(did: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """排版规范性检查（离线规则，GB/T 9704 简化版）：评分 + 问题清单 + 一键规范化文本。"""
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404, "文书不存在")
    rep = gongwen.check_document(d.content)
    audit.write_log(db, user.id, user.username, action="document_check",
                    resource_type="document", resource_id=did,
                    detail={"score": rep["score"], "level": rep["level"],
                            "issues": len(rep["issues"])})
    return rep
