from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, JSON, Index,
)
from app.database import Base


# ============================ 部门（租户）与数据归属 ============================
# 隔离规则见 app/tenancy.py。三种归属方式：
#   DeptOwned          只对数据范围内的部门可见；
#   DeptGrantable      同上，另外可经「移交授权」对接收 / 来源部门可见（信访件、案件、审理事项、党风问题）；
#   DeptOwnedOrShared  dept_id 为空表示平台共享（如内置监督模型），否则同 DeptOwned。
# 不带这些基类的表（法规库、文书模板、校验规则、历史报告库、审计日志等）全平台共享。

class DeptOwned:
    dept_id = Column(Integer, index=True, nullable=True)   # 归属部门（新建时自动取经办人部门）


class DeptGrantable:
    dept_id = Column(Integer, index=True, nullable=True)


class DeptOwnedOrShared:
    dept_id = Column(Integer, index=True, nullable=True)   # 空 = 平台共享


class Department(Base):
    """部门（租户）：账号归属、可用功能模块、数据范围。"""
    __tablename__ = "departments"
    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, index=True, nullable=False)   # 部门编码
    name = Column(String(80), unique=True, nullable=False)
    description = Column(String(300), default="")
    # 功能模块：tenancy.MODULES 中可分配的模块键（工作台始终可用，系统管理仅限平台管理员）
    modules = Column(JSON, default=list)
    # 数据范围：self 本部门 / custom 本部门 + 指定部门 / all 全部部门
    data_scope = Column(String(10), default="self", nullable=False)
    scope_dept_ids = Column(JSON, default=list)      # custom 时可访问的其他部门
    is_system = Column(Boolean, default=False)       # 平台管理部门（系统管理员所在，不参与业务移交）
    enabled = Column(Boolean, default=True)
    sort = Column(Integer, default=0)
    # 业务平台 MCP 服务令牌（部门级）：该部门对应 Nexent 租户注册 ctas-tools 时使用，工具只返回该部门数据范围内的数据
    mcp_token_hash = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class DataGrant(Base):
    """移交授权：跨部门移交时，接收部门可访问来源记录、来源部门可跟踪新记录（只对这一条记录生效）。"""
    __tablename__ = "data_grants"
    __table_args__ = (Index("uq_data_grants", "resource_table", "resource_id", "dept_id", unique=True),)
    id = Column(Integer, primary_key=True)
    resource_table = Column(String(40), nullable=False)   # petitions / cases / trials / conduct_cases
    resource_id = Column(Integer, nullable=False)
    dept_id = Column(Integer, nullable=False, index=True)
    reason = Column(String(60), default="")               # 信访转线索 / 移送审理 / 巡察移交…
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    real_name = Column(String(50), default="")
    # 角色：admin / xinfang(信访室) / banan(办案人员) / shenli(审理室)
    role = Column(String(20), default="banan", nullable=False)
    dept_id = Column(Integer, ForeignKey("departments.id"), index=True, nullable=True)  # 所属部门（租户）
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Regulation(Base):
    __tablename__ = "regulations"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    version = Column(String(50), default="")
    effective_date = Column(String(20), default="")
    status = Column(String(20), default="现行有效")  # 现行有效 / 已废止
    source = Column(String(200), default="公开文本（演示语料）")
    # 正式文档标记：原件入库（官方发布文本），只读、不可编辑
    is_official = Column(Boolean, default=False)
    category = Column(String(50), default="其他")  # 中央党内法规/国家法律法规/纪检监察法规制度/业务工作规范/其他
    # 必须项：该法规/指引是否为「案件审理（审理检查）」环节必须查阅的依据
    required = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class RegulationArticle(Base):
    __tablename__ = "regulation_articles"
    id = Column(Integer, primary_key=True)
    regulation_id = Column(Integer, ForeignKey("regulations.id"), index=True)
    chapter = Column(String(100), default="")
    article_no = Column(String(30), nullable=False)
    content = Column(Text, nullable=False)
    # 本地向量（稀疏词频，JSON dict）+ 云端向量（float list，可选）
    tf_vector = Column(JSON, default=dict)
    dense_vector = Column(JSON, default=None)
    created_at = Column(DateTime, default=datetime.utcnow)

class Petition(DeptGrantable, Base):
    __tablename__ = "petitions"
    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True)  # 举报编号
    content = Column(Text, nullable=False)
    petitioner = Column(String(50), default="匿名")  # 举报人
    channel = Column(String(20), default="网络")      # 来信/来访/网络/电话
    status = Column(String(20), default="待登记")
    # 登记要素抽取结果（JSON：被反映人/问题类型/金额/时间/地点/分类/紧急程度）
    elements = Column(JSON, default=None)
    labels = Column(JSON, default=list)   # 分类标签（问题类别）
    biz_type = Column(String(20), default="")   # 业务类别：检举控告/申诉/批评建议
    summary = Column(Text, default="")          # 信访件摘要（辅助编写）
    summary_mode = Column(String(10), default="")  # rules / llm
    urgency = Column(String(10), default="中")
    duplicate_of = Column(Integer, nullable=True)
    # 材料原件（登记时上传的 PDF / 图片），用于办理时回溯核对
    file_path = Column(String(300), default="")   # 相对路径（data/petitions/）
    file_name = Column(String(200), default="")   # 原件文件名
    file_type = Column(String(20), default="")    # pdf / png / jpg ...
    tf_vector = Column(JSON, default=None)  # 内容稀疏向量（查重用，避免每次重复 embed）
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    # —— 受理法治化 / 办理法治化 / 监督追责法治化（提升效能的关键环节）——
    petitioner_type = Column(String(10), default="")     # 实名 / 匿名（实名须告知+反馈）
    contact = Column(String(120), default="")            # 实名举报人联系方式（告知/反馈）
    accept_decision = Column(String(20), default="")     # 受理 / 不予受理 / 重复留存（甄别结论）
    accept_reason = Column(Text, default="")             # 不予受理原因 / 重复留存说明
    notified_at = Column(DateTime, nullable=True)        # 受理告知时间（实名 15 工作日）
    due_at = Column(DateTime, nullable=True)            # 办理期限（受理起 60 自然日，超期预警）
    feedback_at = Column(DateTime, nullable=True)        # 办结反馈时间（实名 15 工作日）
    feedback_note = Column(Text, default="")             # 办结反馈内容
    transfer_to = Column(String(120), default="")       # 分办去向部门
    source_case_id = Column(Integer, nullable=True)      # 转案管线索后关联的案件 id
    supervised_at = Column(DateTime, nullable=True)      # 督办时间
    supervise_note = Column(Text, default="")            # 督办意见

