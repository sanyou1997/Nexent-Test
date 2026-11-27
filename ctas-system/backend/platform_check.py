"""纪检监察业务辅助平台 · 全模块功能检查（在容器内、对一次性克隆库运行；不触碰业务库）。

用法：由 scripts/run_checks.sh 调用（自动克隆 ctas → ctas_check、拷入 testdata、跑完删除克隆库）。
直接运行时 DATABASE_URL 必须指向非 ctas 的库，否则拒绝执行。

逐模块调用真实接口（含 OCR / ASR / Nexent 场景智能体等外部服务），输出：
  可用 / 部分可用 / 不可用 + 说明 + 耗时；末尾汇总，并把结果写入 /tmp/platform_check.json。
标注 [AI] 的项依赖 Nexent 场景智能体（开发环境为线上 API，部署后切本地模型）。
"""
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

_db = os.environ.get("DATABASE_URL", "")
if not _db or urllib.parse.urlparse(_db.replace("+psycopg", "")).path.strip("/") in ("", "ctas"):
    raise SystemExit("拒绝在业务库上运行：请用 scripts/run_checks.sh（自动克隆一次性检查库 ctas_check）")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import USERS  # noqa: E402

TD = Path(os.environ.get("CTAS_TESTDATA", "/tmp/testdata"))
RESULTS: list[dict] = []
ONLY = set(filter(None, os.environ.get("CHECK_ONLY", "").split(",")))


def rec(group, feature, status, note, t0, ai=False):
    RESULTS.append({"group": group, "feature": feature, "status": status, "note": note, "ai": ai,
                    "ms": int((time.time() - t0) * 1000)})
    print(f"[{status}] {group} · {feature}{' [AI]' if ai else ''} · {note} ({RESULTS[-1]['ms']} ms)", flush=True)


def run(group, feature, fn, ai=False):
    if ONLY and group not in ONLY:
        return
    t0 = time.time()
    try:
        status, note = fn()
    except Exception as e:  # noqa: BLE001
        status, note = "不可用", f"{type(e).__name__}: {str(e)[:200]}"
    rec(group, feature, status, note, t0, ai)


def ok(r):
    return 200 <= r.status_code < 300


def grade(cond_full, cond_partial=True):
    return "可用" if cond_full else ("部分可用" if cond_partial else "不可用")


def up(c, path, rel, H, data=None, timeout=None):
    f = TD / rel
    return c.post(path, headers=H, files={"file": (f.name, f.read_bytes(), "application/octet-stream")}, data=data or {})


