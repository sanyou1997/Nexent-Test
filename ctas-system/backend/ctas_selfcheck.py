"""CTAS 自检脚本：P0 量纪说理引擎 / P1 七章报告 / P2 见面谈话 全量回归。

用法：
    Docker（PostgreSQL）：sh scripts/run_checks.sh   （在一次性克隆库 ctas_check 上运行）
    本地（SQLite）：cd backend && python ctas_selfcheck.py

设计：
- 在一次性副本库上跑完整端到端（TestClient 进程内）：PostgreSQL 用克隆库 ctas_check，
  SQLite 用 data/_selfcheck.db；绝不触碰业务库与正在运行的服务；
- A 组 = P0 引擎单元断言（确定性/兜底/长词优先/条号溯源/无 LLM 依赖）；
- B 组 = P1 报告结构断言（七章编号/公文洁净/署名/状态流转）；
- C 组 = P2 谈话断言（结构化保存/合规 warning/adopt 归一/报告附段）。
退出码：0 = 全过；1 = 有失败（明细打印到 stdout）。
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DB = os.path.join(BASE, "data", "ctas.db")
TMP_DB = os.path.join(BASE, "data", "_selfcheck.db")
_PG = os.environ.get("DATABASE_URL", "")
if _PG:
    # PostgreSQL：只允许跑在一次性克隆库上（scripts/run_checks.sh 自动克隆 ctas → ctas_check）
    from urllib.parse import urlparse
    if urlparse(_PG.replace("+psycopg", "")).path.strip("/") in ("", "ctas"):
        raise SystemExit("拒绝在业务库上运行：请用 scripts/run_checks.sh（自动克隆一次性检查库 ctas_check）")
else:
    for _suf in ("", "-wal", "-shm"):
        _p = TMP_DB + _suf
        if os.path.exists(_p):
            os.remove(_p)
    # 用 sqlite backup API 做一致性快照（源库可能带 WAL 且被服务进程持有）
    _src = sqlite3.connect(SRC_DB)
    _dst = sqlite3.connect(TMP_DB)
    _src.backup(_dst)
    _dst.close()
    _src.close()
    os.environ["CTAS_DB_PATH"] = TMP_DB
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

sys.path.insert(0, BASE)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Trial, Case, Document, DocumentVersion  # noqa: E402
from app import sentencing as S  # noqa: E402

CORPUS_JJJC = os.path.join(BASE, "data", "corpus", "official", "jjjc.txt")
CORPUS_GOV = os.path.join(BASE, "data", "corpus", "articles", "09-公职人员政务处分法-全文.txt")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    line = f"[{mark}] {name}"
    if detail and not cond:
        line += f"  <- {detail}"
    print(line)


def main():
    with TestClient(app) as client:
        # ---------- 登录 ----------
        r = client.post("/api/auth/login", json={"username": "shenli", "password": "Sl@123456"})
        check("C0 登录 shenli", r.status_code == 200, r.text[:200])
        if r.status_code != 200:
            return finish()
        H = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # ---------- 造数：TA 公职人员职务违法（完整合规谈话） ----------
        ta = client.post("/api/trials", headers=H, json={
            "subject": "自检甲", "unit": "某局原局长", "rank": "正处级", "political_status": "中共党员",
            "problem_type": "职务违法", "involved_amount": "18.6万元",
            "brief": "利用职务便利为他人谋取利益，非法收受财物，涉嫌犯罪移送司法",
        }).json()
        # ---------- 造数：TB 廉洁纪律（不合规谈话） ----------
        tb = client.post("/api/trials", headers=H, json={
            "subject": "自检乙", "unit": "某街道办副主任", "problem_type": "违反廉洁纪律",
            "brief": "违规收受管理服务对象礼金",
        }).json()
        # ---------- 造数：TC 无谈话 ----------
        tc = client.post("/api/trials", headers=H, json={
            "subject": "自检丙", "unit": "某镇党委副书记", "problem_type": "违反廉洁纪律",
            "brief": "违规收受管理服务对象礼金",
        }).json()
        # ---------- 造数：TD 非留党察看（撤销党内职务，验证 restore 不适用不阻断报结） ----------
        td = client.post("/api/trials", headers=H, json={
            "subject": "自检丁", "unit": "某局副局长", "rank": "副处级", "political_status": "中共党员",
            "problem_type": "违反廉洁纪律", "brief": "违规从事营利活动",
        }).json()

        # ============ P2：见面谈话 ============
        # TA：完整合规（2 谈话人+记录人+地点+权利告知+申辩全有结论）
        r = client.post(f"/api/trials/{ta['id']}/interview", headers=H, json={
            "attitude": "认错悔错", "note": "核对事实无出入",
            "interviewers": "张审理、李审理", "recorder": "王记录", "place": "审理室谈话室",
            "rights_notified": True,
            "defense_items": [
                {"content": "部分款项系正常人情往来", "stance": "提出申辩", "adopt": "不采纳",
                 "reason": "与银行流水及请托人证言矛盾"},
                {"content": "主动配合调查，请求从轻", "stance": "认可事实", "adopt": "采纳",
                 "reason": "符合条例第十七条"},
            ],
            "summary": "谈话全程录音录像，笔录已交本人核对签字。",
        })
        check("C1 TA 合规谈话保存 200", r.status_code == 200, r.text[:200])
        check("C1 TA 合规谈话 0 warning", r.json().get("warnings") == [],
              str(r.json().get("warnings")))

        # TB：不合规（1 谈话人+未告知权利+2 条待审议+非法 adopt 归一）
        r = client.post(f"/api/trials/{tb['id']}/interview", headers=H, json={
            "attitude": "提出申辩", "interviewers": "张审理", "rights_notified": False,
            "defense_items": [
                {"content": "礼金已当场退还", "adopt": "量子力学"},
                {"content": "数额认定偏高", "adopt": ""},
            ],
        })
        w = r.json().get("warnings") or []
        check("C2 TB 不合规谈话 3 warning", len(w) == 3, str(w))
        check("C2 TB warning 命中谈话人不足2人", any("谈话人不足 2 人" in x for x in w))
        check("C2 TB warning 命中未告知权利", any("申辩的权利" in x for x in w))
        check("C2 TB warning 命中待审议", any("待审议" in x or "尚未明确采纳结论" in x for x in w))
        # adopt 归一：非法值/空值 → 待审议
        db = SessionLocal()
        t_b = db.get(Trial, tb["id"])
        adopts = [it["adopt"] for it in (t_b.defense_items or [])]
        check("C3 adopt 非法值归一为待审议", adopts == ["待审议", "待审议"], str(adopts))
        t_a = db.get(Trial, ta["id"])
        a_adopts = [it["adopt"] for it in (t_a.defense_items or [])]
        check("C3 adopt 合法值保留", a_adopts == ["不采纳", "采纳"], str(a_adopts))
        db.close()

        # ============ P1：七章报告 ============
        r = client.post(f"/api/trials/{ta['id']}/report", headers=H, json={})
        check("B0 TA 报告生成 200", r.status_code == 200, r.text[:300])
        doc = r.json().get("document") or {}
        content = doc.get("content") or ""
        # 七章编号连续
        chaps = re.findall(r"^([一二三四五六七八])、", content, re.M)
        check("B1 报告章节连续一~七", chaps == ["一", "二", "三", "四", "五", "六", "七"],
              str(chaps))
        check("B1 报告无「四、定性量纪意见」残留", "四、定性量纪意见" not in content)
        check("B2 报告无 Python repr 泄漏", "['" not in content and "[\"" not in content)
        check("B2 报告无原始证据状态码", "有·多源印证" not in content and "有·单一来源" not in content)
        check("B2 报告无嵌套（六）小标题", "（六）处理意见与建议" not in content)
        check("B2 涉案金额已显示", "18.60" in content or "18.6" in content)
        check("B2 尾部署名为单行", "审理组负责人：　　　　　　审理人：　　　　　　报告日期：　　年　　月　　日" in content)
        # P2 附段五要素
        check("B3 谈话附段存在", "附：与被审查调查人见面谈话及申辩采纳情况" in content)
        for kw in ["2026年", "审理室谈话室", "谈话人：张审理、李审理", "记录人：王记录", "态度：认错悔错"]:
            check(f"B3 附段含「{kw}」", kw in content)
        check("B3 附段申辩逐条",
              "1. 提出申辩：部分款项系正常人情往来" in content
              and "2. 认可事实：主动配合调查，请求从轻" in content
              and "不采纳（理由：与银行流水及请托人证言矛盾）" in content
              and "采纳（理由：符合条例第十七条）" in content,
              "逐条申辩或采纳理由缺失")
        check("B3 附段合规无提示", "【程序提示】" not in content)
        # 说理稿关键要素
        check("B4 量纪双区间", "党纪区间参考【" in content and "政务处分区间参考【" in content)
        check("B4 问题性质显示承办人原文", "问题性质认定为「职务违法」" in content,
              "应回退展示 problem_type 原文而非分类标签")
        check("B4 集体审议声明", "须经审理组集体审议" in content)
        check("B4 涉案财物法条", "第四十三条" in content and "第二十五条" in content)
        # 状态流转 + 版本留痕
        db = SessionLocal()
        t_a2 = db.get(Trial, ta["id"])
        check("B5 trial 状态=已报告", t_a2.status == "已报告", t_a2.status)
        dv = db.query(DocumentVersion).filter(DocumentVersion.document_id == t_a2.report_doc_id).all()
        check("B5 DocumentVersion v1 入库", len(dv) >= 1 and dv[0].version_no == 1)
        db.close()

        # TC：无谈话 → 无附段、无旧式态度行
        r = client.post(f"/api/trials/{tc['id']}/report", headers=H, json={})
        c2 = (r.json().get("document") or {}).get("content") or ""
        check("B6 无谈话无附段", "附：与被审查调查人见面谈话" not in c2)
        check("B6 无谈话无旧式态度行", "见面谈话，其态度" not in c2)
        chaps2 = re.findall(r"^([一二三四五六七八])、", c2, re.M)
        check("B6 无谈话章节仍连续一~七", chaps2 == ["一", "二", "三", "四", "五", "六", "七"], str(chaps2))
        # 导出接口
        r = client.get(f"/api/trials/{ta['id']}/report/export", headers=H)
        check("B7 报告导出 200", r.status_code == 200, r.text[:200])

        # ============ P0：量纪说理引擎 ============
        # GET / POST reasoning
        r = client.get(f"/api/trials/{ta['id']}/sentencing-reasoning", headers=H)
        j = r.json() if r.status_code == 200 else {}
        check("A0 GET reasoning 200", r.status_code == 200, r.text[:200])
        check("A0 reasoning 双区间字段", bool(j.get("tier_range")) and bool(j.get("gov_tier_range")))
        check("A0 reasoning 情节目录", len(j.get("light_catalog") or []) == 6 and len(j.get("heavy_catalog") or []) == 5)
        r = client.post(f"/api/trials/{ta['id']}/sentencing-reasoning", headers=H,
                        json={"light_keys": ["L4"], "heavy_keys": ["H1"]})
        j2 = r.json()
        check("A5 人工认定写入成稿", "（经审理组人工认定）" in (j2.get("text") or ""))
        check("A5 人工认定 L4 条文", "第十七条（四）" in (j2.get("text") or ""))

        db = SessionLocal()
        t = db.get(Trial, ta["id"])
        c = None
        # A1 确定性：同输入两次调用完全一致
        r1 = S.build_sentencing_reasoning(t, c, db)
        r2 = S.build_sentencing_reasoning(t, c, db)
        check("A1 引擎确定性（两次调用一致）", r1["text"] == r2["text"] and r1 == r2)
        # A2 第一百一十四条关键词收紧：财政补贴审批场景不误命中
        hits = [a["no"] for a in S._match_substantive("工作纪律", "违反工作纪律",
                                                     "在财政补贴审批中把关不严")]
        check("A2 第一百一十四条不误命中财政补贴审批",
              not any("第一百一十四条" in h for h in hits), str(hits))
        check("A2 第一百一十四条正常场景仍命中",
              any("第一百一十四条" in a["no"] for a in
                  S._match_substantive("廉洁纪律", "", "违规自定薪酬滥发津补贴")))
        # A3 涉案财物长词优先
        acts = S._parse_property_actions("涉嫌犯罪问题及涉案财物随案移送司法机关")
        check("A3 长词优先：随案移送不重复列移送",
              "随案移送" in acts and "移送" not in acts, str(acts))
        # A4 金额三级兜底（trial 无金额 → 处置意见文本 → 事实文本）
        p = S.build_property_section(t, c, "职务违法", "heavy", None,
                                    "利用职务便利非法收受财物共计18.6万元")
        check("A4 金额兜底（事实文本）", p["amount_wan"] == 18.6, str(p["amount_wan"]))
        t.involved_amount = ""
        p2p = S.build_property_section(t, c, "职务违法", "heavy", None, "")
        check("A4 金额兜底（全空→None 不臆造）", p2p["amount_wan"] is None, str(p2p["amount_wan"]))
        t.involved_amount = "18.6万元"
        # A6 档位排序
        order_ok = True
        tiers = (j.get("tier_range") or "").split("、")
        idx = [S._DISCIPLINE_ORDER.index(x) for x in tiers if x in S._DISCIPLINE_ORDER]
        order_ok = order_ok and idx == sorted(idx) and len(idx) == len(tiers)
        gt = (j.get("gov_tier_range") or "").split("、")
        gidx = [S._GOV_ORDER.index(x) for x in gt if x in S._GOV_ORDER]
        order_ok = order_ok and gidx == sorted(gidx) and len(gidx) == len(gt)
        check("A6 党纪/政务档位按法定序列排序", order_ok, f"{tiers} / {gt}")
        db.close()

        # A7 条号溯源：条款文本前 12 字须在语料中
        jj = open(CORPUS_JJJC, encoding="utf-8").read()
        gov = open(CORPUS_GOV, encoding="utf-8").read()
        miss = []
        for a in S._ARTICLES:
            frag = a["text"][:12]
            if "纪律处分条例" in a["no"]:
                if frag not in jj:
                    miss.append((a["no"], frag))
            elif "政务处分法" in a["no"]:
                if frag not in gov:
                    miss.append((a["no"], frag))
        check("A7 条款文本全部可溯源至法规语料", not miss, str(miss[:3]))
        # A8 引擎零 LLM 依赖：import 语句仅允许标准库 re
        src = open(os.path.join(BASE, "app", "sentencing.py"), encoding="utf-8").read()
        imps = set()
        for m in re.finditer(r"^\s*(?:import\s+(\w+)|from\s+([\w.]+)\s+import)", src, re.M):
            imps.add(m.group(1) or m.group(2).split(".")[0])
        check("A8 引擎仅依赖标准库 re", imps <= {"re"}, str(imps))

        # ============ D 组：存量演示 trial 批量回归（真实数据） ============
        db = SessionLocal()
        existing = db.query(Trial).filter(
            Trial.id.notin_([ta["id"], tb["id"], tc["id"]])).all()
        snap = [(e.id, e.subject, bool(e.interview_at),
                 e.problem_type or ((db.get(Case, e.case_id).problem_type) if e.case_id else ""))
                for e in existing]
        db.close()
        bad = []
        for eid, subj, has_iv, pt in snap:
            try:
                rr = client.post(f"/api/trials/{eid}/report", headers=H, json={})
                if rr.status_code != 200:
                    bad.append((eid, f"HTTP {rr.status_code}"))
                    continue
                cc = (rr.json().get("document") or {}).get("content") or ""
                if "['" in cc or "[\"" in cc:
                    bad.append((eid, "repr 泄漏"))
                if "有·多源印证" in cc or "有·单一来源" in cc:
                    bad.append((eid, "原始状态码"))
                if "…" in cc:
                    bad.append((eid, "未完结省略号"))
                ch = re.findall(r"^([一二三四五六七八])、", cc, re.M)
                seq = ["一二三四五六七八".index(x) for x in ch]
                if seq != sorted(set(seq)) or not seq:
                    bad.append((eid, f"编号异常 {ch}"))
                if has_iv and "附：与被审查调查人见面谈话" not in cc:
                    bad.append((eid, "有谈话无附段"))
                if not has_iv and "附：与被审查调查人见面谈话" in cc:
                    bad.append((eid, "无谈话却有附段"))
                if pt and f"问题性质认定为「{pt}」" not in cc:
                    bad.append((eid, f"问题性质未显示原文「{pt}」"))
            except Exception as ex:
                bad.append((eid, f"异常 {type(ex).__name__}: {ex}"))
        check(f"D1 存量 {len(snap)} 条 trial 报告批量回归", not bad, str(bad[:5]))

        # ============ E 组：P3 处分决定书 + 执行回证 ============
        # 前置：TA 须定稿
        r = client.post(f"/api/trials/{ta['id']}/confirm", headers=H, json={"note": "自检定稿"})
        check("E0 TA 定稿确认", r.status_code == 200, r.text[:200])
        # 未定稿不可制作决定书 / 宣布执行
        r = client.post(f"/api/trials/{tc['id']}/decision", headers=H, json={})
        check("E3 未定稿不可制作决定书", r.status_code == 400)
        r = client.post(f"/api/trials/{tc['id']}/execute", headers=H, json={})
        check("E9 未定稿不可宣布执行", r.status_code == 400)
        # E1：空裁量生成 → needs_manual + 〔待填〕标注 + 文书入库
        r = client.post(f"/api/trials/{ta['id']}/decision", headers=H, json={})
        check("E1 处分决定书生成 200", r.status_code == 200, r.text[:300])
        dec = r.json().get("decision") or {}
        cc_dec = dec.get("content") or ""
        check("E1 空裁量标 needs_manual", dec.get("needs_manual") is True)
        check("E1 处分档次〔待填〕", "〔处分档次待处分批准机关审定后填写〕" in cc_dec)
        check("E1 文号/机关〔待填〕", "〔文号待填〕" in cc_dec and "〔处分批准机关待填〕" in cc_dec)
        check("E1 不预设处分档次", "给予自检甲党内" not in cc_dec.replace("〔处分档次待处分批准机关审定后填写〕", ""))
        check("E1 决定书文书入库", bool(r.json().get("decision_doc_id")))
        check("E1 申诉权告知（党纪口径）", "提出申诉" in cc_dec and "申诉期间，不停止本决定的执行" in cc_dec
              and "一个月" not in cc_dec)
        check("E1 事实接报告三章", "非法收受财物" in cc_dec)
        # E2：GET decision 可查看
        r = client.get(f"/api/trials/{ta['id']}/decision", headers=H)
        check("E2 GET 决定书 200 且有内容", r.status_code == 200 and bool((r.json().get("decision") or {}).get("content")))
        # E4：填齐裁量 → needs_manual=False
        r = client.post(f"/api/trials/{ta['id']}/decision", headers=H, json={
            "decision_no": "×纪决〔2026〕1号", "decision_result": "撤销党内职务处分",
            "decision_date": "2026-10-20", "decision_approver": "中共××市××区纪委"})
        dec2 = r.json().get("decision") or {}
        check("E4 填齐后 needs_manual=False", dec2.get("needs_manual") is False,
              str(re.findall(r"〔[^〕]*待[^〕]*〕", dec2.get("content") or "")))
        check("E4 标题含处分档次", "关于给予自检甲撤销党内职务处分的决定" in (dec2.get("content") or ""))
        # E13：党纪决定书业务口径——编号条款不援引政务处分法，政务处分另行作出提示
        c4 = dec2.get("content") or ""
        basis = c4.split("四、处分决定")[0]
        numbered = [l for l in basis.split("\n") if re.match(r"^\d+\.\s", l)]
        check("E13 编号依据条款不含政务处分法",
              numbered and all("政务处分法" not in l for l in numbered),
              str([l[:40] for l in numbered]))
        check("E13 编号依据条款均为纪律处分条例",
              numbered and all("纪律处分条例" in l for l in numbered))
        check("E13 政务处分另行作出提示", "另行作出" in c4)
        check("E13 从轻情节只保留党纪依据",
              all("政务处分法" not in l for l in basis.split("\n") if l.startswith("· 从轻")),
              str([l[:40] for l in basis.split("\n") if l.startswith("· 从轻")]))
        # E5：宣布执行结构化 + status 流转 + warnings
        r = client.post(f"/api/trials/{ta['id']}/execute", headers=H, json={
            "note": "已宣布", "announcer": "", "scope": "所在党支部全体党员及本人",
            "delivery": "直接送达", "signed": False})
        check("E5 宣布执行 200", r.status_code == 200, r.text[:200])
        check("E5 status→已执行", r.json().get("status") == "已执行", r.json().get("status"))
        w = r.json().get("warnings") or []
        check("E5 缺宣布人/签收 warning", any("宣布人" in x for x in w) and any("签收" in x for x in w), str(w))
        check("E5 回证清单初始化 5 项", len(r.json().get("execution_items") or []) == 5)
        # TA 此时 decision_result=撤销党内职务（非留党察看）→ restore 自动不适用，blockers=4
        check("E5 blockers=4（restore 不适用）", len(r.json().get("execution_blockers") or []) == 4)
        # E12：非法送达方式
        r = client.post(f"/api/trials/{tb['id']}/confirm", headers=H, json={})
        r = client.post(f"/api/trials/{tb['id']}/execute", headers=H, json={"delivery": "微信送达"})
        check("E12 非法送达方式 400", r.status_code == 400)
        # E14：已执行不可重复宣布
        r = client.post(f"/api/trials/{ta['id']}/execute", headers=H, json={"announcer": "重复宣布"})
        check("E14 已执行不可重复宣布 400", r.status_code == 400)
        # E15：已执行案件可补制/重制决定书（留痕新版本）
        r = client.post(f"/api/trials/{ta['id']}/decision", headers=H, json={"decision_result": "留党察看一年"})
        check("E15 已执行可补制决定书 200", r.status_code == 200, r.text[:200])
        # E6：done 无日期 → 400
        r = client.post(f"/api/trials/{ta['id']}/execution-items", headers=H,
                        json={"key": "rank_adj", "status": "done", "at": ""})
        check("E6 已落实缺日期 400", r.status_code == 400)
        # E7：done 带日期 → 200，blockers 递减
        r = client.post(f"/api/trials/{ta['id']}/execution-items", headers=H,
                        json={"key": "rank_adj", "status": "done", "at": "2026-10-22", "note": "已调整"})
        check("E7 回证回填 200", r.status_code == 200, r.text[:200])
        # 注意：E15 已将 TA 改为留党察看一年（restore 重新适用），rank done 后 blockers=4
        check("E7 blockers 递减至 4", len(r.json().get("execution_blockers") or []) == 4)
        # E8：有 pending 不得报结
        r = client.post(f"/api/trials/{ta['id']}/execution-close", headers=H, json={})
        check("E8 缺项不得报结 400", r.status_code == 400 and "不得报结" in (r.json().get("detail") or ""))
        # 其余项：pay/property/archive done，restore done（联动恢复权利时间）
        for k, at in [("pay_adj", "2026-10-22"), ("property", "2026-10-23"), ("archive", "2026-10-23")]:
            rr = client.post(f"/api/trials/{ta['id']}/execution-items", headers=H,
                             json={"key": k, "status": "done", "at": at})
            check(f"E8 {k} 回填 200", rr.status_code == 200)
        r = client.post(f"/api/trials/{ta['id']}/execution-items", headers=H,
                        json={"key": "restore", "status": "done", "at": "2027-10-22", "note": "留党察看一年期满"})
        check("E8 restore 回填 200", r.status_code == 200)
        check("E8 恢复权利时间联动", (r.json().get("restore_rights_at") or "") == "2027-10-22",
              str(r.json().get("restore_rights_at")))
        # 报结
        r = client.post(f"/api/trials/{ta['id']}/execution-close", headers=H, json={})
        check("E8 全落实可报结 200", r.status_code == 200, r.text[:200])
        check("E8 execution_closed=True", r.json().get("execution_closed") is True)
        # na 项也放行（TB 单独验证）
        r = client.post(f"/api/trials/{tb['id']}/execution-items", headers=H, json={"key": "zzz", "status": "done"})
        check("E11 未知回证项 400", r.status_code == 400)

        # ============ E16：非留党察看案件可正常报结（G1 修复验证） ============
        r = client.post(f"/api/trials/{td['id']}/confirm", headers=H, json={"note": "自检定稿"})
        r = client.post(f"/api/trials/{td['id']}/decision", headers=H, json={
            "decision_no": "×纪决〔2026〕2号", "decision_result": "撤销党内职务处分",
            "decision_date": "2026-10-20", "decision_approver": "中共××市××区纪委"})
        r = client.post(f"/api/trials/{td['id']}/execute", headers=H, json={
            "announcer": "张××", "scope": "所在党支部全体党员及本人",
            "delivery": "直接送达", "signed": True})
        check("E16 非留党察看 restore 不适用", any(
            it.get("key") == "restore" and it.get("status") == "na"
            for it in (r.json().get("execution_items") or [])))
        check("E16 非留党察看 blockers=4（无 restore）",
              len(r.json().get("execution_blockers") or []) == 4)
        for k, at in [("rank_adj", "2026-10-22"), ("pay_adj", "2026-10-22"),
                      ("property", "2026-10-23"), ("archive", "2026-10-23")]:
            rr = client.post(f"/api/trials/{td['id']}/execution-items", headers=H,
                             json={"key": k, "status": "done", "at": at})
            check(f"E16 {k} 回填 200", rr.status_code == 200)
        r = client.post(f"/api/trials/{td['id']}/execution-close", headers=H, json={})
        check("E16 非留党察看可报结 200", r.status_code == 200, r.text[:200])
        check("E16 execution_closed=True", r.json().get("execution_closed") is True)
        # 回证报告（须已报结）
        r = client.post(f"/api/trials/{td['id']}/execution-report", headers=H, json={})
        check("E16 执行回证报告生成 200", r.status_code == 200, r.text[:200])
        check("E16 报告含执行情况章节", "二、处分决定宣布执行情况" in (r.json().get("content") or ""))
        check("E16 报告含恢复权利不适用", "不适用恢复党员权利" in (r.json().get("content") or ""))
        r2 = client.post(f"/api/trials/{ta['id']}/execution-report", headers=H, json={})
        check("E16 留党察看报告恢复权利段", "恢复党员权利" in (r2.json().get("content") or ""))

        # ============ F：演示库基线（只读真实库 SRC_DB，不污染副本库） ============
        import sqlite3 as _sq
        _src = _sq.connect(SRC_DB)
        try:
            _trials = _src.execute(
                "SELECT id, subject, status, report_doc_id, decision_doc_id, executed_at, execution_closed "
                "FROM trials ORDER BY id").fetchall()
            check("F1 演示库 trial 数=3", len(_trials) == 3, str(len(_trials)))
            _bad = [t[1] or "" for t in _trials
                    if ("测试" in (t[1] or "") or "ac0ab9" in (t[1] or "") or "线上验证" in (t[1] or ""))]
            check("F2 无测试/验证字样 trial", not _bad, str(_bad))
            _d1 = _src.execute(
                "SELECT status, decision_doc_id, executed_at, execution_closed FROM trials WHERE id=1").fetchone()
            check("F3 李某闭环完整", _d1 and _d1[0] == "已执行" and _d1[1] and _d1[2] and _d1[3] == 1, str(_d1))
            _d2 = _src.execute(
                "SELECT t.subject, c.subject FROM trials t LEFT JOIN cases c ON t.case_id=c.id WHERE t.id=2").fetchone()
            check("F4 赵某主体由案件带出(非空)", bool(_d2[0] or _d2[1]), str(_d2))
            _d3 = _src.execute("SELECT subject, status FROM trials WHERE id=3").fetchone()
            check("F5 王某独立建档状态正确", _d3 and _d3[0] and _d3[1] == "已报告", str(_d3))
            _docs = [r[0] for r in _src.execute("SELECT title FROM documents").fetchall()]
            check("F6 演示库文档数=4", len(_docs) == 4, str(len(_docs)))
            _docbad = [d for d in _docs if ("测试" in d or "ac0ab9" in d or "线上验证" in d)]
            check("F6 文档无测试字样", not _docbad, str(_docbad))
        finally:
            _src.close()


def finish():
    print("\n========== 自检结果 ==========")
    print(f"PASS {len(PASS)}  FAIL {len(FAIL)}")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(" -", f)
    for _suf in ("", "-wal", "-shm"):
        _p = TMP_DB + _suf
        if os.path.exists(_p):
            try:
                os.remove(_p)
            except OSError:
                pass  # 清理失败不影响自检结论
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        FAIL.append(f"脚本异常：{e}")
    sys.exit(finish())
