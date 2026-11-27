"""一次性导出演示数据夹具（JSON，随代码版本化）。

用法（在 backend 目录）：
  python scripts/export_demo_fixtures.py --regulations <客户 demo.db> --showcase <CTAS ctas.db>

产物：
  app/fixtures/regulations.json   —— 法规与条款全集（客户 demo.db：21 部 / 1537 条）
  app/fixtures/showcase.json      —— 审理全流程示范案件（CTAS：案件、审理事项、送审材料、文书及版本、人员档案）
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CTAS_DB_PATH", os.path.join(os.path.dirname(__file__), "_unused.db"))

from sqlalchemy import create_engine, inspect, select  # noqa: E402

from app.database import Base  # noqa: E402
import app.models  # noqa: E402,F401

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "fixtures")


def _plain(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v


def dump(engine, table_name, where=None):
    t = Base.metadata.tables[table_name]
    src_cols = {c["name"] for c in inspect(engine).get_columns(table_name)}
    cols = [c for c in t.columns if c.name in src_cols]
    q = select(*cols)
    if where is not None:
        q = q.where(where(t))
    with engine.connect() as conn:
        return [{k: _plain(v) for k, v in r._mapping.items()} for r in conn.execute(q)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regulations", required=True)
    ap.add_argument("--showcase", required=True)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    reg = create_engine(f"sqlite:///{a.regulations}")
    data = {"regulations": dump(reg, "regulations"), "regulation_articles": dump(reg, "regulation_articles")}
    for r in data["regulation_articles"]:
        r.pop("tf_vector", None)  # 向量启动时重算，不入夹具
    json.dump(data, open(os.path.join(OUT, "regulations.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print("regulations:", len(data["regulations"]), "articles:", len(data["regulation_articles"]))

    sc = create_engine(f"sqlite:///{a.showcase}")
    show = {name: dump(sc, name) for name in (
        "cases", "trials", "trial_materials", "documents", "document_versions",
        "person_relations", "person_archives", "integrity_reply_records")}
    users = {r["id"]: r["username"] for r in dump(sc, "users")}
    show["_usernames"] = users
    json.dump(show, open(os.path.join(OUT, "showcase.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print({k: len(v) for k, v in show.items()})


if __name__ == "__main__":
    main()
