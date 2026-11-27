// 全系统工作流数据：供「系统介绍」弹窗消费。
// 内容原则（诚实性红线）：
//   1. 只收录已真实实现的功能，步骤链与后端端点一一对应，不写规划中的能力；
//   2. 每个步骤标注执行者 —— AI（本机模型/引擎自动完成）、自动（系统规则）、
//      人工（须工作人员操作或决策），不把辅助包装成自动办理；
//   3. 系统是辅助工具：所有流程节点只做「提效 + 留痕 + 提醒」，业务决定永远由人按程序作出。

export type StepBy = 'ai' | 'auto' | 'human'

export interface FlowStep {
  label: string
  by: StepBy
  note?: string
}

export interface MainStage {
  no: string
  name: string
  module: string
  route: string
  actions: string[]
  handoff: string
}

export interface ModuleFlow {
  name: string
  route: string
  goal: string
  steps: FlowStep[]
}

/** 一段简易清晰的系统总介绍（挂在「系统介绍」弹窗顶部） */
export const SYSTEM_INTRO = {
  lead:
    '这是一套安装在纪检监察工作人员电脑上的离线智能辅助系统。它把日常工作中最耗时的材料处理——信访件录入、银行流水清洗、录音转写、文书校对、档案数字化——交给本机 AI 引擎完成；把信访受理、线索处置、案件审理、处分执行、整改落实、巡察成果运用这些关键业务节点搬到线上，实现全程留痕、限时督办；再用大数据监督模型对多部门数据做碰撞比对，自动生成预警并跟踪到整改销号。所有识别与生成都在本机完成，可完全断网；所有输出只是辅助参考，业务决定永远由工作人员按程序作出。',
  facts: [
    '20+ 功能模块 · 5 大分组',
    '全流程离线可断网',
    '本机 AI：Qwen2.5 大模型 + OCR/ASR 引擎',
    '在线/离网双模式 · 界面一键切换',
    '全程操作留痕 · 审计可溯',
    '辅助工具 · 非业务系统',
  ],
}

/** 主工作流：一条业务主线（信访→案管→审理→处分整改）+ 巡察汇入 + 两条横向层 */
export const MAIN_STAGES: MainStage[] = [
  {
    no: '1',
    name: '信访受理',
    module: '信访智能辅助',
    route: '/petitions',
    actions: ['材料 OCR 录入 / 要素抽取', '自动分类 · 摘要 · 重复件检测', '受理 / 办理 / 督办（SLA 限时）', '办结统计与台账导出'],
    handoff: '构成问题线索的，移交案管处置',
  },
  {
    no: '2',
    name: '线索处置',
    module: '案管智能辅助',
    route: '/cases',
    actions: ['线索登记与处置方式（初核/立案/了结）', '初核、立案进展全程记录', '留置措施与期限管理（延长须审批）', '讯问笔录 · 移送审理'],
    handoff: '审查调查终结，移送案件审理',
  },
  {
    no: '3',
    name: '案件审理',
    module: '审理智能辅助',
    route: '/trials',
    actions: ['审理受理登记', '证据链闭环校验（事实-证据对应）', '程序合规校验（不臆断，须人工确认）', '量纪参考 · 审理报告生成'],
    handoff: '结论确认，进入处分执行',
  },
  {
    no: '4',
    name: '处分执行与党风整改',
    module: '党风智能辅助',
    route: '/conduct',
    actions: ['处分决定与执行台账', '整改任务分派责任单位', '进度跟踪（SLA）· 办结审核', '效能统计'],
    handoff: '整改情况纳入巡察评估与廉政档案',
  },
]

/** 巡察线：与主线在「问题移交」处汇入 */
export const INSPECTION_FLOW: MainStage = {
  no: '5',
  name: '巡察成果运用',
  module: '巡察智能辅助',
  route: '/inspections',
  actions: ['巡察项目与报告登记', '发现问题清单化管理', '问题移交办理（转信访/案管/党风）', '整改跟踪 · 成效评估'],
  handoff: '发现问题按类别移交对应业务线闭环',
}

/** 横向层一：大数据监督闭环（贯穿各业务线） */
export const SUPERVISE_LOOP: FlowStep[] = [
  { label: '数据接入', by: 'human', note: 'Excel/CSV/多银行流水，字段自动识别' },
  { label: '清洗映射', by: 'auto', note: '列语义三级映射 + 完整性质检门禁' },
  { label: '建模对碰', by: 'auto', note: '模型工厂拖拽配置，跨源碰撞' },
  { label: '生成预警', by: 'ai', note: '命中规则自动生成，附依据' },
  { label: '分办处置', by: 'human', note: '督办中枢一键分办，SLA 计时' },
  { label: '整改销号', by: 'human', note: '处置反馈 → 整改 → 销号闭环' },
]

