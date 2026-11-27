"""A2 规则引擎：干部管理权限与分级负责属地方性规则，必须可配置、可解释、可审计。
LLM 只做要素抽取，分办结论由确定性规则产生（V2.0 修订要点）。
示例规则仅为演示结构，正式使用须按本地规范配置并经业务处室审定。
"""

# 紧急程度识别规则（关键词命中即高优先级，且每条命中可解释）
URGENCY_RULES = [
    {"keywords": ["顶风违纪", "十八大后不收敛", "不收手"], "label": "顶风违纪"},
    {"keywords": ["涉黑", "恶势力", "保护伞"], "label": "涉黑恶保护伞"},
    {"keywords": ["群体性", "集体上访", "多人联名"], "label": "群体性事件风险"},
    {"keywords": ["舆情", "媒体曝光", "网络发酵"], "label": "舆情风险"},
]

# 问题类型关键词 → 六项纪律 + 职务违法/犯罪（示例映射）
CATEGORY_RULES = [
    {"label": "违反政治纪律", "keywords": ["政治纪律", "妄议中央", "对抗组织审查"]},
    {"label": "违反组织纪律", "keywords": ["组织纪律", "违规提拔", "个人有关事项", "跑官要官"]},
    {"label": "违反廉洁纪律", "keywords": ["收受", "礼金", "受贿", "经商办企业", "违规兼职", "公车私用", "违规吃喝", "公款"]},
    {"label": "违反群众纪律", "keywords": ["侵害群众利益", "克扣", "惠民资金", "低保"]},
    {"label": "违反工作纪律", "keywords": ["失职", "渎职", "不作为", "乱作为", "形式主义", "官僚主义"]},
    {"label": "违反生活纪律", "keywords": ["生活纪律", "婚外情", "赌博"]},
    {"label": "涉嫌职务违法/犯罪", "keywords": ["贪污", "挪用", "职务侵占", "滥用职权", "受贿犯罪"]},
]

# 分办规则引擎（条件 → 部门 + 依据说明），按顺序匹配，首条命中为准
DISPOSITION_RULES = [
    {
        "name": "属地管理",
        "match": lambda e: any(k in str(e.get("职务", "")) + str(e.get("单位", "")) for k in ["村", "社区", "居委会"]),
        "dept": "转下级纪委监委（属地办理）",
        "reason": "反映村（社区）干部问题，按分级负责原则转属地纪委监委办理",
        "basis": "《纪检监察机关处理检举控告工作规则》分级负责要求（示例规则）",
    },
    {
        "name": "重要岗位",
        "match": lambda e: any(k in str(e.get("职务", "")) for k in ["局长", "主任", "书记", "处长", "科长", "董事长", "一把手"]),
        "dept": "监督检查室（联系该单位）",
        "reason": "被反映人涉及部门主要负责人，建议由监督检查室按程序处置",
        "basis": "干部管理权限规定（示例规则）",
    },
    {
        "name": "派驻管辖",
        "match": lambda e: any(k in str(e.get("单位", "")) for k in ["局", "委", "办", "中心", "集团", "公司"]),
        "dept": "转派驻纪检监察组",
        "reason": "被反映人为驻在单位人员，建议转相应派驻纪检监察组处置",
        "basis": "派驻机构监督驻在单位职责（示例规则）",
    },
]

DEFAULT_DISPOSITION = {
    "name": "默认规则",
    "dept": "信访室登记转办",
    "reason": "未命中专项管辖规则，按常规分办流程办理",
    "basis": "《纪检监察机关处理检举控告工作规则》常规流程（示例规则）",
}


def rule_extract(content: str) -> dict:
    """无 LLM 时的规则抽取降级（正则 + 关键词），保证 Demo 无 Key 可跑。"""
    import re
    name = ""
    m = re.search(r"([\u4e00-\u9fa5]某某)", content) or re.search(r"([\u4e00-\u9fa5]{2,4})同志", content)
    if m:
        name = m.group(1)
    amounts = re.findall(r"(\d+(?:\.\d+)?)(万元|元)", content)
    amount = "".join(amounts[0]) if amounts else ""
    m = re.search(r"(20\d{2})\s*年", content)
    when = f"{m.group(1)}年" if m else ""
    unit, duty = "", ""
    m = re.search(r"(某[\u4e00-\u9fa5]{0,8}?(?:局|委|办|中心|集团|公司|镇|街道|村))", content)
    if m:
        unit = m.group(1)
    m = re.search(r"(?:担任|现任|任)(局长|主任|书记|处长|科长|董事长|村主任|股长|干部)", content)
    if not m:
        m = re.search(r"(局长|主任|书记|处长|科长|董事长|村主任)", content)
    if m:
        duty = m.group(1)
    cat = "检举控告" if any(k in content for k in ["举报", "反映", "控告"]) else (
        "申诉" if "申诉" in content else "批评建议")
    return {
        "被反映人": {"姓名": name, "单位": unit, "职务": duty},
        "问题类型": [c["label"] for c in CATEGORY_RULES if any(k in content for k in c["keywords"])],
        "涉及金额": amount, "时间": when, "地点": "",
        "举报分类": cat,
    }


