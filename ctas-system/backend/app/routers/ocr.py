"""OCR 识别：rapidocr-onnxruntime（CPU 本地推理，模型随包内置）。

设计要点：
- 懒加载：首次调用才初始化引擎，避免拖慢服务启动
- 优雅降级：未安装依赖时返回 501，前端提示安装方式
- 全流程写审计日志
"""
import io
import logging
import json
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from PIL import Image

from app.database import get_db
from app.deps import get_current_user
from app.audit import write_log
from app import recognizer

router = APIRouter(prefix="/api/ocr", tags=["OCR"])
log = logging.getLogger("ctas.ocr")

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


_ENGINE_LABEL_BASE = "PP-OCRv3 · ONNX Runtime · CPU · 离线"
_ENGINE_LABEL = _ENGINE_LABEL_BASE + " · 增强预处理"


# --------------------------------------------------------------------------- #
# 手写增强链路：几何不变预处理(去噪+二值化) + 倾斜纠偏(带坐标逆映射) + 纪检词典纠错
# 设计要点：
# - 去噪/二值化为逐像素操作，不改变坐标，box 可直接用；
# - 纠偏=旋转会改变像素坐标，故记录逆变换矩阵 M_inv，把识别框从「旋转后图像」
#   还原到「原图」坐标，保证前端原文高亮不偏（尤其 PDF 扫描页）。
# 参考：~/.workbuddy/skills/handwritten-ocr（预处理三板斧 + 纪检纠错词典）。
# --------------------------------------------------------------------------- #
_CORR_CACHE: dict | None = None


def _load_corrections() -> dict:
    global _CORR_CACHE
    if _CORR_CACHE is not None:
        return _CORR_CACHE
    p = Path(__file__).parent / "jiwei_corrections.json"
    d: dict = {}
    if p.exists():
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            d = {}
    _CORR_CACHE = d
    return d


