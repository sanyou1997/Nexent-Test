import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Card, Input, Button, Tag, Space, message, Empty, Progress, Select,
  Typography, Alert, Descriptions, List, Upload, Segmented, Tooltip, Modal,
  Drawer, Switch, Popconfirm, Spin, Row, Col,
} from 'antd'
import {
  SafetyCertificateOutlined, FileSearchOutlined, ClearOutlined,
  CloseCircleOutlined, WarningOutlined, InfoCircleOutlined, ImportOutlined,
  FileWordOutlined, FileExcelOutlined, ArrowUpOutlined, ArrowDownOutlined,
  DatabaseOutlined, PlusOutlined, EditOutlined, DeleteOutlined,
  SnippetsOutlined, SaveOutlined, EyeOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import PageHeader from '../components/PageHeader'

interface Item {
  level: 'error' | 'warn' | 'info'; category: string; message: string
  locate?: string; context?: string
  seg_index?: number; start?: number; end?: number
}
interface Segment { locate: string; text: string }
interface VerifyResult {
  items: Item[]; score: number
  stats: { chars: number; segments?: number; errors: number; warns: number; infos: number }
  source?: string | null
  segments?: Segment[]
  truncated?: boolean
}

const LEVEL_META: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  error: { color: 'red', icon: <CloseCircleOutlined style={{ color: '#b91c1c' }} />, label: '必须修正' },
  warn: { color: 'orange', icon: <WarningOutlined style={{ color: '#b45309' }} />, label: '建议修正' },
  info: { color: 'blue', icon: <InfoCircleOutlined style={{ color: '#1d4ed8' }} />, label: '提示' },
}

// 错别字校对已按需求隐藏（离线引擎覆盖过窄，达不到理想校对能力）；仅保留排版/格式/规范类校验
const HIDDEN_CATS = ['错别字', '错别字(智能)']

/** 渲染单段原文：把问题区间渲染成可点击的高亮标记 */
function renderSegment(text: string, hits: (Item & { gi: number })[],
                       active: number | null, onPick: (gi: number) => void) {
  if (!hits.length) return <span>{text}</span>
  const sorted = [...hits].sort((a, b) => (a.start ?? 0) - (b.start ?? 0))
  const out: React.ReactNode[] = []
  let cur = 0
  for (const h of sorted) {
    const s = Math.max(cur, h.start ?? 0)
    const e = Math.min(text.length, h.end ?? s)
    if (e <= cur) continue
    if (s > cur) out.push(<span key={`t${cur}`}>{text.slice(cur, s)}</span>)
    out.push(
      <mark key={`m${s}`} className={`vmark vmark-${h.level}${h.gi === active ? ' vmark-active' : ''}`}
        title={h.message} onClick={() => onPick(h.gi)}>
        {text.slice(s, e)}
      </mark>,
    )
    cur = e
  }
  if (cur < text.length) out.push(<span key="tail">{text.slice(cur)}</span>)
  return <>{out}</>
}