/** 横向层二：智能辅助支撑（被各业务模块复用） */
export const SUPPORT_MODULES: { name: string; route: string; what: string }[] = [
  { name: 'OCR 图片识别', route: '/ocr', what: '扫描件/手写件转文字，带坐标' },
  { name: '语音转录', route: '/transcripts', what: '录音转写，音文对照' },
  { name: '银行流水分析', route: '/bank', what: '多行表头自动识别，异常分析' },
  { name: '档案数字化 OCR', route: '/archives', what: '档案入库编目，原文对照' },
  { name: '文书辅助生成', route: '/documents', what: '模板生成与排版检查' },
  { name: '文书校验', route: '/verify', what: '错别字/标点/排版校对' },
  { name: '法规智能问答', route: '/qa', what: '法规检索 + 条款溯源' },
]

/** 分模块流程明细（按导航分组组织，步骤与真实端点对应） */
export const MODULE_GROUPS: { group: string; flows: ModuleFlow[] }[] = [
  {
    group: '业务办理',
    flows: [
      {
        name: '信访智能辅助',
        route: '/petitions',
        goal: '信访件受理、办理、督办全流程线上化',
        steps: [
          { label: '上传/登记材料', by: 'human', note: '扫描件 OCR 识别，要素自动抽取' },
          { label: '智能分类与摘要', by: 'ai', note: '自动分类、生成摘要、重复件检测' },
          { label: '受理决策', by: 'human', note: '确认受理 / 驳回，系统只建议不代办' },
          { label: '办理与督办', by: 'auto', note: 'SLA 限时，临期提醒，全程留痕' },
          { label: '办结归档', by: 'human', note: '办结确认，统计台账导出' },
        ],
      },
      {
        name: '案管智能辅助',
        route: '/cases',
        goal: '线索到案件的处置全周期管理',
        steps: [
          { label: '线索受理登记', by: 'human' },
          { label: '处置方式决策', by: 'human', note: '初步核实 / 立案 / 了结，人工选定' },
          { label: '进展记录', by: 'human', note: '初核、立案各节点文书与时间线' },
          { label: '留置与讯问管理', by: 'auto', note: '期限预警、延长审批、讯问笔录' },
          { label: '移送审理', by: 'human', note: '达到审理条件一键移送，SLA 引擎计时' },
        ],
      },
      {
        name: '审理智能辅助',
        route: '/trials',
        goal: '案件审理的质量校验与报告辅助',
        steps: [
          { label: '审理受理', by: 'human' },
          { label: '证据链闭环校验', by: 'ai', note: '事实-证据对应、多源印证、缺口提示' },
          { label: '程序合规校验', by: 'ai', note: '只提示风险，结论须人工逐项确认' },
          { label: '量纪参考与报告', by: 'ai', note: '处分依据检索 + 审理报告起草' },
          { label: '确认/退回', by: 'human', note: '退回补证或确认结论，宣布执行' },
        ],
      },
      {
        name: '党风智能辅助',
        route: '/conduct',
        goal: '处分执行与党风整改闭环',
        steps: [
          { label: '整改任务登记', by: 'human' },
          { label: '分派与跟踪', by: 'auto', note: '责任单位、时限、SLA 预警' },
          { label: '办结审核', by: 'human', note: '整改成效人工审核销号' },
        ],
      },
      {
        name: '巡察智能辅助',
        route: '/inspections',
        goal: '巡察发现问题移交与整改运用',
        steps: [
          { label: '巡察项目登记', by: 'human' },
          { label: '问题清单管理', by: 'human', note: '问题分类、定级、责任认领' },
          { label: '移交办理', by: 'human', note: '按类别转信访/案管/党风线' },
          { label: '整改跟踪评估', by: 'auto', note: '进度看板、成效评估统计' },
        ],
      },
      {
        name: '银行流水分析',
        route: '/bank',
        goal: '涉案资金流水的清洗与异常发现',
        steps: [
          { label: '导入流水', by: 'human', note: '工/农/建/招/邮储等模板自动识别' },
          { label: '列映射与清洗', by: 'auto', note: '三级语义映射，非标表头人工兜底' },
          { label: '完整性质检', by: 'auto', note: '借贷平衡、断号、缺列门禁' },
          { label: '异常分析', by: 'ai', note: '大额/夜间/高频对手方/敏感词' },
          { label: '报告辅助', by: 'ai', note: '分析结论起草，人工复核后使用' },
        ],
      },
      {
        name: '文书辅助生成',
        route: '/documents',
        goal: '常用纪检文书按模板生成',
        steps: [
          { label: '选择文书类型', by: 'human' },
          { label: '要素填充', by: 'human', note: '关键要素表单化录入' },
          { label: '模板生成', by: 'auto' },
          { label: '排版校对', by: 'ai', note: '公文排版合规检查，一键修正' },
        ],
      },
    ],
  },
  {
    group: '智能监督',
    flows: [
      {
        name: '大数据监督模型工厂',
        route: '/supervise',
        goal: '零代码搭建数据对碰模型，批量发现异常',
        steps: [
          { label: '数据接入', by: 'human', note: 'Excel/CSV 上传，字段自动识别' },
          { label: '建模配置', by: 'human', note: '选关键字段、设关联规则，拖拽完成' },
          { label: '运行对碰', by: 'auto', note: '跨源碰撞，命中即生成预警' },
        ],
      },
      {
        name: '智能督办预警中枢',
        route: '/alerts',
        goal: '预警与四路 SLA 的统一调度闭环',
        steps: [
          { label: '预警汇总', by: 'auto', note: '模型预警 + 信访/案管/审理/整改四路 SLA' },
          { label: '一键督办分办', by: 'human', note: '交办到人，限时启动' },
          { label: '处置与反馈', by: 'human', note: '处置意见回填，进度可视' },
          { label: '整改销号', by: 'human', note: '整改到位审核销号，形成治理建议' },
        ],
      },
      {
        name: '问题线索智能研判画像',
        route: '/clue',
        goal: '问题线索的多维研判与风险评级',
        steps: [
          { label: '线索导入', by: 'human' },
          { label: '多维研判', by: 'ai', note: '多维度交叉分析，生成研判画像' },
          { label: '风险评级', by: 'ai', note: '仅作排序参考，处置决定人工作出' },
        ],
      },
      {
        name: '廉政档案画像',
        route: '/integrity',
        goal: '人物中心的廉政档案与关系网络',
        steps: [
          { label: '人物建档', by: 'human' },
          { label: '关系网络录入', by: 'human', note: '社会关系/监督足迹结构化' },
          { label: '多维画像', by: 'ai', note: '自动聚合并标注风险节点' },
          { label: '廉政意见回复', by: 'ai', note: '意见书起草，人工复核用印' },
          { label: '跨域预警联动', by: 'auto', note: '与监督模型预警互通' },
        ],
      },
    ],
  },
  {
    group: '智能辅助',
    flows: [
      {
        name: 'OCR 图片识别',
        route: '/ocr',
        goal: '扫描件、手写件转可编辑文字',
        steps: [
          { label: '上传图片/扫描件', by: 'human' },
          { label: '识别增强', by: 'ai', note: '预处理 + 手写增强，返回文字与坐标' },
          { label: '校对与导出', by: 'human', note: '对照原图校对，复制/送入后续模块' },
        ],
      },
      {
        name: '语音转录',
        route: '/transcripts',
        goal: '谈话录音转写与纪要整理',
        steps: [
          { label: '上传录音', by: 'human' },
          { label: '智能转写', by: 'ai', note: 'VAD 切分、标点恢复、热词、说话人分离' },
          { label: '音文对照校对', by: 'human', note: '点击文字定位音频' },
          { label: '纪要辅助', by: 'ai', note: '按转写结果起草，人工修改' },
        ],
      },
      {
        name: '档案数字化 OCR 入库',
        route: '/archives',
        goal: '纸质档案数字化、结构化入库与利用',
        steps: [
          { label: '上传档案', by: 'human', note: '图片/PDF，OCR 识别文字与坐标' },
          { label: '编目入库', by: 'auto', note: '自动编档号，密级/保管期限/分类' },
          { label: '原文对照利用', by: 'human', note: '检索定位到原文位置' },
          { label: '目录导出', by: 'auto', note: '一键导出 xlsx 目录' },
        ],
      },
      {
        name: '法规智能问答',
        route: '/qa',
        goal: '面向法规库的检索式问答',
        steps: [
          { label: '提问', by: 'human' },
          { label: '法规检索', by: 'auto', note: '离线向量检索命中条款' },
          { label: '答案合成', by: 'ai', note: '本机模型组织回答，附条款溯源' },
        ],
      },
      {
        name: '文书校验',
        route: '/verify',
        goal: '文书错别字与公文排版校对',
        steps: [
          { label: '上传/粘贴文书', by: 'human' },
          { label: '校对检查', by: 'ai', note: '错别字/标点/公文排版合规' },
          { label: '修正与导出', by: 'human', note: '逐条确认或一键修正' },
        ],
      },
    ],
  },
  {
    group: '知识库',
    flows: [
      {
        name: '法规库管理',
        route: '/regulations',
        goal: '法规条款的结构化建库与检索',
        steps: [
          { label: '粘贴/导入文本', by: 'human' },
          { label: '自动拆条建库', by: 'auto', note: '按条款拆分，离线建索引' },
          { label: '分类管理', by: 'human' },
          { label: '检索与溯源', by: 'auto', note: '全文检索，问答模块引用来源' },
        ],
      },
    ],
  },
  {
    group: '系统管理',
    flows: [
      {
        name: '用户与权限',
        route: '/users',
        goal: '分角色授权，最小可用',
        steps: [
          { label: '账号管理', by: 'human', note: 'admin 统一管理，角色分权' },
          { label: '登录与鉴权', by: 'auto', note: 'JWT，写操作按角色控制' },
        ],
      },
      {
        name: '审计日志',
        route: '/audit',
        goal: '全程操作留痕，可回溯可举证',
        steps: [
          { label: '自动记录', by: 'auto', note: '关键动作全量入链，含模型调用' },
          { label: '检索导出', by: 'human', note: '按人/模块/时间检索' },
        ],
      },
    ],
  },
]
