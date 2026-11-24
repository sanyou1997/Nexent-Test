# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
"""Use the documented offline OCR protocols, preserving physical page boundaries."""
import asyncio
import base64
from contextlib import closing
from io import BytesIO
from threading import Lock

import httpx
from pypdf import PdfReader, PdfWriter

PATHS = {"paddle_ocr": "/ocr", "paddle_layout": "/layout-parsing", "umi_ocr": "/api/ocr"}
_pdfium_lock = Lock()


def page_payload(reader, number, provider):
    writer = PdfWriter()
    writer.add_page(reader.pages[number - 1])
    buffer = BytesIO()
    writer.write(buffer)
    content = buffer.getvalue()
    if provider == "umi_ocr":
        import pypdfium2 as pdfium
        # PDFium does not permit simultaneous calls from different threads.
        with _pdfium_lock, pdfium.PdfDocument(content) as document, closing(document[0]) as page:
            width, height = page.get_size()
            if min(width, height) <= 0:
                raise ValueError("无效的 PDF 页面尺寸")
            scale = min(2.5, 3600 / max(width, height))
            with closing(page.render(scale=scale)) as bitmap, bitmap.to_pil() as image:
                png = BytesIO()
                image.save(png, "PNG")
        return {"base64": base64.b64encode(png.getvalue()).decode(), "options": {"data.format": "text"}}
    return {"file": base64.b64encode(content).decode(), "fileType": 0, "visualize": False}


def page_text(provider, payload):
    if provider == "umi_ocr":
        if payload.get("code") == 101:
            return ""
        if payload.get("code") != 100 or not isinstance(payload.get("data"), str):
            raise ValueError("Umi-OCR 未返回有效的逐页文字")
        return payload["data"]
    if payload.get("errorCode") != 0:
        raise ValueError("PaddleOCR 未成功完成识别")
    layout = provider == "paddle_layout"
    pages = payload["result"]["layoutParsingResults" if layout else "ocrResults"]
    if not isinstance(pages, list) or len(pages) != 1:
        raise ValueError("识别服务返回的页数与原件不符")
    if layout:
        text = pages[0]["markdown"]["text"]
        if not isinstance(text, str):
            raise ValueError("版面解析结果缺少正文")
        return text
    lines = pages[0]["prunedResult"]["rec_texts"]
    if not isinstance(lines, list) or not all(isinstance(s, str) for s in lines):
        raise ValueError("文字识别返回格式无效")
    return "\n".join(lines)


async def recognize_pdf(content, config, single_page=None):
    reader = PdfReader(BytesIO(content), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF 已加密，无法读取")
    count = len(reader.pages)
    if not count or (single_page is not None and not 1 <= single_page <= count):
        raise ValueError("PDF 页码无效")
    pages = []
    async with asyncio.timeout(600), httpx.AsyncClient(
        base_url=config.url, timeout=150, trust_env=False, follow_redirects=False
    ) as client:
        for number in ([single_page] if single_page else range(1, count + 1)):
            body = await asyncio.to_thread(page_payload, reader, number, config.provider)
            response = await client.post(PATHS[config.provider], json=body)
            response.raise_for_status()
            pages.append(page_text(config.provider, response.json()))
    return pages