def _preprocess(img: np.ndarray, denoise=True, binarize=True, deskew=True):
    """灰度 -> 去噪 -> 二值化 -> 纠偏。返回 (处理后单通道图, 逆变换矩阵|None)。

    M_inv 仅当发生纠偏时非 None；用它可将旋转后图像上的坐标点还原到原图坐标。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()

    if denoise:
        gray = cv2.fastNlMeansDenoising(
            gray, None, h=10, templateWindowSize=7, searchWindowSize=21
        )

    if binarize:
        gray = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10
        )
        if np.mean(gray) < 127:
            gray = cv2.bitwise_not(gray)

    if not deskew:
        return gray, None
    return _deskew(gray)


def _deskew(gray: np.ndarray):
    """Hough 估计整体倾斜角并旋转纠正；返回 (旋转后图, 逆变换矩阵 M_inv|None)。"""
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=10
    )
    if lines is None:
        return gray, None
    angles = []
    for line in lines:
        x1, y1, x2, y2 = np.array(line).reshape(-1)
        ang = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if -45 < ang < 45:
            angles.append(ang)
    if not angles:
        return gray, None
    median_ang = float(np.median(angles))
    if abs(median_ang) < 0.5:
        return gray, None
    h, w = gray.shape[:2]
    center = (w // 2, h // 2)
    warped = cv2.warpAffine(
        gray, cv2.getRotationMatrix2D(center, median_ang, 1.0), (w, h),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )
    # 逆变换：scale=1 时，绕同中心反向旋转即可把旋转后坐标还原到原图坐标
    m_inv = cv2.getRotationMatrix2D(center, -median_ang, 1.0)
    return warped, m_inv


def _inv_map_box(box, m_inv: np.ndarray) -> list:
    out = []
    for x, y in box:
        v = m_inv @ np.array([x, y, 1.0], dtype=np.float64)
        out.append([int(round(v[0])), int(round(v[1]))])
    return out


def _decode(data: bytes) -> np.ndarray | None:
    arr = np.frombuffer(data, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _to_bgr(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if len(img.shape) == 2 else img


def _finalize(result, m_inv, corrections: dict) -> list[dict]:
    """把 RapidOCR 原生结果整理为结构化 boxes（坐标已还原到原图空间，套用纠错词典）。"""
    if not result:
        return []
    items: list[dict] = []
    for box, t, sc in result:
        if m_inv is not None:
            box = _inv_map_box(box, m_inv)
        text = str(t)
        if corrections:
            for wrong, right in corrections.items():
                if wrong and wrong in text:
                    text = text.replace(wrong, right)
        items.append({
            "box": [[int(round(p[0])), int(round(p[1]))] for p in box],
            "text": text,
            "score": round(float(sc), 3),
        })
    items.sort(key=lambda d: (d["box"][0][1], d["box"][0][0]))
    return items


def _run_ocr(data: bytes):
    """调用 rapidocr 引擎，返回原生结果 list[(box, text, score)]。"""
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
    except ImportError:
        raise RuntimeError("OCR 引擎未安装（pip install rapidocr-onnxruntime）")
    if not data:
        raise RuntimeError("内容为空")
    result, _ = _get_engine()(data)
    return result  # None 或 list of (box[[[x,y]*4]], text, score)


def _sort_key(r):
    box = r[0]
    return (box[0][1], box[0][0])  # 先按上边界(y)，再按左边界(x)


def ocr_image_bytes(data: bytes, preprocess: bool = True, correct: bool = True) -> str:
    """对图片字节做 OCR，返回按阅读顺序拼接的文本（供其他模块复用）。"""
    return "\n".join(b["text"] for b in ocr_image_structured(data, preprocess, correct))


def _apply_corrections(items: list[dict], corrections: dict) -> list[dict]:
    if corrections:
        for it in items:
            for wrong, right in corrections.items():
                if wrong and wrong in it["text"]:
                    it["text"] = it["text"].replace(wrong, right)
    items.sort(key=lambda d: (d["box"][0][1], d["box"][0][0]))
    return items


def ocr_image_structured(data: bytes, preprocess: bool = True, correct: bool = True):
    """返回结构化结果：[{box:[[x,y]*4], text, score}, ...]，坐标落在原图空间。

    - 识别服务按「系统管理 → 识别服务」配置：Nexent 的 Umi-OCR / PaddleOCR，或本机内置 RapidOCR；
      外部服务不可用且允许回退时，自动改用本机内置引擎；
    - preprocess=True（仅本机引擎）：先去噪/二值化/纠偏再识别，并逆映射识别框到原图坐标；
    - correct=True：套用纪检纠错词典（仅替换已知同音/形近误识，不改原文语义）。
    """
    corrections = _load_corrections() if correct else {}
    cfg = recognizer.effective()
    if cfg["ocr"]["provider"] != "builtin":
        try:
            return _apply_corrections(recognizer.remote_ocr_image(data, cfg), corrections)
        except Exception as e:
            if not (cfg["ocr"]["fallback_builtin"] and recognizer.builtin_available()["ocr"]):
                raise RuntimeError(f"文字识别服务不可用（{recognizer.ocr_label(cfg)}）：{e}")
            log.warning("remote OCR failed, falling back to builtin: %s", e)
    if not preprocess:
        result = _run_ocr(data)
        return _finalize(result, None, corrections)

    img = _decode(data)
    if img is None:
        # 解码失败兜底：按原链路直识别（无纠错坐标问题）
        result = _run_ocr(data)
        return _finalize(result, None, corrections)

    proc, m_inv = _preprocess(img, denoise=True, binarize=True, deskew=True)
    result, _ = _get_engine()(_to_bgr(proc))
    return _finalize(result, m_inv, corrections)


@router.post("")
async def ocr(
    file: UploadFile = File(...),
    preprocess: bool = Query(True, description="手写增强预处理（去噪/二值化/纠偏），默认开"),
    correct: bool = Query(True, description="纪检纠错词典后处理，默认开"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    if recognizer.effective()["ocr"]["provider"] == "builtin" and not recognizer.builtin_available()["ocr"]:
        raise HTTPException(501, "OCR 引擎未安装（pip install rapidocr-onnxruntime），或在「系统管理 → 识别服务」改用 Nexent 识别服务")

    data = await file.read()
    if not data:
        raise HTTPException(400, "上传文件为空")
    name = (file.filename or "").lower()
    t0 = time.time()

    # ---- PDF：逐页解析（数字文本抽取 + 扫描页 OCR），返回页面图片供原文对照 ----
    if name.endswith(".pdf"):
        if len(data) > 30 * 1024 * 1024:
            raise HTTPException(413, "PDF 超过 30MB 限制")
        from app.rag.pdf_extract import extract_pdf
        try:
            res = extract_pdf(data, file.filename, return_images=True)
        except Exception as e:
            raise HTTPException(422, f"PDF 解析失败：{e}")
        if res.get("encrypted"):
            raise HTTPException(400, "PDF 已加密，请先解密/去除密码后再上传")
        pages = res.get("pages", [])
        full = res.get("text", "")
        all_boxes = []
        for p in pages:
            all_boxes.extend(p.get("boxes", []))
        write_log(db, user_id=user.id, username=user.username, action="ocr",
                  resource_type="pdf", resource_id=file.filename or "",
                  detail={"chars": len(full), "pages": len(pages),
                          "latency_ms": int((time.time() - t0) * 1000)})
        return {"mode": "ocr", "source": "pdf", "text": full,
                "pages": pages, "boxes": all_boxes,
                "line_count": len([b for b in all_boxes if b.get("text")]),
                "engine": res.get("engine", ""), "note": res.get("note", ""),
                "latency_ms": int((time.time() - t0) * 1000)}

    # ---- 图片：本机 OCR + 识别框 ----
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "图片超过 15MB 限制")
    try:
        boxes = ocr_image_structured(data, preprocess=preprocess, correct=correct)
    except Exception as e:
        raise HTTPException(422, f"图片解析失败：{e}")

    text = "\n".join(b["text"] for b in boxes)
    lines = [b["text"] for b in boxes if b["text"]]
    try:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
    except Exception:
        w, h = 0, 0
    page = {"page": 1, "text": text, "boxes": boxes, "ocr": True,
            "page_image": None, "width": w, "height": h}
    write_log(db, user_id=user.id, username=user.username, action="ocr",
              resource_type="image", resource_id=file.filename or "",
              detail={"chars": len(text), "boxes": len(boxes),
                      "latency_ms": int((time.time() - t0) * 1000)})

    return {"mode": "ocr", "source": "image", "text": text, "pages": [page],
            "boxes": boxes, "line_count": len(lines),
            "engine": (recognizer.ocr_label() if recognizer.effective()["ocr"]["provider"] != "builtin"
                       else (_ENGINE_LABEL if preprocess else _ENGINE_LABEL_BASE)),
            "latency_ms": int((time.time() - t0) * 1000)}
