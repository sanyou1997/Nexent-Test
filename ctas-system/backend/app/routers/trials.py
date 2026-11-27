import io as _io
import json
import re
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit, gongwen
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.decision import build_decision_document
from app.llm import provider
from app.sentencing import build_sentencing_reasoning, _LIGHT_CATALOG, _HEAVY_CATALOG
from app.models import User, Trial, Case, Document, DocumentTemplate, DocumentVersion, AuditLog, TrialMaterial, HistoricalReport, ReportReference

router = APIRouter(prefix="/api/trials", tags=["审理（案件审理辅助）"])

TRIAL_STATUS = ["待阅卷", "审理中", "已报告", "已定稿", "暂缓受理", "不予受理", "退回补充调查", "已执行"]

# 审计 action → 时间轴文案
ACTION_LABEL = {
    "trial_create": "创建审理事项",
    "trial_summarize": "阅卷摘要",
    "trial_evidence_check": "证据链校验",
    "trial_procedure_check": "程序合规检查",
    "trial_sentencing_ref": "类案量纪参考",
    "trial_accept": "受理审核（移送把关）",
    "trial_interview": "与被审查调查人见面谈话",
    "trial_return": "退回补充/重新审查调查",
    "trial_execute": "处分决定宣布执行",
    "trial_decision": "制作处分决定书",
    "trial_execution_item": "执行回证回填",
    "trial_execution_close": "执行回证报结",
    "trial_property": "涉案财物处置",
    "trial_report": "生成审理报告",
    "trial_extract": "审理建档字段识别（导入）",
    "trial_confirm": "审理定稿",
    "trial_evidence_review": "物证情况审查",
    "trial_physical_evidence": "物证登记",
    "trial_material_check": "移送材料校验",
}


class TrialCreate(BaseModel):
    case_id: int | None = None          # 可选关联全业务案件（独立建档时留空）
    title: str = ""
    # 独立建档核心字段（不依赖外部案件）
    subject: str = ""                  # 被审查调查人
    unit: str = ""                     # 单位 / 职务
    rank: str = ""                     # 职级
    political_status: str = ""         # 政治面貌
    problem_type: str = ""             # 问题性质（六项纪律/职务违法/职务犯罪）
    source: str = ""                   # 线索来源
    case_status: str = ""             # 立案状态：初核中/已立案/已处置/直接了结
    opened_at: str = ""                # 立案时间（YYYY-MM-DD）
    involved_amount: str = ""          # 涉案金额
    brief: str = ""                    # 简要案情
    transfer_from: str = ""            # 移送审查调查组


class TrialSummarize(BaseModel):
    pass


class TrialReport(BaseModel):
    note: str = ""        # 审理报告补充要点
    template_id: int | None = None   # 可选：选用文书模板库模板生成；不传则用默认审理报告结构
    refs: list[dict] = []  # 类案引用：[{report_id, finding_id, title, year, problem_type, content, query}]
    light_keys: list[str] = []   # 审理组人工认定的从轻/减轻情节（标准情节目录 key）
    heavy_keys: list[str] = []   # 审理组人工认定的从重/加重情节（标准情节目录 key）


class SentencingManual(BaseModel):
    light_keys: list[str] = []   # 从轻/减轻情节 key（见 _LIGHT_CATALOG）
    heavy_keys: list[str] = []   # 从重/加重情节 key（见 _HEAVY_CATALOG）


def _resolve_manual(light_keys, heavy_keys):
    """把勾选的情节 key 解析为 [{point, ref}]（未知 key 忽略，绝不臆造）。"""
    ml = [dict(point=f["point"], ref=f["ref"]) for f in _LIGHT_CATALOG if f["key"] in (light_keys or [])]
    mh = [dict(point=f["point"], ref=f["ref"]) for f in _HEAVY_CATALOG if f["key"] in (heavy_keys or [])]
    return ml, mh


class TrialConfirm(BaseModel):
    note: str = ""        # 定稿审议结论（集体审议意见）


class TrialAccept(BaseModel):
    decision: str = "受理"     # 受理 / 暂缓受理 / 不予受理
    reason: str = ""          # 不予受理/暂缓受理原因


class DefenseItem(BaseModel):
    content: str = ""            # 申辩内容（被审查调查人原意，如实记录）
    stance: str = "提出申辩"      # 异议类型：认可事实 / 提出申辩 / 异议
    adopt: str = "待审议"         # 采纳结论：采纳 / 部分采纳 / 不采纳 / 待审议
    reason: str = ""             # 采纳/部分采纳/不采纳的理由（第五十三条要求写明）


class TrialInterview(BaseModel):
    attitude: str = ""        # 认错悔错 / 无申辩 / 提出申辩 / 部分申辩
    note: str = ""            # 申辩要点 / 谈话记录（速记）
    interviewers: str = ""    # 谈话人（2 名以上审理人员，顿号分隔）
    recorder: str = ""        # 记录人
    place: str = ""           # 谈话地点
    rights_notified: bool = False   # 是否已告知申辩权利（第五十二条）
    defense_items: list[DefenseItem] = []   # 逐条申辩及采纳情况
    summary: str = ""         # 谈话情况说明（第五十三条）


class TrialReturn(BaseModel):
    reason: str = ""          # 退回理由（事实不清/证据不足）
    to: str = "审查调查组"     # 退回对象


class TrialProperty(BaseModel):
    disposal: str = ""        # 涉案财物处置意见（收缴/责令退赔/登记上交/随案移送）


class DecisionCreate(BaseModel):
    """处分决定书：裁量与程序事项一律人工填写，系统不预设。"""
    decision_no: str = ""        # 处分决定文号
    decision_result: str = ""    # 处分决定内容（如：党内警告处分 / 开除党籍处分）
    decision_date: str = ""      # 处分决定作出日期（YYYY-MM-DD）
    decision_approver: str = ""  # 处分批准机关（有处分批准权限的党组织/监察机关）


class TrialExecuteIn(BaseModel):
    """宣布执行结构化（原单 note 升级）。"""
    note: str = ""               # 宣布执行情况 / 执行报告
    announcer: str = ""          # 宣布人
    scope: str = ""              # 宣布范围
    delivery: str = ""           # 送达方式
    signed: bool = False         # 本人是否签收


class ExecutionItemIn(BaseModel):
    """执行回证清单单项回填。"""
    key: str = ""                # 清单项 key（见 _EXECUTION_CHECKLIST）
    status: str = "pending"      # done / pending / na
    note: str = ""
    at: str = ""                 # 完成日期（YYYY-MM-DD，done 时必填）


# 执行回证标准清单（处分决定宣布后须逐项落实回证）
_EXECUTION_CHECKLIST = [
    {"key": "rank_adj", "label": "职务、职级、衔级、级别等调整落实",
     "ref": "《公职人员政务处分法》第二十四条、第五十二条"},
    {"key": "pay_adj", "label": "工资、待遇等变更落实",
     "ref": "《公职人员政务处分法》第二十五条、第五十四条"},
    {"key": "property", "label": "涉案财物没收、追缴、责令退赔等处置落实",
     "ref": "《公职人员政务处分法》第二十五条、第二十七条"},
    {"key": "archive", "label": "处分决定书、执行情况报告等材料归入本人档案",
     "ref": "《中国共产党纪律处分条例》第四十四条、第四十五条"},
    {"key": "restore", "label": "留党察看期满恢复党员权利（无留党察看不适用）",
     "ref": "《中国共产党纪律处分条例》第十二条、第十三条"},
]


def _default_execution_items() -> list:
    return [dict(key=c["key"], label=c["label"], ref=c["ref"], status="pending",
                 note="", at="") for c in _EXECUTION_CHECKLIST]


def _restore_applicable(decision_result: str) -> bool:
    """恢复党员权利仅适用于留党察看；其余处分（含开除党籍）不适用。"""
    return bool(decision_result) and "留党察看" in decision_result


def _normalize_execution_items(raw, decision_result=None) -> list:
    """把库中/入参的回证清单规整为标准结构（缺项补默认、未知 key 保留并标注）。

    非留党察看案件：restore（恢复党员权利）自动标「不适用」，不阻断报结。
    """
    items = _default_execution_items()
    by_key = {it["key"]: it for it in items}
    for r in (raw or []):
        if not isinstance(r, dict):
            continue
        k = (r.get("key") or "").strip()
        if k in by_key:
            it = by_key[k]
            it["status"] = r.get("status") if r.get("status") in ("done", "pending", "na") else "pending"
            it["note"] = (r.get("note") or "").strip()
            it["at"] = (r.get("at") or "").strip()[:10]
        elif k:
            items.append({"key": k, "label": (r.get("label") or k),
                          "ref": r.get("ref") or "",
                          "status": r.get("status") if r.get("status") in ("done", "pending", "na") else "pending",
                          "note": (r.get("note") or "").strip(),
                          "at": (r.get("at") or "").strip()[:10]})
    # 恢复党员权利：留党察看适用（na 复位为 pending，须落实）；其余不适用（置 na，不阻断）
    ri = by_key.get("restore")
    if ri:
        if _restore_applicable(decision_result):
            if ri.get("status") == "na":
                ri["status"] = "pending"
        elif ri.get("status") != "done":
            ri["status"] = "na"
    return items


def _execution_blockers(items: list) -> list:
    """报结阻断项：pending 且有 ref 的标准项未落实 → 不得报结。"""
    return [it["label"] for it in items
            if it.get("status") == "pending" and it.get("key") in
            {c["key"] for c in _EXECUTION_CHECKLIST}]


class PhysicalEvidenceIn(BaseModel):
    name: str = ""            # 物证名称（如：现金人民币、金条、房产）
    category: str = ""        # 查封扣押 / 用赃款购买
    qty: str = ""             # 数量 / 规格
    feature: str = ""         # 特征 / 品牌 / 型号
    storage: str = ""         # 存放地点 / 保管情况
    has_photo: bool = False   # 有无照片（文章要求赃款赃物及相关照片）
    photo_note: str = ""      # 照片说明 / 档号
    remark: str = ""


def _content_disposition(inline: bool, name: str) -> str:
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"trial_export\"; filename*=UTF-8''{quote(name)}"


