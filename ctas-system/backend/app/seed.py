"""种子数据：全部为虚构模拟数据（演示红线 P5）。
法规语料为公开文本节选（演示用，正式使用请替换为官方全文并核对条文）。
幂等：已有数据时跳过。
"""
from datetime import datetime, timedelta

from app.models import (User, Petition, DocumentTemplate, Case, ConductCase,
                        Inspection, InspectionFinding, SuperviseModel, Archive,
                        BankStatement, Transcript, VerifyRule)
from app.security import hash_password
from app.rag import ingest as rag_ingest
from app.config import settings
from app.supervise import MODELS

USERS = [
    ("admin", "Admin@123", "系统管理员", "admin"),
    ("xinfang", "Xf@123456", "信访室·王主任", "xinfang"),
    ("banan", "Ba@123456", "办案人员·李干事", "banan"),
    ("shenli", "Sl@123456", "审理室·赵科长", "shenli"),
]

PETITIONS = [
    ("XF20260901001", "反映某县住建局张某某同志在2024年担任局长期间，违规收受管理服务对象礼金共计5万元，"
     "并涉嫌利用职务便利为私营企业主李某承揽工程提供帮助，数额较大，顶风违纪，性质恶劣。", "实名举报"),
    ("XF20260901002", "反映某镇东风村村主任王某某侵占集体『三资』资金12万元，克扣村民惠民补贴资金，"
     "侵害群众利益，多名村民联名反映。", "多人联名"),
    ("XF20260901003", "反映某市城投集团董事长刘某某违规经商办企业，其配偶在集团下属公司违规持股分红，"
     "未如实报告个人有关事项。", "网络举报"),
    ("XF20260901004", "对2025年受到的党内警告处分不服，认为事实认定不清、量纪过重，申请复查。", "来信申诉"),
    ("XF20260901005", "反映某区民政局副局长陈某某2025年3月在私人会所违规接受宴请，公车私用，"
     "酒后驾驶被交警查处，媒体已有曝光，存在舆情风险。", "网络举报"),
    ("XF20260901006", "反映某县住建局张某某在工程招投标中与私营企业主李某围标串标，收受好处费，"
     "与此前举报情况一致，再次举报。", "再次举报"),
]