class DispositionSuggestion(DeptOwned, Base):
    __tablename__ = "disposition_suggestions"
    id = Column(Integer, primary_key=True)
    petition_id = Column(Integer, ForeignKey("petitions.id"), index=True)
    dept = Column(String(100))            # 建议承办部门
    reason = Column(Text, default="")
    rule_hits = Column(JSON, default=list)  # 命中规则（可解释性）
    confidence = Column(Float, default=0.8)
    status = Column(String(20), default="待确认")  # 待确认 / 已确认 / 已驳回
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class DocumentTemplate(Base):
    __tablename__ = "document_templates"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    doc_type = Column(String(30), default="初核报告")
    template = Column(Text, nullable=False)   # {{字段}} 占位
    fields = Column(JSON, default=list)      # [{key,label,placeholder}]
    # 模板来源：官方模板（内置规范文本）/ 自定义（在线录入）/ 上传（Word/TXT 导入）
    source = Column(String(20), default="自定义")
    # 字段校验规则：{字段key: {required:bool, type:text|date|amount|number, max_len:int, hint:str}}
    field_rules = Column(JSON, default=dict)
    pinned = Column(Boolean, default=False)   # 常用模板置顶
    created_at = Column(DateTime, default=datetime.utcnow)

class Document(DeptOwned, Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    template_id = Column(Integer, ForeignKey("document_templates.id"))
    title = Column(String(200), nullable=False)
    case_name = Column(String(100), default="")
    content = Column(Text, nullable=False)
    status = Column(String(20), default="AI初稿")  # AI初稿 / 已定稿
    source = Column(String(20), default="")       # 来源：流程生成 / 模板生成 / 智能体生成
    created_by = Column(Integer, ForeignKey("users.id"))
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class DocumentVersion(DeptOwned, Base):
    __tablename__ = "document_versions"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    version_no = Column(Integer, default=1)
    content = Column(Text, nullable=False)
    note = Column(String(200), default="")
    source = Column(String(20), default="")       # 版本来源：人工 / 智能体 / 系统
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True)
    username = Column(String(50), default="anonymous")
    action = Column(String(50), nullable=False)     # login/qa_ask/extract/confirm...
    resource_type = Column(String(50), default="")
    resource_id = Column(String(50), default="")
    detail = Column(JSON, default=dict)
    prev_hash = Column(String(64), default="")
    hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class LLMCall(Base):
    __tablename__ = "llm_calls"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True)
    scene = Column(String(30))          # 调用场景：qa / extract / document / verify / minutes ...
    provider = Column(String(30))      # nexent（场景智能体）/ local（旧版直连）
    model = Column(String(50))         # 实际处理的 Nexent 智能体名
    conversation_id = Column(Integer, nullable=True)  # Nexent 会话号（可回溯）
    note = Column(String(200), default="")            # 未调用 / 失败原因（降级可见）
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    ok = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Transcript(DeptOwned, Base):
    """语音转录记录（长音频，如谈话录音、会议录音）。"""
    __tablename__ = "transcripts"
    id = Column(Integer, primary_key=True)
    filename = Column(String(200), default="")
    duration_s = Column(Float, default=0.0)
    language = Column(String(10), default="zh")
    status = Column(String(20), default="转录中")       # 转录中 / 完成 / 失败
    error = Column(String(300), default="")
    segments = Column(JSON, default=list)               # [{start, end, text}]
    text = Column(Text, default="")                      # 全文（可人工校对）
    audio_path = Column(String(300), default="")         # 本机音频落盘路径（音文对照回放用）
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    # ---- ASR 深化（说话人分离 + 纪要）----
    lang = Column(String(10), default="zh")              # 转写语种：zh/yue/en/auto
    speaker_count = Column(Integer, default=0)           # 说话人数（0=未做分离）
    speaker_names = Column(JSON, default=dict)           # {0:"谈话人",1:"被谈话人"} 用户重命名映射
    minutes = Column(Text, default="")                   # 会议纪要（LLM 生成，markdown 全文）
    minutes_json = Column(JSON, default=dict)            # 结构化纪要字段
    minutes_status = Column(String(20), default="")      # 空/生成中/完成/失败
    minutes_model = Column(String(40), default="")       # 生成所用模型标识（离网/在线）
    minutes_error = Column(String(300), default="")


