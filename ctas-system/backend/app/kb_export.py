"""法规库 → Nexent 知识库：导出同步文件。

平台法规库是唯一主本（结构化到条、可维护、可审计）；Nexent 知识库是它的检索副本，供场景智能体与其它
Nexent 智能体统一检索。本模块把「现行有效」的法规逐部导出为 Markdown（每条一个小节，便于切块后仍带条号），
并在文首写入「同步指纹」，同步脚本据此只重传有变化的法规、删除已下架的法规。

用法：python -m app.kb_export <输出目录>   （输出 *.md 与 manifest.json）
同步：docker/nexent/sync_regulations_kb.sh
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from app.database import SessionLocal
from app.models import Regulation, RegulationArticle

_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _filename(title: str) -> str:
    """文件名即 Nexent 检索结果里显示的来源名：用法规简称，去掉文件系统不允许的字符。"""
    m = re.search(r"《([^》]+)》", title)
    name = m.group(1) if m else title
    return _BAD.sub(" ", name).strip()[:80] + ".md"


def render(reg: Regulation, arts: list[RegulationArticle], short: str) -> tuple[str, str]:
    body_lines, chapter = [], None
    for a in arts:
        if (a.chapter or "") != chapter:
            chapter = a.chapter or ""
            if chapter:
                body_lines.append(f"\n## {chapter}\n")
        body_lines.append(f"### {short} {a.article_no}\n\n{(a.content or '').strip()}\n")
    body = "\n".join(body_lines)
    fp = hashlib.sha256((reg.title + reg.version + reg.status + body).encode("utf-8")).hexdigest()[:12]
    head = (f"# {reg.title}\n\n"
            f"- 版本：{reg.version or '未载明'}；施行：{reg.effective_date or '未载明'}；效力：{reg.status}\n"
            f"- 类别：{reg.category}；条文数：{len(arts)}\n"
            f"- 来源：纪检监察业务平台法规库（同步指纹 {fp}）\n")
    return head + "\n" + body, fp


def build(db) -> dict:
    """{文件名: {text, fingerprint, title, regulation_id, articles}}：现行有效法规的同步内容（不落盘）。"""
    out = {}
    for reg in db.query(Regulation).filter(Regulation.status == "现行有效").order_by(Regulation.id).all():
        arts = (db.query(RegulationArticle).filter(RegulationArticle.regulation_id == reg.id)
                .order_by(RegulationArticle.id).all())
        if not arts:
            continue
        fname = _filename(reg.title)
        # 条文小节标题带法规简称：切块后单看一块也知道出处
        text, fp = render(reg, arts, fname[:-3])
        out[fname] = {"text": text, "fingerprint": fp, "title": reg.title, "regulation_id": reg.id, "articles": len(arts)}
    return out


def export(out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.md"):
        old.unlink()
    db = SessionLocal()
    try:
        files = build(db)
    finally:
        db.close()
    manifest = {}
    for fname, f in files.items():
        (out / fname).write_text(f["text"], encoding="utf-8")
        manifest[fname] = {"fingerprint": f["fingerprint"], "title": f["title"], "articles": f["articles"]}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    m = export(sys.argv[1] if len(sys.argv) > 1 else "/app/data/kb_export")
    print(json.dumps({"files": len(m), "articles": sum(v["articles"] for v in m.values())}, ensure_ascii=False))
