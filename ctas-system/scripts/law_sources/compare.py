"""平台法规库 ↔ 官方全文 逐条比对（去空白、统一全半角标点后比较）。"""
import difflib
import json
import re
import sys
from pathlib import Path

D = Path(__file__).parent
lib = json.load(open(D / "library.json", encoding="utf-8"))
PAIRS = {8: "dnjd", 9: "jbkg", 11: "jjcf", 12: "xsgz", 22: "zwcf", 23: "sydw", 24: "gqcf", 25: "cfqx",
         10: "jcfss2021", 27: "jcf2018"}
NAMES = {8: "党内监督条例", 9: "检举控告工作规则", 11: "纪律处分条例", 12: "巡视工作条例", 22: "政务处分法",
         23: "事业单位工作人员处分规定", 24: "国企管理人员处分条例", 25: "处分批准权限和程序规定",
         10: "监察法实施条例(库:2025 vs 官方:2021)", 27: "监察法(库:2024 vs 官方:2018)"}


def norm(s):
    s = re.sub(r"\s+", "", s or "")
    tr = str.maketrans({",": "，", ";": "；", ":": "：", "(": "（", ")": "）", "?": "？", "!": "！"})
    return s.translate(tr)


report = {}
for rid, key in PAIRS.items():
    off = {a["no"]: a["content"] for a in json.load(open(D / f"{key}.json", encoding="utf-8"))["articles"]}
    mine = {a["no"]: a["content"] for a in lib if a["rid"] == rid}
    same, diff, missing, extra = 0, [], sorted(set(off) - set(mine)), sorted(set(mine) - set(off))
    for no, txt in mine.items():
        if no not in off:
            continue
        a, b = norm(txt), norm(off[no])
        if a == b:
            same += 1
        else:
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            diff.append((no, round(ratio, 3), len(a), len(b)))
    report[rid] = {"name": NAMES[rid], "lib": len(mine), "official": len(off), "identical": same,
                   "differ": len(diff), "missing_in_lib": missing[:5], "extra_in_lib": extra[:5],
                   "worst": sorted(diff, key=lambda x: x[1])[:4]}
    print(f"[{rid}] {NAMES[rid]}：库 {len(mine)} 条 / 官方 {len(off)} 条；完全一致 {same}，有差异 {len(diff)}"
          + (f"；缺 {len(missing)}" if missing else "") + (f"；多 {len(extra)}" if extra else "")
          + (f"；差异最大 {sorted(diff, key=lambda x: x[1])[:3]}" if diff else ""))
json.dump(report, open(D / "compare_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
if len(sys.argv) > 2:  # 查看某条差异：compare.py <rid> <条号>
    rid, no = int(sys.argv[1]), sys.argv[2]
    off = {a["no"]: a["content"] for a in json.load(open(D / f"{PAIRS[rid]}.json", encoding="utf-8"))["articles"]}
    mine = {a["no"]: a["content"] for a in lib if a["rid"] == rid}
    for op in difflib.ndiff([norm(mine.get(no, ""))], [norm(off.get(no, ""))]):
        print(op[:600])
