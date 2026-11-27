// CTAS 审理辅助系统 · 专属工作流数据：供「系统介绍」弹窗消费。
// 内容原则（诚实性红线）：
//   1. 只收录已真实实现的功能，步骤链与后端端点一一对应，不写规划中的能力；
//   2. 每个步骤标注执行者 —— AI（本机模型/引擎自动完成）、自动（系统规则）、
//      人工（须工作人员操作或决策），不把辅助包装成自动办理；
//   3. 系统独立运行：不依赖任何外部业务系统，送审材料由审查调查组直接提供（现实形态）；
//   4. 系统是辅助工具：所有流程节点只做「提效 + 留痕 + 提醒」，业务决定永远由人按程序作出。
//
// 主线锚定《监督执纪工作规则》第五十五条起的审理环节 + 材料汇聚现实形态。

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

/** 系统总介绍（挂在「系统介绍」弹窗顶部）——审理辅助定位，非全业务 */
export const SYSTEM_INTRO = {
  lead:
    '这是一套安装在审理工作人员电脑上的离线审理辅助系统（CTAS）。它独立运行，不依赖信访、案管等任何外部系统——现实中审查调查组直接把案件全过程材料送过来，系统从「送审材料汇聚」开始介入：材料批量登记、OCR 识别、完整度检查，缺什么一目了然；受理后辅助阅卷摘要、证据链闭环校验、程序合规校验；量纪时提供处分依据条款级检索与历史类案参考；审理报告可套用文书模板生成，类案引用自动附于文末并逐条留痕。所有识别与生成都在本机完成，可完全断网；所有输出只是辅助参考，业务决定永远由工作人员按程序作出。',
  facts: [
    '独立运行 · 不依赖外部业务系统',
    '送审材料汇聚中心 · 完整度可评估',
    '审理全链条：受理→阅卷→证据→程序→量纪→报告→谈话→执行',
    '全流程离线可断网',
    '本机 AI：Qwen2.5 大模型 + OCR/ASR 引擎',
    '全程操作留痕 · 审计可溯',
    '辅助工具 · 非业务系统',
  ],
}

/** 主工作流：审理辅助一条主线（材料汇聚→受理→阅卷→证据→程序→量纪→报告→定稿→谈话→执行） */
export const MAIN_STAGES: MainStage[] = [
  {
    no: '1',
    name: '建档立项',
    module: '审理台账',
    route: '/trials',
    actions: ['导入审查调查报告/移送材料，Word 自动识别字段回填', '或直接录入被审查人/单位/问题性质独立建档', '可选关联已接入的全业务案件（预填，不强依赖）'],
    handoff: '建项后进入送审材料汇聚',
  },
  {
    no: '2',
    name: '送审材料汇聚',
    module: '送审材料汇聚',
    route: '/ctas-materials',
    actions: ['材料逐项登记 · 卷宗页码 · 原件/复印件标注', '批量上传扫描件，OCR 识别落库', '完整度对照必备清单逐项检查，缺口一目了然'],
    handoff: '材料齐全方可受理（不齐先退回补送）',
  },
  {
    no: '3',
    name: '受理登记',
    module: '审理台账',
    route: '/trials',
    actions: ['对照完整度结论作出受理决定', '受理意见与理由留痕', '退回补证的登记退回原因'],
    handoff: '受理后进入阅卷审查',
  },
  {
    no: '4',
    name: '阅卷与证据校验',
    module: '审理台账',
    route: '/trials',
    actions: ['阅卷摘要：被审查人/案由/主要问题自动归纳', '证据链闭环校验：事实-证据对应、多源印证、缺口提示', '物理证据与笔录登记核对'],
    handoff: '证据存疑的退回复核，无异议进入程序校验',
  },
  {
    no: '5',
    name: '程序合规校验',
    module: '审理台账',
    route: '/trials',
    actions: ['初核/立案审批手续核对', '办案期限核对（超期提示）', '涉案财物、见面核对等程序节点逐项列示'],
    handoff: '合规结论须人工逐项确认后进入量纪',
  },
  {
    no: '6',
    name: '量纪参考',
    module: '审理台账',
    route: '/trials',
    actions: ['处分依据：法规库条款级检索，精确到条', '类案参考：历史审理报告问题片段检索，悬停可见原文', '量纪档次建议仅作参考，决定人工作出'],
    handoff: '量纪意见随报告一并提交',
  },
  {
    no: '7',
    name: '报告生成',
    module: '审理台账',
    route: '/trials',
    actions: ['选用文书模板渲染，缺字段标〔待补充〕不编造', '或按默认结构 AI 起草（不增加事实）', '类案引用附于文末并逐条留痕可查'],
    handoff: '初稿人工修改后定稿',
  },
  {
    no: '8',
    name: '定稿·谈话·执行',
    module: '审理台账',
    route: '/trials',
    actions: ['报告定稿，版本留痕', '见面谈话：核对事实、听取申辩、记录态度', '涉案财物处置意见 · 退回复核 · 执行台账'],
    handoff: '审理结论提交集体审议',
  },
]

/** 横向支撑层：被审理各环节复用（只列审理场景真实用到的） */
export const SUPPORT_MODULES: { name: string; route: string; what: string }[] = [
  { name: 'OCR 材料识别', route: '/ocr', what: '送审扫描件/手写件转文字，带坐标' },
  { name: '谈话录音转录', route: '/transcripts', what: '谈话录音转写，音文对照' },
  { name: '法规智能问答', route: '/qa', what: '处分依据条款级检索 + 溯源' },
  { name: '历史报告库', route: '/historical-reports', what: '历史审理/审计报告导入、类案检索' },
  { name: '廉政档案画像', route: '/integrity', what: '被审查人人物信息参考' },
  { name: '审理文书', route: '/documents', what: '模板库：上传 Word 解析字段、渲染、定稿' },
  { name: '文书校验', route: '/verify', what: '错别字/标点/排版校对' },
]

