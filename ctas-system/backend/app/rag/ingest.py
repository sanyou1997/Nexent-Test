"""法规条款级切块入库：单条为最小检索单元，禁止跨条切分。
语料格式（UTF-8 文本）：
  第一行：法规名|版本|生效日期
  ## 章名 → 章节
  【第X条】条款内容（可多行，直到下一个标记）

健壮性要求（2026-10-21 补强，缘于网页提炼语料静默丢条事故）：
  - 兼容行首「第X条」无括号写法与阿拉伯序号「1.」「1、」（转换为第X条）；
  - 入库前做条目连续性校验：跳号/重号一律写入 report['gaps']，调用方必须处置，
    禁止静默丢弃——"只有数据全了，才有分析这一说"。
"""
import re
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Regulation, RegulationArticle
from app.llm import embeddings

ARTICLE_RE = re.compile(r"^【?(第[一二三四五六七八九十百零]+条)】?\s*")
ARABIC_ARTICLE_RE = re.compile(r"^(\d{1,3})[\.、，,．]\s*")

_CN = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000}


def cn2num(s: str) -> int:
    """中文条号 → 整数（零/空 → 0）。支持第一百零一条式「零」占位。"""
    s = (s or "").replace("第", "").replace("条", "").strip()
    if not s:
        return 0
    section, number = 0, 0
    for ch in s:
        if ch in _CN:
            number = _CN[ch]
        elif ch in _UNITS:
            unit = _UNITS[ch]
            if unit == 10 and number == 0:
                number = 1
            section += (number or 1) * unit
            number = 0
        else:
            return 0
    return section + number


def num2cn(n: int) -> str:
    if n <= 0:
        return ""
    digits = "零一二三四五六七八九"
    out, num = "", n
    for name, factor in (("千", 1000), ("百", 100), ("十", 10), ("", 1)):
        d = num // factor
        num %= factor
        if d:
            out += digits[d] + name
        elif out and num:
            out += "零"
    return out[1:] if out.startswith("一十") else out


def parse_corpus(text: str):
    lines = [l.rstrip() for l in text.strip().splitlines()]
    if not lines or "|" not in lines[0]:
        raise ValueError("语料首行须为：法规名|版本|生效日期")
    title, version, eff = [x.strip() for x in lines[0].split("|", 2)]
    # 防误判：全文已有中文条号时，阿拉伯序号行按普通正文处理
    # （否则「要点提示：1. 2. 3.」会被误转为条文，与正文第一条重号）
    has_cn_article = bool(re.search(r"^【?第[一二三四五六七八九十百零]+条】?", text, re.M))
    chapter, articles, cur = "", [], None
    for line in lines[1:]:
        if not line.strip():
            continue
        if line.startswith("## "):
            chapter = line[3:].strip()
            continue
        m = ARTICLE_RE.match(line.strip())
        if m:
            if cur:
                articles.append(cur)
            cur = {"article_no": m.group(1), "chapter": chapter,
                   "content": ARTICLE_RE.sub("", line.strip(), count=1).strip()}
            continue
        m = ARABIC_ARTICLE_RE.match(line.strip()) if not has_cn_article else None
        if m:
            if cur:
                articles.append(cur)
            cur = {"article_no": f"第{num2cn(int(m.group(1)))}条", "chapter": chapter,
                   "content": ARABIC_ARTICLE_RE.sub("", line.strip(), count=1).strip()}
            continue
        if cur:
            cur["content"] += line.strip()
    if cur:
        articles.append(cur)
    return title, version, eff, articles


def validate_continuity(articles: list) -> dict:
    """条目连续性校验：返回 {gaps:[缺号], dups:[重号]}。空列表=连续。"""
    seen, dups = set(), []
    for a in articles:
        n = cn2num(a["article_no"])
        if n <= 0:
            continue
        if n in seen:
            dups.append(a["article_no"])
        seen.add(n)
    gaps = []
    if seen:
        gaps = [f"第{num2cn(n)}条" for n in sorted(set(range(min(seen), max(seen) + 1)) - seen)]
    return {"gaps": gaps, "dups": dups}


def ingest(db: Session, text: str, source: str = "公开文本（演示语料）",
           strict: bool = False) -> dict:
    """条款级入库。

    strict=True 时，连续性校验发现跳号/重号直接抛错（拒绝带伤入库）；
    默认 strict=False，返回 report 由调用方展示告警（向后兼容旧行为）。
    """
    title, version, eff, articles = parse_corpus(text)
    report = validate_continuity(articles)
    report["article_count"] = len(articles)
    report["title"] = title
    if strict and (report["gaps"] or report["dups"]):
        raise ValueError(
            f"条目不连续，已拒绝入库：缺 {'、'.join(report['gaps'][:10]) or '无'}"
            f"{' …' if len(report['gaps']) > 10 else ''}"
            f"{'；重号 ' + '、'.join(report['dups'][:5]) if report['dups'] else ''}")
    reg = Regulation(title=title, version=version, effective_date=eff, status="现行有效", source=source)
    db.add(reg)
    db.flush()
    dense = embeddings.embed_dense([f"{a['chapter']} {a['article_no']} {a['content']}" for a in articles]) or []
    for i, a in enumerate(articles):
        db.add(RegulationArticle(
            regulation_id=reg.id, chapter=a["chapter"], article_no=a["article_no"],
            content=a["content"],
            tf_vector=embeddings.embed_local(f"{a['chapter']} {a['article_no']} {a['content']}"),
            dense_vector=dense[i] if dense else None,
        ))
    db.commit()
    # 失效检索缓存，使新条款立即对 QA 可见
    from app.rag.retrieve import invalidate_corpus
    invalidate_corpus()
    report["regulation_id"] = reg.id
    return report