TEMPLATES = [
    {
        "name": "初步核实情况报告",
        "doc_type": "初核报告",
        "template": (
            "关于{{被反映人}}{{问题摘要}}的初步核实情况报告\n\n"
            "一、线索来源及初核依据\n"
            "{{线索来源}}。经批准，{{承办部门}}成立核查组，对反映问题开展初步核实。\n\n"
            "二、被反映人基本情况\n"
            "{{基本情况}}\n\n"
            "三、初步核实情况\n"
            "{{核实情况}}\n\n"
            "四、初核意见\n"
            "{{初核意见}}\n\n"
            "核查组成员：{{核查组成员}}\n{{日期}}"
        ),
        "fields": [
            {"key": "被反映人", "label": "被反映人", "placeholder": "如：张某某"},
            {"key": "问题摘要", "label": "问题摘要", "placeholder": "如：违规收受礼金问题"},
            {"key": "线索来源", "label": "线索来源", "placeholder": "如：信访举报（编号XF...）"},
            {"key": "承办部门", "label": "承办部门", "placeholder": "如：第二监督检查室"},
            {"key": "基本情况", "label": "被反映人基本情况", "placeholder": "姓名、单位、职务等"},
            {"key": "核实情况", "label": "初步核实情况", "placeholder": "核查认定的事实"},
            {"key": "初核意见", "label": "初核意见", "placeholder": "如：建议予以了结/建议立案审查"},
            {"key": "核查组成员", "label": "核查组成员", "placeholder": "如：李某某、王某某"},
            {"key": "日期", "label": "成文日期", "placeholder": "如：2026年10月10日"},
        ],
    },
    {
        "name": "党纪处分决定",
        "doc_type": "处分决定",
        "template": (
            "关于给予{{被处分人}}{{处分种类}}处分的决定\n\n"
            "经审理查明：{{违纪事实}}。\n\n"
            "上述行为违反{{违反条款}}之规定。依据《中国共产党纪律处分条例》{{依据条款}}，"
            "经{{决定机关}}研究决定：\n\n"
            "给予{{被处分人}}{{处分种类}}处分。本决定自作出之日起生效。"
            "如对本处分决定不服，可依照《中国共产党纪律处分条例》相关规定提出申诉。\n\n"
            "{{决定机关}}\n{{日期}}"
        ),
        "fields": [
            {"key": "被处分人", "label": "被处分人", "placeholder": "如：张某某"},
            {"key": "处分种类", "label": "处分种类", "placeholder": "警告/严重警告/撤销党内职务/留党察看/开除党籍"},
            {"key": "违纪事实", "label": "违纪事实", "placeholder": "审理认定的事实"},
            {"key": "违反条款", "label": "违反条款", "placeholder": "如：《条例》第二编分则相关条款"},
            {"key": "依据条款", "label": "依据条款", "placeholder": "如：第X条"},
            {"key": "决定机关", "label": "决定机关", "placeholder": "如：中共某某县纪委"},
            {"key": "日期", "label": "成文日期", "placeholder": "如：2026年10月10日"},
        ],
    },
    {
        "name": "谈话笔录",
        "doc_type": "谈话笔录",
        "template": (
            "谈 话 笔 录\n\n"
            "时间：{{谈话时间}}\n地点：{{谈话地点}}\n"
            "谈话人：{{谈话人}}\n记录人：{{记录人}}\n被谈话人：{{被谈话人}}\n"
            "工作单位及职务：{{单位职务}}\n\n"
            "谈话内容：\n"
            "问：{{问话内容}}\n"
            "答：{{答话内容}}\n\n"
            "（以下空白）/ 共{{页数}}页\n\n"
            "被谈话人签名：{{被谈话人签名}}\n谈话人签名：{{谈话人签名}}\n{{日期}}"
        ),
        "fields": [
            {"key": "谈话时间", "label": "谈话时间", "placeholder": "如：2026年10月10日9时至11时"},
            {"key": "谈话地点", "label": "谈话地点", "placeholder": "如：某某县纪委谈话室"},
            {"key": "谈话人", "label": "谈话人", "placeholder": "如：李某某、王某某"},
            {"key": "记录人", "label": "记录人", "placeholder": "如：赵某某"},
            {"key": "被谈话人", "label": "被谈话人", "placeholder": "如：张某某"},
            {"key": "单位职务", "label": "工作单位及职务", "placeholder": "如：某县住建局局长"},
            {"key": "问话内容", "label": "问话内容", "placeholder": "谈话人提问"},
            {"key": "答话内容", "label": "答话内容", "placeholder": "被谈话人回答（如实记录）"},
            {"key": "页数", "label": "页数", "placeholder": "如：3"},
            {"key": "被谈话人签名", "label": "被谈话人签名", "placeholder": ""},
            {"key": "谈话人签名", "label": "谈话人签名", "placeholder": ""},
            {"key": "日期", "label": "成文日期", "placeholder": "如：2026年10月10日"},
        ],
    },
    {
        "name": "初步核实工作方案",
        "doc_type": "初核方案",
        "template": (
            "关于{{被反映人}}{{问题摘要}}的初步核实工作方案\n\n"
            "一、初步核实对象\n{{被反映人}}，{{单位职务}}。\n\n"
            "二、反映的主要问题\n{{问题摘要}}。\n\n"
            "三、初核组成人员\n组长：{{核查组组长}}；成员：{{核查组成员}}。\n\n"
            "四、核实方法与步骤\n{{核实方法}}。\n\n"
            "五、时间安排\n{{时间安排}}。\n\n"
            "六、工作要求\n{{工作要求}}。\n\n"
            "承办部门：{{承办部门}}\n{{日期}}"
        ),
        "fields": [
            {"key": "被反映人", "label": "被反映人", "placeholder": "如：张某某"},
            {"key": "单位职务", "label": "单位及职务", "placeholder": "如：某县住建局局长"},
            {"key": "问题摘要", "label": "问题摘要", "placeholder": "如：违规收受礼金问题"},
            {"key": "核查组组长", "label": "核查组组长", "placeholder": "如：李某某"},
            {"key": "核查组成员", "label": "核查组成员", "placeholder": "如：王某某、赵某某"},
            {"key": "核实方法", "label": "核实方法与步骤", "placeholder": "调取资料/谈话了解/查询账目等"},
            {"key": "时间安排", "label": "时间安排", "placeholder": "如：2026年10月11日至10月31日"},
            {"key": "工作要求", "label": "工作要求", "placeholder": "保密要求/依规依纪等"},
            {"key": "承办部门", "label": "承办部门", "placeholder": "如：第二监督检查室"},
            {"key": "日期", "label": "成文日期", "placeholder": "如：2026年10月10日"},
        ],
    },
    {
        "name": "立案审查调查决定",
        "doc_type": "立案决定",
        "template": (
            "关于对{{被审查调查人}}{{问题摘要}}立案审查调查的决定\n\n"
            "{{被审查调查人}}，{{单位职务}}。\n\n"
            "经初步核实，{{被审查调查人}}涉嫌{{问题摘要}}，需要追究{{责任类型}}责任。"
            "依据《中国共产党纪律检查机关监督执纪工作规则》{{依据条款}}，"
            "经{{批准机关}}批准，决定对{{被审查调查人}}{{问题摘要}}问题立案审查调查。\n\n"
            "承办部门：{{承办部门}}\n立案日期：{{立案日期}}\n{{日期}}"
        ),
        "fields": [
            {"key": "被审查调查人", "label": "被审查调查人", "placeholder": "如：张某某"},
            {"key": "单位职务", "label": "单位及职务", "placeholder": "如：某县住建局局长"},
            {"key": "问题摘要", "label": "问题摘要", "placeholder": "如：违规收受管理服务对象礼金"},
            {"key": "责任类型", "label": "责任类型", "placeholder": "如：纪律/法律"},
            {"key": "依据条款", "label": "依据条款", "placeholder": "如：第三十七条"},
            {"key": "批准机关", "label": "批准机关", "placeholder": "如：县纪委常委会"},
            {"key": "承办部门", "label": "承办部门", "placeholder": "如：第二监督检查室"},
            {"key": "立案日期", "label": "立案日期", "placeholder": "如：2026年10月15日"},
            {"key": "日期", "label": "成文日期", "placeholder": "如：2026年10月15日"},
        ],
    },
    {
        "name": "审理报告",
        "doc_type": "审理报告",
        "template": (
            "关于{{被审查人}}{{问题摘要}}的审理报告\n\n"
            "一、案件来源及审理经过\n{{案件来源}}。{{审理经过}}。\n\n"
            "二、被审查人基本情况\n{{被审查人}}，{{单位职务}}。\n\n"
            "三、审查认定的事实\n{{认定事实}}。\n\n"
            "四、定性及依据\n{{定性依据}}。\n\n"
            "五、审理意见\n{{审理意见}}。\n\n"
            "六、涉案财物处理意见\n{{涉案财物处理}}。\n\n"
            "审理室：{{审理部门}}\n{{日期}}"
        ),
        "fields": [
            {"key": "被审查人", "label": "被审查人", "placeholder": "如：张某某"},
            {"key": "单位职务", "label": "单位及职务", "placeholder": "如：某县住建局局长"},
            {"key": "问题摘要", "label": "问题摘要", "placeholder": "如：违规收受礼金问题"},
            {"key": "案件来源", "label": "案件来源", "placeholder": "如：信访举报（编号XF...）"},
            {"key": "审理经过", "label": "审理经过", "placeholder": "受理/审核证据/谈话核实情况"},
            {"key": "认定事实", "label": "审查认定的事实", "placeholder": "经审理查明的事实"},
            {"key": "定性依据", "label": "定性及依据", "placeholder": "违反何种纪律及条款依据"},
            {"key": "审理意见", "label": "审理意见", "placeholder": "如：建议给予党内警告处分"},
            {"key": "涉案财物处理", "label": "涉案财物处理意见", "placeholder": "收缴/退还/随案移送"},
            {"key": "审理部门", "label": "审理部门", "placeholder": "如：县纪委审理室"},
            {"key": "日期", "label": "成文日期", "placeholder": "如：2026年11月10日"},
        ],
    },
    {
        "name": "监察建议书",
        "doc_type": "监察建议",
        "template": (
            "监 察 建 议 书\n\n"
            "{{被建议单位}}：\n\n"
            "在对{{单位简称}}开展监督工作中，发现{{发现问题}}。"
            "依据《中华人民共和国监察法》{{依据条款}}，提出如下监察建议：\n\n"
            "一、{{建议事项一}}；\n二、{{建议事项二}}；\n三、{{建议事项三}}。\n\n"
            "请于{{整改期限}}内将整改落实情况函复我委。\n\n"
            "{{监察机关}}\n{{日期}}"
        ),
        "fields": [
            {"key": "被建议单位", "label": "被建议单位", "placeholder": "如：某县住建局"},
            {"key": "单位简称", "label": "单位简称", "placeholder": "如：你单位"},
            {"key": "发现问题", "label": "发现问题", "placeholder": "如：工程招投标监管存在漏洞"},
            {"key": "依据条款", "label": "依据条款", "placeholder": "如：第四十五条"},
            {"key": "建议事项一", "label": "建议事项一", "placeholder": "如：完善招投标内控制度"},
            {"key": "建议事项二", "label": "建议事项二", "placeholder": "如：加强关键岗位监督"},
            {"key": "建议事项三", "label": "建议事项三", "placeholder": "如：开展警示教育"},
            {"key": "整改期限", "label": "整改期限", "placeholder": "如：收到建议书后60日内"},
            {"key": "监察机关", "label": "监察机关", "placeholder": "如：某某县监察委员会"},
            {"key": "日期", "label": "成文日期", "placeholder": "如：2026年10月20日"},
        ],
    },
    {
        "name": "信访结果反馈函",
        "doc_type": "信访反馈",
        "template": (
            "关于{{反映人}}反映{{问题摘要}}问题的反馈函\n\n"
            "{{反映人}}：\n\n"
            "你于{{反映日期}}反映{{被反映人}}{{问题摘要}}问题的信访件（编号：{{信访编号}}）收悉。"
            "经核查，{{核查结论}}。\n\n"
            "{{处理情况}}。\n\n"
            "感谢你对纪检监察工作的支持。\n\n"
            "{{承办部门}}\n{{日期}}"
        ),
        "fields": [
            {"key": "反映人", "label": "反映人", "placeholder": "如：李先生"},
            {"key": "反映日期", "label": "反映日期", "placeholder": "如：2026年9月1日"},
            {"key": "被反映人", "label": "被反映人", "placeholder": "如：某县住建局张某某"},
            {"key": "问题摘要", "label": "问题摘要", "placeholder": "如：违规收受礼金"},
            {"key": "信访编号", "label": "信访编号", "placeholder": "如：XF20260901001"},
            {"key": "核查结论", "label": "核查结论", "placeholder": "如：反映问题部分属实"},
            {"key": "处理情况", "label": "处理情况", "placeholder": "如：已对张某某立案审查"},
            {"key": "承办部门", "label": "承办部门", "placeholder": "如：某某县纪委信访室"},
            {"key": "日期", "label": "成文日期", "placeholder": "如：2026年10月25日"},
        ],
    },
]