def rule_classify(content: str) -> dict:
    """信访件分类（离线规则）：业务类别 + 问题类别 + 命中依据。"""
    # 业务类别：申诉 / 批评建议 / 检举控告
    if any(k in content for k in ["申诉", "不服处分", "复审", "复核", "申请重新处理"]):
        biz, biz_hit = "申诉", "含'申诉/不服处分/复审复核'表述"
    elif any(k in content for k in ["建议", "意见", "希望", "提议"]) and not any(
            k in content for k in ["举报", "反映", "控告", "违纪", "违法"]):
        biz, biz_hit = "批评建议", "含建议性表述且无违纪违法指控"
    else:
        biz, biz_hit = "检举控告", "含'举报/反映/控告'或违纪违法指控"

    cats, hits = [], []
    for c in CATEGORY_RULES:
        ks = [k for k in c["keywords"] if k in content]
        if ks:
            cats.append(c["label"])
            hits.append({"label": c["label"], "keywords": ks})
    if not cats:
        cats.append("其他（待人工归类）")
    return {"biz_type": biz, "biz_basis": biz_hit,
            "categories": cats, "category_hits": hits}


def rule_summary(content: str, max_sentences: int = 4) -> str:
    """信访件摘要（离线规则）：按信息密度打分取关键句，保持原文语序。"""
    import re
    text = re.sub(r"\s+", " ", content or "").strip()
    if not text:
        return ""
    if len(text) <= 90:
        return text
    sents = [s.strip() for s in re.split(r"(?<=[。！？；])|\n", text) if len(s.strip()) >= 6]
    if not sents:
        return text[:120]

    person = ""
    m = re.search(r"([\u4e00-\u9fa5]某某)", text) or re.search(r"([\u4e00-\u9fa5]{2,4})同志", text)
    if m:
        person = m.group(1)

    def score(s: str) -> int:
        sc = 0
        if person and person in s:
            sc += 3
        if re.search(r"\d+(?:\.\d+)?\s*(万元|元|万)", s):
            sc += 3
        if re.search(r"20\d{2}\s*年|\d+\s*月", s):
            sc += 2
        for c in CATEGORY_RULES:
            if any(k in s for k in c["keywords"]):
                sc += 2
                break
        if any(k in s for k in ["举报", "反映", "控告", "举报称", "反映称"]):
            sc += 2
        if any(k in s for k in ["局长", "主任", "书记", "处长", "科长", "董事长", "村主任"]):
            sc += 2
        if len(s) > 120:
            sc -= 2
        return sc

    ranked = sorted(range(len(sents)), key=lambda i: -score(sents[i]))
    picked = sorted(ranked[:max_sentences])
    summary = "".join(sents[i] for i in picked)
    return summary[:400]


def rule_urgency(content: str) -> tuple[str, list[dict]]:
    hits = [{"label": r["label"], "keyword": k}
            for r in URGENCY_RULES for k in r["keywords"] if k in content]
    return ("高" if hits else "中"), hits


def rule_disposition(elements: dict) -> tuple[dict, list[dict]]:
    # 展平被反映人嵌套字段，供规则匹配
    person = elements.get("被反映人") if isinstance(elements.get("被反映人"), dict) else {}
    flat = {"单位": person.get("单位", ""), "职务": person.get("职务", "")}
    flat.update({k: v for k, v in elements.items() if k != "被反映人"})
    for r in DISPOSITION_RULES:
        try:
            if r["match"](flat):
                hit = {"rule": r["name"], "basis": r["basis"]}
                return r, [hit]
        except Exception:
            continue
    return DEFAULT_DISPOSITION, [{"rule": DEFAULT_DISPOSITION["name"], "basis": DEFAULT_DISPOSITION["basis"]}]