class KeywordWhitelist(Base):
    """用户可维护的「受保护关键词」白名单：校对时跳过这些词，避免重复命中领域常用词。"""
    __tablename__ = "keyword_whitelist"
    id = Column(Integer, primary_key=True)
    keyword = Column(String(50), unique=True, index=True, nullable=False)  # 受保护词（整词匹配）
    note = Column(String(200), default="")        # 备注（如：纪检领域术语）
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class VerifyRule(Base):
    """文书质检「规则库」：业务可维护的规范表述/敏感词/政治表述/数字金额等校验规则。

    - term：字面匹配词，或当 is_regex=True 时的正则表达式（re.IGNORECASE）。
    - 引擎对全文扫描命中即产出一条校验项（level 决定必改/建议/提示）。
    - builtin=True 为系统预置权威纪检术语，可由业务禁用/编辑，但不应被随意清空；
      若库为空（count==0）由 seed 重新预置，保证校验能力不退化。
    """
    __tablename__ = "verify_rule"
    id = Column(Integer, primary_key=True)
    category = Column(String(30), index=True, nullable=False,
                      default="规范表述")          # 规范表述/绝对化表述/敏感规范词/政治表述/数字金额规范/上下文
    level = Column(String(10), nullable=False, default="warn")  # error / warn / info
    term = Column(String(120), nullable=False)   # 匹配词或正则
    suggestion = Column(String(300), default="")  # 修改建议
    is_regex = Column(Boolean, default=False)     # True 时 term 视为正则表达式
    enabled = Column(Boolean, default=True)        # 是否参与校验
    builtin = Column(Boolean, default=False)       # 是否系统预置
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BankStatement(DeptOwned, Base):
    """银行流水导入批次：表头识别/映射/完整性质检 + 清洗结果 + 分析报告。

    设计要点（直击「各种格式快速完整导入」）：
    - missing_report / completeness_score / completeness_level 给出『数据全不全』量化门禁；
    - mapping / template / header_row 记录本次识别结果，便于复核与一键复用到同类格式。"""
    __tablename__ = "bank_statements"
    id = Column(Integer, primary_key=True)
    filename = Column(String(200), default="")
    account_name = Column(String(100), default="")       # 户名/账号（若表头可识别）
    row_count = Column(Integer, default=0)               # 清洗后有效笔数
    dup_count = Column(Integer, default=0)               # 剔除重复笔数
    total_in = Column(Float, default=0.0)
    total_out = Column(Float, default=0.0)
    cleaned = Column(JSON, default=list)                 # 清洗后明细行
    analysis = Column(JSON, default=dict)                # 分析报告
    # —— 完整性质检（数据全不全的门禁）—— #
    source_rows = Column(Integer, default=0)             # 表头之后原始数据行数
    parsed_rows = Column(Integer, default=0)             # 成功解析为有效交易的行数
    skipped_rows = Column(Integer, default=0)            # 被跳过的行数（空日期/零金额/不可解析）
    missing_report = Column(JSON, default=dict)          # {字段: {present, missing, rate}}
    completeness_score = Column(Float, default=0.0)       # 完整度评分 0-100
    completeness_level = Column(String(10), default="")  # 高/中/低
    header_row = Column(Integer, default=0)              # 识别到的表头行（0-based）
    mapping = Column(JSON, default=dict)                 # 本次采用的 {标准字段: 源列名}
    template = Column(String(60), default="")            # 命中的银行模板名（否则 自定义）
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class Case(DeptGrantable, Base):
    """案管（案件监督管理）：问题线索 / 案件台账。
    是信访→办案→审理之间的总调度台；线索可来自信访一键转入、巡视移交、审计移交等。"""
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, index=True)    # 案号 / 线索编号
    source = Column(String(20), default="信访")           # 信访/巡视/审计/上级交办/主动发现
    subject = Column(String(80), default="")             # 被反映人
    unit = Column(String(120), default="")               # 单位 / 职务
    title = Column(String(200), default="")              # 案由
    content = Column(Text, default="")                   # 反映内容 / 主要问题
    problem_type = Column(String(40), default="")        # 问题类型（六项纪律/职务违法/职务犯罪）
    urgency = Column(String(10), default="中")           # 高/中/低
    status = Column(String(20), default="待研判")         # 待研判/已分办/初核中/已立案/已处置/已了结
    disposition = Column(String(20), default="")         # 谈话函询/初步核实/暂存待查/予以了结
    disposition_note = Column(Text, default="")          # 处置依据
    meeting_note = Column(Text, default="")              # 会议结论
    linked_petition_id = Column(Integer, nullable=True)  # 关联信访件
    opened_doc_id = Column(Integer, nullable=True)       # 立案决定书文书
    opened_at = Column(DateTime, nullable=True)          # 立案时间
    due_at = Column(DateTime, nullable=True)            # 审查调查时限（超期预警）
    extras = Column(JSON, default=dict)                   # 措施/涉案财物等 MVP 台账（JSON）
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    # —— 案管全流程合规（依《监督执纪工作规则》《监察法》）——
    chuhe_at = Column(DateTime, nullable=True)             # 初核开始（初核时限 SLA 起点，60日）
    liuzhi_status = Column(String(20), default="")         # 留置：空/已采取/已延长/已解除
    liuzhi_approved_by = Column(String(120), default="")   # 留置审批单位/审批人
    liuzhi_start = Column(DateTime, nullable=True)         # 留置开始
    liuzhi_end = Column(DateTime, nullable=True)           # 留置届满（默认 start+3月）
    liuzhi_extended = Column(Boolean, default=False)       # 是否已延长一次（最长再3月）
    liuzhi_release_at = Column(DateTime, nullable=True)    # 留置解除
    liuzhi_notify_at = Column(DateTime, nullable=True)     # 留置后24小时通知单位/家属
    liuzhi_notify_note = Column(Text, default="")          # 留置通知情况
    trial_id = Column(Integer, ForeignKey("trials.id"), nullable=True)  # 移送审理关联审理事项
    transferred_at = Column(DateTime, nullable=True)       # 移送审理时间