# 审理时限：自受理之日起 1 个月（30 自然日）内完成；重大复杂经批准可延长。
TRIAL_DUE_DAYS = 30
TRIAL_WARN_DAYS = 5


def _add_days(start, n):
    from datetime import timedelta
    return start + timedelta(days=n)


def _sla(t: Trial) -> dict:
    """审理时限 SLA：受理起 30 日；临期(≤5日)/逾期徽标。"""
    if not t.accepted_at:
        return {"accepted": False, "due_at": "", "needs_attention": False, "overdue": False}
    due = _add_days(t.accepted_at, TRIAL_DUE_DAYS)
    now = datetime.utcnow()
    # 审理时限只约束到定稿：定稿及之后（处分决定、执行、报结）不再计审理超期
    terminal = t.status in ("已定稿", "已执行", "不予受理", "退回补充调查", "暂缓受理")
    overdue = (not terminal) and due < now
    left = (due - now).days
    needs_attention = (not terminal) and (0 <= left <= TRIAL_WARN_DAYS)
    return {
        "accepted": True,
        "due_at": due.strftime("%Y-%m-%d"),
        "needs_attention": needs_attention,
        "overdue": overdue,
    }


# --------------------------------------------------------------------------- #
# 真实判断引擎（离线·可审·确定性）：证据链闭环 + 程序合规门禁
# 设计原则：凡系统能依据字段自动核验的环节给确定结论；凡系统无法自动核验的
#           （如措施具体审批、审理与审调分离的人员安排）一律标注「须人工确认」，
#           绝不臆断「合规」。结论仅供承办人复核参考，不替代人工认定。
# --------------------------------------------------------------------------- #
_EVID_CATS = [
    ("主体身份材料", ["身份", "干部", "任职", "党员", "编制", "职务", "职级"], True),
    ("违纪违法事实证据", ["笔录", "书证", "证言", "证据", "印证", "供述", "证实"], True),
    ("到案经过/交代材料", ["到案", "交代", "供述", "交代材料", "主动交代", "如实供述"], True),
    ("涉案财物/资金证据", ["银行", "流水", "转账", "财物", "资金", "受贿款", "违法所得", "账户"], True),
    ("程序审批手续", ["审批", "呈批", "批准", "集体研究", "会议", "签批", "手续"], True),
    ("鉴定意见（涉财/涉技术）", ["鉴定", "评估", "审计", "价格认定", "司法会计"], False),
]
_EVID_SRC_TYPES = ["书证", "证人证言", "证言", "笔录", "供述", "鉴定意见", "当事人陈述",
                  "视听资料", "勘验", "鉴定"]
_FACT_VERBS = ["收受", "索要", "索取", "贪污", "受贿", "违规", "侵占", "挪用", "礼金", "回扣",
               "好处", "宴请", "旅游", "报销", "虚列", "套取", "私分", "滥发", "借用", "收受礼金",
               "违反", "未按规定", "不如实"]


def _evidence_chain(t: "Trial", c: "Case" = None) -> dict:
    """证据链闭环校验（辅助·离线规则引擎）。

    判断逻辑：
    1) 类别命中：按关键词识别必备证据类别是否存在；
    2) 多源印证：统计材料中出现的不同证据类型（书证/证言/笔录/供述/鉴定…），
       仅出现 1 类则标「单一来源」待补强——避免单一证据定案；
    3) 事实-证据对应：抽取含违纪动词的待证事实句，判断其是否被证据支撑；
    4) 闭环结论：缺失必备类 / 事实无支撑 → 断裂；无缺失但单一来源 → 待补强；
       必备齐全且多源印证 → 闭环。
    """
    text = (c.content if c else "") or (t.brief or "")
    sents = re.split(r"[。；;！\n]", text)
    facts = [s.strip() for s in sents
             if any(v in s for v in _FACT_VERBS) and 6 <= len(s.strip()) <= 120]
    checks, missing, single = [], [], []
    for name, kws, required in _EVID_CATS:
        hit = [k for k in kws if k in text]
        if not hit:
            if required:
                checks.append({"point": name, "status": "缺失/不足", "level": "error",
                               "note": "材料中未检索到该类证据表述，须补充，避免事实无证据支撑"})
                missing.append(name)
            else:
                checks.append({"point": name, "status": "本案未涉及", "level": "info",
                               "note": "当前材料未显现该类证据需求（涉财/涉技术才需要）"})
            continue
        src_hit = [t for t in _EVID_SRC_TYPES if t in text]
        if len(src_hit) >= 2:
            checks.append({"point": name, "status": "有·多源印证", "level": "info",
                           "note": f"命中关键词「{'、'.join(hit)}」；存在多类证据印证（{'、'.join(src_hit)}）"})
        else:
            checks.append({"point": name, "status": "有·单一来源", "level": "warn",
                           "note": f"命中关键词「{'、'.join(hit)}」，但仅见单一证据类型（{src_hit[0] if src_hit else '未标注类型'}），"
                                   f"建议补强避免单一证据定案"})
            single.append(name)
    # 事实-证据对应
    fact_items, gaps = [], []
    for f in facts:
        supported = any(k in f for k in ["笔录", "书证", "证言", "证据", "印证", "供述", "鉴定"]) \
            or len([t for t in _EVID_SRC_TYPES if t in text]) >= 2
        if supported:
            fact_items.append({"fact": f[:42], "status": "有证据支撑", "level": "info"})
        else:
            fact_items.append({"fact": f[:42], "status": "待补证", "level": "warn"})
            gaps.append(f"待证事实缺乏直接证据支撑：{f[:42]}")
    if missing:
        verdict = "断裂"
    elif single or gaps:
        verdict = "待补强"
    else:
        verdict = "闭环"
    return {
        "checks": checks,
        "facts": fact_items,
        "verdict": verdict,
        "missing": missing,
        "single_source": single,
        "gaps": gaps,
        "note": "证据链闭环校验为辅助规则引擎（离线·可审）：以关键词识别证据类别与多源印证，"
                "最终认定须承办人逐项复核，系统不替代人工结论。",
    }


# --------------------------------------------------------------------------- #
# 物证情况审查框架（参考《监察机关办理受贿罪取证要点》）
# 受贿案取证分三大方面；客观方面→收受财物行为下专列「物证」两项：
#   ① 查封、扣押的赃款赃物及相关照片；② 用赃款购买的物品及相关照片。
# 凡系统能依据案件材料(c.content)与登记的物证条目自动核对的给确定结论；
# 凡无法自动核验的环节标注「须人工确认」，不替代人工认定。
_EVID_FRAMEWORK = [
    ("犯罪主体方面", [
        {"point": "身份及任职证明（身份证/任命文件/岗位职责说明）", "kw": ["身份", "任职", "任命", "职务", "职责", "编制", "干部"]},
        {"point": "单位性质证明（营业执照/国资证明/事业单位法人证书）", "kw": ["单位", "企业", "国资", "国有", "事业", "法人", "营业执照"]},
        {"point": "职权行使证据（签批文件/会议纪要/经办材料）", "kw": ["职权", "签批", "审批", "会议", "经办", "项目", "拨付"]},
        {"point": "证人证言（负责人/同事关于职权范围陈述）", "kw": ["证言", "证人", "同事", "负责", "陈述"]},
        {"point": "辅助证明（工资单/社保/通讯/出差记录）", "kw": ["工资", "社保", "通讯", "出差", "绩效"], "optional": True},
    ]),
    ("主观方面", [
        {"point": "被调查人供述（权钱交易主观故意/动机目的）", "kw": ["供述", "故意", "动机", "目的", "明知", "意图"]},
        {"point": "行贿人证言（行贿动机/有无明示暗示）", "kw": ["行贿", "证言", "暗示", "明示", "动机"]},
    ]),
    ("客观方面", [
        {"point": "为他人谋利·被调查人供述（承诺/实施/实现）", "kw": ["谋取", "承诺", "请托", "职权", "利益"]},
        {"point": "为他人谋利·行贿人证言", "kw": ["谋取", "请托", "利益", "证言"]},
        {"point": "为他人谋利·证人证言/书证", "kw": ["请托", "经办", "证言", "书证"], "optional": True},
        {"point": "收受财物·被调查人供述（时间地点方式/存放去向/退交）", "kw": ["收受", "财物", "时间", "地点", "存放", "退还", "上交"]},
        {"point": "收受财物·行贿人证言（给予过程/在场人）", "kw": ["给予", "行贿", "财物", "在场", "接收"]},
        {"point": "收受财物·证人证言（流转保管处置）", "kw": ["证言", "流转", "保管", "处置", "接收"]},
        {"point": "收受财物·书证（流水/借条/发票/账户/出行住宿）", "kw": ["流水", "借条", "发票", "账户", "交易", "出行", "住宿", "凭证"]},
        {"point": "物证：查封、扣押的赃款赃物及相关照片", "kw": ["查封", "扣押", "赃物", "赃款", "照片"], "physical": "seized"},
        {"point": "物证：用赃款购买的物品及相关照片", "kw": ["购买", "物品", "赃物", "照片"], "physical": "purchased"},
        {"point": "其他证据（鉴定意见/电子证据）", "kw": ["鉴定", "短信", "微信", "邮件", "电子"], "optional": True},
    ]),
]


