// 模型工厂 · 三期模板库
// 内置权威监督模型模板（对齐 backend/app/pipeline.py 的六类算子 schema）；
// 用户自建模板存 localStorage。模板节点与画布 PNode 同构（{id,type,x,y,params}），
// 套用即把骨架注入画布，dataset_id 由 hint_dataset 自动匹配、字段预填建议列名。

export interface TplNode { id: string; type: string; x: number; y: number; params: Record<string, any> }
export interface TplEdge { from: string; to: string }
export interface PipelineTemplate {
  id: string
  name: string
  scene: string      // 适用监督场景
  level: string      // 建议预警级别
  nodes: TplNode[]
  edges: TplEdge[]
}

// ---------------- 内置权威模板 ----------------
// 字段用常见中文列名占位，用户选定数据集后若含同名列则自动命中；
// hint_dataset / hint_dataset_b 仅用于套用时自动匹配 dataset_id（匹配后即从 params 剔除）。
export const BUILTIN_TEMPLATES: PipelineTemplate[] = [
  {
    id: 'tpl_subsidy_personnel',
    name: '公职人员违规领取补贴',
    scene: '公职人员名册与惠民补贴发放明细对碰，发现在编人员违规领取补贴',
    level: '中',
    nodes: [
      { id: 'src1', type: 'source', x: 40, y: 60, params: { name: '公职人员名册', hint_dataset: '公职人员' } },
      { id: 'join1', type: 'join', x: 300, y: 60, params: { name: '对碰：名册×补贴发放', rule: 'intersection', key_a: '身份证号', key_b: '身份证号', hint_dataset_b: '补贴' } },
      { id: 'out1', type: 'output', x: 560, y: 60, params: { key_field: '身份证号' } },
    ],
    edges: [{ from: 'src1', to: 'join1' }, { from: 'join1', to: 'out1' }],
  },
  {
    id: 'tpl_subsidy_duplicate',
    name: '重复/死亡冒领补贴',
    scene: '同一身份证号在补贴发放中多次出现，疑似重复或死亡冒领',
    level: '高',
    nodes: [
      { id: 'src1', type: 'source', x: 40, y: 120, params: { name: '补贴发放明细', hint_dataset: '补贴' } },
      { id: 'agg1', type: 'aggregate', x: 300, y: 120, params: { group_by: '身份证号', agg: 'count' } },
      { id: 'th1', type: 'threshold', x: 560, y: 120, params: { field: 'agg_result', op: 'gt', value: 1 } },
      { id: 'out1', type: 'output', x: 820, y: 120, params: { key_field: '身份证号' } },
    ],
    edges: [{ from: 'src1', to: 'agg1' }, { from: 'agg1', to: 'th1' }, { from: 'th1', to: 'out1' }],
  },
  {
    id: 'tpl_fund_reflux',
    name: '资金回流（利益输送）',
    scene: '银行流水付款方与特定关系人账户对碰，发现资金回流式利益输送',
    level: '高',
    nodes: [
      { id: 'src1', type: 'source', x: 40, y: 80, params: { name: '银行账户流水', hint_dataset: '流水' } },
      { id: 'flt1', type: 'filter', x: 300, y: 80, params: { field: '金额', op: 'gt', value: '50000' } },
      { id: 'join1', type: 'join', x: 560, y: 80, params: { name: '对碰：付款方×关系人账户', rule: 'intersection', key_a: '付款方账号', key_b: '账号', hint_dataset_b: '关系人' } },
      { id: 'out1', type: 'output', x: 820, y: 80, params: { key_field: '付款方账号' } },
    ],
    edges: [{ from: 'src1', to: 'flt1' }, { from: 'flt1', to: 'join1' }, { from: 'join1', to: 'out1' }],
  },
  {
    id: 'tpl_bid_rigging',
    name: '围标串标（同一人控多家）',
    scene: '招投标信息中同一联系人/手机号控制多家公司，疑似围标串标',
    level: '中',
    nodes: [
      { id: 'src1', type: 'source', x: 40, y: 80, params: { name: '招投标中标信息', hint_dataset: '招标' } },
      { id: 'join1', type: 'join', x: 300, y: 80, params: { name: '同库重复：联系方式', rule: 'duplicate', key_a: '联系电话' } },
      { id: 'out1', type: 'output', x: 560, y: 80, params: { key_field: '联系电话' } },
    ],
    edges: [{ from: 'src1', to: 'join1' }, { from: 'join1', to: 'out1' }],
  },
  {
    id: 'tpl_large_amount',
    name: '大额资金异动汇总',
    scene: '筛选大额流水并按账户汇总，超出阈值的账户列入预警',
    level: '低',
    nodes: [
      { id: 'src1', type: 'source', x: 40, y: 120, params: { name: '银行账户流水', hint_dataset: '流水' } },
      { id: 'flt1', type: 'filter', x: 300, y: 120, params: { field: '金额', op: 'gt', value: '100000' } },
      { id: 'agg1', type: 'aggregate', x: 560, y: 120, params: { group_by: '账号', agg: 'sum', agg_field: '金额' } },
      { id: 'th1', type: 'threshold', x: 820, y: 120, params: { field: 'agg_result', op: 'gt', value: 500000 } },
      { id: 'out1', type: 'output', x: 1080, y: 120, params: { key_field: '账号' } },
    ],
    edges: [{ from: 'src1', to: 'flt1' }, { from: 'flt1', to: 'agg1' }, { from: 'agg1', to: 'th1' }, { from: 'th1', to: 'out1' }],
  },
]

// ---------------- 用户自建模板（localStorage）----------------
const LS_KEY = 'zqjw_pipeline_templates'

export function loadUserTemplates(): PipelineTemplate[] {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr : []
  } catch { return [] }
}

export function saveUserTemplate(tpl: PipelineTemplate) {
  const arr = loadUserTemplates()
  const idx = arr.findIndex((t) => t.id === tpl.id)
  if (idx >= 0) arr[idx] = tpl
  else arr.push(tpl)
  localStorage.setItem(LS_KEY, JSON.stringify(arr))
}

export function deleteUserTemplate(id: string) {
  const arr = loadUserTemplates().filter((t) => t.id !== id)
  localStorage.setItem(LS_KEY, JSON.stringify(arr))
}