/** 分模块流程明细（与真实端点对应） */
export const MODULE_GROUPS: { group: string; flows: ModuleFlow[] }[] = [
  {
    group: '审理主线',
    flows: [
      {
        name: '审理台账',
        route: '/trials',
        goal: '从独立建档到审理执行的全链条管理',
        steps: [
          { label: '建档立项', by: 'human', note: '导入材料识别回填或手工录入；case_id 可选' },
          { label: '受理登记', by: 'human', note: '对照材料完整度作出受理/退回决定' },
          { label: '阅卷摘要', by: 'ai', note: '自动归纳被审查人/案由/主要问题' },
          { label: '证据链校验', by: 'ai', note: '事实-证据对应、多源印证、缺口提示' },
          { label: '程序合规校验', by: 'ai', note: '只提示风险，结论须人工逐项确认' },
          { label: '量纪参考', by: 'ai', note: '处分依据条款检索 + 历史类案参考' },
          { label: '报告生成', by: 'ai', note: '套模板渲染或默认结构起草，可带类案引用' },
          { label: '定稿谈话执行', by: 'human', note: '定稿留痕、见面谈话、财物处置、退补' },
        ],
      },
      {
        name: '送审材料汇聚',
        route: '/ctas-materials',
        goal: '审查调查组送审材料的登记、OCR 与完整度检查',
        steps: [
          { label: '材料登记', by: 'human', note: '按必备清单逐项登记，卷宗页码/原件标注' },
          { label: '扫描件上传', by: 'human', note: '批量上传，OCR 自动识别落库' },
          { label: '完整度检查', by: 'auto', note: '对照必备清单算齐套率，缺口列明' },
          { label: '受理判断', by: 'human', note: '齐全才受理，不齐退回补送' },
        ],
      },
      {
        name: '历史报告库',
        route: '/historical-reports',
        goal: '历史审理/审计报告导入、结构化与类案检索',
        steps: [
          { label: '批量导入', by: 'ai', note: 'Word/PDF 拖入，自动识别报告类型/日期/地区/定性' },
          { label: '问题片段结构化', by: 'auto', note: '拆出问题条款、金额、严重程度' },
          { label: '多维统计下钻', by: 'auto', note: '点图表任意维度，钻取对应报告明细' },
          { label: '类案检索引用', by: 'ai', note: '审理量纪时检索参考，引用逐条留痕' },
        ],
      },
    ],
  },
  {
    group: '审理工具',
    flows: [
      {
        name: 'OCR 材料识别',
        route: '/ocr',
        goal: '送审扫描件、手写件转可编辑文字',
        steps: [
          { label: '上传图片/扫描件', by: 'human' },
          { label: '识别增强', by: 'ai', note: '预处理 + 手写增强，返回文字与坐标' },
          { label: '校对与导出', by: 'human', note: '对照原图校对，送入材料库' },
        ],
      },
      {
        name: '谈话录音转录',
        route: '/transcripts',
        goal: '谈话录音转写与音文对照',
        steps: [
          { label: '上传录音', by: 'human' },
          { label: '智能转写', by: 'ai', note: 'VAD 切分、标点恢复、说话人分离' },
          { label: '音文对照校对', by: 'human', note: '点击文字定位音频' },
        ],
      },
      {
        name: '法规智能问答',
        route: '/qa',
        goal: '处分依据的条款级检索问答',
        steps: [
          { label: '提问', by: 'human' },
          { label: '法规检索', by: 'auto', note: '离线向量检索命中条款，答案附条款溯源' },
          { label: '答案复核', by: 'human', note: '引用条款可悬停查看原文' },
        ],
      },
      {
        name: '审理文书',
        route: '/documents',
        goal: '审理报告等文书的模板生成与定稿',
        steps: [
          { label: '选择/上传模板', by: 'human', note: '上传 Word 自动解析 {{字段}}' },
          { label: '要素填充', by: 'human', note: '关键要素表单化录入' },
          { label: '模板生成', by: 'auto', note: '缺字段标〔待补充〕，不编造' },
          { label: '定稿留痕', by: 'human', note: '版本管理，全程可溯' },
        ],
      },
      {
        name: '文书校验',
        route: '/verify',
        goal: '审理文书错别字与公文排版校对',
        steps: [
          { label: '上传/粘贴文书', by: 'human' },
          { label: '校对检查', by: 'ai', note: '错别字/标点/公文排版合规' },
          { label: '修正与导出', by: 'human', note: '逐条确认或一键修正' },
        ],
      },
    ],
  },
  {
    group: '参考与管理',
    flows: [
      {
        name: '法规库',
        route: '/regulations',
        goal: '法规条款的结构化建库与检索',
        steps: [
          { label: '粘贴/导入文本', by: 'human' },
          { label: '自动拆条建库', by: 'auto', note: '按条款拆分，离线建索引' },
          { label: '检索与溯源', by: 'auto', note: '全文检索，问答与量纪引用来源' },
        ],
      },
      {
        name: '廉政档案画像',
        route: '/integrity',
        goal: '被审查人人物信息与关系参考',
        steps: [
          { label: '人物建档', by: 'human' },
          { label: '关系网络录入', by: 'human', note: '社会关系/监督足迹结构化' },
          { label: '多维画像', by: 'ai', note: '自动聚合并标注风险节点' },
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
