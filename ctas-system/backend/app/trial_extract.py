"""审理文书字段识别引擎（离线规则优先，绝不编造）。

目标：上传审查调查报告 / 移送审理材料（Word/PDF/TXT）后**自动识别审理建档字段**，
回填「新建审理项目」表单，人工校正后即可建档。

设计原则（与 report_extract 一致）：
1. **规则优先、命中即信**：确定性正则/关键词抽取；未命中的字段不出现并计入
   missing_fields——缺失必须可见，绝不拿猜测值糊弄（审理字段错一字即事实错误）。
2. **离线可用**：纯规则，不依赖大模型；离网信创环境稳定可用。
3. **复用既有能力**：文本抽取走 app.report_extract.extract_text（docx/pdf/txt），
   金额/小标题切段复用其内部工具，避免重复造轮子。
"""
from __future__ import annotations

import re

# ---------------- 问题性质关键词（命中最多者胜出） ----------------
_PROBLEM_TYPE_KW = {
    "职务犯罪": ["涉嫌犯罪", "移送司法", "追究刑事责任", "职务犯罪", "涉嫌受贿", "贪污公款"],
    "职务违法": ["职务违法", "构成违法", "政务处分", "违法行为"],
    "违反政治纪律": ["政治纪律", "拉票贿选", "团团伙伙", "妄议中央", "干扰换届"],
    "违反组织纪律": ["组织纪律", "跑官要官", "违规用人", "个人有关事项", "隐瞒不报", "函询"],
    "违反廉洁纪律": ["廉洁纪律", "收受", "礼金", "礼品", "贿赂", "权钱交易", "违规从事营利", "经商办企业"],
    "违反群众纪律": ["群众纪律", "吃拿卡要", "刁难群众", "克扣", "挪用惠民", "优亲厚友"],
    "违反工作纪律": ["工作纪律", "失职渎职", "不作为", "乱作为", "违规干预", "失职失责"],
    "违反生活纪律": ["生活纪律", "道德败坏", "生活奢靡", "家风不正"],
}

_SOURCE_KW = {
    "信访": ["信访", "举报", "检举", "控告"],
    "巡视": ["巡视", "巡察", "巡视移交", "巡察发现"],
    "审计": ["审计移交", "审计发现", "审计报告", "审计移送"],
    "上级交办": ["交办", "指定管辖", "上级移交", "转办"],
    "主动发现": ["主动发现", "日常监督发现", "监督检查发现"],
}

# 立案状态：按优先级匹配（越"靠后"的状态越优先呈现）
_STATUS_RULES = [
    ("已处置", ["已处置", "给予处分", "处分决定", "处理到位", "已给予"]),
    ("已立案", ["立案"]),   # 广义：决定立案/予以立案/立案审查均含"立案"
    ("初核中", ["初核中", "初步核实", "初核阶段", "正在初核"]),
    ("直接了结", ["予以了结", "直接了结", "谈话了结", "诫勉谈话", "予以结案"]),
]

_RANK_RE = re.compile(r"(厅局级|县处级|乡科级|县区级|科员|办事员)(正|副)?职?")
_POLITICAL_RE = re.compile(r"(中共党员|中共预备党员|预备党员|共青团员|群众|民主党派)")
_DATE_CN = re.compile(r"([〇零○O一二三四五六七八九]{4}|\d{4})\s*年\s*([〇零一二三四五六七八九十]{1,3}|\d{1,2})\s*月\s*([〇零一二三四五六七八九十]{1,3}|\d{1,2})\s*日?")
_DATE_NUM = re.compile(r"((?:19|20)\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})")
_SUBJECT_LABEL_RE = re.compile(
    r"(?:被审查调查人|被审查人|被反映人|被调查人|审查对象|调查对象|核查对象|当事人|涉案人员|姓名)\s*[:：]?\s*([一-龥]{2,4})")
_UNIT_LABEL_RE = re.compile(r"(?:所在单位|工作单位|所在部门|单位)\s*[:：]\s*([一-龥（）()]{2,30})")
_UNIT_WORK_RE = re.compile(r"在([一-龥（）()]{2,20}?)(?:工作|任职|担任)")
_UNIT_TITLE_RE = re.compile(r"([一-龥（）()]{2,20}?(?:局|厅|委|办|处|科|部|中心|政府|街道|镇|乡|学校|医院|公司|集团|银行))")
_TRANSFER_RE = re.compile(r"(?:移送(?:审理)?(?:单位|机关|部门)?|审查调查组|办案单位)\s*[:：]?\s*([一-龥]{2,20}?(?:组|室|委|局|处|部))")

