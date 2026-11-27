"""处分决定书成稿引擎（离线·可审·确定性）。

审理报告定稿、处分决定作出后生成《党纪处分决定书》初稿：

    被处分人基本情况 → 主要违纪事实 → 处分依据 → 处分决定 → 涉案财物处理 → 申诉权告知 → 落款

设计原则（与量纪说理引擎一致）：
- 事实、依据接在案材料与真实法规条文（语料可溯源）；
- 处分档次 / 基本要素 / 批准机关 / 日期等裁量与程序事项一律人工填写，
  缺失时以〔待填〕〔待补充〕标注，系统绝不臆断；
- 申诉权告知依《中国共产党章程》《中国共产党党员权利保障条例》：不服决定可在
  收到决定书之日起向作出决定的党组织或其上级党组织提出申诉（党纪无统一法定期限，
  不沿用政务处分「一个月复审」表述）。
"""

import re


def _brief_identity(trial, case=None) -> list:
    """被处分人基本要素行（能取的取，取不到的标〔待补充〕）。"""
    subj = trial.subject or (case.subject if case else "") or "（未填）"
    unit = trial.unit or (case.unit if case else "") or "〔单位待补充〕"
    rank = (trial.rank or "").strip() or "〔职级待补充〕"
    pol = (trial.political_status or "").strip() or "〔政治面貌待补充〕"
    lines = [f"{subj}，{unit}，{rank}，{pol}。"]
    # 入党时间、参加工作时间等要件系统无采集 → 明确提醒据实补全，不用〔待填〕字样
    # （避免误触 needs_manual 裁量统计）
    lines.append("（入党时间、参加工作时间、简历等要件以干部人事档案为准，须据实补全）")
    return lines


