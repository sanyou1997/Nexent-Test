"""CTAS 送审材料汇聚中心（审理辅助系统核心新增）。

现实场景：审理材料更多是案主（审查调查组）把案件全过程材料直接提供过来，
而不是系统内模块流转。本模块把「材料汇聚」做成审理第一入口：

  按移送审理标准清单登记 → 上传/OCR 识别落库 → 完整度检查（材料全没全一眼可见）
  → 才进入阅卷/证据链/程序合规/量纪/报告的审理链条。

设计原则（用户 #1 命门）：材料完整度可量化、可评估、可见；OCR 结果必须落库
（补齐全业务系统「OCR 实时识别不落库」的老缺口），供阅卷摘要与证据链校验复用。
"""
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.deps import get_current_user
from app.models import User, Trial, TrialMaterial, Case

router = APIRouter(prefix="/api/trial-materials", tags=["CTAS送审材料"])

# 材料类别（登记下拉项）
MATERIAL_CATEGORIES = [
    "审查调查报告", "违纪事实材料", "证据卷宗", "交代忏悔材料", "申辩材料",
    "程序手续", "涉案财物材料", "移送手续", "鉴定意见", "到案经过", "其他",
]

# 移送审理标准必备清单（依《监督执纪工作规则》第五十五条及案件审理工作规程）
# key 与 MATERIAL_CATEGORIES 对应；required=True 的类别缺失将计入完整度缺口
REQUIRED_CHECKLIST = [
    {"key": "审查调查报告", "label": "审查调查报告（含被审查调查人基本情况、主要违纪违法事实、证据清单）"},
    {"key": "违纪事实材料", "label": "违纪违法事实材料（已与被审查调查人见面核对签注）"},
    {"key": "证据卷宗", "label": "证据卷宗（书证/证人证言/笔录/供述等）"},
    {"key": "程序手续", "label": "程序手续材料（初核/立案审批、措施审批、时限文书）"},
    {"key": "涉案财物材料", "label": "涉案财物清单及处理意见"},
    {"key": "交代忏悔材料", "label": "被审查调查人交代/忏悔材料"},
    {"key": "移送手续", "label": "移送审理审批表/移送函"},
]

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "ctas_materials"


class MaterialIn(BaseModel):
    trial_id: int
    name: str = ""
    category: str = "其他"
    seq: str = ""
    pages: int = 0
    formed_at: str = ""
    source: str = "审查调查组"
    is_original: str = "原件"
    sign_note: str = ""
    required: bool = False
    note: str = ""


class CompletenessIn(BaseModel):
    trial_id: int


def _out(m: TrialMaterial) -> dict:
    return {
        "id": m.id, "trial_id": m.trial_id, "case_id": m.case_id,
        "name": m.name, "category": m.category, "seq": m.seq, "pages": m.pages,
        "formed_at": m.formed_at, "source": m.source, "is_original": m.is_original,
        "sign_note": m.sign_note, "required": bool(m.required),
        "file_name": m.file_name, "file_type": m.file_type,
        "ocr_status": m.ocr_status or "",
        "ocr_chars": len(m.ocr_text or ""),
        "note": m.note or "",
        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "",
    }


@router.get("/checklist")
def checklist(user: User = Depends(get_current_user)):
    """移送审理标准必备清单（登记与完整度检查共用同一口径）。"""
    return {"categories": MATERIAL_CATEGORIES, "required": REQUIRED_CHECKLIST}