with TestClient(app) as c:
    creds = {u[3]: (u[0], u[1]) for u in USERS}

    def login(role):
        u, p = creds[role]
        r = c.post("/api/auth/login", json={"username": u, "password": p})
        r.raise_for_status()
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    A = login("admin")
    ROLE = {r: login(r) for r in ("xinfang", "banan", "shenli") if r in creds}
    H = ROLE.get("shenli", A)

    # ================= 工作台 =================
    def f_wb():
        out = []
        for role, h in [("admin", A), *ROLE.items()]:
            d = c.get("/api/workbench", headers=h).json()
            secs = d.get("sections") or []
            out.append(f"{role}:{len(secs)}区/{sum(s.get('total', 0) for s in secs)}项")
        return "可用", "；".join(out)
    run("工作台", "按角色待办", f_wb)

    def f_screen():
        r = c.get("/api/stats", headers=A)
        return grade(ok(r), False), f"驾驶舱统计 {r.status_code}"
    run("工作台", "领导驾驶舱（大屏）", f_screen)

    # ================= 信访受理 =================
    def f_pet_parse():
        r1 = up(c, "/api/petitions/parse", "07_信访材料/检举控告信_打印件.pdf", A)
        r2 = up(c, "/api/petitions/parse", "07_信访材料/检举控告信_手写件.jpg", A)
        t1, t2 = (r1.json().get("text", "") if ok(r1) else ""), (r2.json().get("text", "") if ok(r2) else "")
        return grade(len(t1) > 80 and len(t2) > 40, len(t1) > 80 or len(t2) > 40), \
            f"打印件 PDF {len(t1)} 字（{r1.json().get('engine', '') if ok(r1) else r1.status_code}）；手写件 {len(t2)} 字"
    run("信访受理", "材料识别（PDF / 手写图片）", f_pet_parse)

    PID = {}

    def f_pet_flow():
        txt = (TD / "07_信访材料/来访登记情况说明.txt").read_text(encoding="utf-8", errors="ignore")
        r = c.post("/api/petitions", headers=A, json={"content": txt, "petitioner": "张某", "channel": "来访", "petitioner_type": "实名",
                                                      "contact": "138****0000"})
        if not ok(r):
            return "不可用", f"登记失败 {r.status_code} {r.text[:100]}"
        pid = PID["id"] = r.json()["id"]
        steps = []
        for p, body in (("classify", {}), ("check-duplicate", {}), ("screen", {"decision": "受理", "reason": "属于受理范围"})):
            rr = c.post(f"/api/petitions/{pid}/{p}", headers=A, json=body)
            steps.append(f"{p}:{rr.status_code}")
        d = c.get(f"/api/petitions/{pid}", headers=A)
        bad = [s for s in steps if not s.endswith(":200")]
        return grade(not bad and ok(d), ok(d)), "；".join(steps) + f"；详情 {d.status_code}"
    run("信访受理", "登记 → 分类 / 查重 / 受理甄别", f_pet_flow)

    def f_pet_ai():
        pid = PID.get("id")
        if not pid:
            return "不可用", "无登记件"
        r = c.post(f"/api/petitions/{pid}/extract", headers=A, json={})
        s = c.post(f"/api/petitions/{pid}/summarize", headers=A, json={})
        el = r.json().get("elements") if ok(r) else None
        mode = (s.json().get("summary_mode") or s.json().get("mode")) if ok(s) else s.status_code
        g = c.post(f"/api/petitions/{pid}/suggest", headers=A, json={})
        return grade(ok(r) and bool(el) and ok(s) and mode == "llm" and ok(g), ok(r) and ok(s)), \
            f"要素抽取 {r.status_code}（{len(el or {})} 项）；摘要 {s.status_code}（模式 {mode}）；分办建议 {g.status_code}"
    run("信访受理", "要素抽取 + 摘要 + 分办建议", f_pet_ai, ai=True)

    def f_pet_misc():
        a, b, e = c.get("/api/petitions?per_page=5", headers=A), c.get("/api/petitions/stats", headers=A), c.get("/api/petitions/export", headers=A)
        return grade(ok(a) and ok(b) and ok(e)), f"列表 {a.json().get('total') if ok(a) else a.status_code} 件；统计 {b.status_code}；导出 {e.status_code}"
    run("信访受理", "清单 / 统计 / 导出", f_pet_misc)

    # ================= 线索研判 / 案件管理 =================
    def f_clue():
        lst = c.get("/api/clue?per_page=50", headers=A).json()
        items = lst.get("items") or []
        rank = c.get("/api/clue/rank?limit=5", headers=A)
        prof = c.get(f"/api/clue/{items[0]['id']}", headers=A) if items else None
        return grade(bool(items) and ok(rank) and prof is not None and ok(prof)), \
            f"线索 {lst.get('total', len(items))} 条；风险排行 {rank.status_code}；画像 {prof.status_code if prof else '—'}"
    run("线索研判", "风险评分 / 画像", f_clue)

    def f_cases():
        lst = c.get("/api/cases?per_page=20", headers=A).json()
        items = lst.get("items") or []
        cid = items[0]["id"] if items else 0
        parts = [c.get(p, headers=A).status_code for p in
                 ("/api/cases/stats", f"/api/cases/{cid}", f"/api/cases/{cid}/timeline", f"/api/cases/{cid}/interrogations", "/api/cases/export")]
        return grade(bool(items) and all(200 <= x < 300 for x in parts)), f"案件 {lst.get('total')} 件；统计/详情/轨迹/讯问/导出 {parts}"
    run("案件管理", "台账 / 详情 / 轨迹 / 讯问 / 导出", f_cases)

    def f_ctx():
        cid = (c.get("/api/cases?per_page=1", headers=A).json().get("items") or [{}])[0].get("id")
        pid = PID.get("id") or (c.get("/api/petitions?per_page=1", headers=A).json().get("items") or [{}])[0].get("id")
        out = []
        for kind, sid in (("case", cid), ("petition", pid), ("trial", 1)):
            r = c.get(f"/api/nexent/context/{kind}/{sid}", headers=A)
            out.append((ok(r) and (r.json().get("chars") or 0) > 100, f"{kind}:{r.json().get('chars') if ok(r) else r.status_code}字"))
        return grade(all(x[0] for x in out)), "上下文 " + "；".join(x[1] for x in out)
    run("案件管理", "AI 研判上下文（审理 / 线索案件 / 信访）", f_ctx)

    # ================= 审理 =================
    def f_trial():
        tid = 2
        steps = []
        for path in ("summarize", "evidence-check", "procedure-check", "sentencing-ref"):
            r = c.post(f"/api/trials/{tid}/{path}", headers=H, json={})
            steps.append(f"{path}:{r.status_code}")
        r = c.get(f"/api/trials/{tid}/sentencing-reasoning", headers=H)
        steps.append(f"量纪说理:{r.status_code}")
        cat = r.json().get("category", "—") if ok(r) else "—"
        r = c.post(f"/api/trials/{tid}/report", headers=H, json={"note": "", "template_id": None, "refs": [], "light_keys": [], "heavy_keys": []})
        steps.append(f"报告:{r.status_code}")
        rep = c.get(f"/api/trials/{tid}/report", headers=H)
        content = rep.json().get("content", "") if ok(rep) else ""
        bad = [s for s in steps if not s.endswith(":200")]
        return grade(not bad and len(content) > 500, len(content) > 0), f"{'；'.join(steps)}；定性 {cat}；报告 {len(content)} 字"
    run("审理台账", "阅卷 → 证据链 → 程序 → 量纪 → 报告", f_trial)

    def f_trial_misc():
        a = c.get("/api/trials?per_page=50", headers=H)
        s = c.get("/api/trials/stats", headers=H)
        ap = c.get("/api/appeals", headers=H)
        return grade(ok(a) and ok(s) and ok(ap)), f"审理事项 {a.json().get('total') if ok(a) else a.status_code}；统计 {s.status_code}；申诉复查 {ap.json().get('total') if ok(ap) else ap.status_code} 件"
    run("审理台账", "台账 / 统计 / 申诉复查", f_trial_misc)

    def f_mat():
        ck = c.get("/api/trial-materials/checklist", headers=H).json()
        items = ck.get("required") if isinstance(ck, dict) else ck
        cat = (ck.get("categories") or ["审查调查报告"])[0] if isinstance(ck, dict) else "审查调查报告"
        r = c.post("/api/trial-materials", headers=H, json={"trial_id": 2, "category": cat, "name": "功能检查-材料"})
        if not ok(r):
            return "不可用", f"登记材料失败 {r.status_code} {r.text[:100]}"
        mid = r.json().get("id")
        r2 = up(c, f"/api/trial-materials/{mid}/ocr", "01_手写材料_OCR/打印件_公文_对照样张.png", H)
        if not ok(r2):
            return "部分可用", f"清单 {len(items or [])} 项；材料 OCR {r2.status_code} {r2.text[:120]}"
        n = r2.json().get("ocr_chars") or 0
        comp = c.post("/api/trial-materials/completeness", headers=H, json={"trial_id": 2})
        return grade(ok(r2) and n > 50 and ok(comp), ok(r2)), f"清单 {len(items or [])} 项；材料 OCR {r2.status_code}，{n} 字；完整度 {comp.status_code}"
    run("送审材料", "登记 + 材料 OCR + 完整度", f_mat)

    # ================= 党风政风 / 巡察 =================
    def f_conduct():
        a, s = c.get("/api/conduct", headers=A), c.get("/api/conduct/stats", headers=A)
        return grade(ok(a) and ok(s)), f"台账 {a.json().get('total', len(a.json().get('items', []))) if ok(a) else a.status_code}；统计 {s.status_code}"
    run("党风政风", "台账 / 统计", f_conduct)

    def f_insp():
        a, s, cp = c.get("/api/inspections", headers=A), c.get("/api/inspections/stats", headers=A), c.get("/api/inspections/common-problems", headers=A)
        return grade(ok(a) and ok(s) and ok(cp)), f"巡察 {a.json().get('total', '—') if ok(a) else a.status_code}；统计 {s.status_code}；共性问题 {cp.status_code}"
    run("巡察整改", "台账 / 统计 / 共性问题", f_insp)

    # ================= 监督预警 =================
    DS = {}

    def f_datasets():
        out, good = [], 0
        for fname, expect in [("公职人员名册.xlsx", 8), ("惠民补贴发放明细.xlsx", 8), ("房产登记信息.xlsx", 6), ("工商登记_法人及股东.xlsx", 5)]:
            f = TD / "08_监督模型数据集" / fname
            r = c.post(f"/api/datasets/upload?name={urllib.parse.quote('检查-' + fname)}&note=TestData", headers=A,
                       files={"file": (fname, f.read_bytes(), "application/octet-stream")})
            rows = r.json().get("row_count") if ok(r) else None
            DS[fname] = r.json() if ok(r) else {}
            good += rows == expect
            out.append(f"{fname.split('.')[0]} {rows}/{expect}")
        return grade(good == 4, good > 0), "；".join(out)
    run("监督模型", "数据集接入（4 张表）", f_datasets)

    MODEL = {}

    def f_model():
        a, b = DS.get("公职人员名册.xlsx", {}), DS.get("惠民补贴发放明细.xlsx", {})
        if not a.get("id") or not b.get("id"):
            return "不可用", "数据集未接入"
        r = c.post("/api/supervise/models/build", headers=A, json={
            "name": "检查-公职人员违规领取惠民补贴", "dataset_a": a["id"], "dataset_b": b["id"],
            "join_key_a": "身份证号", "join_key_b": "身份证号", "rule": "intersection", "severity": "高"})
        if not ok(r):
            return "不可用", f"建模失败 {r.status_code} {r.text[:120]}"
        key = MODEL["key"] = r.json()["key"]
        run_ = c.post(f"/api/supervise/models/{key}/run", headers=A, json={})
        hits = run_.json().get("total_hits") if ok(run_) else run_.text[:80]
        made = run_.json().get("upserted") if ok(run_) else None
        return grade(hits == 5 and (made or 0) > 0, ok(run_)), f"名册∩补贴 命中 {hits}（期望 5）；写入预警中枢 {made} 条"
    run("监督模型", "零代码碰撞建模 → 运行 → 生成预警", f_model)

    def f_alerts():
        sc = c.post("/api/alerts/scan", headers=A, json={})
        lst = c.get("/api/alerts", headers=A)
        hub, st = c.get("/api/alerts/hub", headers=A), c.get("/api/alerts/stats", headers=A)
        body = lst.json() if ok(lst) else None
        n = (len(body) if isinstance(body, list) else body.get("total")) if ok(lst) else lst.status_code
        return grade(ok(sc) and ok(lst) and ok(hub) and ok(st)), f"扫描 {sc.status_code}；预警 {n} 条；中枢 {hub.status_code}；统计 {st.status_code}"
    run("预警中枢", "扫描 / 列表 / 中枢 / 统计", f_alerts)

    def f_bank():
        out, good = [], 0
        cases = [("工商银行_个人流水_2024.xlsx", 26), ("招商银行_个人流水_2024.csv", 10), ("建设银行_对公流水_多行抬头.xlsx", 9),
                 ("农业银行_流水_GBK编码.csv", 8), ("邮政储蓄银行_流水_收支两列.xlsx", 9)]
        for fname, expect in cases:
            r = up(c, "/api/bank/parse", f"02_银行流水/{fname}", A)
            rows = (r.json().get("qc") or {}).get("parsed_rows") if ok(r) else None
            good += rows == expect
            out.append(f"{fname[:4]} {rows}/{expect}")
        r = up(c, "/api/bank/parse", "02_银行流水/农商行_非标表头_需人工映射.xlsx", A)
        pend = r.json().get("pending") if ok(r) else None
        out.append(f"农商行待映射 {pend}")
        pr = up(c, "/api/bank/parse", "02_银行流水/工商银行_个人流水_2024.xlsx", A).json()
        cf = up(c, "/api/bank/confirm", "02_银行流水/工商银行_个人流水_2024.xlsx", A,
                data={"mapping": json.dumps(pr.get("suggested") or {}, ensure_ascii=False), "header_row": str(pr.get("header_row") or 0)})
        out.append(f"入库 {cf.status_code}{'' if ok(cf) else ' ' + cf.text[:100]}")
        return grade(good == len(cases) and ok(cf), good > 0), "；".join(out)
    run("资金分析", "6 家银行流水解析 + 入库分析", f_bank)

    def f_integrity():
        lst = c.get("/api/integrity/list", headers=A)
        people = lst.json() if ok(lst) else []
        people = people.get("items", people) if isinstance(people, dict) else people
        if not people:
            return "部分可用", f"画像人员 0（列表 {lst.status_code}）"
        name = people[0].get("name") or people[0].get("person")
        q = urllib.parse.quote(name)
        parts = [c.get(f"/api/integrity/{q}/{p}", headers=A).status_code for p in ("portrait", "archive", "graph", "supervision")]
        return grade(all(200 <= x < 300 for x in parts) and len(people) > 2, True), f"画像人员 {len(people)} 人；{name}：画像/档案/关系图/监督 {parts}"
    run("廉政档案", "人员画像 / 档案 / 关系图", f_integrity)

    # ================= 材料与文书 =================
    def f_ocr():
        out, good = [], 0
        for rel, need in (("01_手写材料_OCR/手写_检举控告信.jpg", 100), ("01_手写材料_OCR/打印件_公文_对照样张.png", 100),
                          ("01_手写材料_OCR/手写_谈话笔录_扫描件_2页.pdf", 150)):
            r = up(c, "/api/ocr", rel, A)
            n = len(r.json().get("text", "")) if ok(r) else 0
            good += n >= need
            out.append(f"{Path(rel).stem[:8]} {n}字{'' if ok(r) else '(' + str(r.status_code) + ')'}")
        return grade(good == 3, good > 0), "；".join(out)
    run("材料识别", "手写 / 打印 / 多页 PDF", f_ocr)

    def f_asr():
        out, good = [], 0
        for rel in ("06_录音转录/谈话录音_片段1.mp3", "06_录音转录/谈话录音_片段1.wav"):
            r = up(c, "/api/asr", rel, A)
            t = r.json().get("text", "") if ok(r) else ""
            good += len(t) > 20
            out.append(f"{Path(rel).suffix} {len(t)}字{'' if ok(r) else '(' + str(r.status_code) + ' ' + r.text[:60] + ')'}")
        return grade(good == 2, good > 0), "；".join(out)
    run("谈话录音", "音频转写（mp3 / wav）", f_asr)

    def f_minutes():
        r = up(c, "/api/transcripts/upload", "06_录音转录/谈话录音_片段2.mp3", A, data={"lang": "zh", "speaker_count": "0"})
        if not ok(r):
            return "不可用", f"上传 {r.status_code} {r.text[:80]}"
        tid = r.json()["id"]
        d = c.get(f"/api/transcripts/{tid}", headers=A).json()
        m = c.post(f"/api/transcripts/{tid}/minutes", headers=A)
        mm = c.get(f"/api/transcripts/{tid}/minutes", headers=A).json() if ok(m) else {}
        status = mm.get("minutes_status") or mm.get("status")
        body = json.dumps(mm.get("minutes") or mm, ensure_ascii=False)
        return grade(d.get("status") == "完成" and status == "完成" and len(body) > 100, d.get("status") == "完成"), \
            f"转写 {d.get('status')}（{len(d.get('text') or '')} 字）；纪要 {status}（{len(body)} 字符）"
    run("谈话录音", "转写任务 + 会议纪要", f_minutes, ai=True)

    def f_archive():
        r = up(c, "/api/archives/ingest", "09_档案数字化/归档文书_扫描件_3页.pdf", A,
               data={"title": "检查-归档扫描件", "source": "功能检查", "category": "文书档案", "secret_level": "内部", "retention": "30年"})
        n = len(r.json().get("ocr_text", "")) if ok(r) else 0
        lst = c.get("/api/archives", headers=A)
        return grade(ok(r) and n > 50 and ok(lst), ok(r)), f"入库 {r.status_code}（{r.json().get('archive_no') if ok(r) else r.text[:60]}，OCR {n} 字）；列表 {lst.status_code}"
    run("档案数字化", "扫描件 OCR 入库", f_archive)

    def f_tpl():
        r = up(c, "/api/documents/templates/upload", "04_文书模板/初步核实情况报告_模板.docx", A, data={"name": "检查-初核报告模板", "doc_type": "初核报告"})
        if not ok(r):
            return "不可用", f"上传 {r.status_code} {r.text[:100]}"
        tid, fields = r.json().get("id"), [x["key"] for x in r.json().get("fields", [])]
        sample = {"被反映人": "赵某", "线索来源": "审计移交", "承办部门": "第三纪检监察室", "日期": "2026年11月20日",
                  "核查组成员": "王某、刘某", "基本情况": "赵某，男，1978年生，某镇农业服务中心主任。",
                  "核实情况": "经核实，赵某在2024年惠农补贴发放中虚报种植面积，套取补贴资金9.2万元。",
                  "初核意见": "建议予以立案审查调查。"}
        g = c.post("/api/documents/generate", headers=A, json={"template_id": tid, "case_name": "功能检查",
                                                              "fields": {k: sample.get(k, f"{k}内容") for k in fields}})
        mode = g.json().get("mode") if ok(g) else g.text[:80]
        if ok(g) and g.json().get("llm_rejected"):
            mode = f"{mode}（润色改动了事实要素 {g.json().get('llm_missing_fields')}，已按事实保全弃用）"
        lst = c.get("/api/documents", headers=A)
        docs = lst.json() if ok(lst) else []
        docs = docs.get("items", []) if isinstance(docs, dict) else docs
        srcs = sorted({d.get("source") or "" for d in docs})
        return grade(len(fields) >= 8 and ok(g) and ("llm" in str(mode) or "事实保全" in str(mode)), ok(g)), \
            f"模板字段 {len(fields)} 个；生成 {g.status_code}（模式 {mode}）；文书库来源 {srcs}"
    run("文书库", "模板上传 → 智能生成", f_tpl, ai=True)

    def f_verify():
        out = []
        r = up(c, "/api/verify/file", "05_待校验文书/问题文书_初核报告_含错别字与标点错误.docx", A)
        items = r.json().get("items", []) if ok(r) else []
        out.append(f"错别字/标点 {len(items)} 项")
        r = up(c, "/api/verify/file", "05_待校验文书/问题文书_模板占位符未清理.txt", A)
        ph = [i for i in (r.json().get("items", []) if ok(r) else []) if i.get("category") == "占位符残留"]
        out.append(f"占位符 {len(ph)} 项")
        r = up(c, "/api/verify/file", "05_待校验文书/问题文书_条款引用存疑.docx", A)
        cite = [i for i in (r.json().get("items", []) if ok(r) else []) if i.get("category") == "条款引用"]
        out.append(f"条款引用 {len(cite)} 项")
        return grade(len(items) >= 5 and len(ph) >= 2 and len(cite) >= 2, len(items) > 0), "；".join(out)
    run("文书校验", "错别字 / 占位符 / 条款引用", f_verify)

    # ================= 知识与智能 =================
    def f_nexent():
        st = c.get("/api/nexent/status", headers=A).json()
        ag = c.get("/api/nexent/agents", headers=A)
        names = [a["name"] for a in (ag.json().get("items") or [])] if ok(ag) else []
        need = {"ctas_extract", "ctas_writer", "ctas_law_qa", "ctas_analyst"}
        auth = bool(st.get("authorized"))
        return grade(auth and need <= set(names), auth), \
            f"连接 {'正常' if auth else st.get('error') or '未就绪'}；已发布智能体 {len(names)} 个；场景智能体齐全 {need <= set(names)}"
    run("智能体中心", "Nexent 连接 / 智能体", f_nexent)

    def f_qa():
        r = c.post("/api/qa/ask", headers=A, json={"question": "党员违规收受礼金应如何处分？"})
        d = r.json() if ok(r) else {}
        hit = "九十七" in (d.get("answer") or "")
        return grade(d.get("mode") == "llm" and hit, ok(r)), f"模式 {d.get('mode')}；引用第九十七条 {hit}；答案 {len(d.get('answer') or '')} 字"
    run("法规问答", "智能体作答（同源知识库 + 条文核对）", f_qa, ai=True)

    def f_regs():
        lst = c.get("/api/regulations", headers=A)
        arts = c.get("/api/regulations/articles?q=礼金", headers=A)
        r = up(c, "/api/regulations/ingest", "03_法规与知识库/纪检监察信访举报工作规范_样本语料.md", A, data={"category": "业务工作规范"})
        n = r.json().get("article_count") if ok(r) else None
        nreg = len(lst.json()) if ok(lst) and isinstance(lst.json(), list) else (lst.json().get("total") if ok(lst) else lst.status_code)
        return grade(ok(lst) and ok(arts) and (n or 0) >= 16), f"法规 {nreg} 部；条文检索 {arts.status_code}；语料入库 {n} 条"
    run("法规库", "浏览 / 检索 / 语料入库", f_regs)

    def f_regs_restart():
        # 重启时的法规相关启动步骤（残本去重 + 夹具加载）不得改动已有法规库：
        # 曾因误判《解释》与《解释（二）》为残本，叠加夹具整库重载，每次重启都用夹具覆盖法规库
        from app import demo_data, migrate
        from app.database import SessionLocal, engine
        from app.models import Regulation, RegulationArticle
        db = SessionLocal()
        try:
            snap = lambda: (sorted(i for (i,) in db.query(Regulation.id).all()),  # noqa: E731
                            db.query(RegulationArticle.id).count(),
                            max((i for (i,) in db.query(RegulationArticle.id).all()), default=0))
            before = snap()
            removed = migrate.dedup_partial_regulations(engine)
            loaded = demo_data.load_regulations(db)
            db.expire_all()
            after = snap()
        finally:
            db.close()
        same = before == after and not removed and loaded == 0
        return grade(same, False), (f"法规 {len(before[0])} 部 / 条文 {before[1]} 条，重启步骤后不变"
                                    if same else f"去重删除 {removed}；夹具重载 {loaded} 条；前 {before[1:]} 后 {after[1:]}")
    run("法规库", "重启不覆盖法规库（界面修改保留）", f_regs_restart)

    def f_hist():
        lst = c.get("/api/historical-reports", headers=A)
        s = c.post("/api/historical-reports/search", headers=A, json={"q": "违规发放津贴补贴", "top_k": 5})
        dims = c.get("/api/historical-reports/stats/dimensions", headers=A)
        hits = len(s.json().get("hits") or []) if ok(s) else 0
        return grade(ok(lst) and hits > 0 and ok(dims)), f"报告 {lst.json().get('total') if ok(lst) else lst.status_code} 份；类案检索命中 {hits}；维度统计 {dims.status_code}"
    run("历史报告库", "列表 / 类案检索 / 统计", f_hist)

    # ================= 系统管理 =================
    def f_sys():
        parts = {p: c.get(p, headers=A).status_code for p in
                 ("/api/data-intake/overview", "/api/audit", "/api/audit/verify", "/api/users", "/api/system/runtime",
                  "/api/system/llm-mode", "/api/nexent/settings", "/api/recognition/status", "/api/recognition/settings")}
        bad = {k: v for k, v in parts.items() if not 200 <= v < 300}
        chain = c.get("/api/audit/verify", headers=A).json()
        return grade(not bad), f"{len(parts) - len(bad)}/{len(parts)} 接口正常{('；异常 ' + str(bad)) if bad else ''}；审计链校验 {chain.get('ok', chain.get('valid'))}"
    run("系统管理", "数据接入 / 审计 / 用户 / Nexent / 识别服务", f_sys)

    def f_recog():
        out = []
        for svc in ("ocr", "pdf", "asr"):
            r = c.post("/api/recognition/test", headers=A, json={"kind": svc})
            d = r.json() if ok(r) else {}
            out.append(f"{svc}:{'通' if d.get('ok') else (d.get('message') or d.get('detail') or r.status_code)}")
        return grade(all(x.endswith(":通") for x in out), any(x.endswith(":通") for x in out)), "；".join(out)
    run("系统管理", "识别服务连通（OCR / PDF / ASR）", f_recog)

    def f_mcp():
        import httpx
        r = httpx.get("http://ctas-mcp:8097/healthz", timeout=5, trust_env=False)
        r2 = httpx.get("http://ctas-mcp:8097/sse", timeout=5, trust_env=False)
        return grade(r.status_code == 200 and r2.status_code == 401), f"健康检查 {r.status_code}；未带令牌访问 {r2.status_code}（应为 401）"
    run("系统管理", "业务平台 MCP 服务（ctas_tools）", f_mcp)

total = len(RESULTS)
summary = {s: sum(1 for r in RESULTS if r["status"] == s) for s in ("可用", "部分可用", "不可用")}
print("\n==== 汇总 ====")
print(f"共 {total} 项：可用 {summary['可用']}，部分可用 {summary['部分可用']}，不可用 {summary['不可用']}")
for r in RESULTS:
    if r["status"] != "可用":
        print(f"  - [{r['status']}] {r['group']} · {r['feature']}：{r['note']}")
Path("/tmp/platform_check.json").write_text(json.dumps({"summary": summary, "results": RESULTS}, ensure_ascii=False, indent=1), encoding="utf-8")
