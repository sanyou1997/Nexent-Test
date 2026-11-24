"""Exact, offline checks. Document coordinates are original text line numbers."""
import json, re, sys
from decimal import Decimal
from pathlib import Path
from collect import cn
ROOT=Path(__file__).resolve().parent
ROWS=json.loads((ROOT/"法规快照/articles.json").read_text(encoding="utf-8"))
BY_KEY={(r["title"],str(r["number"])):r for r in ROWS}
NUMS={cn(i):str(i) for i in range(1,1000)}
HEADINGS={
    "人员履历": ("人员履历","基本情况","被审查人基本情况","被审查调查人基本情况"),
    "事实": ("事实","主要事实","事实及争议","违纪事实"),
    "证据": ("证据","证据材料","随卷证据","证据目录","随卷材料"),
    "引用依据": ("引用依据","定性依据","依据及拟处理意见","定性及依据"),
    "处理建议": ("处理建议","处理意见","拟处理意见","处理建议及理由"),
}
AMOUNT=r"(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d{1,2})?"
def money(value):
    return Decimal(value.replace(",","").replace("，",""))
def article(title,number):
    return BY_KEY.get((title,str(number)),{"status":"快照未收录","title":title,"number":number})
def review(report,lookup=article):
    findings=[]; cited=[]; dates=[]; attachments=set(); mentioned=[]; amounts=[]; total=None
    def flag(line,code,detail): findings.append(dict(line=line,code=code,detail=detail))
    lines=report.splitlines()
    headers=set()
    in_facts=False
    for n,line in enumerate(lines,1):
        heading=re.match(r"^\s*(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)\s*([^：:。]{1,28})\s*$",line)
        if heading:
            title=heading[1]
            for section,aliases in HEADINGS.items():
                if any(alias in title for alias in aliases):headers.add(section)
            in_facts=any(alias in title for alias in HEADINGS["事实"])
        if in_facts:
            for y,m,d in re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日",line):
                dates.append(f"{y}-{int(m):02d}-{int(d):02d}")
        if line.startswith("行为日期："): dates+=re.findall(r"\d{4}-\d\d-\d\d",line)
        for m in re.finditer(r"《([^》]+)》\s*第([一二三四五六七八九十百零\d]+)条",line):
            title,number=m.groups();row=lookup(title,NUMS.get(number,number))
            if "id" not in row:
                flag(n,"查无条文",m.group()+" 在本快照中无匹配，需补充版本");continue
            cited.append(dict(line=n,**row))
            tail=line[m.end():]
            if re.match(r"\s*(?:之[一二三四五六七八九十\d]+|第[一二三四五六七八九十\d]+[款项])",tail):
                flag(n,"细分条款待核","已定位整条，款项及其适用性需复核")
            quote=re.match(r"\s*[：:，,]?\s*[“「]([^”」]+)[”」]",tail)
            if quote and quote[1] not in row['text']:flag(n,"引文不符",quote[1])
        attachment=re.match(r"附件\s*(E\d+)\s*[｜|]",line)
        if attachment:
            if attachment[1] in attachments:flag(n,"重复编号",attachment[1])
            attachments.add(attachment[1])
            am=re.search(r"入账金额[：:]\s*("+AMOUNT+r")元",line)
            if am: amounts.append(money(am[1]))
        else:
            mentioned += [(n,e) for e in re.findall(r"\bE\d+\b",line)]
        m=re.search(r"(?:报告所列入账合计[：:]|两笔收款共计(?:人民币)?)\s*("+AMOUNT+r")元",line)
        if m: total=(n,money(m[1]))
    for c in cited:
        if any(d<c['effective'] for d in dates):
            flag(c["line"],"版本待核","存在早于所引版本施行日的行为；需查对应历史版本及衔接条款")
    for n,e in mentioned:
        if e not in attachments:flag(n,"附件未提供",e)
    if total and amounts and sum(amounts)!=total[1]:
        flag(total[0],"合计不符",f"已列入账凭证金额之和 {sum(amounts)} 元，报告 {total[1]} 元；需核对统计范围")
    for section in ["人员履历","事实","证据","引用依据","处理建议"]:
        if section not in headers and not any(re.match(r"^\s*"+section+r"(?:\s|[：:]|$)",s) for s in lines):flag(None,"模板栏目未识别",section)
    if not cited:flag(None,"未取得可核条文","请核对完整法规名、版本、条号及输入格式")
    return dict(scope="仅检查本次输入、已识别的明确引文和模板字段。附件为文本摘录。事实认定、证据合法性和适用意见待人工复核。",findings=findings,citations=cited,lines=len(lines))
if __name__=="__main__":
    print(json.dumps(review(Path(sys.argv[1]).read_text(encoding="utf-8-sig")),ensure_ascii=False,indent=2))