@router.get("")
def list_materials(trial_id: int = 0, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    q = db.query(TrialMaterial)
    if trial_id:
        q = q.filter(TrialMaterial.trial_id == trial_id)
    ms = q.order_by(TrialMaterial.id.asc()).all()
    return {"items": [_out(m) for m in ms], "total": len(ms)}


@router.post("")
def create_material(data: MaterialIn, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    t = db.get(Trial, data.trial_id)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if data.category not in MATERIAL_CATEGORIES:
        raise HTTPException(400, f"材料类别须为：{'/'.join(MATERIAL_CATEGORIES)}")
    required = data.required or any(c["key"] == data.category for c in REQUIRED_CHECKLIST)
    m = TrialMaterial(trial_id=t.id, case_id=t.case_id, name=data.name or data.category,
                      category=data.category, seq=data.seq, pages=data.pages,
                      formed_at=data.formed_at, source=data.source,
                      is_original=data.is_original, sign_note=data.sign_note,
                      required=required, note=data.note, created_by=user.id)
    db.add(m)
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_material_create",
                    resource_type="trial_material", resource_id=m.id,
                    detail={"trial_id": t.id, "category": data.category})
    return _out(m)


@router.put("/{mid}")
def update_material(mid: int, data: MaterialIn, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    m = db.get(TrialMaterial, mid)
    if not m:
        raise HTTPException(404, "材料不存在")
    for f in ("name", "category", "seq", "pages", "formed_at", "source",
              "is_original", "sign_note", "note"):
        setattr(m, f, getattr(data, f))
    m.required = data.required or any(c["key"] == data.category for c in REQUIRED_CHECKLIST)
    db.commit()
    return _out(m)


@router.delete("/{mid}")
def delete_material(mid: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    m = db.get(TrialMaterial, mid)
    if not m:
        raise HTTPException(404, "材料不存在")
    db.delete(m)
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_material_delete",
                    resource_type="trial_material", resource_id=mid, detail={})
    return {"ok": True}


@router.post("/{mid}/ocr")
async def ocr_material(mid: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """上传材料原件 → OCR 识别 → 文本落库（关闭「OCR 不落库」缺口）。

    支持图片（jpg/png，手写增强预处理）与 PDF（逐页：数字文本 + 扫描页 OCR）。
    文件同时落盘存档，便于回溯核对。
    """
    m = db.get(TrialMaterial, mid)
    if not m:
        raise HTTPException(404, "材料不存在")
    data = await file.read()
    if not data:
        raise HTTPException(400, "上传文件为空")
    name = (file.filename or "").lower()
    t0 = time.time()
    text = ""
    try:
        if name.endswith(".pdf"):
            if len(data) > 30 * 1024 * 1024:
                raise HTTPException(413, "PDF 超过 30MB 限制")
            from app.rag.pdf_extract import extract_pdf
            res = extract_pdf(data, file.filename)
            text = res.get("text", "")
        else:
            if len(data) > 15 * 1024 * 1024:
                raise HTTPException(413, "图片超过 15MB 限制")
            from app.routers.ocr import ocr_image_bytes
            text = ocr_image_bytes(data)
    except HTTPException:
        raise
    except Exception as e:
        m.ocr_status = "识别失败"
        m.note = (m.note or "") + f"\n[OCR失败] {e}"
        db.commit()
        raise HTTPException(422, f"识别失败：{e}")

    # 原件落盘存档
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"m{mid}_{int(time.time())}_{Path(file.filename or 'file').name}"
    (UPLOAD_DIR / safe_name).write_bytes(data)
    m.file_path = str(UPLOAD_DIR / safe_name)
    m.file_name = file.filename or ""
    m.file_type = "pdf" if name.endswith(".pdf") else "image"
    m.ocr_text = text
    m.ocr_status = "已识别" if text.strip() else "识别失败"
    m.ocr_at = datetime_now()
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_material_ocr",
                    resource_type="trial_material", resource_id=mid,
                    detail={"chars": len(text), "file": file.filename,
                            "latency_ms": int((time.time() - t0) * 1000)})
    out = _out(m)
    out["text_preview"] = text[:300]
    return out


def datetime_now():
    from datetime import datetime
    return datetime.utcnow()


@router.post("/completeness")
def completeness(data: CompletenessIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """材料完整度检查（命门功能）：按移送审理标准清单核对登记情况。

    返回：必备项逐项状态（已登记/缺失）+ 整体完整度（已登记必备数 / 必备总数）
    + OCR 覆盖情况 + 缺项清单——「材料是否全了」一眼可见、可评估。
    """
    t = db.get(Trial, data.trial_id)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    mats = (db.query(TrialMaterial)
            .filter(TrialMaterial.trial_id == t.id).all())
    cats = {m.category for m in mats if m.category}
    items, missing = [], []
    for c in REQUIRED_CHECKLIST:
        hit = [m for m in mats if m.category == c["key"]]
        if hit:
            items.append({"key": c["key"], "label": c["label"], "status": "已登记",
                          "count": len(hit),
                          "ocr_done": sum(1 for m in hit if m.ocr_status == "已识别")})
        else:
            items.append({"key": c["key"], "label": c["label"], "status": "缺失", "count": 0})
            missing.append(c["label"])
    total = len(REQUIRED_CHECKLIST)
    done = total - len(missing)
    ocr_done = sum(1 for m in mats if m.ocr_status == "已识别")
    level = "完整" if not missing else ("部分缺失" if done >= total * 0.7 else "严重缺失")
    result = {
        "trial_id": t.id,
        "items": items,
        "required_total": total,
        "required_done": done,
        "completeness": round(done / total, 3),
        "level": level,
        "missing": missing,
        "material_total": len(mats),
        "ocr_done": ocr_done,
        "note": "完整度检查为辅助核对：必备材料未齐前不建议进入实质审理；"
                "最终以承办人按卷核对为准，系统不替代人工。",
    }
    audit.write_log(db, user.id, user.username, action="trial_material_check",
                    resource_type="trial", resource_id=t.id,
                    detail={"level": level, "missing": len(missing)})
    return result