class Interrogation(DeptOwned, Base):
    """讯问合规台账（案管监督讯问权利保障与时限合规，依《监察法》/《规则》）。"""
    __tablename__ = "interrogations"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), index=True)
    interrogated = Column(String(80), default="")          # 被讯问人
    interrogator = Column(String(80), default="")          # 讯问人
    at = Column(DateTime, nullable=True)                   # 讯问时间
    location = Column(String(120), default="")             # 讯问地点
    duration_hours = Column(Float, default=0.0)            # 单次讯问时长（小时）
    rights_informed = Column(Boolean, default=False)       # 是否告知权利义务
    rest_meal_ok = Column(Boolean, default=False)          # 是否保障饮食与休息
    note = Column(Text, default="")
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class Trial(DeptGrantable, Base):
    """审理（案件审理辅助）：挂在已立案/已处置案件上，做阅卷摘要、证据链与程序合规校验、
    类案量纪参考、审理报告生成与定稿确认。"""
    __tablename__ = "trials"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), index=True, nullable=True)  # 可选关联全业务案件（独立建档时为 None）
    # —— 独立建档核心字段：CTAS 可脱离全业务系统单独建项，不依赖外部案件 ——
    subject = Column(String(80), default="")              # 被审查调查人
    unit = Column(String(120), default="")                # 单位 / 职务
    rank = Column(String(40), default="")                 # 职级
    political_status = Column(String(20), default="")     # 政治面貌（中共党员等）
    problem_type = Column(String(40), default="")         # 问题性质（六项纪律/职务违法/职务犯罪）
    source = Column(String(20), default="")               # 线索来源
    case_status = Column(String(20), default="")          # 立案状态：初核中/已立案/已处置/直接了结
    opened_at = Column(DateTime, nullable=True)           # 立案时间
    involved_amount = Column(String(40), default="")      # 涉案金额
    brief = Column(Text, default="")                      # 简要案情
    transfer_from = Column(String(120), default="")       # 移送审查调查组
    title = Column(String(200), default="")              # 审理事项标题
    summary = Column(Text, default="")                   # 阅卷摘要（事实-证据-争议点）
    summary_mode = Column(String(10), default="")        # rules / llm
    evidence_check = Column(JSON, default=list)          # 证据链完整性校验结果
    procedure_check = Column(JSON, default=list)         # 程序合规性检查问题清单
    sentencing_ref = Column(JSON, default=dict)          # 类案量纪参考（区间+类案列表）
    report_doc_id = Column(Integer, nullable=True)       # 审理报告文书
    status = Column(String(20), default="待阅卷")         # 待阅卷/审理中/已报告/已定稿/暂缓受理/不予受理/退回补充调查
    created_by = Column(Integer, ForeignKey("users.id"))
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_note = Column(Text, default="")             # 定稿审议结论
    # —— 审理真实工作流后半段效能闭环（依《监督执纪工作规则》第五十五条及后续）——
    accept_decision = Column(String(20), default="")      # 受理 / 暂缓受理 / 不予受理
    accept_reason = Column(Text, default="")              # 受理审核理由（不予受理/暂缓原因）
    accepted_at = Column(DateTime, nullable=True)         # 受理时间（审理时限 SLA 起点）
    interview_at = Column(DateTime, nullable=True)        # 与被审查调查人见面谈话时间
    interview_attitude = Column(String(20), default="")   # 认错悔错 / 无申辩 / 提出申辩 / 部分申辩
    interview_note = Column(Text, default="")             # 申辩要点 / 谈话记录（速记）
    # —— 见面谈话专业化（《监督执纪工作规则》第五十一~五十三条）——
    interviewers = Column(String(120), default="")        # 谈话人（规则要求 2 名以上审理人员，顿号分隔）
    recorder = Column(String(50), default="")             # 记录人
    interview_place = Column(String(120), default="")     # 谈话地点
    rights_notified = Column(Integer, default=0)          # 是否已告知被审查调查人有申辩的权利（第五十二条）
    defense_items = Column(JSON, default=list)            # 逐条申辩：[{content, stance, adopt, reason}]
    interview_summary = Column(Text, default="")          # 谈话情况说明（第五十三条：如实记录并写出说明）
    returned_at = Column(DateTime, nullable=True)         # 退回补充/重新审查调查时间
    return_reason = Column(Text, default="")              # 退回理由（事实不清/证据不足）
    return_to = Column(String(120), default="")           # 退回对象（如：审查调查组）
    executed_at = Column(DateTime, nullable=True)         # 处分决定宣布执行时间
    execute_note = Column(Text, default="")              # 宣布执行情况 / 执行报告
    # —— P3 处分决定书 + 执行回证（处分决定作出→宣布执行→回证归档闭环）——
    decision_doc_id = Column(Integer, nullable=True)    # 处分决定书文书
    decision_no = Column(String(60), default="")        # 处分决定文号
    decision_result = Column(Text, default="")          # 处分决定内容（人工填写，系统不臆断）
    decision_date = Column(DateTime, nullable=True)     # 处分决定作出日期
    decision_approver = Column(String(120), default="")  # 处分批准机关（有处分批准权限的党组织/监察机关）
    # 宣布执行结构化（原 execute_note 单备注升级）
    execute_announcer = Column(String(120), default="")  # 宣布人
    execute_scope = Column(String(200), default="")      # 宣布范围（向本人及所在党组织全体党员宣布等）
    execute_delivery = Column(String(60), default="")   # 送达方式（直接送达/邮寄/留置送达）
    execute_signed = Column(Integer, default=0)         # 本人签收情况（0未签收/1已签收）
    # 执行回证清单：[{key, label, status(done/pending/na), note, at}]，缺项不报结
    execution_items = Column(JSON, default=list)
    restore_rights_at = Column(DateTime, nullable=True)  # 留党察看期满恢复党员权利时间
    execution_closed = Column(Integer, default=0)       # 回证是否归档报结（缺项不得报结）
    execution_closed_at = Column(DateTime, nullable=True)
    property_disposal = Column(Text, default="")         # 涉案财物处置意见
    physical_evidence = Column(JSON, default=list)        # 物证情况审查：查封扣押/用赃款购买的赃物及照片条目
    evidence_review = Column(JSON, default=dict)          # 物证情况审查（取证要点三维度）结果
    created_at = Column(DateTime, default=datetime.utcnow)


