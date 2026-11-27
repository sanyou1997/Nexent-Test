import io
from pathlib import Path

# 路径自适应：以脚本自身位置推导，避免项目迁移后失效
BASE = str(Path(__file__).resolve().parent)
FILES = [
    "backend/app/seed_historical.py",
    "backend/app/report_extract.py",
    "backend/app/routers/historical_reports.py",
    "frontend/src/pages/CtasDashboard.tsx",
    "backend/regression_test.py",
]

# 顺序：长/具体 → 短/通用，避免"肇庆市端州区"被先拆成"肇庆市"
REPL = [
    ("app.zhaoqing", "app.test_regions"),
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

for rel in FILES:
    p = f"{BASE}/{rel}"
    with io.open(p, encoding="utf-8") as fh:
        s = fh.read()
    orig = s
    for a, b in REPL:
        if a in s:
            s = s.replace(a, b)
    if s != orig:
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write(s)
        print("updated:", rel)
    else:
        print("nochange:", rel)
print("done")