def _evidence_review(c: "Case", t: "Trial", db: Session) -> dict:
    """物证情况审查（辅助·离线规则引擎）。

    按受贿案取证三维度框架，逐项比对案件材料(c.content)与登记的物证条目：
    1) 物证项：按类别（查封扣押/用赃款购买）核对有无登记条目，给出覆盖/缺失；
    2) 文本项：按关键词识别材料是否涵盖该类证据；
    3) 汇总整体取证完整度与结论（闭环/待补强）+ 缺失清单。
    """
    text = (c.content if c else "") or (t.brief or "")
    pe = (t.physical_evidence or []) if t else []

    def match_cat(e: dict, kind: str) -> bool:
        cat = e.get("category") or ""
        if kind == "seized":
            return ("查封" in cat) or ("扣押" in cat)
        if kind == "purchased":
            return "购买" in cat
        return False

    aspects, missing = [], []
    for aname, items in _EVID_FRAMEWORK:
        out_items, acov, atot = [], 0, 0
        for it in items:
            if it.get("physical"):
                kind = it["physical"]
                ents = [e for e in pe if match_cat(e, kind)]
                cnt = len(ents)
                ph = sum(1 for e in ents if e.get("has_photo"))
                if cnt > 0:
                    status, level = "已覆盖", "info"
                    note = f"已登记物证 {cnt} 项，其中 {ph} 项附照片"
                    acov += 1
                else:
                    status, level = "缺失", "error"
                    note = "未登记该类别物证，须查封/扣押（或购买）并附照片（文章要求赃款赃物及相关照片）"
                    missing.append(it["point"])
                atot += 1
                out_items.append({"point": it["point"], "status": status, "level": level,
                                  "note": note, "physical": kind})
            else:
                hit = [k for k in it["kw"] if k in text]
                if hit:
                    status, level = "已覆盖", "info"
                    note = f"材料命中关键词：{', '.join(hit)}"
                    atot += 1
                    acov += 1
                    out_items.append({"point": it["point"], "status": status, "level": level, "note": note})
                elif it.get("optional"):
                    out_items.append({"point": it["point"], "status": "本案未涉及", "level": "default",
                                      "note": "当前材料未显现该证据需求"})
                else:
                    atot += 1
                    missing.append(it["point"])
                    out_items.append({"point": it["point"], "status": "缺失", "level": "error",
                                      "note": "材料中未检索到该类证据表述，须补充"})
        aspects.append({"name": aname, "items": out_items, "covered": acov, "total": atot})
    photos_total = sum(1 for e in pe if e.get("has_photo"))
    covered_all = sum(a["covered"] for a in aspects)
    total_all = sum(a["total"] for a in aspects)
    verdict = "待补强" if missing else "闭环"
    return {
        "aspects": aspects,
        "covered": covered_all, "total": total_all,
        "verdict": verdict,
        "missing": missing,
        "photos_total": photos_total,
        "physical_total": len(pe),
        "note": "物证情况审查为辅助规则引擎（离线·可审）：依据受贿案取证要点框架逐项比对案件材料与登记的物证条目，"
                "最终认定须承办人逐项复核，系统不替代人工结论。",
    }


def _procedure_check(t: "Trial", c: "Case") -> dict:
    """程序合规检查（辅助·数据驱动门禁，离线·可审）。

    每一项的状态都来自案件/审理的真实字段；系统无法自动核验的环节标注
    『须人工确认』，绝不默认「合规」。
    """
    now = datetime.utcnow()
    items = []

    # 线索处置集体研究
    if c and c.disposition_note:
        items.append({"item": "线索处置经集体研究", "status": "合规", "level": "info",
                      "note": "处置有会议结论留痕"})
    else:
        items.append({"item": "线索处置经集体研究", "status": "待核", "level": "warn",
                      "note": "未见处置会议结论留痕，请补集体研究记录"})

    # 初核/立案审批
    opened = t.opened_at or (c.opened_at if c else None)
    if opened:
        items.append({"item": "初核/立案履行审批", "status": "合规", "level": "info",
                      "note": f"立案时间 {opened:%Y-%m-%d}"})
    else:
        items.append({"item": "初核/立案履行审批", "status": "待核", "level": "warn",
                      "note": "未见立案审批手续"})

    # 审查调查措施合规（系统无法自动核验具体措施审批 → 须人工确认）
    items.append({"item": "审查调查措施合规", "status": "须人工确认", "level": "warn",
                  "note": "措施使用须符合法定权限与程序，请承办人逐项核对审批手续"})

    # 办案时限（仅关联案件的案管期限可自动核验；独立建档无此字段，标待核）
    if c and c.due_at and (now - c.due_at).days > 0 and c.status not in ("已处置", "已定稿", "审理中"):
        items.append({"item": "办案时限合规", "status": "超期·不合规", "level": "error",
                      "note": f"已超过办案期限 {c.due_at:%Y-%m-%d}"})
    elif c and c.due_at:
        items.append({"item": "办案时限合规", "status": "合规", "level": "info",
                      "note": f"期限 {c.due_at:%Y-%m-%d}（未超期）"})
    else:
        items.append({"item": "办案时限合规", "status": "待核", "level": "warn",
                      "note": "未设置案管办案期限（独立建档事项无此字段），请人工确认"})

    # 审理与审查调查分离
    items.append({"item": "审理与审查调查分离", "status": "须人工确认", "level": "warn",
                  "note": "审理人员不得参与原审查调查，请在人员安排上确认回避"})

    # 移送受理把关
    if t.accept_decision:
        bad = t.accept_decision == "不予受理"
        items.append({"item": "移送审理受理把关", "status": "已把关" if not bad else "不予受理",
                      "level": "info" if not bad else "warn", "note": f"决定：{t.accept_decision}"})
    else:
        items.append({"item": "移送审理受理把关", "status": "待把关", "level": "warn",
                      "note": "尚未进行移送受理审核"})

    # 见面谈话环节（法定）
    if t.interview_at:
        items.append({"item": "与被审查调查人见面谈话", "status": "已履行", "level": "info",
                      "note": f"态度：{t.interview_attitude or '已记录'}"})
    else:
        items.append({"item": "与被审查调查人见面谈话", "status": "待履行", "level": "warn",
                      "note": "法定环节，须核对事实并听取辩解意见"})

    # 涉案财物处置意见
    if t.property_disposal:
        items.append({"item": "涉案财物处置意见", "status": "已提", "level": "info",
                      "note": t.property_disposal})
    else:
        items.append({"item": "涉案财物处置意见", "status": "待提", "level": "warn",
                      "note": "须提出收缴/责令退赔/登记上交/随案移送意见"})

    # 处分决定宣布执行
    if t.executed_at:
        items.append({"item": "处分决定宣布执行", "status": "已执行", "level": "info",
                      "note": f"执行时间 {t.executed_at:%Y-%m-%d}"})
    else:
        items.append({"item": "处分决定宣布执行", "status": "待执行", "level": "warn",
                      "note": "须先定稿后宣布执行"})

    # 退回补证闭环
    if t.status == "退回补充调查":
        if (c and c.status == "初核中") or t.case_status == "初核中":
            items.append({"item": "退回补证闭环", "status": "已闭环", "level": "info",
                          "note": "案件已退回重新审查调查"})
        else:
            items.append({"item": "退回补证闭环", "status": "待闭环", "level": "warn",
                          "note": "已退回但案件状态未回退，请确认补证链路"})
    else:
        items.append({"item": "退回补证闭环", "status": "不适用", "level": "info",
                      "note": "本案未退回"})

    # 集体审议定稿
    if t.status == "已定稿":
        items.append({"item": "集体审议定稿", "status": "已定稿", "level": "info",
                      "note": "审理已定稿"})
    else:
        items.append({"item": "集体审议定稿", "status": "待定稿", "level": "warn",
                      "note": "尚未定稿"})

    bad = [i for i in items if i["level"] == "error"]
    warn = [i for i in items if i["level"] == "warn"]
    verdict = "不合规" if bad else ("待完善" if warn else "合规")
    return {
        "checks": items,
        "verdict": verdict,
        "gaps": [f"{i['item']}：{i['note']}" for i in warn],
        "note": "程序合规检查为数据驱动规则引擎（离线·可审）：依据案件/审理实际字段逐项判定，"
                "凡系统无法自动核验的环节标注『须人工确认』，不臆断合规。",
    }


# 系统辅助动作：属工具行为而非审理经过节点，不进「二、案件来源及审理经过」
_JOURNEY_SKIP = {"trial_report", "trial_sentencing_ref", "trial_extract"}


def _trial_journey(db: Session, t: Trial, c: "Case" = None) -> str:
    """二、案件来源及审理经过（据案件字段与审计留痕生成，不臆造；同动作聚合压缩）。"""
    parts = []
    if c:
        src = f"案件来源：{c.source or '（未填）'}"
        if c.code:
            src += f"（案件编号 {c.code}）"
        if c.opened_at:
            src += f"，{str(c.opened_at)[:10]} 受理"
        parts.append(src)
    logs = (db.query(AuditLog)
            .filter(AuditLog.resource_type == "trial", AuditLog.resource_id == str(t.id))
            .order_by(AuditLog.id.asc()).all())
    # 按动作聚合：同一动作多次执行合并为「起~止 动作（N次）」，避免轨迹堆砌
    grouped: dict = {}
    order: list = []
    for lg in logs:
        if lg.action in _JOURNEY_SKIP:
            continue
        if lg.action not in grouped:
            grouped[lg.action] = []
            order.append(lg.action)
        grouped[lg.action].append(lg.created_at)
    for action in order:
        times = grouped[action]
        label = ACTION_LABEL.get(action, action)
        if len(times) == 1:
            when = times[0].strftime("%Y年%m月%d日") if times[0] else ""
            parts.append(f"{when}{label}" if when else label)
        else:
            first = times[0].strftime("%Y年%m月%d日") if times[0] else ""
            last = times[-1].strftime("%Y年%m月%d日") if times[-1] else ""
            span = f"{first}至{last}" if first != last else first
            parts.append(f"{span} {label}（{len(times)}次）")
    if not parts:
        return "（案件来源及审理经过以案卷记载为准）"
    return "；".join(parts) + "。"


def _json_cell(raw):
    """兼容 JSON 列经 ORM 返回 dict/list 或库中字符串两种情况。"""
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


# 证据链校验状态 → 公文表述（有·单一来源等原始状态码不进公文）
_EV_STATUS_LABEL = {
    "有·多源印证": "通过（多源印证）",
    "有·单一来源": "通过（单一来源，宜补强）",
    "缺失/不足": "缺失/不足",
    "本案未涉及": "本案未涉及",
}

# 历史数据 note 中的 Python list repr（如 命中['职务']）→ 中文顿号引号形式
_REPR_LIST_RE = re.compile(r"\[((?:'[^']*'(?:,\s*)?)+)\]")


def _clean_repr(text: str) -> str:
    """把 note 里的 ['a', 'b'] repr 清洗为「a、b」（历史数据兼容，新数据已无此问题）。"""
    if not text or "[" not in text:
        return text

    def _sub(m):
        items = re.findall(r"'([^']*)'", m.group(1))
        return "「" + "、".join(items) + "」"

    return _REPR_LIST_RE.sub(_sub, text)