_CN_NUM = {"〇": 0, "零": 0, "○": 0, "O": 0, "一": 1, "二": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_int(s: str) -> int | None:
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    if not s:
        return None
    if "十" in s:
        parts = s.split("十")
        left = _CN_NUM.get(parts[0], 1) if parts[0] else 1
        right = _CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return left * 10 + right
    total = 0
    for ch in s:
        if ch in _CN_NUM:
            total = total * 10 + _CN_NUM[ch]
        else:
            return None
    return total or None


def _pick_by_keywords(text: str, table: dict[str, list[str]]) -> str:
    best, best_n = "", 0
    for key, kws in table.items():
        n = sum(text.count(k) for k in kws)
        if n > best_n:
            best, best_n = key, n
    return best


def _first_date(text: str) -> str:
    """取全文第一个合理日期（立案时间通常出现在文首，落款在文末）。"""
    m = _DATE_NUM.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31 and 2000 <= y <= 2100:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = _DATE_CN.search(text)
    if m:
        y, mo, d = _cn_int(m.group(1)), _cn_int(m.group(2)), _cn_int(m.group(3))
        if y and mo and 1 <= mo <= 12 and 1 <= (d or 1) <= 31 and 2000 <= y <= 2100:
            return f"{y:04d}-{mo:02d}-{(d or 1):02d}"
    return ""


_BRIEF_LABEL_RE = re.compile(
    r"(?:主要违纪事实|主要违法事实|违纪事实|主要问题|基本案情|案情|经查|经审查调查)\s*[：:]\s*(.+)")


def _extract_brief(text: str, max_chars: int = 600) -> str:
    """抽取主要违纪事实/问题概述（先按独立小标题切段，再兜底"标签：内容"同行格式）。"""
    from app.report_extract import _extract_section
    heads = ["主要违纪事实", "主要违法事实", "违纪事实", "主要问题", "查明",
             "经审查调查", "经查", "基本案情", "案情"]
    sec = _extract_section(text, heads, max_chars=max_chars)
    if sec:
        return sec
    m = _BRIEF_LABEL_RE.search(text)
    if m:
        return m.group(1).strip()[:max_chars]
    return ""


def extract_trial_fields(text: str) -> dict:
    """从审理文书全文抽取建档字段。返回 {fields, missing, confidence}。

    fields 只含命中的字段（不编造）；missing 列出未命中的关键字段；confidence 为命中比例。
    """
    text = (text or "").strip()
    fields: dict = {}
    if not text:
        return {"fields": {}, "missing": list(_KEY_FIELDS), "confidence": 0.0}

    # 被审查调查人
    m = _SUBJECT_LABEL_RE.search(text)
    if m:
        fields["subject"] = m.group(1)

    # 单位 / 职务
    m = _UNIT_LABEL_RE.search(text)
    if not m:
        m = _UNIT_WORK_RE.search(text)
    if not m:
        m = _UNIT_TITLE_RE.search(text[:800])
    if m:
        fields["unit"] = m.group(1).strip()

    # 职级 / 政治面貌
    m = _RANK_RE.search(text)
    if m:
        fields["rank"] = (m.group(1) + (m.group(2) or "") + "职") if m.group(2) else (m.group(1) + "职")
    m = _POLITICAL_RE.search(text)
    if m:
        fields["political_status"] = m.group(1)

    # 问题性质 / 线索来源 / 立案状态
    pt = _pick_by_keywords(text, _PROBLEM_TYPE_KW)
    if pt:
        fields["problem_type"] = pt
    src = _pick_by_keywords(text, _SOURCE_KW)
    if src:
        fields["source"] = src
    for status, kws in _STATUS_RULES:
        if any(k in text for k in kws):
            fields["case_status"] = status
            break

    # 立案时间 / 涉案金额
    od = _first_date(text)
    if od:
        fields["opened_at"] = od
    try:
        from app.report_extract import _extract_amount
        amt = _extract_amount(text)
        if amt:
            fields["involved_amount"] = f"{amt}万元"
    except Exception:
        pass

    # 简要案情
    brief = _extract_brief(text)
    if brief:
        fields["brief"] = brief[:600]

    # 移送审查调查组
    m = _TRANSFER_RE.search(text)
    if m:
        fields["transfer_from"] = m.group(1)
    elif "审查调查组" in text:
        fields["transfer_from"] = "审查调查组"

    missing = [k for k in _KEY_FIELDS if not fields.get(k)]
    confidence = round(1 - len(missing) / len(_KEY_FIELDS), 3)
    return {"fields": fields, "missing": missing, "confidence": confidence}


# 关键建档字段（用于缺失清单与置信度；subject/problem_type 为前端必填）
_KEY_FIELDS = [
    "subject", "unit", "problem_type", "case_status",
    "opened_at", "involved_amount", "brief",
]
