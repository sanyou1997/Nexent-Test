import sqlite3, json
from pathlib import Path

# 路径自适应：以脚本自身位置推导，避免项目迁移后失效
DB = str(Path(__file__).resolve().parent / "backend" / "data" / "ctas.db")

REPL = [
    ("肇庆市端州区", "测试地区一区"),
    ("肇庆市鼎湖区", "测试地区二区"),
    ("肇庆市高要区", "测试地区三区"),
    ("肇庆市四会市", "测试地区四区"),
    ("肇庆市广宁县", "测试地区五区"),
    ("肇庆市德庆县", "测试地区六区"),
    ("肇庆市封开县", "测试地区七区"),
    ("肇庆市怀集县", "测试地区八区"),
    ("肇庆市辖", "测试地区"),
    ("肇庆高新区", "测试地区九区"),
    ("肇庆市", "测试地区"),
    ("端州区", "测试地区一区"),
    ("鼎湖区", "测试地区二区"),
    ("高要区", "测试地区三区"),
    ("四会市", "测试地区四区"),
    ("广宁县", "测试地区五区"),
    ("德庆县", "测试地区六区"),
    ("封开县", "测试地区七区"),
    ("怀集县", "测试地区八区"),
    ("高新区", "测试地区九区"),
    ("市审计局", "测试地区审计局"),
    ("市级", "测试地区"),
    ("肇庆", "测试地区"),
]


def fix(s):
    if not isinstance(s, str):
        return s
    for a, b in REPL:
        if a in s:
            s = s.replace(a, b)
    return s


con = sqlite3.connect(DB)
cur = con.cursor()
TEXT_COLS = ["region", "title", "audited_org", "findings", "conclusion", "content"]
rows = cur.execute(
    "SELECT id, region, title, audited_org, findings, conclusion, content, tags "
    "FROM historical_reports"
).fetchall()

n_upd = 0
for r in rows:
    rid = r[0]
    upd = {}
    for i, col in enumerate(TEXT_COLS):
        v = r[i + 1]
        if isinstance(v, str):
            nv = fix(v)
            if nv != v:
                upd[col] = nv
    tags = r[7]
    if isinstance(tags, str) and tags:
        try:
            arr = json.loads(tags)
            narr = [fix(t) if isinstance(t, str) else t for t in arr]
            if narr != arr:
                upd["tags"] = json.dumps(narr, ensure_ascii=False)
        except Exception:
            pass
    if upd:
        setc = ", ".join(f"{k}=?" for k in upd)
        cur.execute(f"UPDATE historical_reports SET {setc} WHERE id=?", list(upd.values()) + [rid])
        n_upd += 1

con.commit()
con.close()
print(f"rows updated: {n_upd}")