class HistoricalReport(Base):
    """CTAS 历史报告库：批量导入历史审计/审查调查/巡视等报告（Word/PDF），
    自动识别内容并结构化建档，用于类案参考与多维度统计。

    设计要点（呼应「数据全了才有分析」）：
    - 结构化字段是统计与类案检索的命门：报告类型/时间/地区/被审计单位/
      问题定性/金额/处理结果/标签，任一字段缺失都会在质量报告中显式暴露；
    - 全文明文落库（content），供 TF-IDF 类案检索与全文引用；
    - extract_confidence 记录自动识别置信度，低置信入「待补录」队列由人工兜底。
    """
    __tablename__ = "historical_reports"
    id = Column(Integer, primary_key=True)
    report_no = Column(String(60), index=True, default="")      # 报告文号（如 审字〔2024〕15号）
    title = Column(String(300), default="")                      # 报告标题
    report_type = Column(String(40), default="", index=True)    # 报告类型（REPORT_TYPES）
    report_date = Column(String(20), default="", index=True)    # 报告日期 YYYY-MM-DD
    year = Column(Integer, default=0, index=True)               # 报告年度（统计主维度）
    region = Column(String(60), default="", index=True)         # 所属地区（省/市/区县）
    region_code = Column(String(20), default="")                # 地区编码
    audited_org = Column(String(150), default="", index=True)   # 被审计/被检查单位
    audit_type = Column(String(40), default="", index=True)     # 审计类型（经济责任/专项/日常/巡视巡察…）
    problem_category = Column(String(40), default="", index=True)  # 问题类别（三公经费/违规发放/工程建设…）
    severity = Column(String(20), default="", index=True)       # 严重程度（一般/重要/重大）
    amount = Column(Float, default=0.0)                         # 涉及金额（万元）
    subject = Column(String(200), default="")                   # 涉及主体/被审计对象
    findings = Column(Text, default="")                         # 主要问题发现（结构化摘要）
    conclusion = Column(Text, default="")                       # 处理意见/结论
    legal_refs = Column(JSON, default=list)                     # 引用法规条款 [{law,article_no}]
    tags = Column(JSON, default=list)                           # 标签
    content = Column(Text, default="")                           # 全文（类案检索与原文引用）
    char_count = Column(Integer, default=0)
    page_count = Column(Integer, default=0)
    file_name = Column(String(200), default="")
    file_type = Column(String(20), default="")
    file_path = Column(String(300), default="")
    content_hash = Column(String(64), default="", index=True)   # 去重哈希
    extract_confidence = Column(Float, default=0.0)             # 自动识别置信度 0-1
    missing_fields = Column(JSON, default=list)                 # 缺失字段清单（质检）
    status = Column(String(20), default="已入库", index=True)   # 已入库/待补录/已复核
    near_duplicate_of = Column(Integer, default=0)              # 疑似重复的报告 id（文号相同但内容不同）
    source = Column(String(60), default="批量导入")              # 导入来源
    tf_vector = Column(JSON, default=dict)                      # TF-IDF 向量（类案检索）
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ReportFinding(Base):
    """历史报告的问题条目（把报告内「一、二、三」结构拆成可统计的条目）。

    为什么要拆条：报告整体只能做「按类型/年度/地区」的粗统计；拆到条目层
    才能回答「虚报面积这类问题近三年出现多少次、集中在哪些乡镇」这类真问题。
    """
    __tablename__ = "report_findings"
    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("historical_reports.id"), index=True)
    seq = Column(String(20), default="")             # 条目序号（一、二、三 / 1. 2. 3.）
    title = Column(String(200), default="")         # 条目标题（可为空）
    content = Column(Text, default="")              # 条目正文
    amount = Column(Float, default=0.0)             # 该条涉及金额（万元）
    problem_type = Column(String(40), default="", index=True)  # 该条问题类型（多关键词投票）
    severity = Column(String(20), default="", index=True)      # 该条严重程度
    legal_refs = Column(JSON, default=list)         # 该条引用的法规条款
    created_at = Column(DateTime, default=datetime.utcnow)