def _seed_verify_rules(db):
    """预置权威纪检文书质检规则（幂等：仅当库为空时写入）。

    覆盖：规范表述 / 绝对化 / 敏感规范词 / 政治表述 / 数字金额 / 上下文。
    接管原 verify.py 中硬编码的 SPOKEN_WORDS / ABSOLUTE_WORDS / SENSITIVE_TIPS，
    改为数据驱动，业务人员可在界面增删、开关、改建议。
    """
    if db.query(VerifyRule).count() > 0:
        return
    R = []
    add = lambda cat, level, term, sug, rx=False: R.append(
        VerifyRule(category=cat, level=level, term=term, suggestion=sug,
                   is_regex=rx, enabled=True, builtin=True))

    # 规范表述（口语化 / 不确定 / 笼统）
    add("规范表述", "warn", "打架", "规范表述建议：'殴打他人'")
    add("规范表述", "warn", "抓了", "口语化表述，建议改为'被查获/被采取强制措施'")
    add("规范表述", "warn", "交代", "建议使用'供述/陈述'")
    add("规范表述", "warn", "很有钱", "口语化表述，建议核实并写明具体财产状况")
    add("规范表述", "warn", "据说", "传闻性表述，应写明证据来源或删除")
    add("规范表述", "warn", "好像是", "不确定表述，应核实后写明认定事实")
    add("规范表述", "warn", "大概", "模糊数量表述，应核实准确数字")
    add("规范表述", "warn", "情况属实", "建议写明查证认定的具体事实，避免笼统下结论")
    add("规范表述", "info", "违法", "注意区分'违纪/违法/犯罪'表述，须与认定口径一致")
    add("规范表述", "info", "贪污", "涉嫌罪名表述应与司法机关认定口径一致")

    # 绝对化表述
    add("绝对化表述", "info", "一定", "绝对化表述，建议注明依据或使用'可能/经查'")
    add("绝对化表述", "info", "肯定", "绝对化表述，建议注明依据")
    add("绝对化表述", "info", "肯定是", "绝对化表述，建议注明依据")

    # 敏感规范词（旧称 / 涉罪口径）
    add("敏感规范词", "info", "涉嫌犯罪", "涉及涉罪表述，建议与司法机关移送材料核对口径")
    add("敏感规范词", "info", "双规", "规范表述为'留置'（《监察法》施行后）")
    add("敏感规范词", "info", "两规", "规范表述为'留置'（《监察法》施行后）")

    # 政治表述（规范公文用语）
    add("政治表述", "warn", "四风", "规范表述为'形式主义、官僚主义、享乐主义和奢靡之风'")
    add("政治表述", "warn", "一岗双责", "建议写明具体责任内容")
    add("政治表述", "warn", "主体责任", "规范表述为'全面从严治党主体责任'")
    add("政治表述", "warn", "四种形态", "规范表述为'监督执纪\"四种形态\"'")

    # 数字金额规范（正则；"截止→截至"已由 proofread 模块覆盖，规则库不再重复）
    add("数字金额规范", "warn", r"\d+(?:\.\d+)?\s*万(?!\s*元)", "金额表述建议补全为「X万元」", rx=True)

    # 上下文规则（确定性，与一键修正同源）
    add("上下文", "warn", r"必需(?![品物费经])", "后接动词「必需」改为「必须」", rx=True)
    add("上下文", "warn", r"处于(?=(警告|严重警告|记过|记大过|降级|撤职|开除|留置))", "处分表述「处于」改为「处以」", rx=True)
    add("上下文", "warn", r"反应(?=(情况|问题|意见|线索|诉求|群众|举报|反映))", "报送表述「反应」改为「反映」", rx=True)
    add("上下文", "warn", r"权利(?=(机关|部门|行使|滥用|公))", "公权表述「权利」改为「权力」", rx=True)
    add("上下文", "warn", r"(做|办|搞|干|说|写|执行|完成|处理|改|查|看|听|学|研究|抓)的(很|较|不|非常|十分|更|还|多|好|快|出|差)", "动词后程度补语「的」改为「得」", rx=True)
    add("上下文", "warn", r"做出(?=了?决定|了?处理|了?处分)", "公文常用'作出'而非'做出'", rx=True)

    db.add_all(R)
    db.commit()


