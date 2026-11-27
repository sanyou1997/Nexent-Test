import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Button, Select, Input, InputNumber, Tag, message, Alert, Drawer, Empty, Space, Tooltip,
} from 'antd'
import {
  PlusOutlined, PlayCircleOutlined, SaveOutlined, ClearOutlined, ExperimentOutlined, BookOutlined,
  DatabaseOutlined, FilterOutlined, NodeIndexOutlined, GroupOutlined, SlidersOutlined, AlertOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { BUILTIN_TEMPLATES, loadUserTemplates, saveUserTemplate, deleteUserTemplate, type PipelineTemplate } from './pipelineTemplates'
import './pipeline-studio.css'

// ---------------- 算子元数据（对齐 backend/app/pipeline.py NODE_TYPES）----------------
const NODE_META: Record<string, { label: string; inputs: number; outputs: number; color: string; icon: any; desc: string }> = {
  source: { label: '数据源', inputs: 0, outputs: 1, color: '#1d4ed8', icon: DatabaseOutlined, desc: '选择已接入的数据集（唯一起点）' },
  filter: { label: '过滤', inputs: 1, outputs: 1, color: '#15803d', icon: FilterOutlined, desc: '按字段条件筛选行' },
  join: { label: '对碰关联', inputs: 2, outputs: 1, color: '#4f46e5', icon: NodeIndexOutlined, desc: '两库交集 / 同库重复 / 包含命中（可接两个上游）' },
  aggregate: { label: '聚合分组', inputs: 1, outputs: 1, color: '#0e7490', icon: GroupOutlined, desc: '按关键字段分组聚合数值' },
  threshold: { label: '阈值判断', inputs: 1, outputs: 1, color: '#b45309', icon: SlidersOutlined, desc: '聚合值与常量比较，保留超阈值行' },
  output: { label: '输出预警', inputs: 1, outputs: 0, color: '#b91c1c', icon: AlertOutlined, desc: '生成预警（唯一终点）' },
}
const NODE_ORDER = ['source', 'filter', 'join', 'aggregate', 'threshold', 'output']
const FILTER_OPS = [
  { value: 'eq', label: '等于 =' }, { value: 'ne', label: '不等于 ≠' },
  { value: 'gt', label: '大于 >' }, { value: 'ge', label: '大于等于 ≥' },
  { value: 'lt', label: '小于 <' }, { value: 'le', label: '小于等于 ≤' },
  { value: 'contains', label: '包含' }, { value: 'not_null', label: '非空' }, { value: 'empty', label: '为空' },
]
const JOIN_RULES = [
  { value: 'intersection', label: '两库交集（同对象出现在两个数据集）' },
  { value: 'duplicate', label: '同库重复（单数据集内重复出现）' },
  { value: 'inclusion', label: '包含命中（A 对象出现在 B 的某字段中）' },
]
const AGG_FNS = [
  { value: 'count', label: '计数 count' }, { value: 'sum', label: '求和 sum' },
  { value: 'avg', label: '平均 avg' }, { value: 'max', label: '最大 max' }, { value: 'min', label: '最小 min' },
]
const TH_OPS = [
  { value: 'gt', label: '大于 >' }, { value: 'ge', label: '大于等于 ≥' },
  { value: 'lt', label: '小于 <' }, { value: 'le', label: '小于等于 ≤' }, { value: 'eq', label: '等于 =' },
]
const SEVERITY_OPTS = ['高', '中', '低', '提示'].map((x) => ({ value: x, label: x }))

const NODE_W = 196
const PORT_Y = 26

// 输入端口纵向位置（按输入数量分布；join=2 时 A 在上、B 在下）
function inputPortPositions(inputs: number): number[] {
  if (inputs <= 1) return [PORT_Y - 7]
  return [PORT_Y - 14, PORT_Y + 16]
}

interface PNode { id: string; type: string; x: number; y: number; params: Record<string, any> }
interface PEdge { from: string; to: string; to_port?: number }

function defaultParams(type: string): Record<string, any> {
  switch (type) {
    case 'source': return { dataset_id: undefined }
    case 'filter': return { field: undefined, op: 'eq', value: '' }
    case 'join': return { rule: 'intersection', key_a: undefined, key_b: undefined, dataset_b_id: undefined, field_b: undefined }
    case 'aggregate': return { group_by: undefined, agg: 'count', agg_field: undefined }
    case 'threshold': return { field: 'agg_result', op: 'gt', value: 0 }
    case 'output': return { key_field: undefined }
    default: return {}
  }
}

function genId(type: string) {
  return `${type}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`
}

// ---------------- 前端轻量校验（mirror 后端 validate_pipeline）----------------
function validatePipeline(nodes: PNode[], edges: PEdge[]): string[] {
  const errs: string[] = []
  if (!nodes.length) return ['画布为空：请至少添加 1 个数据源和 1 个输出节点']
  const byId: Record<string, PNode> = {}
  nodes.forEach((n) => { byId[n.id] = n })
  const sources = nodes.filter((n) => n.type === 'source')
  const outputs = nodes.filter((n) => n.type === 'output')
  if (sources.length < 1) errs.push(`至少需要 1 个数据源节点（当前 ${sources.length} 个）`)
  if (outputs.length !== 1) errs.push(`输出节点必须唯一（当前 ${outputs.length} 个）`)
  const indeg: Record<string, number> = {}, outdeg: Record<string, number> = {}
  nodes.forEach((n) => { indeg[n.id] = 0; outdeg[n.id] = 0 })
  edges.forEach((e) => { if (byId[e.from] && byId[e.to]) { outdeg[e.from]++; indeg[e.to]++ } })
  const deg = { ...indeg }
  const q = Object.keys(deg).filter((k) => deg[k] === 0)
  let seen = 0; const queue = [...q]
  while (queue.length) {
    const c = queue.shift() as string; seen++
    edges.forEach((e) => { if (e.from === c) { deg[e.to]--; if (deg[e.to] === 0) queue.push(e.to) } })
  }
  if (seen !== nodes.length) errs.push('画布存在环，请重新连接')
  nodes.forEach((n) => {
    const t = n.type
    if (t === 'source' && outdeg[n.id] === 0) errs.push('数据源「' + (n.params.name || n.id) + '」还没有连接到下游')
    if (t === 'output' && indeg[n.id] === 0) errs.push('输出节点还没有接入上游')
    if (t !== 'source' && t !== 'output' && (indeg[n.id] === 0 || outdeg[n.id] === 0)) errs.push(`算子「${NODE_META[t].label}」(${n.id}) 连接不完整`)
  })
  nodes.forEach((n) => {
    const p = n.params
    if (n.type === 'source' && !p.dataset_id) errs.push('数据源节点未选择数据集')
    if (n.type === 'filter' && (!p.field || !p.op)) errs.push('过滤节点：字段或条件不完整')
    if (n.type === 'join') {
      if (!p.rule || !p.key_a) errs.push('对碰节点：规则或关键字段不完整')
      const jIn = indeg[n.id] || 0
      if (jIn > 2) errs.push('对碰节点：最多 2 个输入')
      else if (jIn === 2) {
        if (p.rule === 'intersection' && !p.key_b) errs.push('对碰节点：交集模式需指定 B 侧关键字段 key_b')
        if (p.rule === 'inclusion' && !p.field_b) errs.push('对碰节点：包含命中需指定 B 侧字段 field_b')
      } else {
        if ((p.rule === 'intersection' || p.rule === 'inclusion') && !p.dataset_b_id) errs.push('对碰节点：仅接 1 个上游时需选择第二数据集')
        if (p.rule === 'inclusion' && !p.field_b) errs.push('对碰节点：包含命中需指定 B 侧字段 field_b')
      }
    }
    if (n.type === 'aggregate') {
      if (!p.group_by) errs.push('聚合节点：未选择分组字段')
      if (p.agg !== 'count' && !p.agg_field) errs.push('聚合节点：非 count 聚合需选择数值字段')
    }
    if (n.type === 'threshold' && (!p.op || p.value === '' || p.value == null)) errs.push('阈值节点：比较符或阈值不完整')
    if (n.type === 'output' && !p.key_field) errs.push('输出节点：未选择关键字段')
  })
  return errs
}

function compilePipeline(nodes: PNode[], edges: PEdge[]) {
  return {
    nodes: nodes.map((n) => ({ id: n.id, type: n.type, name: n.params.name || NODE_META[n.type].label, ...n.params })),
    edges: edges.map((e) => ({ from: e.from, to: e.to, to_port: e.to_port || 0 })),
  }
}

function bezier(p1: { x: number; y: number }, p2: { x: number; y: number }) {
  const dx = Math.max(40, Math.abs(p2.x - p1.x) / 2)
  return `M ${p1.x} ${p1.y} C ${p1.x + dx} ${p1.y}, ${p2.x - dx} ${p2.y}, ${p2.x} ${p2.y}`
}

function nodeSummary(n: PNode, dsName: (id?: number) => string): string {
  const p = n.params
  switch (n.type) {
    case 'source': return p.dataset_id ? dsName(p.dataset_id) : '未选数据集'
    case 'filter': return p.field ? `${p.field} ${p.op} ${p.value ?? ''}` : '未设条件'
    case 'join': {
      const r = JOIN_RULES.find((x) => x.value === p.rule)?.label.split('（')[0] || p.rule
      return `${r} · 键:${p.key_a || '?'}` + (p.dataset_b_id ? ` × ${dsName(p.dataset_b_id)}` : '')
    }
    case 'aggregate': return `${p.group_by || '?'} ${p.agg}` + (p.agg !== 'count' ? `/${p.agg_field || '?'}` : '')
    case 'threshold': return `${p.field || 'agg_result'} ${p.op} ${p.value}`
    case 'output': return `键:${p.key_field || '?'}`
    default: return ''
  }
}

// ---------------- 组件 ----------------
export default function PipelineStudio({ datasets }: { datasets: any[] }) {
  const [nodes, setNodes] = useState<PNode[]>([])
  const [edges, setEdges] = useState<PEdge[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [linking, setLinking] = useState<{ from: string; mx: number; my: number } | null>(null)
  const [drag, setDrag] = useState<{ id: string; ox: number; oy: number; px: number; py: number } | null>(null)
  const [modelName, setModelName] = useState('')
  const [severity, setSeverity] = useState('中')
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [dryResult, setDryResult] = useState<any>(null)
  const [lastKey, setLastKey] = useState('')
  const [tplOpen, setTplOpen] = useState(false)
  const [userTpls, setUserTpls] = useState<PipelineTemplate[]>([])
  const containerRef = useRef<HTMLDivElement>(null)

  const dsName = (id?: number) => datasets.find((d) => d.id === id)?.name || ''
  const dsCols = (id?: number) => datasets.find((d) => d.id === id)?.columns || []
  const mainCols = useMemo(() => {
    const cols = new Set<string>()
    nodes.filter((n) => n.type === 'source' && n.params.dataset_id)
      .forEach((n) => dsCols(n.params.dataset_id).forEach((c: string) => cols.add(c)))
    return [...cols]
  }, [nodes, datasets])

  const byId: Record<string, PNode> = {}
  nodes.forEach((n) => { byId[n.id] = n })

  const errs = useMemo(() => validatePipeline(nodes, edges), [nodes, edges])

  // 全局拖拽 / 连线
  useEffect(() => {
    const rectXY = (e: PointerEvent) => {
      const rect = containerRef.current!.getBoundingClientRect()
      return { x: e.clientX - rect.left, y: e.clientY - rect.top }
    }
    const move = (e: PointerEvent) => {
      const { x, y } = rectXY(e)
      if (drag) {
        setNodes((ns) => ns.map((n) => n.id === drag.id
          ? { ...n, x: Math.max(0, drag.ox + (e.clientX - drag.px)), y: Math.max(0, drag.oy + (e.clientY - drag.py)) }
          : n))
      }
      if (linking) setLinking((l) => (l ? { ...l, mx: x, my: y } : l))
    }
    const up = (e: PointerEvent) => {
      if (linking) {
        const { x, y } = rectXY(e)
        // 命中输入端口？
        let target: string | null = null; let targetPort = 0; let best = 20
    nodes.forEach((n) => {
      const ins = NODE_META[n.type].inputs
      if (ins === 0) return
      inputPortPositions(ins).forEach((off, i) => {
        const px = n.x, py = n.y + off
        const d = Math.hypot(px - x, py - y)
        if (d < best) { best = d; target = n.id; targetPort = i }
      })
    })
        if (target && target !== linking.from && !edges.some((ed) => ed.from === linking.from && ed.to === target && (ed.to_port || 0) === targetPort)
          && NODE_META[byId[linking.from].type].outputs > 0) {
          setEdges((es) => [...es, { from: linking.from, to: target as string, to_port: targetPort }])
        }
        setLinking(null)
      }
      setDrag(null)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
  }, [drag, linking, nodes, edges])

  const addNode = (type: string) => {
    const id = genId(type)
    const n = nodes.length
    setNodes((ns) => [...ns, {
      id, type, x: 40 + (n % 4) * 64, y: 30 + Math.floor(n / 4) * 30 + n * 8,
      params: { ...defaultParams(type) },
    }])
    setSel(id)
  }
  const delNode = (id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id))
    setEdges((es) => es.filter((e) => e.from !== id && e.to !== id))
    if (sel === id) setSel(null)
  }
  const updateNode = (id: string, patch: Record<string, any>) => {
    setNodes((ns) => ns.map((n) => n.id === id ? { ...n, params: { ...n.params, ...patch } } : n))
  }
  const delEdge = (from: string, to: string, to_port = 0) => {
    setEdges((es) => es.filter((e) => !(e.from === from && e.to === to && (e.to_port || 0) === to_port)))
  }
  const clearAll = () => {
    if (nodes.length === 0) return
    if (!window.confirm('确定清空整个画布？')) return
    setNodes([]); setEdges([]); setSel(null)
  }

  const startDrag = (n: PNode, e: React.PointerEvent) => {
    e.stopPropagation()
    setSel(n.id)
    setDrag({ id: n.id, ox: n.x, oy: n.y, px: e.clientX, py: e.clientY })
  }
  const startLink = (n: PNode, e: React.PointerEvent) => {
    e.stopPropagation()
    const rect = containerRef.current!.getBoundingClientRect()
    setLinking({ from: n.id, mx: e.clientX - rect.left, my: e.clientY - rect.top })
  }

  const compiled = () => compilePipeline(nodes, edges)

  const doDryRun = async () => {
    if (errs.length) { message.error('拓扑校验未通过：' + errs[0]); return }
    setRunning(true)
    try {
      const { data } = await api.post('/supervise/models/build-pipeline', {
        name: modelName || '未命名管线模型', severity, note: '', pipeline: compiled(), dry_run: true,
      })
      setDryResult({ ...data, dry_run: true })
      message.success(`干跑完成，命中 ${data.total_hits} 条`)
    } catch (e: any) { message.error(errMsg(e)) } finally { setRunning(false) }
  }
  const doSave = async () => {
    if (errs.length) { message.error('拓扑校验未通过：' + errs[0]); return }
    setSaving(true)
    try {
      const { data } = await api.post('/supervise/models/build-pipeline', {
        name: modelName || '未命名管线模型', severity, note: '', pipeline: compiled(), dry_run: false,
      })
      setLastKey(data.key)
      message.success(`模型「${data.name}」已保存至模型仓库`)
    } catch (e: any) { message.error(errMsg(e)) } finally { setSaving(false) }
  }
  const doRun = async () => {
    if (!lastKey) { message.warning('请先保存模型，再运行'); return }
    setRunning(true)
    try {
      const { data } = await api.post(`/supervise/models/${lastKey}/run`)
      setDryResult({ ...data, dry_run: false })
      message.success(`运行完成：命中 ${data.total_hits} 条，写入预警 ${data.upserted} 条`)
    } catch (e: any) { message.error(errMsg(e)) } finally { setRunning(false) }
  }

  // ---------------- 模板库：套用 / 存为 ----------------
  const applyTemplate = (tpl: PipelineTemplate) => {
    if (nodes.length && !window.confirm('套用模板将覆盖当前画布，确定继续？')) return
    const newNodes = tpl.nodes.map((n) => {
      const p: Record<string, any> = { ...n.params }
      if (p.hint_dataset) {
        const m = datasets.find((d) => d.name && d.name.includes(p.hint_dataset))
        if (m) p.dataset_id = m.id
        delete p.hint_dataset
      }
      if (p.hint_dataset_b) {
        const m = datasets.find((d) => d.name && d.name.includes(p.hint_dataset_b))
        if (m) p.dataset_b_id = m.id
        delete p.hint_dataset_b
      }
      return { id: n.id, type: n.type, x: n.x, y: n.y, params: p }
    })
    setModelName(tpl.name)
    setNodes(newNodes)
    setEdges(tpl.edges.map((e) => ({ from: e.from, to: e.to, to_port: (e as any).to_port || 0 })))
    setSel(null)
    setTplOpen(false)
    message.success(`已套用模板「${tpl.name}」，请为数据源选择数据集`)
  }

  const saveAsTemplate = () => {
    if (errs.length) { message.error('当前画布校验未通过，无法存为模板：' + errs[0]); return }
    const name = window.prompt('模板名称', modelName || '我的模型')
    if (!name) return
    const scene = window.prompt('适用场景（可选）', '') || ''
    const ppl = compiled()
    const tpl: PipelineTemplate = {
      id: 'user_' + Date.now().toString(36),
      name,
      scene,
      level: severity,
      nodes: nodes.map((n) => ({
        id: n.id, type: n.type, x: n.x, y: n.y,
        params: { ...n.params, name: n.params.name || NODE_META[n.type].label },
      })),
      edges: ppl.edges.map((e) => ({ from: e.from, to: e.to })),
    }
    saveUserTemplate(tpl)
    setUserTpls(loadUserTemplates())
    message.success(`已存为模板「${name}」`)
  }

  const openTpl = () => { setUserTpls(loadUserTemplates()); setTplOpen(true) }

  const selNode = sel ? byId[sel] : null

  return (
    <div className="ps-root">
      {/* 顶部工具栏 */}
      <div className="ps-toolbar">
        <div className="ps-tb-group">
          <span className="ps-tb-icon"><ExperimentOutlined /></span>
          <Input placeholder="模型名称（保存时必填）" style={{ width: 220 }} value={modelName} onChange={(e) => setModelName(e.target.value)} />
          <Select style={{ width: 120 }} value={severity} onChange={setSeverity} options={SEVERITY_OPTS} />
        </div>
        <div className="ps-tb-spacer" />
        <div className="ps-tb-group">
          <Button icon={<BookOutlined />} onClick={openTpl}>从模板开始</Button>
          <Button icon={<SaveOutlined />} onClick={saveAsTemplate} disabled={!nodes.length}>存为模板</Button>
          <Tooltip title="清空画布上的全部节点与连线">
            <Button icon={<ClearOutlined />} onClick={clearAll} disabled={!nodes.length}>清空</Button>
          </Tooltip>
          <span className="ps-tb-divider" />
          <Tooltip title="按当前画布试算命中，不保存、不生成预警">
            <Button icon={<PlayCircleOutlined />} loading={running} onClick={doDryRun} disabled={!!errs.length}>干跑预览</Button>
          </Tooltip>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={doSave} disabled={!!errs.length}>保存模型</Button>
          <Tooltip title={lastKey ? '正式运行：命中结果写入预警中枢' : '先保存模型，才能正式运行'}>
            <Button icon={<PlayCircleOutlined />} disabled={!lastKey} loading={running} onClick={doRun}>运行</Button>
          </Tooltip>
        </div>
      </div>
      <div className={`ps-status ${!nodes.length ? 'ps-status--idle' : errs.length ? 'ps-status--err' : 'ps-status--ok'}`}>
        <span className="ps-status-dot" />
        {!nodes.length
          ? '画布为空：从左侧添加「数据源」开始，或点「从模板开始」载入一个现成模型'
          : errs.length
            ? `待完善：${errs[0]}${errs.length > 1 ? `（另有 ${errs.length - 1} 项）` : ''}`
            : '拓扑校验通过，可以干跑预览或保存'}
      </div>

      <div className="ps-main">
        {/* 左：算子面板 */}
        <div className="ps-palette">
          <div className="ps-palette-title">算子 <small>点击添加</small></div>
          {NODE_ORDER.map((t) => {
            const Icon = NODE_META[t].icon
            const c = NODE_META[t].color
            return (
              <div key={t} className="ps-palette-item" style={{ borderLeftColor: c }} onClick={() => addNode(t)}>
                <span className="ps-pi-icon" style={{ color: c, background: `${c}14` }}><Icon /></span>
                <div className="ps-pi-text">
                  <div className="ps-pi-label">{NODE_META[t].label}</div>
                  <div className="ps-pi-desc">{NODE_META[t].desc}</div>
                </div>
                <PlusOutlined className="ps-pi-add" />
              </div>
            )
          })}
          <div className="ps-help">
            <b>连线：</b>从节点右侧蓝点拖到下一个节点左侧圆点；点连线可删除。<br />
            <b>参数：</b>选中节点后在右侧编辑，字段下拉自动取数据源的列。
          </div>
        </div>

        {/* 中：画布 */}
        <div className="ps-canvas" ref={containerRef} onClick={() => setSel(null)}>
          <svg className="ps-svg">
            {edges.map((e) => {
              const a = byId[e.from], b = byId[e.to]
              if (!a || !b) return null
              const p1 = { x: a.x + NODE_W, y: a.y + PORT_Y }
              const p2 = { x: b.x, y: b.y + PORT_Y }
              return (
                <g key={e.from + '>' + e.to}>
                  <path className="ps-edge-hit" d={bezier(p1, p2)} onClick={(ev) => { ev.stopPropagation(); delEdge(e.from, e.to) }} />
                  <path className="ps-edge" d={bezier(p1, p2)} />
                </g>
              )
            })}
            {linking && byId[linking.from] && (
              <path className="ps-edge-temp"
                d={bezier({ x: byId[linking.from].x + NODE_W, y: byId[linking.from].y + PORT_Y }, { x: linking.mx, y: linking.my })} />
            )}
          </svg>

          {nodes.map((n) => {
            const Icon = NODE_META[n.type].icon
            return (
              <div key={n.id} className={`ps-node${sel === n.id ? ' ps-node--sel' : ''}`}
                style={{ left: n.x, top: n.y, width: NODE_W }}
                onPointerDown={(e) => startDrag(n, e)} onClick={(e) => { e.stopPropagation(); setSel(n.id) }}>
                <div className="ps-node-head" style={{ borderTopColor: NODE_META[n.type].color }}>
                  <Icon style={{ color: NODE_META[n.type].color }} /> <span>{NODE_META[n.type].label}</span>
                  <span className="ps-node-del" onClick={(e) => { e.stopPropagation(); delNode(n.id) }}>×</span>
                </div>
                <div className="ps-node-body">{nodeSummary(n, dsName)}</div>
                {NODE_META[n.type].inputs > 0 && inputPortPositions(NODE_META[n.type].inputs).map((off, i) => (
                  <div key={i} className={`ps-port ps-port-in${i === 1 ? ' ps-port-in-b' : ''}`} style={{ top: off }} />
                ))}
                {NODE_META[n.type].outputs > 0 &&
                  <div className="ps-port ps-port-out" style={{ top: PORT_Y - 7 }} onPointerDown={(e) => startLink(n, e)} />}
              </div>
            )
          })}

          {nodes.length === 0 && (
            <div className="ps-empty">
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={null} />
              <div className="ps-empty-title">从左侧添加算子开始建模</div>
              <div className="ps-empty-steps">数据源 → 过滤 / 对碰关联 / 聚合分组 → 阈值判断 → 输出预警<br />也可以点上方「从模板开始」载入现成模型</div>
            </div>
          )}
        </div>

        {/* 右：属性面板 */}
        <div className="ps-props">
          <div className="ps-props-title">节点参数 {selNode && <small>{NODE_META[selNode.type].label}</small>}</div>
          {!selNode && <div className="ps-props-hint">在画布上选中一个节点<br />即可在这里编辑它的参数</div>}
          {selNode && (
            <NodeProps node={selNode} datasets={datasets} mainCols={mainCols}
              inCount={edges.filter((e) => e.to === selNode.id).length}
              dsCols={dsCols} onChange={(patch: any) => updateNode(selNode.id, patch)} onDelete={() => delNode(selNode.id)} />
          )}
        </div>
      </div>

      {/* 干跑 / 运行结果抽屉 */}
      <Drawer title={dryResult ? `执行结果 · ${dryResult.model}` : ''} open={!!dryResult} width={640}
        onClose={() => setDryResult(null)}>
        {dryResult && (
          <>
            <div className="ps-res-kpis">
              <div><div className="ps-res-kpi-label">命中条数</div><div className="ps-res-kpi-value">{dryResult.total_hits}</div></div>
              {dryResult.dry_run === false && <div><div className="ps-res-kpi-label">写入预警</div><div className="ps-res-kpi-value">{dryResult.upserted}</div></div>}
              <div><div className="ps-res-kpi-label">模式</div><div className="ps-res-kpi-value" style={{ fontSize: 18 }}>{dryResult.dry_run ? '干跑（不落库）' : '正式运行'}</div></div>
            </div>
            {dryResult.steps?.length > 0 && (
              <>
                <div className="ps-res-title">执行步骤（逐步行数）</div>
                <div className="ps-steps">
                  {dryResult.steps.map((s: any, i: number) => (
                    <Tag key={i} color="blue">{s.label} · {s.rows} 行{s.hits != null ? ` / 命中 ${s.hits}` : ''}</Tag>
                  ))}
                </div>
              </>
            )}
            <div className="ps-res-title">命中样例</div>
            <div className="ps-sample">
              {(dryResult.sample || []).map((h: any, i: number) => (
                <div key={i} className="ps-sample-row"><b>{h.key}</b><span>{h.reason}</span></div>
              ))}
              {(dryResult.sample || []).length === 0 && <span style={{ color: '#64748b' }}>无命中</span>}
            </div>
          </>
        )}
      </Drawer>

      {/* 模板库抽屉 */}
      <Drawer title="模型模板库" open={tplOpen} width={560} onClose={() => setTplOpen(false)}>
        <div className="ps-tpl-sec-title">内置权威模板</div>
        {BUILTIN_TEMPLATES.map((t) => (
          <TplCard key={t.id} tpl={t} onApply={() => applyTemplate(t)} />
        ))}
        <div className="ps-tpl-sec-title">我的模板（本地保存）</div>
        {userTpls.length === 0
          ? <Empty description="暂无自建模板，编排好模型后点「存为模板」" />
          : userTpls.map((t) => (
            <TplCard key={t.id} tpl={t}
              onApply={() => applyTemplate(t)}
              onDelete={() => { deleteUserTemplate(t.id); setUserTpls(loadUserTemplates()); message.success('已删除模板') }} />
          ))}
      </Drawer>
    </div>
  )
}

// 模板卡片：算子流缩略（从 source 沿连线走到 output）
function flowLabels(tpl: PipelineTemplate): string[] {
  const byId: Record<string, any> = {}
  tpl.nodes.forEach((n) => { byId[n.id] = n })
  const adj: Record<string, string[]> = {}
  tpl.edges.forEach((e) => { (adj[e.from] = adj[e.from] || []).push(e.to) })
  const start = tpl.nodes.find((n) => n.type === 'source')?.id
  const out: string[] = []; const seen = new Set<string>(); let cur = start
  while (cur && !seen.has(cur)) {
    seen.add(cur)
    out.push(byId[cur].params?.name || NODE_META[byId[cur].type].label)
    cur = (adj[cur] || [])[0]
  }
  return out
}

function TplCard({ tpl, onApply, onDelete }: { tpl: PipelineTemplate; onApply: () => void; onDelete?: () => void }) {
  return (
    <div className="ps-tpl-card">
      <div className="ps-tpl-head">
        <div className="ps-tpl-name">{tpl.name}</div>
        <Tag color="warning">{tpl.level}</Tag>
      </div>
      <div className="ps-tpl-scene">{tpl.scene}</div>
      <div className="ps-tpl-flow">
        {flowLabels(tpl).map((lab, i) => (
          <span key={i} className="ps-tpl-flow-item">
            {i > 0 && <span className="ps-tpl-arrow">→</span>}
            <span className="ps-tpl-chip">{lab}</span>
          </span>
        ))}
      </div>
      <div className="ps-tpl-actions">
        <Button type="primary" size="small" onClick={onApply}>套用</Button>
        {onDelete && <Button size="small" danger onClick={onDelete}>删除</Button>}
      </div>
    </div>
  )
}

// ---------------- 属性面板（按算子类型渲染）----------------
function NodeProps({ node, datasets, mainCols, inCount, dsCols, onChange, onDelete }: any) {
  const t = node.type
  const p = node.params
  const dsOpts = datasets.map((d: any) => ({ label: `${d.name}（${d.row_count}行）`, value: d.id }))
  const colOpts = (cols: string[]) => cols.map((c) => ({ label: c, value: c }))
  // 对碰字段下拉：主源列并集 + 兜底数据集 B 的列
  const joinCols = [...new Set<string>([...(mainCols as string[]), ...(p.dataset_b_id ? dsCols(p.dataset_b_id) : [])])]
  return (
    <div className="ps-props-body">
      {t === 'source' && (
        <SelectField label="数据集" value={p.dataset_id} onChange={(v: any) => onChange({ dataset_id: v })} options={dsOpts} placeholder="选择已接入数据集" />
      )}
      {t === 'filter' && (
        <>
          <SelectField label="字段" value={p.field} onChange={(v: any) => onChange({ field: v })} options={colOpts(mainCols)} placeholder="选字段" />
          <SelectField label="条件" value={p.op} onChange={(v: any) => onChange({ op: v })} options={FILTER_OPS} />
          {p.op !== 'not_null' && p.op !== 'empty' && (
            <div className="ps-field"><label>值</label><Input value={p.value} onChange={(e) => onChange({ value: e.target.value })} placeholder="比较值" /></div>
          )}
        </>
      )}
      {t === 'join' && (
        <>
          <SelectField label="比对规则" value={p.rule} onChange={(v: any) => onChange({ rule: v, dataset_b_id: undefined, key_b: undefined, field_b: undefined })} options={JOIN_RULES} />
          <SelectField label="关键字段 A" value={p.key_a} onChange={(v: any) => onChange({ key_a: v })} options={colOpts(joinCols)} placeholder="主数据集字段" />
          {inCount >= 2 ? (
            <Alert type="success" showIcon style={{ marginBottom: 8 }}
              message="已连接第二上游节点（B 输入口）" description="对碰第二库取自上游节点输出，请在下方填写 B 侧字段。" />
          ) : (
            <SelectField label="数据集 B（兜底）" value={p.dataset_b_id} onChange={(v: any) => onChange({ dataset_b_id: v, key_b: undefined })} options={dsOpts} placeholder="未接第二上游时可在此选" />
          )}
          {p.rule === 'intersection' && (
            <SelectField label="关键字段 B" value={p.key_b} onChange={(v: any) => onChange({ key_b: v })} options={colOpts(joinCols)} placeholder="第二数据集字段（默认同 A）" />
          )}
          {p.rule === 'inclusion' && (
            <SelectField label="B 中比对字段" value={p.field_b} onChange={(v: any) => onChange({ field_b: v })} options={colOpts(joinCols)} placeholder="在 B 的该字段文本中查找" />
          )}
        </>
      )}
      {t === 'aggregate' && (
        <>
          <SelectField label="分组字段" value={p.group_by} onChange={(v: any) => onChange({ group_by: v })} options={colOpts(mainCols)} placeholder="按哪个字段分组" />
          <SelectField label="聚合方式" value={p.agg} onChange={(v: any) => onChange({ agg: v })} options={AGG_FNS} />
          {p.agg !== 'count' && (
            <SelectField label="数值字段" value={p.agg_field} onChange={(v: any) => onChange({ agg_field: v })} options={colOpts(mainCols)} placeholder="参与聚合的数值列" />
          )}
        </>
      )}
      {t === 'threshold' && (
        <>
          <SelectField label="比较字段" value={p.field} onChange={(v: any) => onChange({ field: v })} options={[{ label: '聚合值 agg_result', value: 'agg_result' }, ...colOpts(mainCols)]} />
          <SelectField label="比较符" value={p.op} onChange={(v: any) => onChange({ op: v })} options={TH_OPS} />
          <div className="ps-field"><label>阈值</label><InputNumber style={{ width: '100%' }} value={p.value} onChange={(v: any) => onChange({ value: v })} /></div>
        </>
      )}
      {t === 'output' && (
        <SelectField label="关键字段（按此生成预警）" value={p.key_field} onChange={(v: any) => onChange({ key_field: v })} options={colOpts(mainCols)} placeholder="如：姓名 / 身份证号" />
      )}
      <Button danger block style={{ marginTop: 12 }} onClick={onDelete}>删除该节点</Button>
    </div>
  )
}

function SelectField({ label, value, onChange, options, placeholder }: any) {
  return (
    <div className="ps-field">
      <label>{label}</label>
      <Select style={{ width: '100%' }} value={value} onChange={onChange} options={options}
        placeholder={placeholder} allowClear showSearch optionFilterProp="label" />
    </div>
  )
}