class ReportReference(Base):
    """历史报告引用留痕：审计人员把某份历史报告（或其问题条目）作为类案参考
    复用时记录，形成「谁、何时、引用了哪条」的审计链；被引次数同时是报告
    参考价值的热度指标（被引越多 = 越值得优先看）。
    """
    __tablename__ = "report_references"
    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("historical_reports.id"), index=True)
    finding_id = Column(Integer, ForeignKey("report_findings.id"), nullable=True, index=True)
    query = Column(String(200), default="")        # 触发引用的检索词（可空）
    note = Column(String(200), default="")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ImportBatch(Base):
    """批量导入批次留痕：一次导入 = 一个批次，逐文件结果可回溯（哪份成功/重复/失败/待补录）。"""
    __tablename__ = "import_batches"
    id = Column(Integer, primary_key=True)
    batch_no = Column(String(40), unique=True, index=True, nullable=False)
    total = Column(Integer, default=0)
    ok = Column(Integer, default=0)
    duplicated = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    need_review = Column(Integer, default=0)
    detail_json = Column(Text, default="")     # 逐文件结果 [{file,status,report_id,note,missing}]
    note = Column(String(200), default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TrialMaterial(DeptOwned, Base):
    """CTAS 送审材料：案主（审查调查组）移送审理的全过程材料登记 + OCR 文本落库。

    现实场景：审理材料更多是案主把案件全过程材料直接提供过来（而非系统内模块流转），
    故 CTAS 以「材料汇聚」为第一入口：按移送审理标准清单登记 → 上传/OCR 落库 →
    完整度检查（材料全没全可见）→ 才进入阅卷/证据/程序/量纪/报告审理链条。
    """
    __tablename__ = "trial_materials"
    id = Column(Integer, primary_key=True)
    trial_id = Column(Integer, ForeignKey("trials.id"), index=True)
    case_id = Column(Integer, ForeignKey("cases.id"), index=True, nullable=True)
    name = Column(String(200), default="")             # 材料名称
    category = Column(String(40), default="")          # 材料类别（MATERIAL_CATEGORIES）
    seq = Column(String(30), default="")               # 卷宗序号/编号
    pages = Column(Integer, default=0)                 # 页数
    formed_at = Column(String(20), default="")         # 形成时间
    source = Column(String(60), default="审查调查组")   # 材料来源
    is_original = Column(String(10), default="原件")    # 原件 / 复印件
    sign_note = Column(String(120), default="")        # 签注情况（如：缺被审查调查人签注）
    required = Column(Boolean, default=False)          # 是否必备项（移送审理标准清单）
    file_path = Column(String(300), default="")        # 原件文件路径（上传落盘）
    file_name = Column(String(200), default="")
    file_type = Column(String(20), default="")         # pdf/image
    ocr_text = Column(Text, default="")                # OCR 识别文本（落库，供阅卷/证据校验复用）
    ocr_status = Column(String(10), default="")        # "" / 已识别 / 识别失败
    ocr_at = Column(DateTime, nullable=True)
    note = Column(Text, default="")                    # 阅卷笔记/备注
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class Appeal(DeptOwned, Base):
    """审理申诉复查（党员对处分决定不服提出的申诉，依《党员权利保障条例》《控告申诉工作条例》）。

    流程：登记 → 受理/不予受理/暂缓受理 →（受理）指定复查承办人（原承办人回避）→ 复查结论（维持/变更/撤销）
    → 生成复查决定书 → 送达备案；对复查决定不服可再申诉（二级终结）。申诉期间原处分决定不停止执行。"""
    __tablename__ = "appeals"
    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, index=True)          # 申诉编号（申YYYY-XXXX）
    trial_id = Column(Integer, ForeignKey("trials.id"), index=True)   # 原处分决定（原审理事项）
    case_id = Column(Integer, ForeignKey("cases.id"), index=True, nullable=True)
    appellant = Column(String(80), default="")                  # 申诉人（被处分人）
    appellant_unit = Column(String(120), default="")            # 申诉人单位/职务
    reason = Column(Text, default="")                           # 申诉理由/主要诉求
    source = Column(String(40), default="本人书面")             # 申诉来源：本人书面/上级交办/其他
    appeal_at = Column(DateTime, default=datetime.utcnow)       # 申诉日期
    status = Column(String(20), default="待受理")               # 待受理/复查中/不予受理/已维持/已变更/已撤销/已终结
    accept_decision = Column(String(20), default="")            # 受理 / 不予受理 / 暂缓受理
    accept_reason = Column(Text, default="")
    accepted_at = Column(DateTime, nullable=True)               # 受理时间（复查时限 SLA 起点）
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # 复查承办人（原承办人须回避）
    recusal_note = Column(Text, default="")                     # 回避说明（原承办人回避落实）
    review_note = Column(Text, default="")                      # 复核意见
    conclusion = Column(String(20), default="")                 # 维持 / 变更 / 撤销
    conclusion_reason = Column(Text, default="")
    decision_doc_id = Column(Integer, nullable=True)           # 复查决定书文书
    reviewed_at = Column(DateTime, nullable=True)               # 复查结论作出时间
    served_at = Column(DateTime, nullable=True)                 # 送达时间
    served_note = Column(Text, default="")
    reappealed = Column(Boolean, default=False)                # 是否再申诉（二级终结）
    reappeal_note = Column(Text, default="")
    finalized_at = Column(DateTime, nullable=True)              # 终结时间
    correction_note = Column(Text, default="")                  # 纠错执行说明（变更/撤销时恢复权利/待遇/名誉）
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class ConductCase(DeptGrantable, Base):
    """党风（党风政风监督）：四风问题台账、专项治理、通报曝光、政治生态统计。"""
    __tablename__ = "conduct_cases"
    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, index=True)
    subject = Column(String(80), default="")             # 涉及人员 / 单位
    unit = Column(String(120), default="")               # 单位
    category = Column(String(40), default="")            # 违规吃喝/公车私用/收送礼品礼金/滥发津补贴/形式主义官僚主义
    title = Column(String(200), default="")              # 问题概要
    content = Column(Text, default="")
    source = Column(String(20), default="日常监督")       # 日常监督/节点监督/巡视巡察/信访/媒体
    status = Column(String(20), default="待处置")         # 待处置/整改中/已通报/已办结
    exposure = Column(String(10), default="否")          # 是否通报曝光
    linked_case_id = Column(Integer, nullable=True)      # 转案管线索（关联 cases）
    rectify_req = Column(Text, default="")               # 整改要求（交办时填写）
    rectify_due = Column(String(20), default="")         # 整改时限（如 2026-12-31）
    rectify_note = Column(Text, default="")              # 整改落实情况（办结时填写）
    rectified_at = Column(DateTime, nullable=True)       # 整改办结时间
    # —— 党风整改预警深化：整改下达 / 时限 SLA / 销号闭环 ——
    rectify_issued_at = Column(DateTime, nullable=True)  # 整改下达日期
    rectify_due_at = Column(DateTime, nullable=True)     # 整改期限（SLA 倒计时基准）
    rectify_stage = Column(String(20), default="")       # 待整改/整改中/申请销号/已销号
    rectify_report_at = Column(DateTime, nullable=True)  # 最近报送进展时间
    rectify_closed_at = Column(DateTime, nullable=True)  # 审核销号时间
    rectify_closed_by = Column(String(60), default="")   # 审核销号人
    extras = Column(JSON, default=dict)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class Inspection(DeptOwned, Base):
    """巡察（巡视巡察监督）：巡察台账、问题清单、移交闭环、整改看板。"""
    __tablename__ = "inspections"
    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, index=True)   # 巡字编号
    round_name = Column(String(60), default="")         # 轮次（如：一届市委第五轮巡察）
    target = Column(String(200), default="")            # 巡察对象
    group_name = Column(String(60), default="")         # 巡察组
    start_date = Column(String(20), default="")
    end_date = Column(String(20), default="")
    status = Column(String(20), default="准备中")         # 准备中/进驻中/反馈中/整改中/已结束
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class InspectionFinding(DeptOwned, Base):
    """巡察发现问题清单：可一键移交案管线索 / 党风问题 / 信访件，形成闭环。"""
    __tablename__ = "inspection_findings"
    id = Column(Integer, primary_key=True)
    inspection_id = Column(Integer, ForeignKey("inspections.id"), index=True)
    content = Column(Text, default="")                  # 问题摘要
    category = Column(String(40), default="")           # 问题类别
    severity = Column(String(10), default="中")         # 高/中/低
    transfer_to = Column(String(20), default="")        # 移交去向：案管/党风/信访/未移交
    transfer_ref_id = Column(Integer, nullable=True)    # 移交后生成的关联记录 id
    fix_status = Column(String(20), default="待整改")    # 待整改/整改中/已销号
    due_at = Column(DateTime, nullable=True)            # 整改完成时限（超期预警）
    fix_req = Column(Text, default="")                  # 整改要求
    fix_note = Column(Text, default="")                 # 整改落实情况
    fix_at = Column(DateTime, nullable=True)          # 整改销号/办结时间
    fix_effect = Column(String(20), default="未评估")   # 整改成效：未评估/好/中/差（成果运用评估）            # 整改销号/办结时间
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class SuperviseModel(DeptOwnedOrShared, Base):
    """大数据监督模型工厂：内置监督模型的启用/阈值配置（每条 = 一个可启停的"模型"）。

    检测器逻辑在 backend/app/supervise.py 的注册表中按 key 实现；
    本表仅保存配置（是否启用、严重级别、阈值），实现"工厂化"管理。
    用户自建碰撞模型（kind='user'）的配置存于 config（JSON）。"""
    __tablename__ = "supervise_models"
    id = Column(Integer, primary_key=True)
    key = Column(String(40), unique=True, index=True, nullable=False)   # 与注册表 key 对应 / 用户模型 user_<uuid>
    name = Column(String(80), default="")
    category = Column(String(40), default="")        # 时限类/聚集类/合规类/资金类/碰撞类
    description = Column(Text, default="")
    enabled = Column(Boolean, default=True)
    severity = Column(String(10), default="中")       # 高/中/低/提示
    threshold = Column(Integer, default=0)           # 模型参数（如超期天数/停留天数）
    builtin = Column(Boolean, default=True)
    note = Column(String(200), default="")
    kind = Column(String(10), default="builtin")      # builtin / user（用户自建碰撞模型）
    config = Column(JSON, default=dict)               # 用户模型配置：{dataset_a,dataset_b,join_key_a,join_key_b,rule,params,rule_label}
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Dataset(DeptOwned, Base):
    """智能监督数据接入：用户上传的 Excel/CSV 数据集（如公职人员名册、补贴发放表、中标人名册）。

    字段 schema + 行数据（JSON）一并存储，碰撞引擎在内存中用 pandas 关联对碰。"""
    __tablename__ = "datasets"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), default="")
    source_filename = Column(String(200), default="")
    columns = Column(JSON, default=list)              # 字段名列表
    row_count = Column(Integer, default=0)
    rows_json = Column(Text, default="")              # JSON 行数据（list[dict]）
    note = Column(String(200), default="")
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


