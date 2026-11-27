"""把核对过的官方全文写入平台法规库（在 ctas 容器内运行：python apply_official.py /tmp/lawfetch）。

1. 更正：库中已有、且拿到官方全文的法规 —— 按条号就地更新条文（保留条款 ID，已有引用不断链），记录来源网址；
2. 新增：官方全文的纪检监察核心党内法规 / 法律；
3. 标注：拿不到官方原文或已确认不完整的条目，改效力状态（不再作为问答与知识库依据），并在来源中写明原因。
幂等：重复运行结果一致。
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

from app.database import SessionLocal
from app.llm import embeddings
from app.models import Regulation, RegulationArticle
from app.rag import retrieve

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/lawfetch")
TODAY = date.today().isoformat()
TRAIL = re.compile(r"(发布时间[：:]|（新华社|延伸阅读|责任编辑|《人民日报》（|\n分享\n|\n打印|相关链接|【纠错】)")

URLS = {k: v for k, v in (ln.split("|", 1) for f in ("batch1.txt", "batch2.txt", "batch3.txt", "batch4.txt")
                          for ln in (SRC / f).read_text(encoding="utf-8").splitlines() if "|" in ln)}
SITE = lambda u: "新华网（新华社受权发布）" if "xinhuanet" in u or "news.cn" in u else ("人民网（转载新华社电）" if "people.com.cn" in u else "共产党员网")  # noqa: E731


def clean(t: str) -> str:
    m = TRAIL.search(t)
    t = t[:m.start()] if m else t
    return re.sub(r"\n{2,}", "\n", t).strip()


def official(key: str) -> list[dict]:
    d = json.loads((SRC / f"{key}.json").read_text(encoding="utf-8"))
    arts = [{"chapter": a["chapter"], "no": a["no"], "content": clean(a["content"])} for a in d["articles"]]
    return arts


def src_note(key: str) -> str:
    u = URLS[key]
    return f"官方全文·{SITE(u)}｜{u}｜{TODAY} 逐条核对"


def vec(chapter, no, content):
    return embeddings.embed_local(f"{chapter or ''} {no or ''} {content or ''}")


# ---- 1. 更正：已有法规 → 官方全文（rid: 抓取文件） ----
FIX = {8: "dnjd", 9: "jbkg", 11: "jjcf", 12: "xsgz", 22: "zwcf", 23: "sydw", 24: "gqcf", 25: "cfqx"}
# ---- 2. 新增 ----
NEW = [
    ("jdzj", "中国共产党纪律检查机关监督执纪工作规则", "2018年12月中办印发（2019年修订）", "2019-01-01", "纪检监察法规制度"),
    ("wztl", "中国共产党问责条例", "2019年修订", "2019-09-01", "中央党内法规"),
    ("jwgztl", "中国共产党纪律检查委员会工作条例", "2021年12月24日中共中央发布", "2021-12-24", "中央党内法规"),
    ("pzjg", "纪检监察机关派驻机构工作规则", "2022年6月22日中共中央办公厅发布", "2022-06-22", "纪检监察法规制度"),
    ("qlbz", "中国共产党党员权利保障条例", "2020年12月25日修订发布", "2020-12-25", "中央党内法规"),
    ("jcgf", "中华人民共和国监察官法", "2021年8月20日通过", "2022-01-01", "国家法律法规"),
    ("dz", "中国共产党章程", "2022年10月22日党的二十大部分修改", "2022-10-22", "中央党内法规"),
    ("tw2016", "最高人民法院 最高人民检察院关于办理贪污贿赂刑事案件适用法律若干问题的解释", "法释〔2016〕9号", "2016-04-18", "国家法律法规"),
    ("tw2026", "最高人民法院 最高人民检察院关于办理贪污贿赂刑事案件适用法律若干问题的解释（二）", "法释〔2026〕6号", "2026-05-01", "国家法律法规"),
]
# ---- 3. 标注 ----
MARK = {
    13: ("历史文本", "文本不完整：仅 262 条，缺第三百八十三条（贪污罪）、第三百九十条（行贿罪）等，且为 1997 年文本、未含历次修正案；不得作为现行依据，待按全国人大发布的现行文本替换"),
    17: ("未核实", "公开渠道未查到该文件（中央纪委国家监委网站、新华网、共产党员网），来源为客户演示语料；不得作为正式依据"),
    18: ("未核实", "公开渠道未查到该文件，来源为客户演示语料；不得作为正式依据"),
    19: ("未核实", "公开渠道未查到该文件，来源为客户演示语料；不得作为正式依据"),
    20: ("未核实", "公开渠道未查到该文件，来源为客户演示语料；不得作为正式依据"),
    21: ("未核实", "公开渠道未查到该文件，来源为客户演示语料；不得作为正式依据"),
    14: ("未核实", "文件确有印发，但未取得可核对的公开全文，条文来源为客户演示语料；核对原件后可恢复为现行有效"),
    16: ("未核实", "文件确有印发，但未取得可核对的公开全文，条文来源为客户演示语料；核对原件后可恢复为现行有效"),
    15: ("参考资料", "实务整理，非规范性文件；仅供参考，不作为问答与知识库依据"),
    26: ("参考资料", "实务整理，非规范性文件；仅供参考，不作为问答与知识库依据"),
}
SPOT = {
    27: "官方原文网站（全国人大网 / 国家法律法规数据库）当前网络不可达，未能逐条比对；已按全国人大常委会 2024年12月25日修改决定抽查：新增条文 5 处条号与内容均一致，章节结构一致",
    10: "官方原文网站（中央纪委国家监委网站）当前网络不可达，未能逐条比对；条文完整度与 2021 年版一致（非摘要），待官网可达后核对",
}

db = SessionLocal()
out = {"fixed": [], "added": [], "marked": [], "spot": []}
try:
    for rid, key in FIX.items():
        reg = db.get(Regulation, rid)
        arts = official(key)
        mine = {a.article_no: a for a in db.query(RegulationArticle).filter(RegulationArticle.regulation_id == rid).all()}
        changed = 0
        for a in arts:
            row = mine.pop(a["no"], None)
            if row is None:
                row = RegulationArticle(regulation_id=rid, article_no=a["no"], chapter=a["chapter"], content=a["content"])
                db.add(row)
                changed += 1
            elif row.content != a["content"] or (row.chapter or "") != (a["chapter"] or ""):
                row.content, row.chapter = a["content"], a["chapter"]
                changed += 1
            row.tf_vector = vec(a["chapter"], a["no"], a["content"])
        for extra in mine.values():  # 库里多出来、官方没有的条
            db.delete(extra)
        reg.source, reg.is_official, reg.status = src_note(key), True, "现行有效"
        out["fixed"].append((reg.title[:24], len(arts), changed))

    for key, title, version, eff, cat in NEW:
        reg = db.query(Regulation).filter(Regulation.title == title).first()
        if not reg:
            reg = Regulation(title=title, version=version, effective_date=eff, status="现行有效", category=cat,
                             source=src_note(key), is_official=True, required=False)
            db.add(reg)
            db.flush()
        else:
            reg.version, reg.effective_date, reg.category, reg.source, reg.status = version, eff, cat, src_note(key), "现行有效"
            db.query(RegulationArticle).filter(RegulationArticle.regulation_id == reg.id).delete()
        arts = official(key)
        if key == "dz":  # 党章总纲（序言）同样是重要依据
            d = json.loads((SRC / "dz.json").read_text(encoding="utf-8"))["text"]
            s, e = d.find("总纲"), d.find("第一章")
            if 0 <= s < e:
                arts = [{"chapter": "总纲", "no": "总纲", "content": clean(d[s + 2:e])}] + arts
        for a in arts:
            db.add(RegulationArticle(regulation_id=reg.id, article_no=a["no"], chapter=a["chapter"], content=a["content"],
                                     tf_vector=vec(a["chapter"], a["no"], a["content"])))
        out["added"].append((title, len(arts)))

    for rid, (status, why) in MARK.items():
        reg = db.get(Regulation, rid)
        if reg:
            base = (reg.source or "").split("｜核对结论：")[0]
            reg.status, reg.is_official = status, False
            reg.source = f"{base}｜核对结论：{why}"[:200]
            out["marked"].append((reg.title[:24], status))
    for rid, why in SPOT.items():
        reg = db.get(Regulation, rid)
        if reg:
            base = (reg.source or "").split("｜核对结论：")[0]
            reg.source = f"{base}｜核对结论：{why}"[:200]
            out["spot"].append(reg.title[:24])
    db.commit()
finally:
    db.close()
retrieve.invalidate_corpus()
print(json.dumps(out, ensure_ascii=False, indent=1))
