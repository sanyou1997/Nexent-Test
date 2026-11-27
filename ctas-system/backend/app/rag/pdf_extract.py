"""PDF 材料解析（本机·离线，不依赖 MinerU / 大模型）。

解析策略：
- 优先用 pypdfium2 直接抽取数字文本（快、准、保留版面阅读顺序）；
- 抽不到文本的页（扫描件 / 图片型 PDF）自动渲染为图片，用本机 RapidOCR 识别；
- 加密 PDF 明确报错，不再误报"文件未加密"；
- 数字文本页与扫描页均返回识别框（boxes）与页面图片（data URL），供前端"原文对照"：
  数字文本页的 boxes 由文本层逐行坐标换算得到，扫描页的 boxes 由 RapidOCR 得到。

注意：进程内已设置 KMP_DUPLICATE_LIB_OK=TRUE（见 app/main.py），
规避 onnxruntime 与 opencv 的 OpenMP 重复库冲突。
"""
from __future__ import annotations

import base64
import io

from PIL import Image

from app.routers.ocr import ocr_image_structured, _ENGINE_LABEL


def _pil_to_bytes(pil: Image.Image) -> bytes:
    buf = io.BytesIO()
    pil.save(buf, "PNG")
    return buf.getvalue()


def _pil_to_data_url(pil: Image.Image) -> str:
    b64 = base64.b64encode(_pil_to_bytes(pil)).decode("ascii")
    return "data:image/png;base64," + b64


def _is_encryption_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(k in msg for k in ("password", "encrypt", "encrypted", "decrypt"))


def _textpage_line_boxes(tp, page_w_pts: float, page_h_pts: float, scale: float,
                         max_chars: int = 12000) -> list[dict]:
    """从 PDF 文本层抽取逐行 bounding box，使数字文本页也能"点击右侧定位左侧原文"。

    返回结构与 RapidOCR 结构化结果一致：[{box:[[x,y]*4], text, score}, ...]，
    坐标已换算到页面渲染图（preview_dpi 对应）的像素空间，可直接供前端 SVG 叠加。
    box 四点顺序：左上 / 右上 / 右下 / 左下。
    """
    n = tp.count_chars()
    if n == 0:
        return []
    if n > max_chars:
        # 超长页（如带图表的扫描式 PDF 文本层）跳过框提取，避免卡顿，仅保留文本
        return []

    # 收集每个字符的 PDF 点坐标（y 向上），并按 (上边界↓, 左边界→) 排序
    chars: list[tuple] = []
    for i in range(n):
        b = tp.get_charbox(i)  # (left, yA, right, yB)，y 轴向上
        if not b:
            continue
        l, ya, r, yb = b
        x0, x1 = min(l, r), max(l, r)
        yb0, yt1 = min(ya, yb), max(ya, yb)  # 底边 / 顶边
        cx = (x0 + x1) / 2.0
        cy = (yb0 + yt1) / 2.0
        chars.append((cx, cy, x0, yb0, x1, yt1))
    if not chars:
        return []
    chars.sort(key=lambda c: (-c[1], c[0]))  # 先按上边界(大→小)，再按左边界

    # 贪心按行聚类：当前行与新字符的垂直带重叠即并入，否则断行
    lines: list[list] = []
    cur: list = []
    for ch in chars:
        if cur:
            ctop = max(c[5] for c in cur)   # 当前行顶边
            cbot = min(c[3] for c in cur)   # 当前行底边
            lh = max(ctop - cbot, 1.0)
            # 新字符顶/底
            chtop, chbot = ch[5], ch[3]
            overlap = min(ctop, chtop) - max(cbot, chbot)
            if overlap >= -0.35 * lh:
                cur.append(ch)
                continue
            lines.append(cur)
            cur = [ch]
        else:
            cur = [ch]
    if cur:
        lines.append(cur)

    out: list[dict] = []
    for ln in lines:
        lx = min(c[2] for c in ln)
        rx = max(c[4] for c in ln)
        ltop = max(c[5] for c in ln)  # 顶边（PDF y 大）
        lbot = min(c[3] for c in ln)  # 底边（PDF y 小）
        try:
            txt = (tp.get_text_bounded(lx, lbot, rx, ltop) or "").strip()
        except Exception:
            txt = ""
        if not txt:
            continue
        # 换算到渲染图像素空间（y 向下）
        ix0 = lx * scale
        ix1 = rx * scale
        iytop = (page_h_pts - ltop) * scale
        iybot = (page_h_pts - lbot) * scale
        out.append({
            "box": [
                [int(round(ix0)), int(round(iytop))],
                [int(round(ix1)), int(round(iytop))],
                [int(round(ix1)), int(round(iybot))],
                [int(round(ix0)), int(round(iybot))],
            ],
            "text": txt,
            "score": 1.0,
        })
    return out