class PersonRelation(DeptOwned, Base):
    """廉政档案人物关系图谱：被监督对象的社会关系网络（家庭成员/亲属/同事/特定关系人/利益关联等）。

    用于发现「核心社交圈内涉及被处分人员的危险主体」，把静态档案升级为关系网络画像。"""
    __tablename__ = "person_relations"
    id = Column(Integer, primary_key=True)
    person = Column(String(80), index=True, nullable=False)     # 主体（被监督对象）
    related_name = Column(String(80), default="")               # 关联人
    relation_type = Column(String(40), default="其他")           # 家庭成员/亲属/同事/特定关系人/利益关联/其他
    source = Column(String(40), default="手工录入")              # 案管/党风/信访/工商/手工录入
    note = Column(Text, default="")
    bidirectional = Column(Boolean, default=False)  # 双向关系（如配偶）：自动生成反向边，双方档案均可见
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PersonArchive(DeptOwned, Base):
    """廉政档案人工维护资料：组织人工掌握的情况（与系统聚合数据分区呈现）。

    聚合数据（线索/四风/预警/督办）由系统自动生成、只读；
    本表承载人工录入的静态档案要素，可编辑、留痕（updated_by/updated_at）。"""

    __tablename__ = "person_archives"
    id = Column(Integer, primary_key=True)
    __table_args__ = (Index("uq_person_archives_dept_person", "dept_id", "person", unique=True),)
    person = Column(String(80), index=True, nullable=False)  # 部门内唯一（各部门各自建档）
    unit = Column(String(120), default="")            # 单位
    post = Column(String(80), default="")             # 职务
    rank = Column(String(40), default="")             # 职级
    org_notes = Column(Text, default="")              # 组织掌握的其他情况
    personal_report = Column(Text, default="")        # 个人事项报告要点
    remark = Column(Text, default="")                 # 备注
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IntegrityReplyRecord(DeptOwned, Base):
    """廉政意见出具记录：每次出具《廉政情况回复意见》留痕入档。"""

    __tablename__ = "integrity_reply_records"
    id = Column(Integer, primary_key=True)
    person = Column(String(80), index=True, nullable=False)
    purpose = Column(String(60), default="干部选拔任用")   # 用途
    rating = Column(String(20), default="")                # 出具时廉政评级
    document_id = Column(Integer, nullable=True)           # 关联 A3 文书 id
    template_id = Column(Integer, nullable=True)           # 复用模板（如有）
    note = Column(String(200), default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Alert(DeptOwned, Base):
    """智能督办预警中枢：监督模型扫描产出的预警条目（持久化，支持确认/忽略）。

    fingerprint = 模型key + 关联对象，保证重复扫描时更新而非重复插入。"""
    __tablename__ = "alerts"
    __table_args__ = (Index("uq_alerts_dept_fingerprint", "dept_id", "fingerprint", unique=True),)
    id = Column(Integer, primary_key=True)
    fingerprint = Column(String(120), index=True, nullable=False)  # 部门内唯一
    level = Column(String(10), default="中")          # 高/中/低/提示
    source = Column(String(40), default="")          # 超期/重复举报/关联穿透/长期未处置/巡察未整改/资金异常
    title = Column(String(200), default="")
    detail = Column(Text, default="")
    ref_type = Column(String(20), default="")        # case/conduct/inspection/finding/person/bank
    ref_id = Column(String(40), default="")          # 关联对象 id（person 为姓名）
    status = Column(String(20), default="待处理")     # 待处理/已确认/已忽略
    acked_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    acked_at = Column(DateTime, nullable=True)
    due_at = Column(DateTime, nullable=True)         # 处置时限：按级别 高24h/中72h/低168h，提示级不设
    last_seen = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class AlertFeedback(DeptOwned, Base):
    """模型预警误报反馈：人工判定某条模型预警「误报 / 属实」，形成模型迭代闭环。

    - fingerprint 与 Alert 一一对应（unique），同一预警重复反馈为覆盖更新；
    - model_key / obj_key 从 fingerprint 解析（内置模型 key:type:id，用户模型 user:key:对象），
      用于「该模型下该对象被判误报 → 再次扫描/运行时抑制产出」，让反馈真正改变模型行为；
    - verdict：误报 / 属实；reason 必填于误报（调优依据），属实可留空。
    """
    __tablename__ = "alert_feedback"
    id = Column(Integer, primary_key=True)
    __table_args__ = (Index("uq_alert_feedback_dept_fingerprint", "dept_id", "fingerprint", unique=True),)
    alert_id = Column(Integer, index=True, nullable=False)
    fingerprint = Column(String(120), index=True, nullable=False)  # 部门内唯一
    model_key = Column(String(60), default="", index=True)   # 产出该预警的模型 key
    obj_key = Column(String(120), default="", index=True)    # 命中对象（case:12 / 身份证号 / 姓名）
    verdict = Column(String(10), default="误报")              # 误报 / 属实
    reason = Column(String(300), default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SuperviseDispatch(DeptOwned, Base):
    """智能督办预警中枢：一键督办下发记录，形成「时限预警 → 督办 → 反馈 → 闭环」追溯链。

    横向打通案管/党风/巡察/信访四路时限 SLA 预警，对超期/临期事项发起督办，
    记录督办对象与要求，并支持反馈与闭环，避免「预警发了没人管」。"""
    __tablename__ = "supervise_dispatches"
    id = Column(Integer, primary_key=True)
    source = Column(String(20), default="")         # 案管/党风/巡察/信访
    ref_type = Column(String(20), default="")       # case/conduct/inspection/petition
    ref_id = Column(Integer, nullable=False)
    ref_code = Column(String(40), default="")       # 关联业务编号
    ref_title = Column(String(200), default="")     # 关联事项标题
    ref_unit = Column(String(100), default="")      # 责任单位/被反映对象
    level = Column(String(10), default="超期")       # 超期/临期
    urge_to = Column(String(100), default="")       # 督办对象（承办人/责任单位）
    note = Column(Text, default="")                 # 督办要求/说明
    status = Column(String(20), default="已下发")   # 已下发/已反馈/已闭环
    due_at = Column(DateTime, nullable=True)         # 办理期限：超期事项3天/临期事项7天（自下发日起）
    escalate_count = Column(Integer, default=0)      # 逾期自动升级次数（督办"长牙"的计数）
    escalated_at = Column(DateTime, nullable=True)   # 最近一次逾期升级时间（留痕）
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    feedback = Column(Text, default="")
    feedback_at = Column(DateTime, nullable=True)


class Archive(DeptOwned, Base):
    """档案数字化：扫描件 OCR 入库（文书/案件/财务等），支持检索与编目。"""
    __tablename__ = "archives"
    id = Column(Integer, primary_key=True)
    archive_no = Column(String(40), unique=True, index=True, nullable=False)  # 档号
    title = Column(String(200), default="")
    source = Column(String(100), default="")        # 来源 / 移交单位
    category = Column(String(50), default="文书档案")  # 文书档案/案件档案/财务档案/其他
    ocr_text = Column(Text, default="")
    page_count = Column(Integer, default=1)
    char_count = Column(Integer, default=0)
    status = Column(String(20), default="已入库")     # 已入库/待编目/已归档
    file_name = Column(String(200), default="")
    file_type = Column(String(10), default="")
    file_path = Column(String(300), default="")  # 原件存储相对路径（data/archives/）
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # —— 编目增强 ——
    secret_level = Column(String(20), default="内部")  # 公开/内部/秘密/机密
    retention = Column(String(20), default="30年")      # 永久/30年/10年
    content_hash = Column(String(64), default="", index=True)  # 原件内容哈希（去重）
    boxes_json = Column(Text, default="")   # 结构化坐标（原文对照）：{"pages":[{page,boxes,w,h}]}
    ocr_dpi = Column(Integer, default=0)    # PDF 原文对照渲染 dpi（与 boxes 坐标一致）


class NexentConversation(DeptOwned, Base):
    """CTAS 发起的 Nexent 智能体会话台账。

    CTAS 以「全系统共用一个 Nexent API Key」接入北向接口，Nexent 侧所有会话都记在同一账号下；
    因此由 CTAS 自己登记「哪个 CTAS 用户、在哪个审理事项里、用哪个智能体」发起了哪条会话，
    据此做会话归属隔离（每人只能看到/续聊/停止自己的会话）与审计留痕。
    """
    __tablename__ = "nexent_conversations"
    id = Column(Integer, primary_key=True)
    nexent_conversation_id = Column(Integer, index=True, nullable=False)  # Nexent 会话 ID
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    username = Column(String(50), default="")
    agent_name = Column(String(120), default="")
    agent_display_name = Column(String(120), default="")
    trial_id = Column(Integer, ForeignKey("trials.id"), index=True, nullable=True)  # 案件工作台内发起时关联
    subject_kind = Column(String(20), default="")    # 发起所在业务对象：trial / case / petition
    subject_id = Column(Integer, nullable=True, index=True)
    title = Column(String(200), default="")
    with_context = Column(Boolean, default=False)   # 首轮是否附带了案件上下文
    last_answer = Column(Text, default="")          # 最近一次回答摘要（列表预览用）
    hidden = Column(Boolean, default=False)         # 用户从 CTAS 列表移除（不删除 Nexent 侧记录）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