def _evidence_section(t: Trial) -> str:
    """四、证据情况（据证据链校验结果与物证清单生成）。"""
    lines = []
    ev = _json_cell(t.evidence_check) or {}
    checks = ev.get("checks") or [] if isinstance(ev, dict) else []
    if checks:
        lines.append("证据链校验情况：")
        for ck in checks:
            note = _clean_repr(ck.get("note") or "")
            status = _EV_STATUS_LABEL.get(ck.get("status", ""), ck.get("status", ""))
            note_part = f"（{note}）" if note else ""
            lines.append(f"· {ck.get('point', '')}：{status}{note_part}")
    else:
        lines.append("证据链校验：尚未进行或未通过校验，须先完成证据链完整性校验。")
    phys = _json_cell(t.physical_evidence)
    if isinstance(phys, list) and phys:
        lines.append(f"物证及涉案物品共 {len(phys)} 件，详见随案移送清单。")
    lines.append("证据目录及卷宗材料详见案卷。")
    return "\n".join(lines)


def _interview_section(t: Trial) -> str:
    """附：与被审查调查人见面谈话及申辩采纳情况（《监督执纪工作规则》第五十一~五十三条）。

    仅在已谈话时生成；申辩逐条如实记录并写明采纳结论及理由；未明确结论的标「待审议」，
    不臆断采纳结论。谈话人不足 2 人 / 未告知申辩权利的，如实标注提示（系统不替代人工认定）。
    """
    if not t.interview_at:
        return ""
    lines = ["附：与被审查调查人见面谈话及申辩采纳情况"]
    when = t.interview_at.strftime("%Y年%m月%d日")
    parts = [f"审理组于{when}"]
    if t.interview_place:
        parts.append(f"在{t.interview_place}")
    parts.append("与被审查调查人"
                 f"{t.subject or ''}进行见面谈话")
    if t.interviewers:
        parts.append(f"谈话人：{t.interviewers}")
    if t.recorder:
        parts.append(f"记录人：{t.recorder}")
    lines.append("，".join(parts) + "。")
    if t.interview_attitude:
        lines.append(f"谈话中，被审查调查人态度：{t.interview_attitude}。")
    items = t.defense_items or []
    if items:
        lines.append("其申辩及采纳情况如下：")
        for i, it in enumerate(items, 1):
            stance = it.get("stance") or "提出申辩"
            adopt = it.get("adopt") or "待审议"
            reason = (it.get("reason") or "").strip()
            tail = f"；{adopt}" + (f"（理由：{reason}）" if reason else "")
            lines.append(f"{i}. {stance}：{it.get('content') or ''}{tail}")
    elif t.interview_attitude in ("无申辩", "认错悔错"):
        lines.append("被审查调查人对违纪违法事实无申辩意见。")
    if t.interview_summary:
        lines.append(f"谈话情况说明：{t.interview_summary}")
    # 程序合规如实标注（第五十一~五十三条），不阻断、不臆断
    notes = []
    interviewer_list = [x for x in (t.interviewers or "").replace(",", "、").replace("，", "、").split("、") if x.strip()]
    if len(interviewer_list) < 2:
        notes.append("谈话人不足 2 人")
    if not t.rights_notified:
        notes.append("未记录已告知申辩权利")
    pending = [x for x in items if (x.get("adopt") or "待审议") == "待审议"]
    if pending:
        notes.append(f"{len(pending)} 条申辩待审议明确采纳结论")
    if notes:
        lines.append("【程序提示】" + "；".join(notes) + "（须承办人复核补正，系统不替代人工认定）。")
    return "\n".join(lines)


def _ensure_active(t: Trial):
    """不予受理的案件不可继续审理流程（对标信访不予受理红线）。"""
    if t.accept_decision == "不予受理":
        raise HTTPException(400, "该案已决定不予受理，不可继续审理流程")


def _trial_out(db: Session, t: Trial) -> dict:
    creator = db.get(User, t.created_by) if t.created_by else None
    case = db.get(Case, t.case_id) if t.case_id else None
    # 送审材料完整度摘要（CTAS 材料汇聚中心；全业务库无材料时为零值，不影响）
    from app.routers.trial_materials import REQUIRED_CHECKLIST
    mats = db.query(TrialMaterial).filter(TrialMaterial.trial_id == t.id).all()
    cats = {m.category for m in mats}
    missing = [c["label"] for c in REQUIRED_CHECKLIST if c["key"] not in cats]
    subj = t.subject or (case.subject if case else "")
    opened = t.opened_at or (case.opened_at if case else None)
    title = t.title or (f"{subj}涉嫌违纪案审理" if subj else "审理事项")
    return {
        "id": t.id, "case_id": t.case_id,
        "case_code": case.code if case else "",
        "case_subject": subj,
        "title": title,
        "subject": t.subject or "", "unit": t.unit or "", "rank": t.rank or "",
        "political_status": t.political_status or "",
        "problem_type": t.problem_type or (case.problem_type if case else ""),
        "case_source": t.source or (case.source if case else ""),
        "case_status": t.case_status or (case.status if case else ""),
        "opened_at": opened.strftime("%Y-%m-%d") if opened else "",
        "involved_amount": t.involved_amount or "",
        "brief": (t.brief or (case.content if case else ""))[:500],
        "transfer_from": t.transfer_from or "",
        "summary": t.summary, "summary_mode": t.summary_mode,
        "evidence_check": t.evidence_check or {},
        "evidence_review": t.evidence_review or {},
        "physical_evidence": t.physical_evidence or [],
        "procedure_check": t.procedure_check or {},
        "sentencing_ref": t.sentencing_ref or {},
        "report_doc_id": t.report_doc_id,
        "status": t.status,
        "created_by": creator.real_name or creator.username if creator else "",
        "confirmed_at": t.confirmed_at.strftime("%Y-%m-%d %H:%M") if t.confirmed_at else "",
        "confirmed_note": t.confirmed_note or "",
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "",
        # —— 受理 / 见面谈话 / 退回 / 宣布执行 / 涉案财物 ——
        "accept_decision": t.accept_decision or "",
        "accept_reason": t.accept_reason or "",
        "accepted_at": t.accepted_at.strftime("%Y-%m-%d %H:%M") if t.accepted_at else "",
        "interview_at": t.interview_at.strftime("%Y-%m-%d %H:%M") if t.interview_at else "",
        "interview_attitude": t.interview_attitude or "",
        "interview_note": t.interview_note or "",
        "interviewers": t.interviewers or "",
        "recorder": t.recorder or "",
        "interview_place": t.interview_place or "",
        "rights_notified": bool(t.rights_notified),
        "defense_items": t.defense_items or [],
        "interview_summary": t.interview_summary or "",
        "returned_at": t.returned_at.strftime("%Y-%m-%d %H:%M") if t.returned_at else "",
        "return_reason": t.return_reason or "",
        "return_to": t.return_to or "",
        "executed_at": t.executed_at.strftime("%Y-%m-%d %H:%M") if t.executed_at else "",
        "execute_note": t.execute_note or "",
        # —— P3 处分决定书 + 执行回证 ——
        "decision_doc_id": t.decision_doc_id,
        "decision_no": t.decision_no or "",
        "decision_result": t.decision_result or "",
        "decision_date": t.decision_date.strftime("%Y-%m-%d") if t.decision_date else "",
        "decision_approver": t.decision_approver or "",
        "execute_announcer": t.execute_announcer or "",
        "execute_scope": t.execute_scope or "",
        "execute_delivery": t.execute_delivery or "",
        "execute_signed": bool(t.execute_signed),
        "execution_items": _normalize_execution_items(t.execution_items, t.decision_result),
        "execution_blockers": _execution_blockers(_normalize_execution_items(t.execution_items, t.decision_result)),
        "restore_rights_at": t.restore_rights_at.strftime("%Y-%m-%d") if t.restore_rights_at else "",
        "execution_closed": bool(t.execution_closed),
        "execution_closed_at": t.execution_closed_at.strftime("%Y-%m-%d %H:%M") if t.execution_closed_at else "",
        "property_disposal": t.property_disposal or "",
        "sla": _sla(t),
        "materials": {
            "total": len(mats),
            "required_total": len(REQUIRED_CHECKLIST),
            "required_missing": missing,
            "complete": not missing,
        },
    }