export default function Verify() {
  const [content, setContent] = useState('')
  const [result, setResult] = useState<VerifyResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [view, setView] = useState<'edit' | 'compare'>('edit')
  const [active, setActive] = useState<number | null>(null)
  const [levelFilter, setLevelFilter] = useState<string[]>(['error', 'warn', 'info'])
  const [catFilter, setCatFilter] = useState<string>('all')
  const [docs, setDocs] = useState<{ id: number; title: string; content: string }[]>([])
  const [fixLoading, setFixLoading] = useState(false)
  const [fixModal, setFixModal] = useState(false)
  const [fixResult, setFixResult] = useState<{ fixed: string; fixes: { type: string; desc: string; count: number }[]; applied: number; changed: boolean } | null>(null)

  // ---- 质检规则库（数据驱动，业务可维护）----
  const RULE_CATS = ['规范表述', '绝对化表述', '敏感规范词', '政治表述', '数字金额规范', '上下文']
  const [rulesOpen, setRulesOpen] = useState(false)
  const [rules, setRules] = useState<{ id: number; category: string; level: string; term: string; suggestion: string; is_regex: boolean; enabled: boolean; builtin: boolean }[]>([])
  const [ruleModal, setRuleModal] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [rf, setRf] = useState<{ category: string; level: string; term: string; suggestion: string; is_regex: boolean; enabled: boolean }>(
    { category: '规范表述', level: 'warn', term: '', suggestion: '', is_regex: false, enabled: true })

  const segRefs = useRef<(HTMLDivElement | null)[]>([])
  const itemRefs = useRef<(HTMLDivElement | null)[]>([])

  // ---- 文书模板库 ----
  const [tplOpen, setTplOpen] = useState(false)
  const [templates, setTemplates] = useState<{ id: number; name: string; doc_type: string; template: string; fields: { key: string; label: string; placeholder: string }[] }[]>([])
  const [tplLoading, setTplLoading] = useState(false)
  const [useTpl, setUseTpl] = useState<any>(null)          // 正在使用的模板（填参）
  const [tplVals, setTplVals] = useState<Record<string, string>>({})
  const [previewTpl, setPreviewTpl] = useState<any>(null)  // 预览中的模板
  const [editTpl, setEditTpl] = useState<any>(null)        // 编辑/新建中的模板
  const [savingTpl, setSavingTpl] = useState(false)

  const loadTemplates = async () => {
    setTplLoading(true)
    try { const { data } = await api.get('/documents/templates'); setTemplates(data) }
    catch (e) { message.error(errMsg(e)) } finally { setTplLoading(false) }
  }
  const openTplLib = async () => { setTplOpen(true); await loadTemplates() }

  const useTemplate = (t: any) => {
    setUseTpl(t)
    setTplVals(Object.fromEntries((t.fields || []).map((f: any) => [f.key, ''])))
  }
  const submitUseTemplate = async () => {
    if (!useTpl) return
    try {
      const { data } = await api.post(`/documents/templates/${useTpl.id}/render`, { fields: tplVals })
      setContent(data.content); setResult(null); setView('edit')
      setUseTpl(null)
      message.success(data.pending
        ? `已按「${data.template_name}」填入编辑器，还有 ${data.pending} 处〔待补充〕请补全`
        : `已按「${data.template_name}」生成内容并填入编辑器`)
    } catch (e) { message.error(errMsg(e)) }
  }
  const saveAsTemplate = async () => {
    if (!content.trim()) { message.warning('编辑器内容为空，无法保存为模板'); return }
    const keys = [...new Set([...content.matchAll(/\{\{([^{}\n]+)\}\}/g)].map((m) => m[1].trim()))]
    setEditTpl({ id: null, name: '', doc_type: '自定义', template: content,
      isNew: true, hint: keys.length ? `检测到 ${keys.length} 个占位符：${keys.join('、')}` : '未检测到 {{字段}} 占位符，将保存为固定模板' })
  }
  const saveTemplate = async () => {
    if (!editTpl) return
    if (!editTpl.name.trim()) { message.warning('请填写模板名称'); return }
    setSavingTpl(true)
    try {
      if (editTpl.id == null) {
        await api.post('/documents/templates', { name: editTpl.name, doc_type: editTpl.doc_type, template: editTpl.template })
        message.success('模板已保存到模板库')
      } else {
        await api.put(`/documents/templates/${editTpl.id}`, { name: editTpl.name, doc_type: editTpl.doc_type, template: editTpl.template })
        message.success('模板已更新')
      }
      setEditTpl(null); await loadTemplates()
    } catch (e) { message.error(errMsg(e)) } finally { setSavingTpl(false) }
  }
  const delTemplate = async (id: number) => {
    try { await api.delete(`/documents/templates/${id}`); message.success('已删除'); await loadTemplates() }
    catch (e) { message.error(errMsg(e)) }
  }

  useEffect(() => {
    api.get('/documents/lite').then(({ data }) => setDocs(data)).catch(() => {})
  }, [])

  const run = async () => {
    if (!content.trim()) { message.warning('请先粘贴或导入文书内容'); return }
    setLoading(true)
    try {
      const { data } = await api.post('/verify', { content })
      setResult(data); setActive(null); setView('compare')
    } catch (e) { message.error(errMsg(e)) } finally { setLoading(false) }
  }

  const uploadFile = async (file: File) => {
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const { data } = await api.post('/verify/file', fd, { timeout: 120000 })
      setResult(data); setActive(null); setView('compare')
      message.success(`文件「${file.name}」校验完成，得分 ${data.score} 分`)
    } catch (e) {
      message.error(errMsg(e))
    } finally { setUploading(false) }
  }

  // ---- 一键排版修正 ----
  const runFix = async () => {
    if (!content.trim()) { message.warning('请先粘贴或导入文书内容'); return }
    setFixLoading(true)
    try {
      const { data } = await api.post('/verify/fix', { content })
      setFixResult(data)
      setFixModal(true)
    } catch (e) { message.error(errMsg(e)) } finally { setFixLoading(false) }
  }
  const applyFix = async () => {
    if (!fixResult) return
    const fixed = fixResult.fixed
    setFixModal(false)
    setContent(fixed)
    try {
      const { data } = await api.post('/verify', { content: fixed })
      setResult(data); setActive(null); setView('compare')
      message.success(`已应用排版修正 ${fixResult.applied} 处，并重新校验`)
    } catch (e) { message.error(errMsg(e)) }
  }

  // ---- 质检规则库：加载 / 开关 / 增删改 ----
  const loadRules = async () => {
    try {
      const { data } = await api.get('/verify/rules')
      setRules(data)
    } catch (e) { message.error(errMsg(e)) }
  }
  const openRules = async () => { setRulesOpen(true); await loadRules() }
  const toggleRule = async (r: any) => {
    try {
      await api.put(`/verify/rules/${r.id}`, { enabled: !r.enabled })
      setRules((rs) => rs.map((x) => (x.id === r.id ? { ...x, enabled: !x.enabled } : x)))
    } catch (e) { message.error(errMsg(e)) }
  }
  const delRule = async (id: number) => {
    try {
      await api.delete(`/verify/rules/${id}`)
      setRules((rs) => rs.filter((x) => x.id !== id))
      message.success('已删除规则')
    } catch (e) { message.error(errMsg(e)) }
  }
  const openRuleForm = (r?: any) => {
    setEditingId(r ? r.id : null)
    setRf(r ? { category: r.category, level: r.level, term: r.term, suggestion: r.suggestion, is_regex: r.is_regex, enabled: r.enabled }
            : { category: '规范表述', level: 'warn', term: '', suggestion: '', is_regex: false, enabled: true })
    setRuleModal(true)
  }
  const saveRule = async () => {
    if (!rf.term.trim()) { message.warning('匹配词/正则不能为空'); return }
    try {
      if (editingId != null) {
        await api.put(`/verify/rules/${editingId}`, { ...rf })
        message.success('已更新规则')
      } else {
        await api.post('/verify/rules', { ...rf })
        message.success('已新增规则')
      }
      setRuleModal(false)
      await loadRules()
    } catch (e) { message.error(errMsg(e)) }
  }

  // 问题项带全局序号，便于高亮与列表联动（隐藏错别字类别）
  const items = useMemo(
    () => (result?.items ?? [])
      .filter((it) => !HIDDEN_CATS.includes(it.category))
      .map((it, gi) => ({ ...it, gi })),
    [result],
  )
  const cats = useMemo(() => [...new Set(items.map((i) => i.category))], [items])
  const shown = useMemo(
    () => items.filter((i) => levelFilter.includes(i.level) && (catFilter === 'all' || i.category === catFilter)),
    [items, levelFilter, catFilter],
  )
  const segments = result?.segments ?? []
  const hitsBySeg = useMemo(() => {
    const m: Record<number, (Item & { gi: number })[]> = {}
    for (const it of items) {
      if (it.seg_index != null && it.seg_index >= 0) {
        (m[it.seg_index] ||= []).push(it)
      }
    }
    return m
  }, [items])

  const pick = (gi: number, scrollToItem = false) => {
    setActive(gi)
    const it = items[gi]
    if (it?.seg_index != null && it.seg_index >= 0) {
      segRefs.current[it.seg_index]?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
    if (scrollToItem) {
      itemRefs.current[gi]?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  }

  const step = (dir: 1 | -1) => {
    if (!shown.length) return
    const curIdx = shown.findIndex((i) => i.gi === active)
    const next = shown[(curIdx + dir + shown.length) % shown.length]
    pick(next.gi, true)
  }

  return (
    <>
      <PageHeader
        icon={<FileSearchOutlined />}
        title="文书校验 · 排版修正"
        subtitle="标点 · 公文格式 · 公文排版 · 规范表述 · 条款引用 · 连贯性复核 · 一键修正 · 质检规则可维护"
        extra={
          <Space>
            <Select placeholder="从已生成文书导入" style={{ width: 220 }} allowClear
              options={docs.map((d) => ({ value: d.id, label: d.title }))}
              onChange={(v) => {
                const d = docs.find((x) => x.id === v)
                if (d) { setContent(d.content); setResult(null); setView('edit') }
              }} />
            <Button icon={<SaveOutlined />} onClick={saveAsTemplate}>存为模板</Button>
            <Button icon={<SnippetsOutlined />} onClick={openTplLib}>文书模板库</Button>
            <Button icon={<ClearOutlined />} onClick={() => { setContent(''); setResult(null); setView('edit') }}>清空</Button>
            <Button icon={<FileSearchOutlined />} loading={fixLoading} onClick={runFix}>一键排版修正</Button>
            <Button icon={<DatabaseOutlined />} onClick={openRules}>质检规则库</Button>
            <Button type="primary" icon={<SafetyCertificateOutlined />} loading={loading} onClick={run}>开始校验</Button>
          </Space>
        } />
      <Card>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="检查标点、公文格式、公文排版（Word 字体 / 页边距）、规范表述、条款引用（对照平台法规库）与文本连贯性"
        description="规则校验在本机运行；连贯性复核调用「文书审查」场景智能体，Nexent 不可用时自动跳过。「一键排版修正」只做确定性修正（标点、引号、省略号、的/得/地、截止/截至等）。校验后在「对照查看」中点击问题可定位到原文高亮处。" />

      {/* 文件上传 */}
      <Card size="small" type="inner" style={{ marginBottom: 12 }}
        title={<Space><ImportOutlined />上传 Word / Excel 文件离线校验</Space>}>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
          <Upload accept=".docx,.xlsx,.txt,.md" showUploadList={false} maxCount={1}
            customRequest={({ file }) => uploadFile(file as unknown as File)}>
            <Button icon={<FileWordOutlined />} loading={uploading}>选择文件（.docx / .xlsx / .txt / .md）</Button>
          </Upload>
          <Typography.Text type="secondary"><FileWordOutlined /> Word：逐段落 + 表格</Typography.Text>
          <Typography.Text type="secondary"><FileExcelOutlined /> Excel：逐单元格（Sheet!坐标）</Typography.Text>
          {result?.source && (
            <Tag color="green">当前来源：{result.source}</Tag>
          )}
        </div>
      </Card>

      <Segmented value={view} onChange={(v) => setView(v as 'edit' | 'compare')} style={{ marginBottom: 12 }}
        options={[{ label: '编辑 / 粘贴文本', value: 'edit' },
                  { label: '对照查看（原文 ↔ 问题）', value: 'compare', disabled: !result }]} />

      {view === 'edit' ? (
        <div>
          <Typography.Text strong>待校验文书</Typography.Text>
          <Input.TextArea rows={20} value={content} onChange={(e) => setContent(e.target.value)}
            style={{ marginTop: 8, fontFamily: 'inherit' }}
            placeholder={'粘贴文书全文，或从上方下拉导入已生成的文书。\n\n示例：\n关于给予张某某党内警告处分的决定\n\n经审理查明……\n\n依据《中国共产党纪律处分条例》第X条……'} />
        </div>
      ) : !result ? (
        <Empty description="请先校验" />
      ) : (
        <div>
          {/* 概览 */}
          <Card size="small" style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap' }}>
              <Progress type="circle" size={92}
                strokeColor={result.score >= 85 ? '#15803d' : result.score >= 60 ? '#b45309' : '#b91c1c'}
                percent={result.score} format={(p) => `${p}分`} />
              <Descriptions column={4} size="small" style={{ flex: 1 }}>
                <Descriptions.Item label="字数">{result.stats.chars}</Descriptions.Item>
                <Descriptions.Item label="段落/单元格">{result.stats.segments ?? segments.length}</Descriptions.Item>
                <Descriptions.Item label="必改"><Tag color="red">{result.stats.errors}</Tag></Descriptions.Item>
                <Descriptions.Item label="建议修正"><Tag color="orange">{result.stats.warns}</Tag></Descriptions.Item>
              </Descriptions>
              <Space>
                <Tooltip title="上一处"><Button icon={<ArrowUpOutlined />} onClick={() => step(-1)} /></Tooltip>
                <Tooltip title="下一处"><Button icon={<ArrowDownOutlined />} onClick={() => step(1)} /></Tooltip>
              </Space>
            </div>
          </Card>

          {/* 筛选 */}
          <Space style={{ marginBottom: 10 }} wrap>
            <Typography.Text type="secondary">筛选：</Typography.Text>
            <Select mode="multiple" style={{ minWidth: 220 }} value={levelFilter}
              onChange={setLevelFilter}
              options={[{ value: 'error', label: '必须修正' }, { value: 'warn', label: '建议修正' }, { value: 'info', label: '提示' }]} />
            <Select style={{ minWidth: 180 }} value={catFilter} onChange={setCatFilter}
              options={[{ value: 'all', label: '全部类别' }, ...cats.map((c) => ({ value: c, label: c }))]} />
            <Typography.Text type="secondary">共 {shown.length} 项</Typography.Text>
          </Space>

          {/* 左右对照 */}
          <div className="verify-compare-grid">
            <div>
              <Typography.Text strong>原文（问题处已高亮，点击可定位右侧问题）</Typography.Text>
              <div style={{ marginTop: 8, maxHeight: 520, overflow: 'auto', border: '1px solid #eef1f5',
                borderRadius: 8, padding: 12, background: '#fff', lineHeight: 2 }}>
                {segments.map((s, i) => (
                  <div key={i} ref={(el) => { segRefs.current[i] = el }}
                    style={{ display: 'flex', gap: 8, padding: '2px 4px', borderRadius: 4 }}>
                    <span style={{ flexShrink: 0, color: '#64748b', fontSize: 12, minWidth: 92,
                      fontFamily: 'monospace' }}>{s.locate}</span>
                    <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                      {renderSegment(s.text, hitsBySeg[i] ?? [], active, (gi) => pick(gi, true))}
                    </span>
                  </div>
                ))}
                {result.truncated && (
                  <Alert style={{ marginTop: 8 }} type="warning" showIcon
                    message={`原文过长，仅展示前 ${segments.length} 段，其余问题请在右侧清单中查看`} />
                )}
              </div>
            </div>

            <div>
              <Typography.Text strong>问题清单（点击定位到原文）</Typography.Text>
              <div style={{ marginTop: 8, maxHeight: 520, overflow: 'auto', border: '1px solid #eef1f5',
                borderRadius: 8, padding: 8, background: '#fff' }}>
                {shown.length === 0 && <Empty description="无匹配问题" />}
                <List size="small" dataSource={shown}
                  renderItem={(item) => (
                    <div ref={(el) => { itemRefs.current[item.gi] = el }}
                      onClick={() => pick(item.gi)}
                      style={{
                        padding: '8px 10px', borderRadius: 6, cursor: 'pointer', marginBottom: 6,
                        border: `1px solid ${item.gi === active ? '#1d4ed8' : '#eef1f5'}`,
                        background: item.gi === active ? '#f0f7ff' : '#fff',
                        transition: 'background .15s, border-color .15s',
                      }}>
                      <Space align="start" style={{ width: '100%' }}>
                        {LEVEL_META[item.level]?.icon}
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: 13 }}>
                            <Tag color="geekblue" style={{ marginRight: 4 }}>#{item.gi + 1}</Tag>
                            {item.locate && <Tag style={{ marginRight: 4 }}>{item.locate}</Tag>}
                            {item.message}
                          </div>
                          {item.context && (
                            <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                              …{item.context}…
                            </div>
                          )}
                          <Tag style={{ marginTop: 4 }} color={LEVEL_META[item.level]?.color}>
                            {item.category} · {LEVEL_META[item.level]?.label}
                          </Tag>
                        </div>
                      </Space>
                    </div>
                  )} />
              </div>
            </div>
          </div>
        </div>
      )}

      <Modal title="一键排版修正 · 修正清单"
        open={fixModal} onCancel={() => setFixModal(false)} onOk={applyFix}
        okText={`应用并重新校验（${fixResult?.applied ?? 0} 处）`} cancelText="取消"
        width={560}>
        {fixResult && (
          <div>
            <Alert type={fixResult.changed ? 'success' : 'info'} showIcon style={{ marginBottom: 12 }}
              message={fixResult.changed
                ? `共发现 ${fixResult.applied} 处可确定性修正（标点 / 格式 / 规范表述）`
                : '未发现可自动修正的排版问题'} />
            {fixResult.fixes.length === 0 ? (
              <Empty description="无需修正" />
            ) : (
              <List size="small" dataSource={fixResult.fixes}
                renderItem={(f) => (
                  <List.Item>
                    <Tag color="blue">{f.type}</Tag>
                    <span style={{ flex: 1 }}>{f.desc}</span>
                    <Tag color="red">{f.count} 处</Tag>
                  </List.Item>
                )} />
            )}
            <Typography.Paragraph type="secondary" style={{ marginTop: 10, marginBottom: 0 }}>
              修正均为确定性、零语义风险的排版类改动，不会变更正文含义；应用后将自动重新校验以查看剩余问题。
            </Typography.Paragraph>
          </div>
        )}
      </Modal>

      {/* 质检规则库 Drawer */}
      <Drawer title="文书质检规则库（数据驱动 · 业务可维护）" width={720} open={rulesOpen}
        onClose={() => setRulesOpen(false)} extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openRuleForm()}>新增规则</Button>
        }>
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="校验的「规范表述 / 绝对化 / 敏感词 / 政治表述 / 数字金额 / 上下文」词表已迁移为可编辑规则库。停用某条即不再参与校验；新增规则即时生效。" />
        {rules.length === 0 && <Empty description="暂无规则，点击右上角新增" />}
        {RULE_CATS.map((cat) => {
          const list = rules.filter((r) => r.category === cat)
          if (!list.length) return null
          return (
            <div key={cat} style={{ marginBottom: 16 }}>
              <Typography.Title level={5} style={{ margin: '4px 0 8px' }}>{cat}</Typography.Title>
              <List size="small" dataSource={list} renderItem={(r) => (
                <List.Item
                  style={{ border: '1px solid #eef1f5', borderRadius: 8, padding: '8px 12px', marginBottom: 6,
                    opacity: r.enabled ? 1 : 0.5 }}>
                  <div style={{ flex: 1 }}>
                    <Space wrap>
                      <Tag color={r.level === 'error' ? 'red' : r.level === 'warn' ? 'orange' : 'blue'}>{r.level === 'error' ? '必改' : r.level === 'warn' ? '建议' : '提示'}</Tag>
                      <Tag color={r.is_regex ? 'purple' : 'default'}>{r.is_regex ? '正则' : '字面'}</Tag>
                      {r.builtin && <Tag color="green">内置</Tag>}
                      <code style={{ fontSize: 13 }}>{r.term}</code>
                    </Space>
                    <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>{r.suggestion}</div>
                  </div>
                  <Space>
                    <Switch size="small" checked={r.enabled} onChange={() => toggleRule(r)} />
                    <Button size="small" icon={<EditOutlined />} onClick={() => openRuleForm(r)} />
                    <Popconfirm title="确认删除该规则？" onConfirm={() => delRule(r.id)}>
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                </List.Item>
              )} />
            </div>
          )
        })}
      </Drawer>

      {/* 规则 新增/编辑 Modal */}
      <Modal title={editingId != null ? '编辑质检规则' : '新增质检规则'} open={ruleModal}
        onCancel={() => setRuleModal(false)} onOk={saveRule}
        okText="保存" cancelText="取消" width={560}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <div>
            <Typography.Text strong>类别</Typography.Text>
            <Select style={{ width: '100%', marginTop: 4 }} value={rf.category}
              onChange={(v) => setRf({ ...rf, category: v })}
              options={RULE_CATS.map((c) => ({ value: c, label: c }))} />
          </div>
          <div>
            <Typography.Text strong>级别</Typography.Text>
            <Select style={{ width: '100%', marginTop: 4 }} value={rf.level}
              onChange={(v) => setRf({ ...rf, level: v })}
              options={[{ value: 'error', label: '必改（error）' }, { value: 'warn', label: '建议（warn）' }, { value: 'info', label: '提示（info）' }]} />
          </div>
          <div>
            <Typography.Text strong>匹配词 / 正则</Typography.Text>
            <Input style={{ marginTop: 4 }} value={rf.term} onChange={(e) => setRf({ ...rf, term: e.target.value })}
              placeholder={rf.is_regex ? '如：\\d+\\s*万(?!\\s*元)' : '如：双规 / 打架 / 涉嫌犯罪'} />
            <div style={{ marginTop: 4 }}>
              <Space>
                <Switch size="small" checked={rf.is_regex} onChange={(v) => setRf({ ...rf, is_regex: v })} />
                <Typography.Text type="secondary">视为正则表达式（re.IGNORECASE）</Typography.Text>
              </Space>
            </div>
          </div>
          <div>
            <Typography.Text strong>修改建议</Typography.Text>
            <Input.TextArea rows={2} style={{ marginTop: 4 }} value={rf.suggestion}
              onChange={(e) => setRf({ ...rf, suggestion: e.target.value })}
              placeholder="如：规范表述为'留置'；金额建议补全为「X万元」" />
          </div>
          <div>
            <Space>
              <Switch size="small" checked={rf.enabled} onChange={(v) => setRf({ ...rf, enabled: v })} />
              <Typography.Text type="secondary">启用（参与校验）</Typography.Text>
            </Space>
          </div>
        </Space>
      </Modal>
      {/* ============ 文书模板库 ============ */}
      <Drawer title="文书模板库（权威纪检文书 · 可维护）" width={760} open={tplOpen}
        onClose={() => setTplOpen(false)} extra={
          <Button type="primary" icon={<PlusOutlined />}
            onClick={() => setEditTpl({ id: null, name: '', doc_type: '自定义', template: '', isNew: true })}>
            新增模板
          </Button>
        }>
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="选择模板 → 填写字段 → 自动生成规范文书草稿填入编辑器；未填项统一标记〔待补充〕，不会残留占位符。校验通过后可直接定稿。" />
        <Spin spinning={tplLoading}>
          {templates.length === 0 && <Empty description="暂无模板" />}
          {[...new Set(templates.map((t) => t.doc_type))].map((dt) => (
            <div key={dt} style={{ marginBottom: 16 }}>
              <Typography.Title level={5} style={{ margin: '4px 0 8px' }}>{dt}</Typography.Title>
              <Row gutter={[8, 8]}>
                {templates.filter((t) => t.doc_type === dt).map((t) => (
                  <Col key={t.id} span={12}>
                    <Card size="small" style={{ height: '100%' }}
                      actions={[
                        <Button key="use" size="small" type="link" icon={<FileWordOutlined />}
                          onClick={() => useTemplate(t)}>使用模板</Button>,
                        <Button key="view" size="small" type="link" icon={<EyeOutlined />}
                          onClick={() => setPreviewTpl(t)}>预览</Button>,
                        <Button key="edit" size="small" type="link" icon={<EditOutlined />}
                          onClick={() => setEditTpl({ ...t, isNew: false })}>编辑</Button>,
                        <Popconfirm key="del" title="确认删除该模板？" onConfirm={() => delTemplate(t.id)}>
                          <Button size="small" type="link" danger icon={<DeleteOutlined />}>删除</Button>
                        </Popconfirm>,
                      ]}>
                      <div style={{ fontWeight: 500, marginBottom: 4 }}>{t.name}</div>
                      <Space wrap>
                        <Tag color="geekblue">{t.doc_type}</Tag>
                        <Tag>{(t.fields || []).length} 个字段</Tag>
                      </Space>
                      <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>
                        {(t.fields || []).slice(0, 6).map((f) => f.label).join(' / ')}
                        {(t.fields || []).length > 6 ? ' …' : ''}
                      </div>
                    </Card>
                  </Col>
                ))}
              </Row>
            </div>
          ))}
        </Spin>
      </Drawer>

      {/* 使用模板：填写字段 → 渲染填入编辑器 */}
      <Modal title={`使用模板 · ${useTpl?.name ?? ''}`} open={!!useTpl} width={560}
        onCancel={() => setUseTpl(null)} onOk={submitUseTemplate} okText="生成并填入编辑器" cancelText="取消">
        {useTpl && (
          <div>
            <Alert type="info" showIcon style={{ marginBottom: 12 }}
              message="按提示填写各字段；留空的字段将在成文中标记为〔待补充〕，请务必补全后再定稿。" />
            {(useTpl.fields || []).length === 0 && (
              <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                message="该模板没有 {{字段}} 占位符，将原样填入编辑器。" />
            )}
            <div style={{ maxHeight: 380, overflow: 'auto', paddingRight: 4 }}>
              {(useTpl.fields || []).map((fl: any) => (
                <div key={fl.key} style={{ marginBottom: 10 }}>
                  <Typography.Text strong>{fl.label}</Typography.Text>
                  <Input style={{ marginTop: 4 }} value={tplVals[fl.key] ?? ''}
                    placeholder={fl.placeholder || `请填写${fl.label}`}
                    onChange={(e) => setTplVals({ ...tplVals, [fl.key]: e.target.value })} />
                </div>
              ))}
            </div>
          </div>
        )}
      </Modal>

      {/* 模板预览 */}
      <Modal title={`模板预览 · ${previewTpl?.name ?? ''}`} open={!!previewTpl} width={640}
        footer={<Button onClick={() => setPreviewTpl(null)}>关闭</Button>}
        onCancel={() => setPreviewTpl(null)}>
        <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', background: '#f8fafc',
          border: '1px solid #eef1f5', borderRadius: 8, padding: 12, maxHeight: 480, overflow: 'auto' }}>
          {previewTpl?.template}
        </pre>
      </Modal>

      {/* 模板 新增/编辑 */}
      <Modal title={editTpl?.id == null ? '新增文书模板' : `编辑模板 · ${editTpl?.name ?? ''}`}
        open={!!editTpl} width={680} onCancel={() => setEditTpl(null)} onOk={saveTemplate}
        okText="保存" cancelText="取消" confirmLoading={savingTpl}>
        {editTpl && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <div>
              <Typography.Text strong>模板名称</Typography.Text>
              <Input style={{ marginTop: 4 }} value={editTpl.name} maxLength={60}
                placeholder="如：谈话笔录"
                onChange={(e) => setEditTpl({ ...editTpl, name: e.target.value })} />
            </div>
            <div>
              <Typography.Text strong>文书类型</Typography.Text>
              <Input style={{ marginTop: 4 }} value={editTpl.doc_type} maxLength={30}
                placeholder="如：谈话笔录 / 初核报告 / 处分决定"
                onChange={(e) => setEditTpl({ ...editTpl, doc_type: e.target.value })} />
            </div>
            <div>
              <Typography.Text strong>模板内容（用 {'{{字段名}}'} 标记可填写内容）</Typography.Text>
              <Input.TextArea rows={12} style={{ marginTop: 4, fontFamily: 'inherit' }}
                value={editTpl.template}
                placeholder={'如：谈 话 笔 录\n\n时间：{{谈话时间}}\n被谈话人：{{被谈话人}}'}
                onChange={(e) => setEditTpl({ ...editTpl, template: e.target.value })} />
              {editTpl.isNew && editTpl.hint && (
                <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>{editTpl.hint}</div>
              )}
            </div>
          </Space>
        )}
      </Modal>
    </Card>
    </>
  )
}
