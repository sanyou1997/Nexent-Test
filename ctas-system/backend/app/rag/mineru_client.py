"""MinerU 4.x 文档解析集成：PDF/扫描件 → 结构化文本，供法规/业务语料入库。

模型目录通过 MINERU_HOME 指向（离网部署时指向包内 models/mineru）。
MinerU 不可用时返回明确错误提示，不影响其他功能。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.config import settings

_parser = None


def _get_parser():
    global _parser
    if _parser is None:
        models_dir = settings.MINERU_MODELS_DIR
        if models_dir and Path(models_dir).exists():
            # 必须在 import mineru 之前设置（其配置在导入时读取）
            os.environ["MINERU_HOME"] = str(Path(models_dir).resolve())
            os.environ.setdefault("MINERU_MODEL_SOURCE", "local")
        from mineru import MinerUParser
        # tier=basic：纯 ONNX 管线（版面+OCR+表格），CPU 离网机器友好，无需 VLM
        _parser = MinerUParser(tier="basic", parse_mode="auto")
    return _parser


def parse_pdf_to_text(pdf_bytes: bytes, filename: str = "doc.pdf") -> str:
    """用 MinerU 解析 PDF，返回 Markdown 结构文本。"""
    try:
        parser = _get_parser()
    except Exception as e:
        raise RuntimeError(f"MinerU 加载失败：{e}")

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / (filename or "doc.pdf")
        p.write_bytes(pdf_bytes)
        try:
            result = parser.parse(str(p))
        except Exception as e:
            raise RuntimeError(f"MinerU 解析失败：{e}")

    # ParseResult.markdown() 为方法，返回 Markdown 文本
    try:
        md = result.markdown()
        if isinstance(md, str) and md.strip():
            return md
    except Exception:
        pass
    # 退路：遍历 blocks 拼接文本
    try:
        parts = []
        for blk in getattr(result, "blocks", []) or []:
            texts = [getattr(s, "text", "") for s in getattr(blk, "spans", []) or []]
            if not texts:
                texts = [getattr(blk, "text", "")]
            parts.append("".join(t for t in texts if t))
        if any(parts):
            return "\n\n".join(p for p in parts if p.strip())
    except Exception:
        pass
    raise RuntimeError("MinerU 未返回可用的解析文本")


import re
from datetime import date

_MD_ARTICLE = re.compile(r"^(第[一二三四五六七八九十百零]+条)\s*[：:.、]?\s*(.*)$")
_MD_NOISE = re.compile(r"!\[[^\]]*\]\([^)]*\)|\*\*|^#{1,6}\s*")


def md_to_corpus(md: str, title: str = "") -> str:
    """MinerU Markdown → 语料格式：首行元信息 + ## 章节 + 【第X条】。"""
    lines = [l.rstrip() for l in md.splitlines()]
    # 标题：首个标题行，否则用传入名
    reg_title = title or "PDF 导入语料"
    out_articles = []
    chapter = ""
    cur = None
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            head = _MD_NOISE.sub("", s).strip()
            # 含"第X条"的标题行按条款处理，否则当章节名
            m = _MD_ARTICLE.match(head)
            if m:
                if cur:
                    out_articles.append(cur)
                cur = {"no": m.group(1), "chapter": chapter, "content": m.group(2)}
            else:
                chapter = head
            continue
        s = _MD_NOISE.sub("", s)
        m = _MD_ARTICLE.match(s)
        if m:
            if cur:
                out_articles.append(cur)
            cur = {"no": m.group(1), "chapter": chapter, "content": m.group(2)}
        elif cur:
            cur["content"] += s
        elif not any(c in s for c in "|") and len(s) < 60 and not out_articles:
            reg_title = s  # 正文开始前的孤立行视作法规名
    if cur:
        out_articles.append(cur)
    if not out_articles:
        raise ValueError("PDF 中未识别到『第X条』式条款标记，无法条款级切块入库")
    header = f"{reg_title}|PDF导入|{date.today().isoformat()}"
    body = []
    last_ch = ""
    for a in out_articles:
        if a["chapter"] != last_ch and a["chapter"]:
            body.append(f"## {a['chapter']}")
            last_ch = a["chapter"]
        body.append(f"【{a['no']}】{a['content']}")
    return header + "\n" + "\n".join(body)