@router.get("")
def list_trials(status: str = "", q: str = "", overdue: bool = False, page: int = 1, per_page: int = 20,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """审理台账：支持状态/关键词/超期筛选 + 服务端分页。"""
    query = db.query(Trial)
    if status:
        query = query.filter(Trial.status == status)
    if q:
        like = f"%{q}%"
        query = (query.join(Case, Case.id == Trial.case_id, isouter=True)
                 .filter((Trial.title.like(like)) | (Case.code.like(like)) | (Case.subject.like(like))))
    all_ts = query.order_by(Trial.id.desc()).all()
    # 超期筛选在 Python 侧按 SLA 命中（与信访一致）
    if overdue:
        all_ts = [t for t in all_ts if _sla(t)["overdue"]]
    total = len(all_ts)
    page = max(1, page)
    per_page = max(1, min(200, per_page))
    ts = all_ts[(page - 1) * per_page: page * per_page]
    cids = {t.case_id for t in ts if t.case_id}
    cases = {c.id: c for c in db.query(Case).filter(Case.id.in_(cids)).all()} if cids else {}
    items = []
    for t in ts:
        o = _trial_out(db, t)
        o["case_subject"] = t.subject or (cases.get(t.case_id).subject if cases.get(t.case_id) else "")
        items.append(o)
    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.get("/stats")
def trials_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ts = db.query(Trial).all()
    by_status: dict = {}
    for t in ts:
        by_status[t.status] = by_status.get(t.status, 0) + 1
    done = sum(1 for t in ts if t.status == "已定稿")
    accepted = [t for t in ts if t.accept_decision == "受理"]
    overdue = sum(1 for t in ts if _sla(t)["overdue"])
    interviewed = sum(1 for t in ts if t.interview_at)
    executed = sum(1 for t in ts if t.executed_at)
    returned = sum(1 for t in ts if t.returned_at)
    not_accepted = sum(1 for t in ts if not t.accept_decision)
    by_accept: dict = {}
    for t in ts:
        key = t.accept_decision or "待受理"
        by_accept[key] = by_accept.get(key, 0) + 1
    return {
        "total": len(ts), "by_status": by_status,
        "reported": sum(1 for t in ts if t.report_doc_id),
        "done": done,
        # —— 效能看板 ——
        "by_accept": by_accept,
        "accepted_total": len(accepted),
        "not_accepted_total": not_accepted,
        "overdue_total": overdue,
        "interviewed_total": interviewed,
        "returned_total": returned,
        "executed_total": executed,
        "decision_total": sum(1 for t in ts if t.decision_doc_id),
        "execution_closed_total": sum(1 for t in ts if t.execution_closed),
        "unresolved_total": sum(1 for t in ts if t.status not in ("已定稿", "已执行", "不予受理")),
    }


@router.post("")
def create_trial(data: TrialCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """新建审理事项：CTAS 可独立建档（不依赖外部案件）；可选关联全业务已立案案件。"""
    case = db.get(Case, data.case_id) if data.case_id else None
    if data.case_id and not case:
        raise HTTPException(404, "关联案件不存在")
    if case and case.status not in ("已立案", "初核中", "已处置"):
        raise HTTPException(400, "关联案件须为已立案/初核中/已处置状态")
    opened = None
    if data.opened_at:
        try:
            opened = datetime.strptime(data.opened_at, "%Y-%m-%d")
        except ValueError:
            opened = None
    subj = data.subject
    title = data.title or (f"{subj}涉嫌违纪案审理" if subj else "审理事项")
    t = Trial(
        case_id=data.case_id,
        title=title,
        subject=subj, unit=data.unit, rank=data.rank,
        political_status=data.political_status, problem_type=data.problem_type,
        source=data.source, case_status=data.case_status,
        opened_at=opened, involved_amount=data.involved_amount,
        brief=data.brief, transfer_from=data.transfer_from,
        created_by=user.id, status="待阅卷",
    )
    db.add(t)
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_create",
                    resource_type="trial", resource_id=t.id,
                    detail={"case_id": data.case_id, "subject": subj})
    return _trial_out(db, t)


@router.get("/{tid}/timeline")
def trial_timeline(tid: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """流程轨迹：把该审理事项的审计记录串成时间轴。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    logs = (db.query(AuditLog)
            .filter(AuditLog.resource_type == "trial", AuditLog.resource_id == str(tid))
            .order_by(AuditLog.id.asc()).all())
    items = []
    for lg in logs:
        d = lg.detail if isinstance(lg.detail, dict) else {}
        label = ACTION_LABEL.get(lg.action, lg.action)
        desc = ""
        if lg.action == "trial_report":
            desc = f"生成审理报告（文书 #{d.get('doc_id', '')}）"
        elif lg.action == "trial_confirm":
            desc = "审理定稿" + (f"；审议结论：{d.get('note', '')}" if d.get('note') else "")
        elif lg.action == "trial_create":
            desc = f"关联案件 #{d.get('case_id', '')}"
        elif lg.action == "trial_sentencing_ref":
            desc = f"检索类案 {d.get('refs', 0)} 条"
        elif lg.action == "trial_accept":
            desc = f"决定：{d.get('decision', '')}" + (f"；{d.get('reason', '')}" if d.get('reason') else "")
        elif lg.action == "trial_interview":
            desc = f"态度：{d.get('attitude', '')}" + (f"；{d.get('note', '')}" if d.get('note') else "")
        elif lg.action == "trial_return":
            desc = f"退回至 {d.get('to', '')}" + (f"；{d.get('reason', '')}" if d.get('reason') else "")
        elif lg.action == "trial_execute":
            desc = "处分决定宣布执行" + (f"；{d.get('note', '')}" if d.get('note') else "")
        elif lg.action == "trial_property":
            desc = "涉案财物处置意见：" + (d.get('disposal', '') or '')
        items.append({
            "id": lg.id, "action": lg.action, "label": label, "desc": desc,
            "username": lg.username,
            "created_at": lg.created_at.strftime("%Y-%m-%d %H:%M") if lg.created_at else "",
        })
    return {"trial_id": tid, "status": t.status, "items": items}


@router.get("/{tid}/report/export")
def trial_report_export(tid: int, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """审理报告下载（txt，UTF-8）。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if not t.report_doc_id:
        raise HTTPException(404, "尚未生成审理报告")
    d = db.get(Document, t.report_doc_id)
    if not d:
        raise HTTPException(404, "审理报告已不存在")
    c = db.get(Case, t.case_id) if t.case_id else None
    fname = f"{c.code if c else t.id}_审理报告.txt"
    return Response(
        content=(d.content or "").encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(False, fname)},
    )


@router.get("/{tid}/report")
def trial_report_view(tid: int, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """查看已生成的审理报告正文（只读，不重新生成）。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if not t.report_doc_id:
        raise HTTPException(404, "尚未生成审理报告")
    d = db.get(Document, t.report_doc_id)
    if not d:
        raise HTTPException(404, "审理报告已不存在")
    c = db.get(Case, t.case_id) if t.case_id else None
    return {"id": d.id, "title": d.title, "content": d.content or "",
            "case_code": c.code if c else "", "status": d.status}


@router.get("/{tid}")
def get_trial(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    return _trial_out(db, t)


@router.post("/{tid}/summarize")
def summarize(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """审理阅卷摘要：汇总案件主要事实、证据与争议点（规则合成，离线可用）。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    c = db.get(Case, t.case_id) if t.case_id else None
    subj = t.subject or (c.subject if c else "（未填）")
    unit = t.unit or (c.unit if c else "单位/职务未填")
    apptitle = t.title or (c.title if c else "") or (t.problem_type or (c.problem_type if c else "") or "（未填）")
    facts = (c.content if c else "") or (t.brief or "（案件无文字材料，请补充阅卷笔录）")
    disp = (c.disposition if c else "") or (t.case_status or "未处置")
    opened = t.opened_at or (c.opened_at if c else None)
    opened_str = opened.strftime("%Y-%m-%d") if opened else "未立案"
    summary = (
        f"【被审查调查人】{subj}（{unit}）\n"
        f"【案由】{apptitle}\n"
        f"【主要问题】{facts[:600]}\n"
        f"【处置/立案】{disp}；立案时间 {opened_str}\n"
        f"【争议点提示】请重点核对事实认定、证据链条、量纪档次与程序手续。"
    )
    t.summary = summary
    t.summary_mode = "rules"
    t.status = "审理中" if t.status == "待阅卷" else t.status
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_summarize",
                    resource_type="trial", resource_id=tid, detail={"mode": "rules"})
    return {"summary": summary, "mode": "rules"}


@router.post("/{tid}/evidence-check")
def evidence_check(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """证据链闭环校验（辅助·离线规则引擎）：类别命中 + 多源印证 + 事实-证据对应，给出闭环/待补强/断裂结论。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    c = db.get(Case, t.case_id) if t.case_id else None
    result = _evidence_chain(t, c)
    t.evidence_check = result
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_evidence_check",
                    resource_type="trial", resource_id=tid,
                    detail={"verdict": result["verdict"], "count": len(result["checks"])})
    return result


@router.post("/{tid}/evidence-review")
def evidence_review(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """物证情况审查（辅助·离线规则引擎）：按受贿案取证三维度框架生成可视化审查矩阵。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    _ensure_active(t)
    c = db.get(Case, t.case_id) if t.case_id else None
    result = _evidence_review(c, t, db)
    t.evidence_review = result
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_evidence_review",
                    resource_type="trial", resource_id=tid,
                    detail={"verdict": result["verdict"], "missing": len(result["missing"])})
    return result


@router.post("/{tid}/physical-evidence")
def add_physical_evidence(tid: int, data: PhysicalEvidenceIn,
                          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """登记一件物证（查封扣押 / 用赃款购买）。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    _ensure_active(t)
    pe = list(t.physical_evidence or [])
    eid = max([e.get("id", 0) for e in pe], default=0) + 1
    pe.append({"id": eid, **data.dict()})
    t.physical_evidence = pe
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_physical_evidence",
                    resource_type="trial", resource_id=tid, detail={"op": "add", "id": eid})
    return {"id": eid, "physical_evidence": pe}


@router.put("/{tid}/physical-evidence/{eid}")
def update_physical_evidence(tid: int, eid: int, data: PhysicalEvidenceIn,
                            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """修改一件物证条目。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    pe = list(t.physical_evidence or [])
    found = False
    for e in pe:
        if e.get("id") == eid:
            e.update(data.dict())
            found = True
            break
    if not found:
        raise HTTPException(404, "物证条目不存在")
    t.physical_evidence = pe
    db.commit()
    return {"physical_evidence": pe}


@router.delete("/{tid}/physical-evidence/{eid}")
def delete_physical_evidence(tid: int, eid: int,
                            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """删除一件物证条目。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    pe = [e for e in (t.physical_evidence or []) if e.get("id") != eid]
    t.physical_evidence = pe
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_physical_evidence",
                    resource_type="trial", resource_id=tid, detail={"op": "del", "id": eid})
    return {"physical_evidence": pe}


@router.post("/{tid}/procedure-check")
def procedure_check(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """程序合规检查（辅助·数据驱动门禁）：依据案件/审理真实字段逐项判定，凡系统无法核验的环节标『须人工确认』。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    c = db.get(Case, t.case_id) if t.case_id else None
    result = _procedure_check(t, c)
    t.procedure_check = result
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_procedure_check",
                    resource_type="trial", resource_id=tid,
                    detail={"verdict": result["verdict"], "count": len(result["checks"])})
    return result


@router.post("/{tid}/sentencing-ref")
def sentencing_ref(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """类案量纪参考（辅助，只给区间不给结论）：基于法规 RAG 检索同类条款。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    c = db.get(Case, t.case_id) if t.case_id else None
    q = t.problem_type or (c.problem_type if c else "") or (c.title if c else "") or "违纪"
    refs = []
    try:
        # 原实现把 app.rag.retrieve 模块当函数调用，异常被吞后永远返回空列表；改为调用模块内的 retrieve()
        from app.rag import retrieve as _rt
        hits = _rt.retrieve(db, q, top_k=5).get("hits", [])
        for h in hits:
            refs.append({"article": h.get("article_no", ""),
                         "content": (h.get("content") or "")[:160],
                         "regulation": h.get("regulation", "")})
    except Exception:
        refs = []
    result = {
        "query": q,
        "refs": refs,
        "note": "以上为同类条款与处分档次区间参考，最终量纪须集体审议决定，系统不替代人工结论。",
    }
    t.sentencing_ref = result
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_sentencing_ref",
                    resource_type="trial", resource_id=tid, detail={"refs": len(refs)})
    return result


@router.get("/{tid}/sentencing-reasoning")
def sentencing_reasoning(tid: int, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """量纪说理稿（离线·确定性）：依据在案事实与法规条文，生成结构化定性与量纪说理，
    供承办人审理说理参考；最终处分档次须集体审议，系统不替代人工结论。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    c = db.get(Case, t.case_id) if t.case_id else None
    return build_sentencing_reasoning(t, c, db)


@router.post("/{tid}/sentencing-reasoning")
def sentencing_reasoning_manual(tid: int, data: SentencingManual, db: Session = Depends(get_db),
                                user: User = Depends(get_current_user)):
    """量纪说理稿（含审理组人工认定情节）：勾选从轻/从重情节后重算，
    人工项与系统自动识别项去重合并，成稿标注「经审理组人工认定」。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    c = db.get(Case, t.case_id) if t.case_id else None
    manual_light, manual_heavy = _resolve_manual(data.light_keys, data.heavy_keys)
    return build_sentencing_reasoning(t, c, db, manual_light=manual_light, manual_heavy=manual_heavy)


# 审理字段 → 文书模板字段 别名映射（用于审理报告套模板）
_TRIAL_FIELD_ALIASES = {
    "subject": ["被审查人", "被反映人", "被调查人", "审查对象", "调查对象", "核查对象",
                "姓名", "对象", "当事人", "人员", "涉案人员"],
    "unit": ["单位", "所在单位", "工作单位", "所在部门", "所在地区", "职务"],
    "rank": ["职级", "级别", "行政级别"],
    "political_status": ["政治面貌", "面貌"],
    "problem_type": ["问题性质", "问题类别", "违纪类型", "案由", "问题类型", "类别", "违纪性质", "定性"],
    "case_status": ["立案状态", "案件状态", "状态", "办理状态", "初核状态"],
    "source": ["线索来源", "来源", "案件来源"],
    "opened_at": ["立案时间", "立案日期", "初核时间", "受理时间", "登记时间"],
    "involved_amount": ["涉案金额", "涉及金额", "金额", "数额", "赃款", "违法所得", "涉案财物", "款项"],
    "brief": ["简要案情", "案情", "主要事实", "违纪事实", "主要问题", "问题概要", "事实", "案情摘要"],
    "transfer_from": ["移送单位", "移送审查调查组", "移送机关", "移送部门", "审查调查组"],
    "title": ["标题", "事项", "案件名称", "事由", "名称"],
    "code": ["编号", "案件编号", "文号"],
}


def _trial_template_fields(t: Trial, c) -> dict:
    """把审理事项（含可选关联案件）收敛为模板字段的规范化取值。"""
    opened = t.opened_at or (c.opened_at if c else None)
    return {
        "subject": t.subject or (c.subject if c else "") or "",
        "unit": t.unit or (c.unit if c else "") or "",
        "rank": t.rank or "",
        "political_status": t.political_status or "",
        "problem_type": t.problem_type or (c.problem_type if c else "") or "",
        "case_status": t.case_status or (c.status if c else "") or "",
        "source": t.source or (c.source if c else "") or "",
        "opened_at": opened.strftime("%Y-%m-%d") if opened else "",
        "involved_amount": t.involved_amount or "",
        "brief": t.brief or ((c.content if c else "") or ""),
        "transfer_from": t.transfer_from or "",
        "title": t.title or "",
        "code": c.code if c else "",
    }


def _render_trial_template(tpl: DocumentTemplate, fields: dict) -> str:
    """按模板渲染审理报告：字段缺的一律标〔待补充〕，绝不编造。"""
    content = tpl.template
    for f in (tpl.fields or []):
        key = (f.get("key") or "").strip()
        if not key:
            continue
        val = ""
        for canon, aliases in _TRIAL_FIELD_ALIASES.items():
            if key in aliases or any(a in key for a in aliases):
                val = fields.get(canon, "")
                break
        content = content.replace("{{" + key + "}}", val or "〔待补充〕")
    return re.sub(r"\{\{[^}]+\}\}", "〔待补充〕", content)


def _attach_refs(db: Session, user: User, t: Trial, refs: list[dict]) -> list[dict]:
    """校验并落痕类案引用，返回可写入报告的引用清单。

    只认「报告存在 + 内容非空」的引用；报告已删的静默跳过（前端选的可能是旧数据）。
    每条引用写一条 ReportReference（note 标注来自哪次审理），满足审计链可回溯。
    """
    cited = []
    for r in (refs or [])[:10]:
        try:
            rid = int(r.get("report_id") or 0)
            fid = int(r.get("finding_id") or 0)
        except (TypeError, ValueError):
            continue
        txt = (r.get("content") or "").strip()
        if not rid or not txt:
            continue
        rep = db.get(HistoricalReport, rid)
        if not rep:
            continue
        db.add(ReportReference(
            report_id=rid, finding_id=fid or None,
            query=(r.get("query") or "")[:200],
            note=f"审理报告引用(审理#{t.id})", user_id=user.id))
        cited.append({
            "report_id": rid, "finding_id": fid or 0,
            "title": (r.get("title") or rep.title or "")[:120],
            "year": int(r.get("year") or rep.year or 0),
            "problem_type": (r.get("problem_type") or rep.problem_category or "")[:40],
            "content": txt[:300],
        })
    return cited


@router.post("/extract")
async def intake_extract(file: UploadFile = File(...), db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """上传审查调查报告/移送材料（Word/PDF/TXT）→ 自动识别审理建档字段，回填新建表单。

    规则识别、离线可用；未命中的字段不返回（缺失在 missing 中可见），绝不编造。
    """
    from app.report_extract import extract_text
    from app.trial_extract import extract_trial_fields as _extract_fields
    data = await file.read()
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(413, "文件过大（≤30MB）")
    try:
        ex = extract_text(data, file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    res = _extract_fields(ex.get("text") or "")
    audit.write_log(db, user.id, user.username, action="trial_extract",
                    resource_type="trial", resource_id=0,
                    detail={"filename": file.filename, "confidence": res["confidence"],
                            "matched": len(res["fields"])})
    return {"fields": res["fields"], "missing": res["missing"],
            "confidence": res["confidence"], "engine": ex.get("engine", ""),
            "filename": file.filename}


@router.post("/{tid}/report")
def gen_report(tid: int, data: TrialReport, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """审理报告生成（A3）：套模板生成初稿，走 AI初稿→人工修改→审理确认 责任链。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    _ensure_active(t)
    tpl = db.get(DocumentTemplate, data.template_id) if data.template_id else None
    if data.template_id and not tpl:
        raise HTTPException(404, "所选文书模板不存在")
    c = db.get(Case, t.case_id) if t.case_id else None
    subj = t.subject or (c.subject if c else "×")
    unit = t.unit or (c.unit if c else "×")
    facts = (c.content if c else "") or (t.brief or "（请补充阅卷事实）")
    code = c.code if c else ""
    title = f"{subj}涉嫌违纪案审理报告" if (t.subject or c) else "审理报告"
    # 旧版简式态度行：仅在没有结构化谈话信息（无谈话人/逐条申辩）时兜底挂在第四章，
    # 避免与「附：见面谈话及申辩采纳情况」段重复
    has_structured_interview = bool(t.interviewers or t.defense_items)
    attitude_line = (
        f"（审理阶段已与被审查调查人见面谈话，其态度：{t.interview_attitude or '待核对'}；{t.interview_note or ''}）"
        if (t.interview_attitude and not has_structured_interview) else ""
    )
    # 量纪说理引擎：把「定性量纪意见」由一句空话升级为结构化说理稿（离线·确定性，不臆断档次）
    manual_light, manual_heavy = _resolve_manual(getattr(data, "light_keys", []), getattr(data, "heavy_keys", []))
    reasoning = build_sentencing_reasoning(t, c, db, manual_light=manual_light, manual_heavy=manual_heavy)
    reasoning_block = reasoning["text"]

    # 报告前段（一~四：基本情况 / 案件来源及审理经过 / 主要违纪违法事实 / 证据情况）
    # 结构对标《监督执纪工作规则》第五十六条：基本情况、主要违纪违法事实、证据、
    # 定性量纪意见、涉案财物情况、处理意见。
    status_txt = f"，{t.rank}" if t.rank else ""
    status_txt += f"，{t.political_status}" if t.political_status else ""
    head = (
        f"{title}\n\n"
        f"一、被审查调查人基本情况\n{subj}（{unit}）{status_txt}。\n\n"
        f"二、案件来源及审理经过\n{_trial_journey(db, t, c)}\n\n"
        f"三、主要违纪违法事实\n{facts}\n\n"
        f"四、证据情况\n{_evidence_section(t)}{attitude_line}\n\n"
    )
    # 附段：见面谈话及申辩采纳情况（第五十一~五十三条；未谈话则不生成，不打乱七章编号）
    interview_block = _interview_section(t)
    if interview_block:
        head += interview_block + "\n\n"
    # 说理稿拆为三章：五、定性量纪意见（一~四）｜六、涉案财物情况（五）｜七、处理意见（六+说明）
    p5, p6 = "（五）涉案财物处置意见", "（六）处理意见与建议"
    i5, i6 = reasoning_block.find(p5), reasoning_block.find(p6)
    if 0 <= i5 < i6:
        before_prop = reasoning_block[:i5].rstrip()
        if before_prop.startswith("四、定性量纪意见"):
            before_prop = before_prop[len("四、定性量纪意见"):].lstrip()
        # 七、章剥掉说理稿自带的「（六）」小标题（章标题已由报告层给出，避免同题重复）
        after_prop = reasoning_block[i6 + len(p6):].lstrip()
        body = (
            "五、定性量纪意见\n" + before_prop + "\n\n"
            "六、涉案财物情况\n" + reasoning_block[i5 + len(p5):i6].strip() + "\n\n"
            "七、处理意见与建议\n" + after_prop
        )
    else:
        body = "五、定性量纪意见\n" + reasoning_block
    # 尾部：审理组署名与报告日期（单行，避免全角空格被规范化层 strip 后视觉折断）
    body += "\n\n审理组负责人：　　　　　　审理人：　　　　　　报告日期：　　年　　月　　日"
    if tpl:
        # 模板模式：按选用模板 + 审理字段渲染（缺字段标〔待补充〕，绝不编造）
        content = gongwen.format_document(_render_trial_template(tpl, _trial_template_fields(t, c)))
        used_template = tpl.name
        # 模板结构各异，将结构化说理稿（七章重组后）作为补充章节附于文末（类案之前）
        content = content.rstrip() + "\n\n" + body
    else:
        # 默认结构：前段 + 七章结构化正文（确定性生成，不走 LLM 改写以保全事实、不臆断处分档次）
        content = gongwen.format_document(head + body)
        used_template = ""
    # 类案参考：审理人员从历史报告库选用的问题表述，原样附于报告末尾（供定性量纪
    # 参照），并逐条落 ReportReference 留痕——引用可回溯是审理辅助的底线要求。
    cited = _attach_refs(db, user, t, data.refs or [])
    if cited:
        lines = ["八、类案参考",
                 "（以下表述引自历史审计报告，供定性量纪参照；事实认定以本案证据为准）", ""]
        for i, cr in enumerate(cited, 1):
            head = f"{i}. {cr['title'] or '历史报告'}"
            meta = "｜".join(x for x in [str(cr["year"] or ""), cr["problem_type"] or ""] if x)
            if meta:
                head += f"（{meta}）"
            lines += [head, f"   {cr['content']}", ""]
        content = content.rstrip() + "\n\n" + gongwen.format_document("\n".join(lines).rstrip())
    d = Document(template_id=tpl.id if tpl else None, source="流程生成", title=f"{code}——{title}（AI初稿）" if code else f"{title}（AI初稿）",
                 case_name=code, content=content,
                 status="AI初稿", created_by=user.id)
    db.add(d)
    db.flush()
    db.add(DocumentVersion(document_id=d.id, version_no=1, content=content,
                           note=f"审理报告·{used_template or '默认结构'}生成", created_by=user.id))
    t.report_doc_id = d.id
    # 重生成报告不覆盖已定稿/已执行等终态；其它状态统一记为"已报告"
    if t.status not in ("已定稿", "已执行"):
        t.status = "已报告"
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_report",
                    resource_type="trial", resource_id=tid,
                    detail={"doc_id": d.id, "refs": len(cited), "template": used_template})
    out = _trial_out(db, t)
    out["document"] = {"id": d.id, "title": d.title, "content": content}
    out["cited"] = cited
    return out


@router.post("/{tid}/confirm")
def confirm_trial(tid: int, data: TrialConfirm, db: Session = Depends(get_db),
                  user: User = Depends(require_roles("shenli", "admin"))):
    """审理定稿确认（责任链末端）：须审理室/管理员确认，并记录集体审议结论。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    _ensure_active(t)
    if t.status == "已定稿":
        raise HTTPException(400, "该案已定稿，请勿重复确认")
    t.status = "已定稿"
    t.confirmed_at = datetime.utcnow()
    t.confirmed_note = data.note or ""
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_confirm",
                    resource_type="trial", resource_id=tid, detail={"note": data.note or ""})
    return _trial_out(db, t)


@router.post("/{tid}/accept")
def accept_trial(tid: int, data: TrialAccept, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """受理审核（移送审理把关）：审核是否符合移送条件，决定受理 / 暂缓受理 / 不予受理。
    受理后启动审理时限（受理起 1 个月）；不予受理则阻断后续审理流程（红线）。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if data.decision not in ("受理", "暂缓受理", "不予受理"):
        raise HTTPException(400, "decision 须为 受理 / 暂缓受理 / 不予受理")
    if t.accept_decision == "不予受理":
        raise HTTPException(400, "该案已决定不予受理，不可重复受理")
    t.accept_decision = data.decision
    t.accept_reason = data.reason or ""
    if data.decision == "受理":
        t.accepted_at = datetime.utcnow()
        if t.status in ("待阅卷", "暂缓受理"):
            t.status = "审理中"
    elif data.decision == "暂缓受理":
        t.accepted_at = None
        t.status = "暂缓受理"
    else:  # 不予受理
        t.accepted_at = None
        t.status = "不予受理"
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_accept",
                    resource_type="trial", resource_id=tid,
                    detail={"decision": data.decision, "reason": data.reason or ""})
    return _trial_out(db, t)


@router.post("/{tid}/interview")
def interview_trial(tid: int, data: TrialInterview, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """与被审查调查人见面谈话：核对事实、听取申辩并如实记录（《监督执纪工作规则》第五十一~五十三条）。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    _ensure_active(t)
    if t.status in ("不予受理", "退回补充调查"):
        raise HTTPException(400, "当前状态不可进行见面谈话")
    t.interview_at = datetime.utcnow()
    t.interview_attitude = data.attitude or ""
    t.interview_note = data.note or ""
    t.interviewers = (data.interviewers or "").strip()
    t.recorder = (data.recorder or "").strip()
    t.interview_place = (data.place or "").strip()
    t.rights_notified = 1 if data.rights_notified else 0
    # 逐条申辩：只收结构化字段（content/stance/adopt/reason），未知值归一为「待审议」，不臆断采纳结论
    items = []
    for it in (data.defense_items or []):
        content = (it.content or "").strip()
        if not content:
            continue
        adopt = (it.adopt or "").strip()
        items.append({
            "content": content,
            "stance": (it.stance or "提出申辩").strip() or "提出申辩",
            "adopt": adopt if adopt in ("采纳", "部分采纳", "不采纳") else "待审议",
            "reason": (it.reason or "").strip(),
        })
    t.defense_items = items
    t.interview_summary = (data.summary or "").strip()
    db.commit()
    # 程序合规提示（不阻断保存：提示须承办人复核，系统不替代人工认定合规性）
    warnings: list[str] = []
    interviewer_list = [x for x in t.interviewers.replace(",", "、").replace("，", "、").split("、") if x.strip()]
    if len(interviewer_list) < 2:
        warnings.append("谈话人不足 2 人（规则第五十一条要求 2 名以上审理人员在场），须复核补正")
    if not t.rights_notified:
        warnings.append("未记录已告知被审查调查人有申辩的权利（第五十二条），须在谈话笔录中补记")
    pending = [x for x in items if x["adopt"] == "待审议"]
    if pending:
        warnings.append(f"有 {len(pending)} 条申辩尚未明确采纳结论及理由（第五十三条），须审理组审议后补填")
    audit.write_log(db, user.id, user.username, action="trial_interview",
                    resource_type="trial", resource_id=tid,
                    detail={"attitude": data.attitude or "", "note": data.note or "",
                            "interviewers": t.interviewers, "recorder": t.recorder,
                            "place": t.interview_place, "rights_notified": t.rights_notified,
                            "defense_count": len(items), "summary": t.interview_summary,
                            "warnings": warnings})
    out = _trial_out(db, t)
    out["warnings"] = warnings
    return out


@router.post("/{tid}/return")
def return_trial(tid: int, data: TrialReturn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """退回补充 / 重新审查调查：事实不清、证据不足的，经批准退回审查调查部门（回链案件）。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if t.status == "退回补充调查":
        raise HTTPException(400, "该案已退回，请勿重复退回")
    t.status = "退回补充调查"
    t.returned_at = datetime.utcnow()
    t.return_reason = data.reason or ""
    t.return_to = data.to or "审查调查组"
    # 回链案件：退回重新审查调查
    c = db.get(Case, t.case_id) if t.case_id else None
    if c and c.status in ("已立案", "已处置", "审理中"):
        c.status = "初核中"
        db.commit()
        audit.write_log(db, user.id, user.username, action="case_return",
                        resource_type="case", resource_id=c.id,
                        detail={"from_trial": tid, "reason": data.reason or "", "to": t.return_to})
    else:
        db.commit()
    audit.write_log(db, user.id, user.username, action="trial_return",
                    resource_type="trial", resource_id=tid,
                    detail={"reason": data.reason or "", "to": t.return_to})
    return _trial_out(db, t)


@router.post("/{tid}/execute")
def execute_trial(tid: int, data: TrialExecuteIn, db: Session = Depends(get_db),
                  user: User = Depends(require_roles("shenli", "admin"))):
    """处分决定宣布执行（结构化）：须已定稿；宣布人/范围/送达/签收逐项记录；
    处分决定作出后 1 个月内宣布执行，逾期在 warnings 中如实提示。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if t.status != "已定稿":
        raise HTTPException(400, "须先审理定稿，再宣布执行处分决定")
    if t.executed_at:
        raise HTTPException(400, "已宣布执行，不可重复宣布（如需更正执行记录请联系管理员后台修订）")
    if data.delivery and data.delivery not in ("直接送达", "邮寄送达", "留置送达", "转交送达"):
        raise HTTPException(400, "delivery 须为 直接送达 / 邮寄送达 / 留置送达 / 转交送达")
    t.executed_at = datetime.utcnow()
    t.execute_note = data.note or ""
    t.execute_announcer = (data.announcer or "").strip()
    t.execute_scope = (data.scope or "").strip()
    t.execute_delivery = (data.delivery or "").strip()
    t.execute_signed = 1 if data.signed else 0
    t.status = "已执行"
    # 回证清单初始化（尚未报结的，重登执行时保留已回填项）
    t.execution_items = _normalize_execution_items(t.execution_items, t.decision_result)
    db.commit()
    # 程序提示：宣布时限（自处分决定作出起 1 个月）逾期如实提示，不阻断
    warnings: list[str] = []
    if t.decision_date:
        days = (t.executed_at - t.decision_date).days
        if days > 31:
            warnings.append(f"处分决定作出已 {days} 日方宣布执行，超出 1 个月时限，须书面说明原因")
    elif not t.decision_date:
        warnings.append("未记录处分决定作出日期，无法核算宣布时限，须补录")
    if not t.execute_announcer:
        warnings.append("未填写宣布人，须补录")
    if not t.execute_signed:
        warnings.append("未记录本人签收，须补录签收情况（拒绝签收的应注明并见证）")
    audit.write_log(db, user.id, user.username, action="trial_execute",
                    resource_type="trial", resource_id=tid,
                    detail={"announcer": t.execute_announcer, "scope": t.execute_scope,
                            "delivery": t.execute_delivery, "signed": t.execute_signed,
                            "note": t.execute_note, "warnings": warnings})
    out = _trial_out(db, t)
    out["warnings"] = warnings
    return out


@router.post("/{tid}/decision")
def gen_decision(tid: int, data: DecisionCreate, db: Session = Depends(get_db),
                 user: User = Depends(require_roles("shenli", "admin"))):
    """生成《党纪处分决定书》初稿：须已定稿；裁量与程序事项（处分档次/文号/
    批准机关/日期）由人工填写，系统不预设；缺项以〔待填〕标注。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if t.status not in ("已定稿", "已执行"):
        raise HTTPException(400, "须先审理定稿，再制作处分决定书")
    decision_date = None
    if (data.decision_date or "").strip():
        try:
            decision_date = datetime.strptime(data.decision_date.strip()[:10], "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "decision_date 须为 YYYY-MM-DD")
    c = db.get(Case, t.case_id) if t.case_id else None
    d = build_decision_document(t, c, decision_no=data.decision_no,
                                decision_result=data.decision_result,
                                decision_date=decision_date,
                                decision_approver=data.decision_approver)
    t.decision_no = (data.decision_no or "").strip()
    t.decision_result = (data.decision_result or "").strip()
    t.decision_date = decision_date
    t.decision_approver = (data.decision_approver or "").strip()
    content = gongwen.format_document(d["content"])
    title = f"关于给予{t.subject or (c.subject if c else '被处分人')}党纪处分的决定"
    if t.decision_result:
        title = f"关于给予{t.subject or (c.subject if c else '被处分人')}{t.decision_result}的决定"
    doc = Document(title=title + "（AI初稿）", source="流程生成", case_name=(c.code if c else ""),
                   content=content, status="AI初稿", created_by=user.id)
    db.add(doc)
    db.flush()
    db.add(DocumentVersion(document_id=doc.id, version_no=1, content=content,
                           note="处分决定书生成", created_by=user.id))
    t.decision_doc_id = doc.id
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_decision",
                    resource_type="trial", resource_id=tid,
                    detail={"doc_id": doc.id, "decision_result": t.decision_result,
                            "approver": t.decision_approver,
                            "needs_manual": d["needs_manual"]})
    out = _trial_out(db, t)
    out["decision"] = {"doc_id": doc.id, "title": doc.title, "content": content,
                       "needs_manual": d["needs_manual"]}
    return out


@router.get("/{tid}/decision")
def get_decision(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """查看已生成的处分决定书（未生成返回 decision=None）。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if not t.decision_doc_id:
        return {"decision": None}
    doc = db.get(Document, t.decision_doc_id)
    if not doc:
        return {"decision": None}
    return {"decision": {"doc_id": doc.id, "title": doc.title, "content": doc.content,
                         "decision_no": t.decision_no, "decision_result": t.decision_result,
                         "decision_date": t.decision_date.strftime("%Y-%m-%d") if t.decision_date else "",
                         "decision_approver": t.decision_approver}}


@router.post("/{tid}/execution-items")
def update_execution_items(tid: int, data: ExecutionItemIn, db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    """执行回证清单逐项回填：落实项须填完成日期；未知 key 追加并标注。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if not t.executed_at:
        raise HTTPException(400, "须先宣布执行处分决定，再回填执行回证")
    if not (data.key or "").strip():
        raise HTTPException(400, "key 不能为空")
    items = _normalize_execution_items(t.execution_items, t.decision_result)
    hit = None
    for it in items:
        if it["key"] == data.key.strip():
            hit = it
            break
    if not hit:
        raise HTTPException(400, f"未知回证项 key：{data.key}")
    hit["status"] = data.status if data.status in ("done", "pending", "na") else "pending"
    hit["note"] = (data.note or "").strip()
    hit["at"] = (data.at or "").strip()[:10]
    if hit["status"] == "done" and not hit["at"]:
        raise HTTPException(400, "标记为「已落实」须填写完成日期")
    t.execution_items = items
    # 恢复党员权利时间联动（restore 项落实时记录，留党察看专用）
    if data.key.strip() == "restore" and hit["status"] == "done" and hit["at"]:
        t.restore_rights_at = datetime.strptime(hit["at"], "%Y-%m-%d")
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_execution_item",
                    resource_type="trial", resource_id=tid,
                    detail={"key": data.key, "status": hit["status"], "at": hit["at"]})
    out = _trial_out(db, t)
    out["blockers"] = _execution_blockers(items)
    return out


@router.post("/{tid}/execution-close")
def close_execution(tid: int, db: Session = Depends(get_db),
                    user: User = Depends(require_roles("shenli", "admin"))):
    """回证报结：存在未落实标准项时不得报结（缺项不报结，系统不代人工放行）。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if not t.executed_at:
        raise HTTPException(400, "尚未宣布执行，不可报结")
    items = _normalize_execution_items(t.execution_items, t.decision_result)
    blockers = _execution_blockers(items)
    if blockers:
        raise HTTPException(400, "尚有执行事项未落实，不得报结：" + "；".join(blockers))
    t.execution_closed = 1
    t.execution_closed_at = datetime.utcnow()
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_execution_close",
                    resource_type="trial", resource_id=tid, detail={})
    return _trial_out(db, t)


def _build_execution_report(t: "Trial", case=None) -> str:
    """生成《党纪处分决定执行情况报告》（执行回证）正文。"""
    subj = t.subject or (case.subject if case else "（被处分人）")
    items = _normalize_execution_items(t.execution_items, t.decision_result)
    lines = []
    lines.append(f"关于给予{subj}党纪处分决定执行情况的报告")
    lines.append("")
    # 一、处分决定基本情况
    lines.append("一、处分决定基本情况")
    lines.append(f"被处分人：{subj}（{t.unit or '〔单位待补充〕'}）")
    lines.append(f"处分档次：{t.decision_result or '〔待填〕'}")
    lines.append(f"文号：{t.decision_no or '〔待填〕'}")
    lines.append(f"批准机关：{t.decision_approver or '〔待填〕'}")
    lines.append(f"处分决定日期：{t.decision_date.strftime('%Y年%m月%d日') if t.decision_date else '〔待填〕'}")
    lines.append("")
    # 二、宣布执行情况
    lines.append("二、处分决定宣布执行情况")
    lines.append(f"宣布日期：{t.executed_at.strftime('%Y年%m月%d日') if t.executed_at else '〔待填〕'}")
    lines.append(f"宣布人：{t.execute_announcer or '〔待填〕'}")
    lines.append(f"宣布范围：{t.execute_scope or '〔待填〕'}")
    lines.append(f"送达方式：{t.execute_delivery or '〔待填〕'}")
    lines.append(f"本人签收：{'已签收' if t.execute_signed else '未签收（应注明并见证）'}")
    lines.append(f"执行情况说明：{t.execute_note or '（无）'}")
    lines.append("")
    # 三、执行回证落实情况
    lines.append("三、执行回证落实情况")
    for i, it in enumerate(items, 1):
        st = {"done": "已落实", "na": "不适用", "pending": "未落实"}.get(it["status"], it["status"])
        extra = f"（{it['at']}）" if it.get("at") else ""
        note = f" — {it['note']}" if it.get("note") else ""
        lines.append(f"{i}. {it['label']}：{st}{extra}{note}")
    lines.append("")
    # 四、恢复党员权利（仅留党察看）
    lines.append("四、恢复党员权利")
    if _restore_applicable(t.decision_result):
        if t.restore_rights_at:
            lines.append(f"留党察看期满后，于 {t.restore_rights_at.strftime('%Y年%m月%d日')} 恢复党员权利。")
        else:
            lines.append("（留党察看期内，期满恢复党员权利时间待落实后回填）")
    else:
        lines.append("本案非留党察看处分，不适用恢复党员权利。")
    lines.append("")
    # 落款
    lines.append(f"承办：{t.created_by or ''}　报结日期：{datetime.utcnow().strftime('%Y年%m月%d日')}")
    return "\n".join(lines)


@router.post("/{tid}/execution-report")
def gen_execution_report(tid: int, db: Session = Depends(get_db),
                         user: User = Depends(require_roles("shenli", "admin"))):
    """生成《党纪处分决定执行情况报告》（执行回证）并入库；须已报结。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    if not t.execution_closed:
        raise HTTPException(400, "须先完成执行回证报结，再生成执行情况报告")
    c = db.get(Case, t.case_id) if t.case_id else None
    content = gongwen.format_document(_build_execution_report(t, c))
    doc = Document(source="流程生成", title=f"关于给予{t.subject or (c.subject if c else '被处分人')}处分决定执行情况的报告（AI初稿）",
                   case_name=(c.code if c else ""), content=content,
                   status="AI初稿", created_by=user.id)
    db.add(doc)
    db.flush()
    db.add(DocumentVersion(document_id=doc.id, version_no=1, content=content,
                           note="执行回证报告生成", created_by=user.id))
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_execution_report",
                    resource_type="trial", resource_id=tid, detail={"doc_id": doc.id})
    return {"doc_id": doc.id, "title": doc.title, "content": content}


@router.post("/{tid}/property")
def property_trial(tid: int, data: TrialProperty, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """涉案财物处置意见：依规收缴 / 责令退赔 / 登记上交 / 随案移送（规则第五十八条）。"""
    t = db.get(Trial, tid)
    if not t:
        raise HTTPException(404, "审理事项不存在")
    _ensure_active(t)
    t.property_disposal = data.disposal or ""
    db.commit()
    audit.write_log(db, user.id, user.username, action="trial_property",
                    resource_type="trial", resource_id=tid, detail={"disposal": data.disposal or ""})
    return _trial_out(db, t)