def build_decision_document(trial, case=None, decision_no="", decision_result="",
                            decision_date=None, decision_approver="") -> dict:
    """生成《党纪处分决定书》初稿内容。

    返回 {content, decision_result_filled, needs_manual}：
    - content：可直接入 Document 库的成稿文本；
    - needs_manual：True 表示存在〔待填〕裁量事项，须人工补全后方可定稿。
    """
    from app.sentencing import (build_sentencing_reasoning, _parse_amount,
                                build_property_section, _classify_category,
                                _severity_band)

    subj = trial.subject or (case.subject if case else "") or "（被处分人姓名待补充）"
    facts = (case.content if case else "") or (trial.brief or "")
    category = _classify_category(trial.problem_type, facts,
                                  case.problem_type if case else "")
    amount = _parse_amount(trial.involved_amount) if trial.involved_amount else None
    band = _severity_band(facts, amount)
    reasoning = build_sentencing_reasoning(trial, case, None)
    substantive = reasoning["substantive"]
    property_plan = build_property_section(trial, case, category, band, amount, facts)

    filled = bool((decision_result or "").strip())
    result_txt = (decision_result or "").strip() or "〔处分档次待处分批准机关审定后填写〕"
    approver = (decision_approver or "").strip() or "〔处分批准机关待填〕"
    no_txt = (decision_no or "").strip() or "〔文号待填〕"
    date_txt = (decision_date.strftime("%Y年%m月%d日") if decision_date else "〔日期待填〕")

    lines = []
    # 标题（含处分档次——未定时用中性标题）
    if filled:
        lines.append(f"关于给予{subj}{result_txt}的决定")
    else:
        lines.append(f"关于给予{subj}党纪处分的决定")
    lines.append("")
    lines.append(f"{approver}文件　{no_txt}")
    lines.append("")
    # 一、被处分人基本情况
    lines.append("一、被处分人基本情况")
    lines += _brief_identity(trial, case)
    lines.append("")
    # 二、主要违纪事实
    lines.append("二、主要违纪违法事实")
    lines.append(facts or "（以审理报告「三、主要违纪违法事实」为准）")
    lines.append("")
    # 三、处分依据（党纪决定书只列党纪条款；政务处分法条款是监察机关并行决定的依据，
    #    不在党纪决定书中援引，仅在处分决定段落后作匹配提示）
    lines.append("三、处分依据")
    party_arts = [a for a in substantive if "政务处分法" not in a["no"]]
    if party_arts:
        for i, a in enumerate(party_arts, 1):
            lines.append(f"{i}. {a['no']}：{a['text']}")
    else:
        lines.append("（以审理报告「五、定性量纪意见」所援引条款为准）")
    # 从轻从重（只保留党纪依据的表述；政务处分法项不入党纪决定书）
    light_only = [f for f in (reasoning.get("light") or [])
                  if "纪律处分条例" in (f.get("ref") or "")]
    heavy_only = [f for f in (reasoning.get("heavy") or [])
                  if "纪律处分条例" in (f.get("ref") or "")]
    if light_only or heavy_only:
        for f in light_only:
            tag = "（经审理组人工认定）" if f.get("manual") else ""
            lines.append(f"· 从轻/减轻：{f['point']}{tag}（{f['ref']}）。")
        for f in heavy_only:
            tag = "（经审理组人工认定）" if f.get("manual") else ""
            lines.append(f"· 从重/加重：{f['point']}{tag}（{f['ref']}）。")
    lines.append("")
    # 四、处分决定（核心裁量段）
    lines.append("四、处分决定")
    lines.append(
        f"经{approver}研究决定，依据上述条款，给予{subj}{result_txt}。"
    )
    # 政务处分匹配提示（公职人员党纪政务处分相匹配；政务决定由监察机关/任免机关另行作出）
    if category in ("职务违法", "职务犯罪") or any("政务处分法" in a["no"] for a in substantive):
        lines.append(
            f"{subj}系公职人员，依照党纪政务处分相匹配原则，相应政务处分由监察机关或者任免机关、单位"
            "依据《中华人民共和国公职人员政务处分法》另行作出。"
        )
    if not filled:
        lines.append(
            "【说明】处分档次属批准机关裁量事项，本系统不预设结论；"
            "请以处分批准机关审定意见为准填入后定稿。"
        )
    # 留党察看须明确期限；开除党籍附加 5 年内不得重新入党（均不臆断，缺则待填）
    if "留党察看" in result_txt and "一年" not in result_txt and "二年" not in result_txt:
        lines.append("（留党察看须明确期限〔一年/二年〕，请据实填写后定稿）")
    if "开除" in result_txt:
        lines.append(
            "（被审查调查人自本处分决定生效之日起，五年内不得重新入党，"
            "也不得推荐担任与其原任职务相当或者高于其原任职务的党外职务。)"
        )
    lines.append("")
    # 五、涉案财物处理
    lines.append("五、涉案财物处理")
    if property_plan.get("actions"):
        lines.append(f"承办人处置意见：{'、'.join(property_plan['actions'])}。原文：「{property_plan['raw']}」。")
    elif property_plan.get("suggestions"):
        lines.append("（建议方向，须处分批准机关审定后填写，系统不作处置结论）：")
        for s in property_plan["suggestions"]:
            lines.append(f"· {s}")
    else:
        lines.append("（以随案移送清单及审理报告「六、涉案财物情况」为准）")
    lines.append("")
    # 六、申诉权告知（党纪口径：向本级或上级党组织申诉，无统一法定期限；
    #    不沿用政务处分「一个月复审」表述，避免党纪决定书误植政务口径）
    lines.append("六、申诉权告知")
    lines.append(
        f"如不服本决定，{subj}可在收到本决定书之日起，向作出本决定的党组织（{approver}）"
        "或者其上级党组织提出申诉。申诉期间，不停止本决定的执行。"
    )
    lines.append("")
    # 落款
    lines.append(f"{approver}")
    lines.append(date_txt)

    content = "\n".join(lines)
    # 〔待填〕类裁量事项统计
    needs_manual = bool(re.search(r"〔[^〕]*待[^〕]*〕", content))
    return {"content": content, "decision_result_filled": filled,
            "needs_manual": needs_manual}
