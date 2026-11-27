import sqlite3
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
tables = [r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
total = 0
for t in tables:
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
    if not cols:
        continue
    rows = cur.execute(f"SELECT {','.join(cols)} FROM {t}").fetchall()
    for row in rows:
        rid = row[0]
        upd = {}
        for c, v in zip(cols, row):
            if isinstance(v, str):
                nv = fix(v)
                if nv != v:
                    upd[c] = nv
        if upd:
            setc = ", ".join(f"{k}=?" for k in upd)
            cur.execute(f"UPDATE {t} SET {setc} WHERE {cols[0]}=?", list(upd.values()) + [rid])
            total += 1

con.commit()
con.close()
print("tables scanned:", len(tables))
print("rows updated:", total)
