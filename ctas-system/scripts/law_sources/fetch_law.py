"""从权威网站（共产党员网 12371.cn / 新华网）抓取法规全文 → 解析为「章 / 条」结构 → 输出平台语料格式。

用法：python fetch_law.py <url> <out.json> [--title 标题]
输出 JSON：{url, title, raw_chars, chapters:[...], articles:[{chapter, no, content}], corpus}
平台语料格式：首行 `法规名|版本|生效日期`，`## 章名`，`【第X条】内容`。
"""
import html
import json
import re
import sys
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
CN_NUM = "零一二三四五六七八九十百千"


def fetch(url: str) -> str:
    import time
    last = None
    for i in range(5):
        for op in (urllib.request.build_opener(), urllib.request.build_opener(urllib.request.ProxyHandler({}))):
            try:
                raw = op.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read()
                break
            except Exception as e:  # 政府网站偶发重置连接
                last = e
        else:
            time.sleep(2 + i * 2)
            continue
        break
    else:
        raise last
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def body_text(page: str) -> tuple[str, str]:
    title = ""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S) or re.search(r"<title>(.*?)</title>", page, re.S)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    # 12371 正文容器 / 新华网正文容器，取不到就退回全文
    for pat in (r'<div[^>]+class="word"[^>]*>(.*?)<div[^>]+class="(?:editor|tool|share)', r'<div[^>]+id="detail"[^>]*>(.*?)</div>\s*<div',
                r'<div[^>]+id="p-detail"[^>]*>(.*?)<div[^>]+class="(?:p-tags|editor)', r'<div[^>]+class="(?:article|content|main-aticle)"[^>]*>(.*?)</div>\s*</div>'):
        m = re.search(pat, page, re.S)
        if m and len(m.group(1)) > 2000:
            seg = m.group(1)
            break
    else:
        seg = page
    seg = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", seg, flags=re.S)
    seg = re.sub(r"<br\s*/?>|</p>|</div>|</h\d>", "\n", seg)
    seg = re.sub(r"<[^>]+>", "", seg)
    seg = html.unescape(seg).replace("　", " ").replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in seg.split("\n")]
    return title, "\n".join(ln for ln in lines if ln)


ART = re.compile(r"^第([零一二三四五六七八九十百千]+)条\s*(.*)$")
CHAP = re.compile(r"^第([零一二三四五六七八九十百]+)(章|编|节)\s*(.*)$")


def parse(text: str) -> tuple[list, list]:
    chapters, articles, cur_ch, cur = [], [], "", None
    started = False
    for ln in text.split("\n"):
        mc = CHAP.match(ln)
        ma = ART.match(ln)
        if mc and len(ln) <= 40:
            name = f"第{mc.group(1)}{mc.group(2)} {mc.group(3)}".strip()
            if mc.group(2) == "节" and cur_ch:
                name = f"{cur_ch.split(' 第')[0]} {name}"
            cur_ch = name
            chapters.append(cur_ch)
            started = True
            continue
        if ma:
            started = True
            cur = {"chapter": cur_ch, "no": f"第{ma.group(1)}条", "content": ma.group(2).strip()}
            articles.append(cur)
            continue
        if started and cur is not None:
            if re.match(r"^(责任编辑|来源|【|（责任编辑|编辑：|相关链接|分享到)", ln):
                cur = None
                continue
            cur["content"] = (cur["content"] + "\n" + ln).strip()
    return chapters, articles


def to_corpus(name: str, version: str, eff: str, articles: list) -> str:
    out, ch = [f"{name}|{version}|{eff}"], None
    for a in articles:
        if a["chapter"] and a["chapter"] != ch:
            ch = a["chapter"]
            out.append(f"## {ch}")
        out.append(f"【{a['no']}】{a['content']}")
    return "\n".join(out)


if __name__ == "__main__":
    url, out = sys.argv[1], sys.argv[2]
    page = fetch(url)
    title, text = body_text(page)
    chapters, arts = parse(text)
    nums = [a["no"] for a in arts]
    json.dump({"url": url, "title": title, "raw_chars": len(text), "chapters": chapters,
               "articles": arts, "text": text}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"title": title, "chars": len(text), "chapters": len(chapters), "articles": len(arts),
                      "first": nums[:2], "last": nums[-2:]}, ensure_ascii=False))
