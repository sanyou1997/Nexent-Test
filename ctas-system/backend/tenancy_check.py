"""部门（租户）隔离自检：功能模块把关、数据隔离、移交授权、数据范围配置、MCP 部门令牌。

用法：由 scripts/run_checks.sh 调用（一次性克隆库 ctas_check 上运行，跑完即删）；
直接运行时 DATABASE_URL 必须指向非 ctas 的库，否则拒绝执行。不调用任何外部 AI 服务。
"""
from __future__ import annotations

import os
import sys
import time

_db = os.environ.get("DATABASE_URL", "")
if not _db or _db.rstrip("/").endswith("/ctas"):
    raise SystemExit("拒绝在业务库上运行：请用 scripts/run_checks.sh（自动克隆一次性检查库 ctas_check）")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import USERS  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, note: str = "") -> bool:
    RESULTS.append((name, bool(cond), note))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{(' · ' + note) if note else ''}", flush=True)
    return bool(cond)


with TestClient(app) as c:
    creds = {u[3]: (u[0], u[1]) for u in USERS}

    def login(username, password):
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        return r

    def hdr(username, password):
        r = login(username, password)
        r.raise_for_status()
        return {"Authorization": "Bearer " + r.json()["access_token"]}, r.json()["user"]

    A, admin_me = hdr(*creds["admin"])
    XF, xf_me = hdr(*creds["xinfang"])
    SC, sc_me = hdr(*creds["banan"])
    SL, sl_me = hdr(*creds["shenli"])

    # ---------------- 1. 默认部门与账号归属 ----------------
    depts = c.get("/api/departments", headers=A).json()["items"]
    by_code = {d["code"]: d for d in depts}
    check("默认部门已建立", {"SYS", "XF", "SC", "SL"} <= set(by_code), "、".join(d["name"] for d in depts))
    users = c.get("/api/users", headers=A).json()
    check("所有账号都已归属部门", all(u["dept_id"] for u in users), f"{len(users)} 个账号")
    check("登录返回部门与模块", xf_me.get("dept_name") == "信访室" and "petitions" in (xf_me.get("modules") or [])
          and "cases" not in (xf_me.get("modules") or []), f"信访室模块 {len(xf_me.get('modules') or [])} 个")
    check("平台管理员不受模块 / 范围限制", admin_me.get("modules") is None and admin_me.get("data_scope") == "all")

    # ---------------- 2. 模块把关（接口级） ----------------
    r1, r2, r3 = c.get("/api/cases", headers=XF), c.get("/api/bank", headers=XF), c.get("/api/petitions", headers=XF)
    check("未开通模块的接口返回 403", r1.status_code == 403 and r2.status_code == 403,
          f"信访室 → 案件 {r1.status_code} / 资金 {r2.status_code}；{r1.json().get('detail', '')[:40]}")
    check("已开通模块可访问", r3.status_code == 200, f"信访室 → 信访 {r3.status_code}")
    check("非管理员不可管理部门 / 查审计", c.get("/api/departments", headers=SC).status_code == 403
          and c.get("/api/audit", headers=SC).status_code == 403)
    check("共享能力（法规库）各部门可用", all(c.get("/api/regulations", headers=h).status_code == 200 for h in (XF, SC, SL)))

    # ---------------- 3. 新部门 + 账号 ----------------
    sc = by_code["SC"]
    stamp = str(int(time.time()))[-6:]
    r = c.post("/api/departments", headers=A, json={
        "code": f"SC2_{stamp}", "name": f"第二审查调查室{stamp}", "description": "自检",
        "modules": sc["modules"], "data_scope": "self", "scope_dept_ids": []})
    check("新建部门", r.status_code == 200, r.text[:120])
    sc2 = r.json()
    pwd = "Check@" + stamp + "x"
    r = c.post("/api/users", headers=A, json={"username": f"banan2_{stamp}", "password": pwd, "real_name": "二室办案",
                                               "role": "banan", "dept_id": sc2["id"]})
    check("在新部门下建账号", r.status_code == 200 and r.json()["dept_id"] == sc2["id"], r.text[:120])
    r = c.post("/api/users", headers=A, json={"username": f"nodept_{stamp}", "password": pwd, "role": "banan"})
    check("建账号必须选部门", r.status_code == 400)
    SC2, _ = hdr(f"banan2_{stamp}", pwd)

    # ---------------- 4. 数据隔离 ----------------
    r = c.post("/api/cases", headers=SC, json={"source": "主动发现", "subject": f"隔离测试甲{stamp}", "unit": "某局",
                                                "title": "一室线索", "content": "自检", "problem_type": "违反廉洁纪律"})
    check("一室登记线索", r.status_code == 200, r.text[:100])
    cid1 = r.json()["id"]
    l2 = c.get(f"/api/cases?q=隔离测试甲{stamp}", headers=SC2).json()
    check("二室列表看不到一室线索", l2.get("total") == 0, f"total={l2.get('total')}")
    check("二室按 ID 访问一室线索 404", c.get(f"/api/cases/{cid1}", headers=SC2).status_code == 404)
    check("二室无法修改一室线索", c.post(f"/api/cases/{cid1}/advance", headers=SC2,
                                         json={"status": "已分办"}).status_code == 404)
    check("一室自己可见", c.get(f"/api/cases?q=隔离测试甲{stamp}", headers=SC).json().get("total") == 1)
    check("管理员可见全部", c.get(f"/api/cases?q=隔离测试甲{stamp}", headers=A).json().get("total") == 1)
    w_sc2 = c.get("/api/workbench", headers=SC2).json()
    check("工作台按模块分区且只统计本部门", {s["key"] for s in w_sc2["sections"]} >= {"cases"}
          and all("隔离测试甲" not in (i.get("title") or "") for s in w_sc2["sections"] for i in s["items"]),
          "、".join(s["title"] for s in w_sc2["sections"]))
    w_xf = c.get("/api/workbench", headers=XF).json()
    check("信访室工作台只有信访分区", [s["key"] for s in w_xf["sections"]] == ["petitions"])
    s_a, s_sc2 = c.get("/api/stats", headers=A).json(), c.get("/api/stats", headers=SC2).json()
    check("统计按部门隔离", s_sc2.get("petition_total") == 0 and (s_a.get("petition_total") or 0) > 0,
          f"新部门可见信访件 {s_sc2.get('petition_total')} / 全平台 {s_a.get('petition_total')}")

    # ---------------- 5. 移交：信访转线索（多个承办部门须选择） ----------------
    r = c.post("/api/petitions", headers=XF, json={"content": f"反映隔离测试乙{stamp}在工程招标中收受好处费。",
                                                    "petitioner": "李某", "channel": "来信"})
    pid = r.json()["id"]
    rc = c.get("/api/departments/receivers?modules=clue", headers=XF).json()
    rids = {i["id"] for i in rc["items"]}
    check("承办部门候选（开通线索研判的部门）", {sc["id"], sc2["id"]} <= rids and by_code["SL"]["id"] not in rids
          and rc["default"] is None,
          "、".join(i["name"] for i in rc["items"]))
    r = c.post(f"/api/petitions/{pid}/transfer-case", headers=XF, json={})
    check("多个可承办部门时须选择", r.status_code == 400, r.json().get("detail", "")[:60])
    r = c.post(f"/api/petitions/{pid}/transfer-case", headers=XF, json={"dept_id": sc2["id"]})
    check("转线索到第二审查调查室", r.status_code == 200 and r.json().get("dept_name") == sc2["name"], r.text[:120])
    case2 = r.json().get("case_id")
    check("接收部门可见新线索", c.get(f"/api/cases/{case2}", headers=SC2).status_code == 200)
    check("其他部门不可见", c.get(f"/api/cases/{case2}", headers=SC).status_code == 404)
    check("接收部门可查来源信访件（移交授权）", c.get(f"/api/petitions/{pid}", headers=SC2).status_code == 200)
    check("未接收的部门查不到该信访件", c.get(f"/api/petitions/{pid}", headers=SC).status_code == 404)
    pd = c.get(f"/api/petitions/{pid}", headers=XF).json()
    check("信访室可跟踪转出的线索", pd.get("source_case_id") == case2)

    # ---------------- 6. 移交：移送审理 ----------------
    for st in ("已分办", "初核中", "已立案"):
        c.post(f"/api/cases/{case2}/advance", headers=SC2, json={"status": st})
    r = c.post(f"/api/cases/{case2}/transfer-trial", headers=SC2, json={"title": f"隔离测试乙{stamp}案审理"})
    check("移送审理自动到案件审理室", r.status_code == 200 and (r.json().get("trial") or {}).get("dept_name") == "案件审理室",
          r.text[:120])
    tid = (r.json().get("trial") or {}).get("id")
    check("审理室可见审理事项", c.get(f"/api/trials/{tid}", headers=SL).status_code == 200)
    check("审理室可查移送的案件", c.get(f"/api/cases/{case2}", headers=SL).status_code == 200)
    check("审理室查不到未移送的案件", c.get(f"/api/cases/{cid1}", headers=SL).status_code == 404)
    check("移送部门可跟踪审理事项（移交授权）", c.get(f"/api/trials/{tid}", headers=SC2).status_code == 200)
    check("其他办案部门查不到该审理事项", c.get(f"/api/trials/{tid}", headers=SC).status_code == 404)
    check("无审理相关模块的部门接口 403", c.get(f"/api/trials/{tid}", headers=XF).status_code == 403)

    # ---------------- 7. 数据范围配置 ----------------
    sl = by_code["SL"]
    before = c.post("/api/departments/preview-scope", headers=A,
                    json={"dept_id": sl["id"], "data_scope": "self"}).json()["counts"]
    after = c.post("/api/departments/preview-scope", headers=A,
                   json={"dept_id": sl["id"], "data_scope": "custom", "scope_dept_ids": [sc["id"]]}).json()["counts"]
    check("范围预览：加入审查调查室后可见案件增加", after["cases"] > before["cases"],
          f"案件 {before['cases']} → {after['cases']}")
    body = {k: sl[k] for k in ("code", "name", "description", "modules", "enabled", "sort")}
    r = c.put(f"/api/departments/{sl['id']}", headers=A, json={**body, "data_scope": "custom", "scope_dept_ids": [sc["id"]]})
    check("保存数据范围", r.status_code == 200 and r.json()["data_scope"] == "custom", r.text[:100])
    check("审理室按新范围可见一室线索", c.get(f"/api/cases/{cid1}", headers=SL).status_code == 200)
    c.put(f"/api/departments/{sl['id']}", headers=A, json={**body, "data_scope": "self", "scope_dept_ids": []})
    check("恢复后不可见", c.get(f"/api/cases/{cid1}", headers=SL).status_code == 404)

    # ---------------- 8. 模块配置即时生效 ----------------
    xf = by_code["XF"]
    xbody = {k: xf[k] for k in ("code", "name", "description", "enabled", "sort", "data_scope", "scope_dept_ids")}
    c.put(f"/api/departments/{xf['id']}", headers=A, json={**xbody, "modules": xf["modules"] + ["bank"]})
    ok_on = c.get("/api/bank", headers=XF).status_code == 200
    me = c.get("/api/auth/me", headers=XF).json()
    c.put(f"/api/departments/{xf['id']}", headers=A, json={**xbody, "modules": xf["modules"]})
    check("开通模块即时生效（接口 + 导航）", ok_on and "bank" in (me.get("modules") or [])
          and c.get("/api/bank", headers=XF).status_code == 403)

    # ---------------- 9. 部门内唯一：各部门各自预警 / 档案 ----------------
    r1, r2 = c.post("/api/alerts/scan", headers=SC), c.post("/api/alerts/scan", headers=SC2)
    check("各部门分别扫描预警不冲突", r1.status_code == 200 and r2.status_code == 200, f"{r1.status_code}/{r2.status_code}")
    def alert_ids(h):
        d = c.get("/api/alerts", headers=h).json()
        return {a["id"] for a in (d if isinstance(d, list) else d.get("items", []))}
    a_sc, a_sc2 = alert_ids(SC), alert_ids(SC2)
    check("预警各自可见、互不重叠", not (a_sc & a_sc2), f"一室 {len(a_sc)} 条 / 二室 {len(a_sc2)} 条")

    # ---------------- 10. 停用部门 ----------------
    s2body = {k: sc2[k] for k in ("code", "name", "description", "modules", "data_scope", "scope_dept_ids", "sort")}
    c.put(f"/api/departments/{sc2['id']}", headers=A, json={**s2body, "enabled": False})
    check("部门停用后账号不能登录", login(f"banan2_{stamp}", pwd).status_code == 403)
    check("停用部门的已签发令牌也失效", c.get("/api/cases", headers=SC2).status_code == 403)
    c.put(f"/api/departments/{sc2['id']}", headers=A, json={**s2body, "enabled": True})
    r = c.delete(f"/api/departments/{sc2['id']}", headers=A)
    check("有账号 / 数据的部门不能删除", r.status_code == 400, r.json().get("detail", "")[:40])

    # ---------------- 11. MCP 部门令牌 ----------------
    tok = c.post(f"/api/departments/{sc2['id']}/mcp-token", headers=A).json()["token"]
    from app import mcp_server, tenancy  # noqa: E402
    from app.database import SessionLocal  # noqa: E402
    db = SessionLocal()
    try:
        scope = tenancy.scope_for_mcp_token(db, tok)
    finally:
        db.close()
    check("部门令牌解析为部门范围", scope is not None and scope.dept_id == sc2["id"])
    mcp_server._SCOPE.set(scope)
    mine = mcp_server.search_cases(keyword=f"隔离测试乙{stamp}")
    other = mcp_server.search_cases(keyword=f"隔离测试甲{stamp}")
    check("MCP 只返回本部门数据", len(mine) == 1 and len(other) == 0, f"本部门 {len(mine)} / 他部门 {len(other)}")
    mcp_server._SCOPE.set(None)
    check("平台级令牌可见全部", len(mcp_server.search_cases(keyword=f"隔离测试甲{stamp}")) == 1)
    db = SessionLocal()
    try:
        check("错误令牌不解析", tenancy.scope_for_mcp_token(db, tok + "x") is None)
    finally:
        db.close()

passed = sum(1 for _, ok, _ in RESULTS if ok)
print(f"\n部门隔离自检：{passed}/{len(RESULTS)} 通过")
sys.exit(0 if passed == len(RESULTS) else 1)