def extract_pdf(pdf_bytes: bytes, filename: str = "doc.pdf",
                ocr_dpi: int = 150, min_text_len: int = 12,
                return_images: bool = False, preview_dpi: int = 110) -> dict:
    """解析 PDF → {encrypted, text, pages, engine, note, char_count}。

    pages: [{page, text, boxes, ocr(bool), page_image(data url|None), width, height}]
    - ocr=False 的页为本机文本抽取；当 return_images=True 时同样渲染为页面图片
      （page_image 为渲染图），前端"原文区域"即可显示真实页面，而非占位提示；
    - ocr=True  的页为扫描页 OCR（page_image 为渲染图，boxes 为识别框坐标）。
    """
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception as e:
        if _is_encryption_error(e):
            return {"encrypted": True, "text": "", "pages": [], "engine": "",
                    "note": "PDF 已加密，请先解密/去除密码后再上传", "char_count": 0}
        raise RuntimeError(f"PDF 打开失败（可能已损坏）：{e}")

    n = len(pdf)
    # 扫描版 PDF 可整份交 Nexent · MinerU 解析（系统管理 → 识别服务）；失败时回到逐页 OCR
    from app import recognizer
    rcfg = recognizer.effective()
    mineru_pages: list[str] | None = None
    if rcfg["ocr"]["pdf_mode"] == "mineru":
        try:
            scanned = 0
            for i in range(n):
                _tp = pdf[i].get_textpage()
                try:
                    if len((_tp.get_text_range() or "").strip()) < min_text_len:
                        scanned += 1
                finally:
                    _tp.close()
            if scanned:
                mineru_pages = recognizer.mineru_pdf_pages(pdf_bytes, n, rcfg)
        except Exception:  # MinerU 不可用：回到逐页 OCR
            mineru_pages = None
    pages_out: list[dict] = []
    full: list[str] = []
    text_pages = 0
    ocr_pages = 0
    try:
        for i in range(n):
            page = pdf[i]
            tp = page.get_textpage()
            raw = (tp.get_text_range() or "").strip()
            try:
                if raw and len(raw) >= min_text_len:
                    # 数字文本页：抽取文本 + 始终从文本层抽取逐行 bounding box
                    # （使"点击右侧识别结果高亮左侧原文位置"生效，与是否渲染图片无关）；
                    # 仅当 return_images=True 时额外渲染页面图供前端"原文对照"展示。
                    scale = preview_dpi / 72.0
                    pw, ph = page.get_size()
                    w, h = int(pw * scale), int(ph * scale)
                    boxes: list[dict] = []
                    try:
                        boxes = _textpage_line_boxes(tp, pw, ph, scale)
                    except Exception:
                        boxes = []
                    page_image = None
                    if return_images:
                        try:
                            pil = page.render(scale=scale).to_pil()
                            w, h = pil.size
                            page_image = _pil_to_data_url(pil)
                        except Exception:
                            page_image = None
                    pages_out.append({"page": i + 1, "text": raw,
                                      "boxes": boxes, "ocr": False,
                                      "page_image": page_image, "width": w, "height": h})
                    full.append(raw)
                    text_pages += 1
                else:
                    # 扫描页：渲染为图片 → 本机 RapidOCR
                    scale = ocr_dpi / 72.0
                    pil = page.render(scale=scale).to_pil()
                    w, h = pil.size
                    if mineru_pages is not None:
                        boxes = []
                        text = mineru_pages[i] if i < len(mineru_pages) else ""
                    else:
                        boxes = ocr_image_structured(_pil_to_bytes(pil))
                        text = "\n".join(b["text"] for b in boxes)
                    page_image = _pil_to_data_url(pil) if return_images else None
                    pages_out.append({"page": i + 1, "text": text,
                                      "boxes": boxes, "ocr": True,
                                      "page_image": page_image, "width": w, "height": h})
                    full.append(text)
                    ocr_pages += 1
            finally:
                tp.close()
            page.close()
    finally:
        pdf.close()

    scan_label = (f"Nexent · MinerU（{rcfg['ocr']['urls']['mineru']}）" if mineru_pages is not None
                  else (recognizer.ocr_label(rcfg) if rcfg["ocr"]["provider"] != "builtin" else _ENGINE_LABEL))
    if ocr_pages == 0:
        engine = "pypdfium2 文本抽取（本机·离线）"
    elif text_pages == 0:
        engine = scan_label
    else:
        engine = f"pypdfium2 文本抽取 + {scan_label}"
    note = f"共 {n} 页：文本抽取 {text_pages} 页，扫描页 OCR 补识别 {ocr_pages} 页"
    return {"encrypted": False, "text": "\n\n".join(full), "pages": pages_out,
            "engine": engine, "note": note,
            "char_count": sum(len(t) for t in full)}
