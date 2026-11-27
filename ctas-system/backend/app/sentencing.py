"""量纪说理引擎（离线·可审·确定性）。

把审理报告「定性量纪意见」从一句空话，升级为结构化说理稿：

    定性分析 → 法条依据（真实条号+条文） → 量纪档次区间 → 从轻/从重说理 → 处理意见

设计原则（与项目既有证据链/程序合规引擎一致）：
- 凡系统能依据在案事实与法规条文自动推导的，给确定说理；
- 凡须人工裁量的（最终处分档次），一律标注「须经审理组集体审议」，绝不臆断结论；
- 最终量纪须经集体审议并按程序报批，系统不替代人工认定。
"""

import re

# --------------------------------------------------------------------------- #
# 纪律类别识别：problem_type / 事实关键词 → 六项纪律之一 / 职务违法·职务犯罪
# --------------------------------------------------------------------------- #
_DISCIPLINE_KEYWORDS = {
    "廉洁纪律": ["廉洁", "礼金", "礼品", "财物", "消费卡", "宴请", "旅游", "健身", "娱乐",
                "营利", "经商", "办企业", "股份", "证券", "兼职", "有偿", "公款", "接待",
                "公务车", "公务交通", "特定关系人", "收受", "退赔", "上交", "报销", "吃请"],
    "工作纪律": ["不作为", "乱作为", "失职", "渎职", "推诿", "不敢斗争", "不愿担当", "敷衍塞责",
                "弄虚作假", "瞒报", "漏报", "失察", "滥权", "违规决策"],
    "群众纪律": ["群众", "吃拿卡要", "优亲厚友", "克扣", "摊派", "漠视", "刁难", "损害群众"],
    "政治纪律": ["政治", "妄议", "对抗组织", "信仰", "团团伙伙", "阳奉阴违", "政治问题", "公开表态"],
    "组织纪律": ["组织", "隐瞒", "不如实报告", "个人有关事项", "跑官", "拉票", "违规用人",
                "不请示", "不报告", "档案造假"],
    "生活纪律": ["生活", "奢靡", "享乐", "家风", "不正当关系", "赌博", "低级趣味"],
}
# problem_type 直接标注的类别优先
_PROBLEM_TYPE_CAT = {
    "违反廉洁纪律": "廉洁纪律", "违反工作纪律": "工作纪律", "违反群众纪律": "群众纪律",
    "违反政治纪律": "政治纪律", "违反组织纪律": "组织纪律", "违反生活纪律": "生活纪律",
    "职务违法": "职务违法", "职务犯罪": "职务犯罪",
}


def _classify_category(problem_type: str, facts: str, case_problem_type: str = "") -> str:
    pt = (problem_type or "").strip() or (case_problem_type or "").strip()
    if pt in _PROBLEM_TYPE_CAT:
        return _PROBLEM_TYPE_CAT[pt]
    text = (pt + " " + (facts or ""))
    best, best_n = "其他违纪", 0
    for cat, kws in _DISCIPLINE_KEYWORDS.items():
        n = sum(1 for k in kws if k in text)
        if n > best_n:
            best, best_n = cat, n
    if best_n == 0:
        # 职务相关兜底识别（含贪污型：套取/虚报/截留/骗取/侵占/挪用）
        if any(k in text for k in ["贪污", "受贿", "贿赂", "职务", "犯罪",
                                   "套取", "虚报", "截留", "骗取", "侵占", "挪用"]):
            return "职务违法"
    return best