def run(db):
    if db.query(User).count() == 0:
        for username, pwd, real_name, role in USERS:
            db.add(User(username=username, password_hash=hash_password(pwd),
                        real_name=real_name, role=role))
        db.commit()

    if db.query(Petition).count() == 0:
        for i, (code, content, petitioner) in enumerate(PETITIONS):
            db.add(Petition(code=code, content=content, petitioner=petitioner,
                            channel=["网络", "来信", "来访"][i % 3]))
        db.commit()

    # 文书模板库：按名幂等补齐（老库只有 2 条时也能补齐新增模板，不重复插入）
    _have = {t.name for t in db.query(DocumentTemplate).all()}
    _new = [DocumentTemplate(name=t["name"], doc_type=t["doc_type"],
                             template=t["template"], fields=t["fields"])
            for t in TEMPLATES if t["name"] not in _have]
    if _new:
        db.add_all(_new)
        db.commit()

    from app.models import Regulation
    if db.query(Regulation).count() == 0:
        # 顶层 *.txt：演示节选语料
        files = sorted(settings.CORPUS_DIR.glob("*.txt"))
        # official/ 子目录：正式全文（此前因 glob 不递归被漏导，现一并入库并标记只读）
        official_dir = settings.CORPUS_DIR / "official"
        if official_dir.exists():
            files += sorted(official_dir.glob("*.txt"))
        for f in files:
            is_official = f.parent.name == "official"
            try:
                text = f.read_text(encoding="utf-8")
                src = (f"正式文档原件（演示语料）｜{f.name}" if is_official
                       else "公开文本（演示语料）")
                rag_ingest.ingest(db, text, source=src)
                if is_official:
                    title = text.strip().splitlines()[0].split("|")[0].strip()
                    reg = (db.query(Regulation).filter(Regulation.title == title)
                           .order_by(Regulation.id.desc()).first())
                    if reg and not reg.is_official:
                        reg.is_official = True
                        db.commit()
            except ValueError:
                pass

    # ---- 四大业务模块 + 监督模型 演示数据（幂等）----
    if db.query(Case).count() == 0:
        now = datetime.utcnow()
        c1 = Case(code="AJ20260901001", source="信访", subject="张某某",
                  unit="某县住建局", title="违规收受管理服务对象礼金",
                  content="2024年担任局长期间违规收受管理服务对象礼金5万元，并为私营企业主承揽工程提供帮助。",
                  problem_type="违反廉洁纪律", urgency="高", status="初核中",
                  linked_petition_id=1, due_at=now - timedelta(days=12), created_by=3)
        c2 = Case(code="AJ20260901006", source="信访", subject="张某某",
                  unit="某县住建局", title="工程招投标围标串标",
                  content="在工程招投标中与私营企业主围标串标、收受好处费。",
                  problem_type="职务违法", urgency="中", status="已立案",
                  opened_at=now - timedelta(days=5), linked_petition_id=6,
                  due_at=now + timedelta(days=10), created_by=3)
        c3 = Case(code="AJ20260901002", source="信访", subject="王某某",
                  unit="某镇东风村", title="侵占集体资金侵害群众利益",
                  content="侵占集体『三资』资金12万元，克扣村民惠民补贴。",
                  problem_type="群众纪律", urgency="高", status="待研判",
                  linked_petition_id=2, due_at=now + timedelta(days=40),
                  created_at=now - timedelta(days=90), created_by=3)
        c4 = Case(code="AJ20260901003", source="信访", subject="刘某某",
                  unit="某市城投集团", title="违规经商办企业未如实报告",
                  content="违规经商办企业，配偶在下属公司违规持股分红，未如实报告个人有关事项。",
                  problem_type="违反廉洁纪律", urgency="中", status="已分办",
                  linked_petition_id=3, due_at=now + timedelta(days=20), created_by=3)
        c5 = Case(code="AJ20260901005", source="信访", subject="陈某某",
                  unit="某区民政局", title="违规接受宴请、公车私用",
                  content="在私人会所违规接受宴请，公车私用，酒后驾驶被查，存在舆情风险。",
                  problem_type="违反中央八项规定精神", urgency="高", status="初核中",
                  linked_petition_id=5, due_at=now + timedelta(days=5), created_by=3)
        c6 = Case(code="AJ20260901099", source="主动发现", subject="赵某某",
                  unit="某区财政局", title="津补贴发放不规范",
                  content="自行设立项目发放津补贴，涉嫌违规。",
                  problem_type="违反廉洁纪律", urgency="低", status="待研判",
                  due_at=now + timedelta(days=60), created_by=3)
        db.add_all([c1, c2, c3, c4, c5, c6])
        db.flush()

        cc1 = ConductCase(code="DF20260901001", subject="陈某某", unit="某区民政局",
                          category="违规吃喝", title="私人会所违规接受宴请",
                          content="在私人会所接受管理服务对象宴请。",
                          source="日常监督", status="待处置", exposure="否", created_by=3)
        cc2 = ConductCase(code="DF20260901002", subject="孙某某", unit="某街道",
                          category="收送礼品礼金", title="收受节日礼品礼金",
                          content="春节期间收受管理服务对象礼品礼金。",
                          source="节点监督", status="已通报", exposure="是", created_by=3)
        cc3 = ConductCase(code="DF20260901003", subject="周某某", unit="某局",
                          category="公车私用", title="公车私用办私事",
                          content="多次使用单位公车办理个人事务。",
                          source="日常监督", status="整改中", exposure="否", created_by=3)
        db.add_all([cc1, cc2, cc3])
        db.flush()

        insp = Inspection(code="XZ2026080001", round_name="一届市委第五轮巡察",
                          target="某县", group_name="第一巡察组",
                          start_date="2026-08-01", end_date="2026-08-31",
                          status="整改中", created_by=3)
        db.add(insp)
        db.flush()
        f1 = InspectionFinding(inspection_id=insp.id,
                               content="某县住建局工程项目招投标不规范，存在围标串标嫌疑",
                               category="工程招投标", severity="高",
                               transfer_to="案管", transfer_ref_id=c1.id,
                               fix_status="整改中", due_at=now + timedelta(days=20), created_by=3)
        f2 = InspectionFinding(inspection_id=insp.id,
                               content="某县违规发放津补贴，超范围设立奖金项目",
                               category="滥发津补贴", severity="中",
                               transfer_to="党风", transfer_ref_id=cc3.id,
                               fix_status="待整改", due_at=now - timedelta(days=35), created_by=3)
        f3 = InspectionFinding(inspection_id=insp.id,
                               content="财务内控制度缺失，大额支出未集体研究",
                               category="财务管理", severity="中",
                               transfer_to="", fix_status="待整改",
                               due_at=now + timedelta(days=10),
                               created_at=now - timedelta(days=45), created_by=3)
        db.add_all([f1, f2, f3])
        db.commit()

    if db.query(SuperviseModel).count() == 0:
        for key, spec in MODELS.items():
            db.add(SuperviseModel(key=key, name=spec["name"], category=spec["category"],
                                  description=spec["description"], enabled=True,
                                  severity=spec["default_severity"],
                                  threshold=spec["default_threshold"], builtin=True))
        db.commit()

    if db.query(Archive).count() == 0:
        _samples = [
            ("DA202601010001", "关于给予张某党内警告处分的决定", "县纪委", "案件档案",
             "经查，张某违反廉洁纪律，收受管理服务对象礼金人民币伍万元。依据《中国共产党纪律处分条例》相关规定，经研究决定给予张某党内警告处分。", "已归档"),
            ("DA202601010002", "某单位2025年度党风廉政建设工作总结", "党风室", "文书档案",
             "2025年度，我单位严格落实全面从严治党主体责任，深入开展党风廉政教育，持续纠治四风，全年未发生重大违纪问题。", "已入库"),
            ("DA202601010003", "财务内控制度与大额支出审批台账（节选）", "财务科", "财务档案",
             "大额支出须集体研究并附会议记录；单笔超过五万元支出由班子会审批，严禁拆分报销。", "待编目"),
        ]
        for no, title, source, cat, text, st in _samples:
            db.add(Archive(archive_no=no, title=title, source=source, category=cat,
                           ocr_text=text, page_count=1, char_count=len(text),
                           status=st, file_name="", file_type="", uploaded_by=1))
        db.commit()

    # ---- 银行流水 / 语音转录 演示数据（此前无 seed，首屏为空）----
    if db.query(BankStatement).count() == 0:
        bank = BankStatement(
            filename="对公账户流水_某县城投_2026Q1.xlsx",
            account_name="某县城建投资有限公司·对公账户",
            row_count=5, dup_count=1,
            total_in=1280000.0, total_out=965000.0,
            cleaned=[
                {"date": "2026-01-08", "desc": "收到财政拨付款", "counterparty": "县财政局", "amount": 600000.0, "direction": "in"},
                {"date": "2026-02-14", "desc": "支付工程款", "counterparty": "某建筑工程有限公司", "amount": 480000.0, "direction": "out"},
                {"date": "2026-03-02", "desc": "备用金转入", "counterparty": "法人账户", "amount": 200000.0, "direction": "in"},
                {"date": "2026-03-19", "desc": "向关联个人转出", "counterparty": "刘某某（个人）", "amount": 185000.0, "direction": "out"},
                {"date": "2026-03-19", "desc": "向关联个人转出（重复记账）", "counterparty": "刘某某（个人）", "amount": 185000.0, "direction": "out"},
            ],
            analysis={
                "summary": "本期对公账户流入 128.0 万元、流出 96.5 万元；发现 1 笔关联方个人大额转出、1 笔疑似重复记账。",
                "risk_points": [
                    {"type": "关联方交易", "level": "高",
                     "desc": "向个人账户刘某某转出 18.5 万元，对方为被反映人关联人，建议核查资金来源与用途"},
                    {"type": "重复记账", "level": "中",
                     "desc": "2026-03-19 同一笔 18.5 万元转出出现两次，疑为拆分或重复记账"},
                ],
            },
            created_by=3,
        )
        db.add(bank)
        db.commit()

    if db.query(Transcript).count() == 0:
        segs = [
            {"start": 0.0, "end": 14.0, "text": "问：请说明你单位津补贴发放的具体项目和依据。"},
            {"start": 14.0, "end": 36.0, "text": "答：我们按上级文件设立了几项补贴，但具体审批台账现在不太齐全。"},
            {"start": 36.0, "end": 58.0, "text": "问：是否存在超范围、超标准发放的情况？"},
            {"start": 58.0, "end": 82.0, "text": "答：这个……个别项目可能标准偏高，我们回去再核实。"},
        ]
        transcript = Transcript(
            filename="谈话录音_赵某某_津补贴_20260815.wav",
            duration_s=82.0, language="zh", status="完成",
            segments=segs, text="\n".join(s["text"] for s in segs),
            audio_path="", created_by=3,
        )
        db.add(transcript)
        db.commit()

    # ---- 文书质检规则库（权威纪检术语，数据驱动，业务可编辑）----
    _seed_verify_rules(db)

    # ---- 历史报告库演示语料（与 CTAS 同源，见 app/seed_historical.py）----
    from app import seed_historical
    seed_historical.run(db, operator_id=1)
