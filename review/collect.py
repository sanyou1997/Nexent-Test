"""Build a dated snapshot from the official party rules directory."""
from pathlib import Path
from datetime import date
from zoneinfo import ZoneInfo
import hashlib, html, json, re, urllib.request

ROOT = Path(__file__).resolve().parent
INDEX = "https://www.12371.cn/special/dnfg/jdbz/"
SPECS = [("处分", "中国共产党纪律处分条例", 158, "2024-01-01"), ("执纪", "中国共产党纪律检查机关监督执纪工作规则", 77, "2019-01-01"), ("问责", "中国共产党问责条例", 27, "2019-09-01")]
NUM = "零一二三四五六七八九"
def cn(i):
    if i < 10: return NUM[i]
    if i < 100: return (NUM[i//10] if i >= 20 else "") + "十" + (NUM[i%10] if i%10 else "")
    rest = i % 100
    return NUM[i//100]+"百"+("零"+cn(rest) if 0<rest<10 else "一"+cn(rest) if 10<=rest<20 else cn(rest) if rest else "")
def get(url):
    with urllib.request.urlopen(url,timeout=25) as response:
        if response.url != url: raise ValueError("Unexpected redirect: "+response.url)
        return response.read()
def parse(raw,n):
    page=raw.decode("utf-8")
    ps=[html.unescape(re.sub("<[^>]+>","",p)).strip() for p in re.findall(r"<p(?:\s[^>]*)?>(.*?)</p>",page,re.S|re.I)]
    out=[]
    for p in ps:
        if re.match(r"^第[零一二三四五六七八九十百]+条[\s　]",p):
            if not p.startswith("第"+cn(len(out)+1)+"条"): raise ValueError(p)
            out.append([p])
        elif out:
            if len(out)==n and (p.startswith(("关于我们","延伸阅读","相关链接","相关阅读","《中国共产党纪律处分条例》学习专栏","编辑：","来源：")) or "版权所有" in p): break
            if p and not re.match(r"^第[一二三四五六七八九十百]+[编章节目]\s",p): out[-1].append(p)
    assert len(out)==n,(len(out),n)
    return ["\n".join(a) for a in out]
def main():
    dest=ROOT/"法规快照";dest.mkdir(exist_ok=True)
    directory=get(INDEX); (dest/"目录.html").write_bytes(directory)
    mapping={t:u for u,t in re.findall(r"'link_add':'([^']*)','title':'([^']*)'",directory.decode())}
    records=[];manifest=[]
    for code,title,n,start in SPECS:
        url=mapping[title]; assert url.startswith("https://www.12371.cn/")
        raw=get(url); articles=parse(raw,n)
        if code=="处分":
            mirror="https://www.12371.cn/2023/12/27/ARTI1703688691926144.shtml"
            other=parse(get(mirror),n)
            normalize=lambda s:re.sub(r"\s","",s)
            assert list(map(normalize,articles))==list(map(normalize,other)),"两份官方文本不一致，需复核"
        (dest/(code+".html")).write_bytes(raw)
        manifest.append(dict(title=title,url=url,effective=start,count=n,sha256=hashlib.sha256(raw).hexdigest()))
        for i,body in enumerate(articles,1):
            records.append(dict(id=f"{code}-{start}-{i}",title=title,number=i,effective=start,url=url,text=body))
    (dest/"articles.json").write_text(json.dumps(records,ensure_ascii=False,indent=1),encoding="utf-8")
    (dest/"manifest.json").write_text(json.dumps(dict(retrieved=date.today().isoformat(),directory=INDEX,sources=manifest),ensure_ascii=False,indent=2),encoding="utf-8")
    print(len(records))
if __name__=="__main__": main()