# --------------------------------------------------------------------------- #
# 权威条款知识库（条号 + 条文均来自真实法规语料：纪律处分条例 jjjc.txt / 政务处分法）
#   tier_light / tier_mid / tier_heavy：对应「情节较轻 / 情节较重 / 情节严重」的处分幅度
#   always_mitigation：总则类从轻/从重条款，按情节说理时引用
# --------------------------------------------------------------------------- #
_ARTICLES = [
    {
        "no": "《中国共产党纪律处分条例》第九十七条", "cat": "廉洁纪律",
        "kw": ["收受", "礼金", "礼品", "财物", "消费卡", "有价证券", "股权", "金融产品"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"],
        "tier_heavy": ["开除党籍"],
        "text": "收受可能影响公正执行公务的礼品、礼金、消费卡（券）和有价证券、股权、其他金融产品等财物，情节较轻的，给予警告或者严重警告处分；情节较重的，给予撤销党内职务或者留党察看处分；情节严重的，给予开除党籍处分。收受其他明显超出正常礼尚往来的财物的，依照前款规定处理。",
    },
    {
        "no": "《中国共产党纪律处分条例》第九十八条", "cat": "廉洁纪律",
        "kw": ["赠送", "送礼", "变相送礼", "讲课费", "课题费", "咨询费"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"],
        "tier_heavy": [],
        "text": "向从事公务的人员及其配偶、子女及其配偶等亲属和其他特定关系人赠送明显超出正常礼尚往来的礼品、礼金、消费卡（券）和有价证券、股权、其他金融产品等财物，情节较重的，给予警告或者严重警告处分；情节严重的，给予撤销党内职务或者留党察看处分。以讲课费、课题费、咨询费等名义变相送礼的，依照前款规定处理。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百零一条", "cat": "廉洁纪律",
        "kw": ["宴请", "旅游", "健身", "娱乐", "活动安排"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"],
        "tier_heavy": [],
        "text": "接受、提供可能影响公正执行公务的宴请或者旅游、健身、娱乐等活动安排，情节较重的，给予警告或者严重警告处分；情节严重的，给予撤销党内职务或者留党察看处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百零二条", "cat": "廉洁纪律",
        "kw": ["消费卡", "会所", "会员卡", "高尔夫"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"],
        "tier_heavy": [],
        "text": "违反有关规定取得、持有、实际使用运动健身卡、会所和俱乐部会员卡、高尔夫球卡等各种消费卡（券），或者违反有关规定出入私人会所，情节较重的，给予警告或者严重警告处分；情节严重的，给予撤销党内职务或者留党察看处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百零三条", "cat": "廉洁纪律",
        "kw": ["营利", "经商", "办企业", "股份", "证券", "买卖股票", "兼职", "有偿中介", "投资入股"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"],
        "tier_heavy": ["开除党籍"],
        "text": "违反有关规定从事营利活动，有下列行为之一，情节较轻的，给予警告或者严重警告处分；情节较重的，给予撤销党内职务或者留党察看处分；情节严重的，给予开除党籍处分：（一）经商办企业；（二）拥有非上市公司（企业）的股份或者证券；（三）买卖股票或者进行其他证券投资；（四）从事有偿中介活动；（五）在国（境）外注册公司或者投资入股；（六）其他违反有关规定从事营利活动的行为。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百零四条", "cat": "廉洁纪律",
        "kw": ["亲属", "特定关系人", "配偶", "子女", "谋取利益", "吸收存款", "推销金融产品", "经营名贵特产"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"],
        "tier_heavy": ["开除党籍"],
        "text": "利用职权或者职务上的影响，为配偶、子女及其配偶等亲属和其他特定关系人在审批监管、资源开发、金融信贷、大宗采购、土地使用权出让、房地产开发、工程招投标以及公共财政收支等方面谋取利益，情节较轻的，给予警告或者严重警告处分；情节较重的，给予撤销党内职务或者留党察看处分；情节严重的，给予开除党籍处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百一十三条", "cat": "廉洁纪律",
        "kw": ["公款", "用公款", "购买赠送", "发放礼品"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"],
        "tier_heavy": ["开除党籍"],
        "text": "违反有关规定组织、参加用公款支付的宴请、娱乐、健身活动，或者用公款购买赠送或者发放礼品、消费卡（券）等，对直接责任者和领导责任者，情节较轻的，给予警告或者严重警告处分；情节较重的，给予撤销党内职务或者留党察看处分；情节严重的，给予开除党籍处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百一十六条", "cat": "群众纪律",
        "kw": ["接待", "超标准", "超范围", "大吃大喝"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务"],
        "tier_heavy": [],
        "text": "违反接待管理规定，超标准、超范围接待或者借机大吃大喝，对直接责任者和领导责任者，情节较重的，给予警告或者严重警告处分；情节严重的，给予撤销党内职务处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百一十七条", "cat": "廉洁纪律",
        "kw": ["公务车", "公务交通", "交通工具", "装饰", "使用公务"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"],
        "tier_heavy": [],
        "text": "违反有关规定配备、购买、更换、装饰、使用公务交通工具或者有其他违反公务交通工具管理规定的行为，对直接责任者和领导责任者，情节较重的，给予警告或者严重警告处分；情节严重的，给予撤销党内职务或者留党察看处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第九十九条", "cat": "廉洁纪律",
        "kw": ["借用", "钱款", "住房", "车辆", "民间借贷", "大额回报", "借用公物"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"],
        "tier_heavy": ["开除党籍"],
        "text": "借用管理和服务对象的钱款、住房、车辆等，可能影响公正执行公务，情节较重的，给予警告或者严重警告处分；情节严重的，给予撤销党内职务、留党察看或者开除党籍处分。通过民间借贷等金融活动获取大额回报，可能影响公正执行公务的，依照前款规定处理。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百条", "cat": "廉洁纪律",
        "kw": ["婚丧喜庆", "操办", "敛财", "大操大办"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务"], "tier_heavy": [],
        "text": "利用职权或者职务上的影响操办婚丧喜庆事宜，造成不良影响的，给予警告或者严重警告处分；情节严重的，给予撤销党内职务处分；借机敛财或者有其他侵犯国家、集体和人民利益行为的，从重或者加重处分，直至开除党籍。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百零五条", "cat": "廉洁纪律",
        "kw": ["离职", "退（离）休", "退休后", "兼职", "营利活动", "从事营利"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务"], "tier_heavy": ["留党察看"],
        "text": "离职或者退（离）休后违反有关规定接受原任职务管辖的地区和业务范围内或者与原工作业务直接相关的企业和中介机构等单位的聘用，或者个人从事与原任职务管辖业务或者与原工作业务直接相关的营利活动，情节较轻的，给予警告或者严重警告处分；情节较重的，给予撤销党内职务处分；情节严重的，给予留党察看处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百一十二条", "cat": "廉洁纪律",
        "kw": ["公物", "占用公物", "借用公物", "营利活动"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"], "tier_heavy": ["开除党籍"],
        "text": "利用职权或者职务上的影响，违反有关规定占用公物归个人使用，时间超过六个月，情节较重的，给予警告或者严重警告处分；情节严重的，给予撤销党内职务处分。占用公物进行营利活动的，给予警告或者严重警告处分；情节较重的，给予撤销党内职务或者留党察看处分；情节严重的，给予开除党籍处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百一十四条", "cat": "廉洁纪律",
        "kw": ["自定薪酬", "滥发", "违规发放", "乱发津补贴", "乱发福利", "巧立名目发放"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"], "tier_heavy": ["开除党籍"],
        "text": "违反有关规定自定薪酬或者滥发津贴、补贴、奖金、福利等，对直接责任者和领导责任者，情节较轻的，给予警告或者严重警告处分；情节较重的，给予撤销党内职务或者留党察看处分；情节严重的，给予开除党籍处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第七十四条", "cat": "工作纪律",
        "kw": ["主体责任", "从严治党", "监督责任", "不履行责任", "管党治党"],
        "tier_light": ["警告", "严重警告"], "tier_mid": ["撤销党内职务", "留党察看"], "tier_heavy": [],
        "text": "不履行全面从严治党主体责任、监督责任或者履行全面从严治党主体责任、监督责任不力，给党组织造成严重损害或者严重不良影响的，对直接责任者和领导责任者，给予警告或者严重警告处分；情节严重的，给予撤销党内职务或者留党察看处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第一百三十一条", "cat": "工作纪律",
        "kw": ["不敢斗争", "不愿担当", "临阵退缩", "重大矛盾冲突", "危机困难"],
        "tier_light": ["警告", "严重警告"], "tier_mid": [],
        "tier_heavy": ["撤销党内职务", "留党察看", "开除党籍"],
        "text": "工作中不敢斗争、不愿担当，面对重大矛盾冲突、危机困难临阵退缩，造成不良影响或者严重后果的，给予警告或者严重警告处分；情节严重的，给予撤销党内职务、留党察看或者开除党籍处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第二十九条", "cat": "职务违法·职务犯罪",
        "kw": ["贪污", "贿赂", "受贿", "套取", "虚报", "截留", "骗取", "侵占", "挪用",
              "滥用职权", "玩忽职守", "权力寻租", "利益输送", "徇私舞弊",
              "浪费国家资财", "涉嫌犯罪", "职务犯罪", "职务违法"],
        "tier_fixed": ["撤销党内职务", "留党察看", "开除党籍"],
        "text": "党组织在纪律审查中发现党员有贪污贿赂、滥用职权、玩忽职守、权力寻租、利益输送、徇私舞弊、浪费国家资财等违反法律涉嫌犯罪行为的，应当给予撤销党内职务、留党察看或者开除党籍处分。",
    },
    {
        "no": "《中国共产党纪律处分条例》第三十条", "cat": "职务违法·职务犯罪",
        "kw": ["刑法", "不构成犯罪", "违反治安管理", "破坏社会主义市场经济秩序"],
        "tier_light": ["警告"], "tier_mid": ["严重警告", "撤销党内职务", "留党察看"],
        "tier_heavy": ["开除党籍"],
        "text": "党组织在纪律审查中发现党员有刑法规定的行为，虽不构成犯罪但须追究党纪责任的，或者有其他破坏社会主义市场经济秩序、违反治安管理等违法行为，损害党、国家和人民利益的，应当视具体情节给予警告直至开除党籍处分。",
    },
    # —— 政务处分（公职人员须党纪政务相匹配） ——
    {
        "no": "《中华人民共和国公职人员政务处分法》第三十三条", "cat": "职务违法·职务犯罪", "gov": True,
        "kw": ["贪污", "贿赂", "套取", "虚报", "截留", "骗取", "侵占", "挪用", "谋取私利", "特定关系人谋取私利", "纵容默许"],
        "tier_light": ["警告", "记过", "记大过"], "tier_mid": ["降级", "撤职"],
        "tier_heavy": ["开除"],
        "text": "贪污贿赂、利用职权或职务影响为本人或他人谋取私利、纵容默许特定关系人谋取私利的，警告、记过、记大过；情节较重降级、撤职；情节严重开除。拒不纠正特定关系人违规任职兼职或从事经营且不服从职务调整的，撤职。",
    },
    {
        "no": "《中华人民共和国公职人员政务处分法》第三十四条", "cat": "廉洁纪律", "gov": True,
        "kw": ["收受", "礼品礼金", "宴请", "旅游", "健身", "娱乐", "有价证券"],
        "tier_light": ["警告", "记过", "记大过"], "tier_mid": ["降级", "撤职"],
        "tier_heavy": ["开除"],
        "text": "收受可能影响公正行使公权力的礼品礼金有价证券等财物的，警告、记过、记大过；情节较重降级、撤职；情节严重开除。向公职人员及其特定关系人赠送可能影响公正的礼品礼金等、或接受提供可能影响公正的宴请旅游健身娱乐等活动安排情节较重的，警告、记过、记大过；情节严重降级、撤职。",
    },
    # —— 总则：处分种类 ——
    {
        "no": "《中国共产党纪律处分条例》第八条", "cat": "总则·处分种类",
        "kw": [], "always": False, "general": True,
        "text": "对党员的纪律处分种类：（一）警告；（二）严重警告；（三）撤销党内职务；（四）留党察看；（五）开除党籍。",
    },
    # —— 总则：从轻或者减轻 ——
    {
        "no": "《中国共产党纪律处分条例》第十七条", "cat": "总则·从轻减轻",
        "kw": [], "always": False, "mitigation": "light",
        "text": "有下列情形之一的，可以从轻或者减轻处分：（一）主动交代本人应当受到党纪处分的问题；（二）在组织谈话函询、初步核实、立案审查过程中，能够配合核实审查工作，如实说明本人违纪违法事实；（三）检举同案人或者其他人应当受到党纪处分或者法律追究的问题，经查证属实，或者有其他立功表现；（四）主动挽回损失、消除不良影响或者有效阻止危害结果发生；（五）主动上交或者退赔违纪所得；（六）党内法规规定的其他从轻或者减轻处分情形。",
    },
    {
        "no": "《中华人民共和国公职人员政务处分法》第十一条", "cat": "总则·从轻减轻",
        "kw": [], "always": False, "mitigation": "light",
        "text": "有下列情形之一的，可以从轻或者减轻给予政务处分：主动交代；配合调查如实说明；检举他人经查证属实；主动避免挽回损失或消除不良影响；共同中起次要辅助作用；主动上交退赔违法所得；其他从轻减轻情节。",
    },
    # —— 总则：从重或者加重 ——
    {
        "no": "《中国共产党纪律处分条例》第二十条", "cat": "总则·从重加重",
        "kw": [], "always": False, "mitigation": "heavy",
        "text": "有下列情形之一的，应当从重或者加重处分：（一）强迫、唆使他人违纪；（二）拒不上交或者退赔违纪所得；（三）违纪受处分后又因故意违纪应当受到党纪处分；（四）违纪受处分后，又被发现其受处分前没有交代的其他应当受到党纪处分的问题；（五）党内法规规定的其他从重或者加重处分情形。",
    },
    {
        "no": "《中华人民共和国公职人员政务处分法》第十三条", "cat": "总则·从重加重",
        "kw": [], "always": False, "mitigation": "heavy",
        "text": "有下列情形之一的，应当从重给予政务处分：政务处分期内再次故意违法应受处分；阻止他人检举提供证据；串供或伪造隐匿毁灭证据；包庇同案人员；胁迫唆使他人违法；拒不上交退赔违法所得；其他从重情节。",
    },
]

# 处分档次排序（用于计算区间上下限）
_DISCIPLINE_ORDER = ["警告", "严重警告", "撤销党内职务", "留党察看", "开除党籍"]
# 政务处分种类（公职人员须党纪政务处分相匹配）
_GOV_ORDER = ["警告", "记过", "记大过", "降级", "撤职", "开除"]
# 条号 → 条款（稳健查找，避免下标脆弱）
_ART_BY_NO = {a["no"]: a for a in _ARTICLES}

# --------------------------------------------------------------------------- #
# 从轻 / 从重「标准情节目录」：供审理组人工认定勾选（系统自动识别之外的法定情节）
#   point 与自动识别项保持同一措辞的，合并时按措辞去重；key 供前端勾选回传。
# --------------------------------------------------------------------------- #
_LIGHT_CATALOG = [
    {"key": "L1", "point": "主动交代本人应当受到党纪处分的问题",
     "ref": "《中国共产党纪律处分条例》第十七条（一）"},
    {"key": "L2", "point": "被审查调查人在审查调查期间能够配合核实审查、如实说明问题",
     "ref": "《中国共产党纪律处分条例》第十七条（二）"},
    {"key": "L3", "point": "检举同案人或者其他人应当受到党纪处分或者法律追究的问题，经查证属实，或者有其他立功表现",
     "ref": "《中国共产党纪律处分条例》第十七条（三）"},
    {"key": "L4", "point": "主动挽回损失、消除不良影响或者有效阻止危害结果发生",
     "ref": "《中国共产党纪律处分条例》第十七条（四）"},
    {"key": "L5", "point": "主动上交或者退赔违纪所得",
     "ref": "《中国共产党纪律处分条例》第十七条（五）/《政务处分法》第十一条（六）"},
    {"key": "L6", "point": "在共同违纪违法行为中起次要或者辅助作用",
     "ref": "《中华人民共和国公职人员政务处分法》第十一条"},
]
_HEAVY_CATALOG = [
    {"key": "H1", "point": "强迫、唆使他人违纪",
     "ref": "《中国共产党纪律处分条例》第二十条（一）"},
    {"key": "H2", "point": "拒不上交或者退赔违纪所得",
     "ref": "《中国共产党纪律处分条例》第二十条（二）"},
    {"key": "H3", "point": "违纪受处分后又因故意违纪应当受到党纪处分",
     "ref": "《中国共产党纪律处分条例》第二十条（三）"},
    {"key": "H4", "point": "违纪受处分后，又被发现其受处分前没有交代的其他应当受到党纪处分的问题",
     "ref": "《中国共产党纪律处分条例》第二十条（四）"},
    {"key": "H5", "point": "串供或者伪造、隐匿、毁灭证据",
     "ref": "《中华人民共和国公职人员政务处分法》第十三条"},
]


def _match_substantive(category: str, problem_type: str, facts: str) -> list:
    """匹配实质性分则条款（按关键词命中事实/问题性质）。"""
    text = (problem_type or "") + " " + (facts or "")
    hits = []
    for a in _ARTICLES:
        if a.get("general") or a.get("mitigation"):
            continue
        if a["cat"] == "职务违法·职务犯罪" and category in ("职务违法", "职务犯罪"):
            # 职务类：问题性质命中即纳入（贪污贿赂受贿等）
            if any(k in text for k in a["kw"]):
                hits.append(a)
            continue
        # 普通分则：关键词命中即纳入
        if any(k in text for k in a["kw"]):
            hits.append(a)
    # 去重保序
    seen, uniq = set(), []
    for a in hits:
        if a["no"] not in seen:
            seen.add(a["no"]); uniq.append(a)
    return uniq


def _parse_amount(involved_amount: str) -> float | None:
    """从'涉案金额'字段抽取数额（万元）。"""
    s = (involved_amount or "").replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*万", s)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*元", s)
    if m:
        return float(m.group(1)) / 10000.0
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if m and ("万" in s or "元" in s):
        return float(m.group(1))
    return None


def _severity_band(facts: str, amount: float | None) -> str:
    """综合'情节'关键词与涉案数额，估计违纪情节档次（参考区间，非最终认定）。"""
    t = (facts or "")
    if any(k in t for k in ["特别严重", "情节特别严重", "数额特别巨大", "重大损失", "恶劣"]):
        return "heavy"
    if any(k in t for k in ["情节严重", "数额较大", "数额巨大", "较重", "造成损失"]):
        return "mid"
    if any(k in t for k in ["情节较轻", "数额较小", "轻微", "苗头性"]):
        return "light"
    # 无关键词时按数额参考（执纪常见区间，仅供参考）
    if amount is not None:
        if amount >= 20:
            return "heavy"
        if amount >= 3:
            return "mid"
        return "light"
    return "mid"


def _tier_for_article(a: dict, band: str) -> list:
    if a.get("tier_fixed"):
        return a["tier_fixed"]
    key = {"light": "tier_light", "mid": "tier_mid", "heavy": "tier_heavy"}.get(band, "tier_mid")
    return a.get(key) or []


def _mitigation_factors(trial, facts: str) -> tuple:
    """依据在案字段推导从轻/从重情节。返回 (light:list, heavy:list)。"""
    light, heavy = [], []
    att = (trial.interview_attitude or "").strip()
    disp = (trial.property_disposal or "")
    ptxt = (trial.problem_type or "") + " " + facts

    # 从轻：认错悔错 / 配合 / 无异议
    if att in ("认错悔错", "无异议", "配合调查"):
        light.append({
            "point": "被审查调查人在审查调查期间能够配合核实审查、如实说明问题",
            "ref": "《中国共产党纪律处分条例》第十七条（二）",
        })
        light.append({
            "point": "能够配合调查、如实说明本人违纪违法事实",
            "ref": "《中华人民共和国公职人员政务处分法》第十一条（二）",
        })
    if att == "提出申辩":
        light.append({
            "point": "被审查调查人提出申辩，依法行使申辩权利；其申辩意见须经审理组复核，不当然构成从重",
            "ref": "程序性情节（须人工复核申辩内容是否成立）",
        })
    # 从轻：主动上交 / 退赔违纪所得
    if "退赔" in disp or "上交" in disp or "收缴" in disp or any(k in ptxt for k in ["主动上交", "主动退赔", "退赃"]):
        light.append({
            "point": "主动上交或者退赔违纪所得",
            "ref": "《中国共产党纪律处分条例》第十七条（五）/《政务处分法》第十一条（六）",
        })
    # 从重：拒不上交 / 不退赔 / 对抗
    if any(k in ptxt for k in ["拒不上交", "不退赔", "对抗组织", "串供", "伪造", "毁灭证据", "不知悔改"]):
        heavy.append({
            "point": "存在拒不上交或者退赔违纪所得、对抗审查等情节",
            "ref": "《中国共产党纪律处分条例》第二十条（二）/《政务处分法》第十三条（六）",
        })
    if att == "提出申辩" and any(k in ptxt for k in ["拒不", "无理缠访", "反复申诉无新事实"]):
        pass  # 不直接从重
    return light, heavy


# —— 涉案财物处置（结构化） ——
# 承办人已填处置意见中的动作识别词（只识别，不臆造；长词在前，命中后其短词不重复列出）
_PROPERTY_ACTION_KW = {
    "随案移送": "随案移送",
    "移送": "移送",
    "收缴": "收缴",
    "责令退赔": "责令退赔",
    "退赔": "退赔",
    "追缴": "追缴",
    "没收": "没收",
    "退还": "退还",
    "登记上交": "登记上交",
    "冻结": "冻结",
    "查封": "查封",
    "扣押": "扣押",
    "上缴国库": "上缴国库",
}
# 长词 → 其包含的短词（短词不再单独列出）
_PROPERTY_ACTION_SHADOW = {"随案移送": "移送", "责令退赔": "退赔"}

# 涉案财物段法条依据（条文取自语料，不臆造）
_PROPERTY_ARTICLES = [
    {"no": "《中国共产党纪律处分条例》第四十三条",
     "text": "对于违纪行为所获得的经济利益，应当收缴或者责令退赔。对于主动上交的违纪所得和经济损失赔偿，应当予以接收，并按照规定收缴或者返还有关单位、个人。对于违纪行为所获得的职务、职级、职称、学历、学位、奖励、资格等其他利益，应当由承办案件的纪检机关或者由其上级纪检机关建议有关组织、部门、单位按照规定予以纠正。"},
    {"no": "《中华人民共和国公职人员政务处分法》第二十五条",
     "text": "违法取得的财物和用于违法行为的本人财物，除依法应由其他机关没收追缴或责令退赔的，由监察机关没收追缴或责令退赔；应退还原所有人的依法退还；属国家财产或不退还应退的，上缴国库。因违法获得的职务职级衔级级别岗位职员等级职称待遇资格学历学位荣誉奖励等其他利益，建议有关机关单位组织纠正。"},
    {"no": "《中华人民共和国公职人员政务处分法》第二十七条",
     "text": "已退休公职人员退休前或后有违法的，不再给予政务处分但可立案调查；依法应降级撤职开除的相应调整待遇，违法取得的财物和用于违法的本人财物依照本法第二十五条处理。离职死亡履职期间有违法的依前款规定。"},
]


def _parse_property_actions(text: str) -> list:
    """从承办人已填处置意见文本中识别处置动作（长词命中时其短词不重复列出）。"""
    covered = set()
    for kw in _PROPERTY_ACTION_KW:
        if kw in text:
            short = _PROPERTY_ACTION_SHADOW.get(kw)
            if short:
                covered.add(short)
    actions = []
    for kw, label in _PROPERTY_ACTION_KW.items():
        if kw in covered or kw not in text:
            continue
        if label not in actions:
            actions.append(label)
    return actions


def _property_default_plan(category: str, band: str) -> list:
    """承办人尚未提出处置意见时，按问题性质给出建议方向（须审理组确认）。"""
    plan = []
    if category in ("职务违法", "职务犯罪"):
        plan.append("违法取得的财物由监察机关没收、追缴或者责令退赔（政务处分法第二十五条）")
        if category == "职务犯罪" or band == "heavy":
            plan.append("涉嫌犯罪问题及涉案财物随案移送司法机关，由司法机关依法处理")
    else:
        plan.append("违纪行为所获得的经济利益予以收缴或者责令退赔（条例第四十三条）")
    plan.append("主动上交的违纪所得和经济损失赔偿按规定接收，收缴或者退还有关单位、个人")
    plan.append("因违纪违法获得的职务、职级、职称、学历、奖励、资格等其他利益，建议有关组织、部门按规定纠正")
    return plan


def build_property_section(trial, case, category: str, band: str, amount, facts: str) -> dict:
    """结构化涉案财物处置段。返回 property_plan dict（含建议、依据、移送提示）。"""
    raw = (trial.property_disposal or "").strip()
    # 金额兜底：关联案时 involved_amount 挂在 Case 上
    amt = amount
    if amt is None and case is not None:
        amt = _parse_amount(getattr(case, "involved_amount", "") or "")
    if amt is None and raw:
        # 兜底：承办人处置意见文本中的涉案数额（如「违纪违法所得18.6万元予以收缴」）
        amt = _parse_amount(raw)
    if amt is None and facts:
        # 兜底：在案事实文本中的涉案数额（仅识别含「万/元」的数额，年份等不误命中）
        amt = _parse_amount(facts)
    actions = _parse_property_actions(raw)
    # 事实文本出现刑事移送迹象而承办人意见未含移送 → 提示核对
    transfer_flag = (
        any(k in (facts or "") for k in ["涉嫌犯罪", "移送司法", "移送检察", "提起公诉"])
        and "移送" not in "".join(actions)
    )
    return {
        "amount_wan": amt,
        "raw": raw,
        "actions": actions,
        "suggestions": [] if actions else _property_default_plan(category, band),
        "needs_confirm": not actions,   # 无承办人意见 → 成稿标注须人工确认
        "transfer_flag": transfer_flag,
        "articles": [a["no"] for a in _PROPERTY_ARTICLES],
    }


_UNKNOWN_STATUS = {"", "—", "-", "－", "无", "未知", "未填", "（未填）", "不详", "待核"}


def _party_member(political_status: str | None) -> bool | None:
    """政治面貌 → True 中共党员（含预备党员）/ False 明确非党员 / None 未载明。

    党纪处分以中共党员身份为前提：未载明时不能默认按党员量纪，须先补身份材料。
    """
    s = (political_status or "").strip()
    if s in _UNKNOWN_STATUS:
        return None
    return "党员" in s and "非" not in s and "共青团员" not in s


def build_sentencing_reasoning(trial, case=None, db=None, manual_light=None, manual_heavy=None) -> dict:
    """生成结构化量纪说理稿（离线·确定性）。

    返回 dict：{category, substantive, severity_band, amount_wan, tier_range, gov_tier_range,
                light, heavy, light_catalog, heavy_catalog, property_text, text}
    manual_light / manual_heavy：审理组人工认定的从轻/从重情节（[{point, ref}]），
    与系统自动识别项按措辞去重后合并，成稿中标注「经审理组人工认定」。
    text 为可直接写入审理报告「定性量纪意见」章节的成稿。
    """
    subj = trial.subject or (case.subject if case else "（未填）")
    unit = trial.unit or (case.unit if case else "（未填）")
    facts = (case.content if case else "") or (trial.brief or "")
    category = _classify_category(trial.problem_type, facts, case.problem_type if case else "")
    amount = _parse_amount(trial.involved_amount) if trial.involved_amount else None
    band = _severity_band(facts, amount)

    substantive = _match_substantive(category, trial.problem_type, facts)
    if not substantive:
        # 兜底：按类别给一条代表性条款（稳健按条号查找，避免下标脆弱）
        fallback_no = {
            "廉洁纪律": "《中国共产党纪律处分条例》第九十七条",
            "职务违法": "《中国共产党纪律处分条例》第二十九条",
            "职务犯罪": "《中国共产党纪律处分条例》第二十九条",
            "工作纪律": "《中国共产党纪律处分条例》第一百三十一条",
            "群众纪律": "《中国共产党纪律处分条例》第一百一十六条",
        }.get(category)
        if fallback_no and fallback_no in _ART_BY_NO:
            substantive = [_ART_BY_NO[fallback_no]]

    # 量纪档次区间
    tiers = []
    for a in substantive:
        tiers += _tier_for_article(a, band)
    # 刑事涉嫌（条例29）固定最高三档
    if any(a["cat"] == "职务违法·职务犯罪" for a in substantive) and band == "heavy":
        tiers += ["开除党籍"]
    tiers = sorted({t for t in tiers if t in _DISCIPLINE_ORDER}, key=_DISCIPLINE_ORDER.index)
    tier_range = "、".join(tiers) if tiers else "（须结合具体情节由审理组集体审议确定）"

    # 政务处分区间（公职人员须党纪政务处分相匹配）
    gov_tiers = []
    for a in substantive:
        if a.get("gov"):
            key = {"light": "tier_light", "mid": "tier_mid", "heavy": "tier_heavy"}.get(band, "tier_mid")
            gov_tiers += (a.get(key) or [])
    gov_tiers = sorted({t for t in gov_tiers if t in _GOV_ORDER}, key=_GOV_ORDER.index)
    gov_tier_range = "、".join(gov_tiers) if gov_tiers else "（须结合具体情节由审理组集体审议确定）"

    light, heavy = _mitigation_factors(trial, facts)
    # 合并审理组人工认定情节（按措辞去重，避免与自动识别重复）
    for m in (manual_light or []):
        if not any(f["point"] == m["point"] for f in light):
            light.append({"point": m["point"], "ref": m["ref"], "manual": True})
    for m in (manual_heavy or []):
        if not any(f["point"] == m["point"] for f in heavy):
            heavy.append({"point": m["point"], "ref": m["ref"], "manual": True})
    property_plan = build_property_section(trial, case, category, band, amount, facts)
    property_text = property_plan["raw"] or "（待审理组提出处置意见）"

    # —— 成稿 ——
    lines = []
    lines.append("四、定性量纪意见")
    # （一）定性分析
    lines.append("（一）定性分析")
    if case:
        rel = "本案系移送审理案件，"
    else:
        rel = "本案系审理辅助系统独立建档审理事项，"
    # 事实摘要：超长截断时以公文化收尾（不写「…」显得未完结），全文以三章为准
    if facts:
        fact_brief = facts[:160]
        if len(facts) > 160:
            fact_brief = fact_brief.rstrip("，。；、") + "（详见本报告「三、主要违纪违法事实」）"
    else:
        fact_brief = "（请补充阅卷事实）"
    # 问题性质：优先用承办人/案件原文（如「违反廉洁纪律」），缺失才回退到分类标签
    pt_origin = trial.problem_type or (case.problem_type if case else "") or category
    lines.append(
        f"{rel}被审查调查人{subj}（{unit}），问题性质认定为「{pt_origin}」。"
        f"经阅卷与证据核验，其主要违纪事实为：{fact_brief}"
    )
    political_status = (getattr(trial, "political_status", "") or "").strip()
    party = _party_member(political_status)
    if party is None:
        lines.append(
            "【身份待核】在案材料未载明被审查调查人政治面貌。党纪处分以中共党员身份为前提，"
            "须先调取入党材料、干部任免审批表等身份证据核实；如非中共党员，不适用党纪处分，仅依法给予政务处分。"
        )
    elif party is False:
        lines.append(
            f"被审查调查人政治面貌为「{political_status}」，不适用党纪处分，依法给予政务处分；下列党纪条款仅作对照参考。"
        )
    if category in ("职务违法", "职务犯罪"):
        lines.append(
            "其行为不仅违反党的廉洁纪律相关要求，同时构成职务违法"
            + ("，涉嫌职务犯罪，须依规依纪依法给予党纪政务处分，并移送有关机关依法处理。" if category == "职务犯罪" else "，须党纪政务处分相匹配。")
        )
    # （二）法条依据
    lines.append("（二）法条依据")
    for i, a in enumerate(substantive, 1):
        lines.append(f"{i}. {a['no']}：{a['text']}")
    # （三）量纪档次
    lines.append("（三）量纪档次（参考区间）")
    amt_txt = f"涉案数额约 {amount:.2f} 万元，" if amount is not None else ""
    band_label = {"light": "情节较轻", "mid": "情节较重", "heavy": "情节严重"}.get(band, "综合情节")
    if party is False:
        lines.append(
            f"被审查调查人非中共党员，不适用党纪处分档次；结合在案事实（{amt_txt}违纪违法情节综合判断为「{band_label}」），"
            "按下述政务处分幅度裁量。"
        )
    else:
        lines.append(
            ("如经核实为中共党员，" if party is None else "")
            + f"依据上述条款规定的处分幅度，结合在案事实（{amt_txt}违纪情节综合判断为「{band_label}」），"
            f"建议量纪区间参考为【{tier_range}】。"
        )
    if gov_tier_range and gov_tier_range != "（须结合具体情节由审理组集体审议确定）":
        # 只引用本案实际匹配到的政务处分条款（如收受礼品礼金只对应第三十四条，不连带第三十三条贪污贿赂）
        gov_law = "《中华人民共和国公职人员政务处分法》"
        gov_nos = "、".join(dict.fromkeys(a["no"].replace(gov_law, "") for a in substantive if a.get("gov")))
        lines.append(
            f"鉴于被审查调查人{subj}系公职人员，须依规给予相应政务处分；依据{gov_law}"
            f"{gov_nos}规定的处分幅度，建议政务处分区间参考为【{gov_tier_range}】"
            + ("，做到党纪政务处分相匹配。" if party is not False else "。")
        )
    lines.append(
        "具体处分档次须经审理组集体审议，并对照《中国共产党纪律处分条例》第八条规定的处分种类"
        "（警告、严重警告、撤销党内职务、留党察看、开除党籍）在幅度内裁量确定，系统不作最终认定。"
    )
    # （四）从轻、从重说理
    lines.append("（四）从轻、从重情节说理")
    if light:
        for f in light:
            tag = "（经审理组人工认定）" if f.get("manual") else ""
            lines.append(f"· 从轻/减轻情节：{f['point']}{tag}（依据{f['ref']}）。")
    else:
        lines.append("· 从轻/减轻情节：经核实在案材料，暂未发现法定从轻、减轻情节（如有主动交代、主动退赔等，须承办人据实补充认定）。")
    if heavy:
        for f in heavy:
            tag = "（经审理组人工认定）" if f.get("manual") else ""
            lines.append(f"· 从重/加重情节：{f['point']}{tag}（依据{f['ref']}）。")
    else:
        lines.append("· 从重/加重情节：经核实在案材料，暂未发现法定从重、加重情节。")
    # （五）涉案财物
    lines.append("（五）涉案财物处置意见")
    if property_plan["amount_wan"] is not None:
        lines.append(f"1. 在案涉案财物数额约 {property_plan['amount_wan']:.2f} 万元（以查封、扣押、冻结清单为准）。")
    else:
        lines.append("1. 在案涉案财物以查封、扣押、冻结清单及随案移送清单为准（数额待核）。")
    if property_plan["actions"]:
        lines.append(f"2. 承办人已提处置意见：{'、'.join(property_plan['actions'])}。原文：「{property_plan['raw']}」。")
    else:
        lines.append("2. 承办人尚未提出具体处置意见。结合问题性质，建议方向如下（须经审理组审核确认后填写，系统不作处置结论）：")
        for s in property_plan["suggestions"]:
            lines.append(f"   · {s}")
    if property_plan["transfer_flag"]:
        lines.append("3. 在案材料显示涉嫌犯罪问题须移送司法机关的，涉案财物应随案移送，由司法机关依法处理（政务处分法第二十五条）。")
    basis_no = "4" if property_plan["transfer_flag"] else "3"
    lines.append(
        f"{basis_no}. 处置依据：《中国共产党纪律处分条例》第四十三条（违纪所得收缴或者责令退赔、其他利益纠正）；"
        "《中华人民共和国公职人员政务处分法》第二十五条（没收、追缴、责令退赔、退还、上缴国库）、"
        "第二十七条（退休、离职、死亡人员涉案财物参照第二十五条处理）。"
    )
    # （六）处理意见
    lines.append("（六）处理意见与建议")
    gov_txt = (f"建议政务处分区间参考【{gov_tier_range}】"
               if (gov_tier_range and gov_tier_range != "（须结合具体情节由审理组集体审议确定）")
               else "建议由监察机关或任免机关（单位）依规给予相应政务处分")
    if party is False:
        lines.append(
            f"综上，{subj}非中共党员，不适用党纪处分；其系公职人员，{gov_txt}。涉案财物依法予以收缴或者责令退赔。"
            "本意见经审理组集体审议并按管理权限报批后执行。"
        )
    else:
        lines.append(
            "综上，"
            + (f"如经核实为中共党员，建议给予{subj}党内相应处分" if party is None else f"建议给予{subj}同志党内相应处分")
            + f"（党纪区间参考【{tier_range}】）；其系公职人员，{gov_txt}，"
            "做到党纪政务处分相匹配。涉案财物依法予以收缴或者责令退赔。"
            "本意见经审理组集体审议并报有处分批准权限的党组织审批后执行。"
        )
    lines.append(
        "【说明】本说理稿由系统依据在案事实与法规条文离线辅助生成，仅供承办人审理说理参考；"
        "凡涉及处分档次的最终认定，须经审理组集体审议并按程序报批，系统不替代人工结论。"
    )

    return {
        "category": category,
        "substantive": [{"no": a["no"], "cat": a["cat"], "text": a["text"]} for a in substantive],
        "severity_band": band,
        "amount_wan": amount,
        "tier_range": tier_range,
        "gov_tier_range": gov_tier_range,
        "party_member": party,
        "light": light,
        "heavy": heavy,
        "light_catalog": _LIGHT_CATALOG,
        "heavy_catalog": _HEAVY_CATALOG,
        "property_text": property_text,
        "property_plan": property_plan,
        "text": "\n".join(lines),
    }
