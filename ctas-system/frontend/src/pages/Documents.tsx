import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Drawer, Form, Input, Select, Tag, Space, message,
  Popconfirm, Timeline, Upload, Tooltip, Typography, Row, Col, Collapse, Progress, Alert, Switch, Modal,
} from 'antd'
import {
  FileAddOutlined, EditOutlined, CheckOutlined, UploadOutlined,
  FileWordOutlined, DeleteOutlined, EyeOutlined, AppstoreOutlined, FileTextOutlined,
  CopyOutlined, FileSearchOutlined, SearchOutlined, PushpinOutlined, SettingOutlined,
  RobotOutlined, DiffOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { useAuth } from '../auth'
import PageHeader from '../components/PageHeader'
import DocReviewModal from '../components/nexent/DocReviewModal'

interface FieldRule { required?: boolean; type?: string; max_len?: number; hint?: string }
interface Tpl {
  id: number; name: string; doc_type: string; template: string
  fields: { key: string; label: string; placeholder: string }[]
  source?: string; field_rules?: Record<string, FieldRule>; pinned?: boolean; doc_count?: number
}
interface Version { version_no: number; note: string; created_at: string; content: string; source?: string }
interface Doc {
  id: number; title: string; case_name: string; content: string; status: string
  template_name: string; created_by: string; confirmed_by: string; confirmed_at: string
  created_at: string; versions: Version[]; mode?: string; warnings?: string[]; source?: string
}

const SOURCE_COLOR: Record<string, string> = { 流程生成: 'blue', 模板生成: 'purple', 智能体生成: 'gold' }

/** 逐行对比：标出删除行与新增行（按行集合比对，适合公文这类段落文本） */
function LineDiff({ before, after }: { before: string; after: string }) {
  const a = before.split('\n'); const b = after.split('\n')
  const sa = new Set(a.map((x) => x.trim())); const sb = new Set(b.map((x) => x.trim()))
  const box = { whiteSpace: 'pre-wrap' as const, fontSize: 13, lineHeight: 1.8, maxHeight: 460, overflow: 'auto', background: '#f8fafc', padding: 10, borderRadius: 8 }
  return (
    <Row gutter={12}>
      <Col span={12}><div style={{ fontWeight: 600, marginBottom: 6 }}>当前正文（红色：修改稿中已改动的行）</div><div style={box}>
        {a.map((l, i) => <div key={i} style={l.trim() && !sb.has(l.trim()) ? { background: '#fee2e2' } : undefined}>{l || '\u00A0'}</div>)}
      </div></Col>
      <Col span={12}><div style={{ fontWeight: 600, marginBottom: 6 }}>智能体修改稿（绿色：改动后的行）</div><div style={box}>
        {b.map((l, i) => <div key={i} style={l.trim() && !sa.has(l.trim()) ? { background: '#dcfce7' } : undefined}>{l || '\u00A0'}</div>)}
      </div></Col>
    </Row>
  )
}
interface CheckIssue { severity: 'error' | 'warning' | 'info'; rule: string; message: string; line?: number }
interface CheckRes { score: number; level: string; issues: CheckIssue[]; normalized: string; changed: boolean }

/** 公文样式预览：标题居中、正文缩进、落款右对齐 */
function GongwenPreview({ content }: { content: string }) {
  const lines = content.split('\n')
  // 尾部落款（短行 + 日期行）右对齐
  const lastIdx: number[] = []
  for (let i = lines.length - 1; i >= Math.max(0, lines.length - 4); i--) {
    const t = lines[i].trim()
    if (!t) break
    lastIdx.unshift(i)
    if (/^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日$/.test(t)) { lines.splice(i, 1, `\u00A0\u00A0${t}`); break }
  }
  const sigIdx = new Set(lastIdx)
  return (
    <div style={{ background: '#fff', border: '1px solid #e5e5e5', padding: '32px 40px', minHeight: 400 }}>
      {lines.map((l, i) => {
        const t = l.trim()
        if (!t) return <div key={i} style={{ height: 14 }} />
        const isTitle = i === 0 || (i === 1 && !/^[一二三四五六七八九十]+[、（(]/.test(t) && t.length <= 30 && lines[0].startsWith('关于'))
        if (isTitle && i < 3)
          return <div key={i} style={{ textAlign: 'center', fontSize: 18, fontFamily: '"FZXiaoBiaoSong-B05S","SimSun",serif', fontWeight: 600, margin: '4px 0', lineHeight: 1.8 }}>{t}</div>
        if (sigIdx.has(i))
          return <div key={i} style={{ textAlign: 'right', fontSize: 15, fontFamily: 'FangSong, "FangSong_GB2312", serif', lineHeight: 2 }}>{t}</div>
        if (/^[一二三四五六七八九十]+、/.test(t))
          return <div key={i} style={{ fontSize: 15, fontFamily: '"SimHei", serif', lineHeight: 2, textIndent: '2em' }}>{t}</div>
        if (/^[（(][一二三四五六七八九十]+[)）]/.test(t))
          return <div key={i} style={{ fontSize: 15, fontFamily: '"KaiTi", "KaiTi_GB2312", serif', fontWeight: 600, lineHeight: 2, textIndent: '2em' }}>{t}</div>
        return <div key={i} style={{ fontSize: 15, fontFamily: 'FangSong, "FangSong_GB2312", serif', lineHeight: 2, textIndent: '2em' }}>{t}</div>
      })}
    </div>
  )
}

const SEV_COLOR: Record<string, string> = { error: 'red', warning: 'orange', info: 'blue' }
const SEV_LABEL: Record<string, string> = { error: '错误', warning: '警告', info: '建议' }

export default function Documents() {
  const { user } = useAuth()
  const [docs, setDocs] = useState<Doc[]>([])
  const [tpls, setTpls] = useState<Tpl[]>([])
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<Doc | null>(null)
  const [editText, setEditText] = useState('')
  const [editNote, setEditNote] = useState('')
  const [preview, setPreview] = useState(true)
  const [tplView, setTplView] = useState<Tpl | null>(null)
  const [form] = Form.useForm()

  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [reviewOpen, setReviewOpen] = useState(false)
  const [compareVer, setCompareVer] = useState<Version | null>(null)
  const [checkRes, setCheckRes] = useState<CheckRes | null>(null)
  const [checking, setChecking] = useState(false)
  const [norming, setNorming] = useState(false)

  // 模板库深化：检索 / 来源 / 置顶 / 复制 / 字段校验配置
  const [tplSearch, setTplSearch] = useState('')
  const [tplType, setTplType] = useState('')
  const [rulesTpl, setRulesTpl] = useState<Tpl | null>(null)
  const [rulesDraft, setRulesDraft] = useState<Record<string, FieldRule>>({})
  // 案件要素联动 + 生成前预检
  const [caseSources, setCaseSources] = useState<any[]>([])
  const [precheck, setPrecheck] = useState<any>(null)

  const load = async (q = tplSearch, dt = tplType) => {
    const [d, t] = await Promise.all([
      api.get('/documents'),
      api.get('/documents/templates', { params: { q, doc_type: dt } }),
    ])
    setDocs(d.data)
    setTpls(t.data)
  }
  const reloadTpls = () => load(tplSearch, tplType)
  useEffect(() => { load() }, [])

  const filtered = docs.filter((dd) => {
    const kw = search.trim()
    if (kw && !(dd.title.includes(kw) || (dd.case_name || '').includes(kw))) return false
    if (statusFilter && dd.status !== statusFilter) return false
    if (sourceFilter && dd.source !== sourceFilter) return false
    return true
  })

  const generate = async (v: any) => {
    const { template_id, case_name, fields } = v
    try {
      const { data } = await api.post('/documents/generate', { template_id, case_name, fields: fields || {} })
      if (data.warnings?.length) {
        const head = data.warnings.slice(0, 3).join('；')
        message.warning(`已生成，但有 ${data.warnings.length} 项待补充/格式问题：${head}${data.warnings.length > 3 ? ' 等' : ''}`)
      } else if (data.llm_rejected) {
        message.info(`已按模板生成：智能体润色改动了填报的事实要素（${(data.llm_missing_fields || []).slice(0, 2).join('、')}），为保全事实已弃用润色稿`, 6)
      } else {
        message.success(`AI 初稿已生成（${data.mode === 'template+llm' ? '智能体润色' : '公文规则排版'}），请核对后修改定稿`)
      }
      setOpen(false); form.resetFields(); setPrecheck(null); load(); setDetail(data); setEditText(data.content)
    } catch (e) { message.error(errMsg(e)) }
  }

  const cloneTpl = async (t: Tpl) => {
    try {
      const { data } = await api.post(`/documents/templates/${t.id}/clone`)
      message.success(`已复制为「${data.name}」，可在其基础上改造`)
      reloadTpls()
    } catch (e) { message.error(errMsg(e)) }
  }

  const pinTpl = async (t: Tpl) => {
    try { await api.post(`/documents/templates/${t.id}/pin`); reloadTpls() }
    catch (e) { message.error(errMsg(e)) }
  }

  const openRules = (t: Tpl) => {
    setRulesTpl(t)
    const draft: Record<string, FieldRule> = {}
    t.fields.forEach((f) => {
      draft[f.key] = { required: false, type: 'text', max_len: 0, hint: '', ...(t.field_rules?.[f.key] || {}) }
    })
    setRulesDraft(draft)
  }

  const saveRules = async (auto: boolean) => {
    if (!rulesTpl) return
    try {
      await api.put(`/documents/templates/${rulesTpl.id}/rules`, { rules: rulesDraft, auto })
      message.success(auto ? '已按字段名智能重推校验规则' : '校验规则已保存')
      setRulesTpl(null); reloadTpls()
    } catch (e) { message.error(errMsg(e)) }
  }

  const loadCaseSources = (q = '') => {
    api.get('/documents/case-sources', { params: { q } })
      .then(({ data }) => setCaseSources(data)).catch(() => setCaseSources([]))
  }

  const applyCase = async (val: string) => {
    if (!val) return
    const [ct, id] = val.split(':')
    const tid = form.getFieldValue('template_id')
    try {
      const { data } = await api.get(`/documents/case-sources/${ct}/${id}/fields`,
        { params: { template_id: tid || 0 } })
      const cur = form.getFieldValue('fields') || {}
      const merged: Record<string, string> = { ...cur }
      let hit = 0
      Object.entries(data.values || {}).forEach(([k, v]) => {
        if (v) { merged[k] = v as string; hit += 1 }
      })
      form.setFieldsValue({ fields: merged, case_name: data.source_label || form.getFieldValue('case_name') })
      message.success(`已从「${data.source_label}」带入 ${hit} 个要素`)
    } catch (e) { message.error(errMsg(e)) }
  }

  const runPrecheck = async () => {
    const tid = form.getFieldValue('template_id')
    if (!tid) { message.warning('请先选择文书模板'); return }
    const fields = form.getFieldValue('fields') || {}
    try {
      const { data } = await api.post(`/documents/templates/${tid}/check-fields`, { fields })
      setPrecheck(data)
    } catch (e) { message.error(errMsg(e)) }
  }

  const save = async () => {
    if (!detail) return
    try {
      const { data } = await api.put(`/documents/${detail.id}`, { content: editText, note: editNote })
      message.success('已保存新版本（修改留痕）')
      setDetail(data); setEditText(data.content); setEditNote(''); setCheckRes(null); load()
    } catch (e) { message.error(errMsg(e)) }
  }

  const confirmDoc = async () => {
    if (!detail) return
    try {
      const { data } = await api.post(`/documents/${detail.id}/confirm`)
      message.success('文书已定稿（审理确认 · 责任链闭合）')
      setDetail(data); setCheckRes(null); load()
    } catch (e) { message.error(errMsg(e)) }
  }

  const runCheck = async () => {
    if (!detail) return
    setChecking(true); setCheckRes(null)
    try {
      const { data } = await api.post(`/documents/${detail.id}/check`)
      setCheckRes(data)
      if (data.issues.length === 0) message.success('排版检查通过：格式规范')
    } catch (e) { message.error(errMsg(e)) }
    finally { setChecking(false) }
  }

  const applyNorm = async () => {
    if (!detail || !checkRes) return
    setNorming(true)
    try {
      const { data } = await api.put(`/documents/${detail.id}`, { content: checkRes.normalized, note: '排版规范化（一键应用）' })
      message.success('已应用规范化排版，生成新版本')
      setDetail(data); setEditText(data.content); setCheckRes(null); load()
    } catch (e) { message.error(errMsg(e)) }
    finally { setNorming(false) }
  }

  const copyText = async () => {
    if (!detail) return
    try { await navigator.clipboard.writeText(detail.content); message.success('正文已复制到剪贴板') }
    catch { message.error('复制失败，请手动选择') }
  }

  const uploadTpl = async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    try {
      const { data } = await api.post('/documents/templates/upload', fd)
      message.success(`模板「${data.name}」已导入，解析出 ${data.fields.length} 个字段`)
      load()
    } catch (e) { message.error(errMsg(e)) }
    return false
  }

  const exportDocx = async () => {
    if (!detail) return
    try {
      const res = await api.get(`/documents/${detail.id}/docx`, { responseType: 'blob' })
      const url = URL.createObjectURL(res.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${detail.title.replace(/[\\/:*?"<>|]/g, '_')}.docx`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) { message.error(errMsg(e)) }
  }

  /** 重新取单份文书（智能体新版本保存后刷新抽屉） */
  const refreshDetail = async (did: number) => {
    const { data } = await api.get('/documents')
    const list: Doc[] = Array.isArray(data) ? data : (data.items || [])
    setDocs(list)
    const d = list.find((x) => x.id === did)
    if (d) setDetail(d)
    return d
  }
  const isAdopted = (v: Version) => !!detail?.versions.some((x) => x.version_no > v.version_no && (x.note || '').startsWith(`采用 v${v.version_no}`))
  const adopt = async (v: Version) => {
    if (!detail) return
    try {
      const { data } = await api.put(`/documents/${detail.id}`, { content: v.content, note: `采用 v${v.version_no}（智能体修改稿）` })
      message.success(`已采用 v${v.version_no}，生成新版本`)
      setDetail(data); setEditText(data.content); setCompareVer(null); setCheckRes(null); load()
    } catch (e) { message.error(errMsg(e)) }
  }

  const columns = [
    { title: '标题', dataIndex: 'title', width: 300,
      render: (t: string, r: Doc) => <a onClick={() => { setDetail(r); setEditText(r.content); setCheckRes(null) }}>{t}</a> },
    { title: '来源', dataIndex: 'source', width: 96,
      render: (v: string) => v ? <Tag color={SOURCE_COLOR[v] || 'default'} bordered={false}>{v}</Tag> : '-' },
    { title: '案件', dataIndex: 'case_name', width: 150, ellipsis: true, render: (t: string) => t || '-' },
    { title: '模板', dataIndex: 'template_name', width: 120, ellipsis: true },
    { title: '状态', dataIndex: 'status', width: 100,
      render: (s: string) => <Tag color={s === '已定稿' ? 'green' : s === 'AI初稿' ? 'gold' : 'blue'}>{s}</Tag> },
    { title: '创建人', dataIndex: 'created_by', width: 100, ellipsis: true },
    { title: '确认人', dataIndex: 'confirmed_by', width: 100, ellipsis: true, render: (t: string) => t || '-' },
    { title: '版本', width: 60, render: (_: any, r: Doc) => r.versions.length },
  ]

  const tplColumns = [
    {
      title: '模板名称', dataIndex: 'name', render: (t: string, r: Tpl) => (
        <Space size={4} wrap>
          {r.pinned ? <Tag color="gold">置顶</Tag> : null}
          <a onClick={() => setTplView(r)}>{t}</a>
        </Space>
      ),
    },
    { title: '类型', dataIndex: 'doc_type', width: 100, ellipsis: true },
    {
      title: '来源', dataIndex: 'source', width: 88,
      render: (s: string) => {
        const color = s === '官方模板' ? 'gold' : s === '上传' ? 'purple' : 'blue'
        return <Tag color={color}>{s || '自定义'}</Tag>
      },
    },
    { title: '字段', width: 56, render: (_: any, r: Tpl) => r.fields.length },
    { title: '已生成', width: 66, render: (_: any, r: Tpl) => (r.doc_count != null && r.doc_count >= 0 ? r.doc_count : '-') },
    {
      title: '操作', width: 168, render: (_: any, t: Tpl) => (
        <Space wrap>
          <Tooltip title="预览模板全文"><Button size="small" icon={<EyeOutlined />} onClick={() => setTplView(t)} /></Tooltip>
          <Tooltip title="复制模板（在其基础上改造）"><Button size="small" icon={<CopyOutlined />} onClick={() => cloneTpl(t)} /></Tooltip>
          <Tooltip title={t.pinned ? '取消置顶' : '置顶为常用'}>
            <Button size="small" type={t.pinned ? 'primary' : 'default'} icon={<PushpinOutlined />} onClick={() => pinTpl(t)} />
          </Tooltip>
          <Tooltip title="字段校验配置"><Button size="small" icon={<SettingOutlined />} onClick={() => openRules(t)} /></Tooltip>
          {user?.role === 'admin' && (
            <Popconfirm title="删除该模板？（已生成文书的模板不可删）" onConfirm={async () => {
              try { await api.delete(`/documents/templates/${t.id}`); message.success('已删除'); reloadTpls() }
              catch (e) { message.error(errMsg(e)) }
            }}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  const currentTplId = Form.useWatch('template_id', form)
  const currentTpl = tpls.find((t) => t.id === currentTplId)

  return (
    <>
      <PageHeader
        icon={<FileTextOutlined />}
        title="审理文书库"
        subtitle="流程生成、模板生成、智能体生成的文书统一归档：人工修改留痕 → 智能体校对 / 润色（另存新版本）→ 审理确认定稿"
        extra={<Button type="primary" icon={<FileAddOutlined />} onClick={() => setOpen(true)}>生成文书</Button>} />
      <Row gutter={16} align="top">
        <Col xs={24} xxl={17}>
          <Card size="small" style={{ marginBottom: 12 }}>
            <Space wrap>
              <Input allowClear prefix={<SearchOutlined />} placeholder="搜索标题 / 案件名"
                style={{ width: 220 }} value={search} onChange={(e) => setSearch(e.target.value)} />
              <Select allowClear placeholder="按状态筛选" style={{ width: 140 }}
                value={statusFilter || undefined} onChange={(v) => setStatusFilter(v || '')}
                options={[{ value: 'AI初稿', label: 'AI初稿' }, { value: '已定稿', label: '已定稿' }]} />
              <Select allowClear placeholder="按来源筛选" style={{ width: 140 }}
                value={sourceFilter || undefined} onChange={(v) => setSourceFilter(v || '')}
                options={['流程生成', '模板生成', '智能体生成'].map((x) => ({ value: x, label: x }))} />
              <Typography.Text type="secondary">共 {filtered.length} 份</Typography.Text>
            </Space>
          </Card>
          <Card>
            <Table rowKey="id" size="middle" columns={columns} dataSource={filtered} pagination={{ pageSize: 10 }} scroll={{ x: 1060 }} />
          </Card>
        </Col>
        <Col xs={24} xxl={7}>
          <Card size="small" title={<Space><AppstoreOutlined />模板库</Space>}
            extra={
              <Upload accept=".docx,.txt,.md" showUploadList={false} beforeUpload={uploadTpl}>
                <Tooltip title="上传 Word/TXT 模板"><Button size="small" icon={<UploadOutlined />} /></Tooltip>
              </Upload>
            }>
            <Space wrap style={{ marginBottom: 8 }}>
              <Input allowClear prefix={<SearchOutlined />} placeholder="搜索模板名称"
                style={{ width: 130 }} value={tplSearch}
                onChange={(e) => setTplSearch(e.target.value)}
                onPressEnter={() => load(tplSearch, tplType)} />
              <Select allowClear placeholder="按类型" style={{ width: 110 }}
                value={tplType || undefined}
                onChange={(v) => { setTplType(v || ''); load(tplSearch, v || '') }}
                options={Array.from(new Set([
                  '初核报告', '审查调查报告', '审理报告', '立案决定书', '廉政意见', '自定义',
                  ...tpls.map((t) => t.doc_type),
                ])).map((x) => ({ value: x, label: x }))} />
            </Space>
            <Collapse ghost defaultActiveKey={[]} items={[{
              key: 'tpl', label: <span style={{ fontSize: 12 }}>模板列表（{tpls.length} 个，置顶优先，点击展开管理）</span>,
              children: (
                <>
                  <Typography.Paragraph type="secondary" style={{ fontSize: 11 }}>
                    模板中用 <code>{'{{字段名}}'}</code> 标记填写内容，系统自动解析生成表单；
                    可配置字段<strong>必填 / 格式 / 字数</strong>校验，支持复制改造与常用置顶。
                  </Typography.Paragraph>
                  <Table rowKey="id" size="small" columns={tplColumns} dataSource={tpls} pagination={false} />
                </>
              ),
            }]} />
          </Card>
        </Col>
      </Row>

      <Drawer title="生成文书" open={open} width={640} onClose={() => { setOpen(false); setPrecheck(null) }}
        extra={currentTpl ? <Button onClick={runPrecheck}>预检填写</Button> : null}>
        <Form form={form} layout="vertical" onFinish={generate}>
          <Form.Item name="template_id" label="文书模板" rules={[{ required: true }]}>
            <Select placeholder="选择模板（可先在上方上传自定义模板）" showSearch optionFilterProp="label"
              options={tpls.map((t) => ({ value: t.id, label: `${t.name}（${t.doc_type} · ${t.fields.length}字段）` }))} />
          </Form.Item>

          {/* 案件要素联动：从案管/党风带入，免重复录入、与业务数据一致 */}
          <Form.Item label="从案件带入（可选）"
            extra="选择案管案件或党风问题事项，自动填充匹配字段（被审查人 / 单位 / 案由等）">
            <Select allowClear showSearch placeholder="搜索案件/事项标题、被审查人"
              filterOption={false}
              onFocus={() => loadCaseSources('')} onSearch={loadCaseSources}
              onChange={applyCase}
              options={caseSources.map((c) => ({
                value: `${c.case_type}:${c.id}`,
                label: `${c.code || ''} ${c.title || ''}${c.subject ? ' · ' + c.subject : ''}`,
              }))} />
          </Form.Item>

          <Form.Item name="case_name" label="案件名称"><Input placeholder="如：张某某违规收受礼金案" /></Form.Item>

          {currentTpl?.fields.map((f) => {
            const rule = currentTpl.field_rules?.[f.key] || {}
            const typeLabel = rule.type === 'date' ? '日期' : rule.type === 'amount' ? '金额'
              : rule.type === 'number' ? '数字' : ''
            const hint = rule.hint || (rule.max_len ? `不超过 ${rule.max_len} 字` : '') || f.placeholder
            return (
              <Form.Item key={f.key} name={['fields', f.key]}
                label={<span>{f.label}{rule.required ? <span style={{ color: '#b91c1c' }}> *</span> : null}
                  {typeLabel ? <Tag color="blue" style={{ marginLeft: 6, fontSize: 11 }}>{typeLabel}</Tag> : null}</span>}
                extra={hint}>
                <Input.TextArea rows={f.key.includes('情况') || f.key.includes('事实') ? 4 : 1} placeholder={hint} />
              </Form.Item>
            )
          })}

          {precheck && (
            <Alert style={{ marginBottom: 12 }} showIcon
              type={precheck.ok ? 'success' : 'warning'}
              message={precheck.ok ? '填写完整，可以生成' : `待补充 ${precheck.missing.length} 项，格式问题 ${precheck.invalid.length} 项`}
              description={precheck.ok ? null : (
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {precheck.missing.map((m: any) => <li key={m.key}>缺失必填：{m.label}</li>)}
                  {precheck.invalid.map((i: any) => <li key={i.key}>{i.message}</li>)}
                </ul>
              )} />
          )}

          <Button type="primary" htmlType="submit" block size="large">生成 AI 初稿</Button>
        </Form>
      </Drawer>

      <Drawer title="模板全文预览" open={!!tplView} width={640} onClose={() => setTplView(null)}>
        {tplView && (
          <GongwenPreview content={tplView.template} />
        )}
      </Drawer>

      <Modal title={`字段校验配置 · ${rulesTpl?.name || ''}`} open={!!rulesTpl}
        onCancel={() => setRulesTpl(null)} width={680}
        footer={[
          <Button key="auto" onClick={() => saveRules(true)}>智能重推</Button>,
          <Button key="cancel" onClick={() => setRulesTpl(null)}>取消</Button>,
          <Button key="ok" type="primary" onClick={() => saveRules(false)}>保存规则</Button>,
        ]}>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          为每个字段设定<strong>必填</strong>与<strong>格式</strong>（日期 / 金额 / 数字）与字数上限；
          生成前「预检填写」会列出缺失项与格式错误，让"是否填全"显式可见。
          「智能重推」按字段名自动推断（如"立案日期"→日期、"涉案金额"→金额）。
        </Typography.Paragraph>
        <Table size="small" rowKey="key" pagination={false}
          dataSource={rulesTpl?.fields.map((f) => ({ key: f.key, label: f.label })) || []}
          columns={[
            { title: '字段', dataIndex: 'label' },
            {
              title: '必填', dataIndex: 'key', width: 70,
              render: (k: string) => (
                <Switch size="small" checked={!!rulesDraft[k]?.required}
                  onChange={(v) => setRulesDraft((s) => ({ ...s, [k]: { ...s[k], required: v } }))} />
              ),
            },
            {
              title: '格式', dataIndex: 'key', width: 110,
              render: (k: string) => (
                <Select size="small" style={{ width: 100 }} value={rulesDraft[k]?.type || 'text'}
                  options={[{ value: 'text', label: '文本' }, { value: 'date', label: '日期' },
                            { value: 'amount', label: '金额' }, { value: 'number', label: '数字' }]}
                  onChange={(v) => setRulesDraft((s) => ({ ...s, [k]: { ...s[k], type: v } }))} />
              ),
            },
            {
              title: '字数上限', dataIndex: 'key', width: 100,
              render: (k: string) => (
                <Input size="small" style={{ width: 80 }} placeholder="不限"
                  value={rulesDraft[k]?.max_len || ''}
                  onChange={(e) => setRulesDraft((s) => ({
                    ...s, [k]: { ...s[k], max_len: Number(e.target.value.replace(/\D/g, '')) || 0 },
                  }))} />
              ),
            },
          ]} />
      </Modal>

      <Drawer title={detail?.title} open={!!detail} width={860} onClose={() => setDetail(null)}
        extra={detail && (
          <Space>
            {detail.status !== '已定稿' && (
              <Button icon={<RobotOutlined />} onClick={() => setReviewOpen(true)}>智能体校对 / 润色</Button>
            )}
            <Button icon={<FileSearchOutlined />} loading={checking} onClick={runCheck}>排版检查</Button>
            <Button icon={<CopyOutlined />} onClick={copyText}>复制全文</Button>
            {detail.status !== '已定稿' && (user?.role === 'shenli' || user?.role === 'admin') && (
              <Popconfirm title="确认定稿？定稿后不可修改" onConfirm={confirmDoc}>
                <Button type="primary" icon={<CheckOutlined />}>审理确认定稿</Button>
              </Popconfirm>
            )}
            {detail.status === '已定稿' ? <Tag color="green">已定稿</Tag> : <Tag color="gold">待审理确认</Tag>}
          </Space>
        )}>
        {detail && (
          <>
            {detail.warnings?.length ? (
              <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                message={`本稿有 ${detail.warnings.length} 项要素待补充 / 格式待修正`}
                description={<ul style={{ margin: 0, paddingLeft: 18 }}>
                  {detail.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>} />
            ) : null}
            <Space style={{ marginBottom: 12 }}>
              <Button type="primary" icon={<FileWordOutlined />} onClick={exportDocx}>
                导出 Word（公文格式）
              </Button>
              <Button icon={<EyeOutlined />} onClick={() => setPreview(!preview)}>
                {preview ? '切换为编辑模式' : '切换为公文预览'}
              </Button>
            </Space>
            {preview ? (
              <GongwenPreview content={detail.content} />
            ) : (
              <Card size="small" title={<span><EditOutlined /> 修改文书（保存即生成新版本，全程留痕）</span>}
                style={{ marginBottom: 12 }}>
                <Input.TextArea rows={16} value={editText} onChange={(e) => setEditText(e.target.value)} />
                <Space.Compact style={{ width: '100%', marginTop: 8 }}>
                  <Input placeholder="修改说明（留痕）" value={editNote} onChange={(e) => setEditNote(e.target.value)} />
                  <Button type="primary" onClick={save}>保存新版本</Button>
                </Space.Compact>
              </Card>
            )}

            {checkRes && (
              <Card size="small" title={<span><FileSearchOutlined /> 排版规范性检查</span>} style={{ marginTop: 12 }}>
                <Space align="center" style={{ marginBottom: 8 }}>
                  <Progress type="dashboard" size={72} percent={checkRes.score}
                    strokeColor={checkRes.score >= 90 ? '#15803d' : checkRes.score >= 70 ? '#b45309' : '#b91c1c'} />
                  <div>
                    <div>综合评级：
                      <Tag color={checkRes.level === '合格' ? 'green' : checkRes.level === '基本合格' ? 'gold' : 'red'}>
                        {checkRes.level}
                      </Tag>
                    </div>
                    <div style={{ color: '#64748b' }}>共检出 {checkRes.issues.length} 项问题</div>
                  </div>
                </Space>
                <div style={{ maxHeight: 220, overflow: 'auto' }}>
                  {checkRes.issues.length === 0 ? (
                    <Tag color="green">未发现问题，格式符合 GB/T 9704 简化规范</Tag>
                  ) : checkRes.issues.map((it, idx) => (
                    <div key={idx} style={{ marginBottom: 6 }}>
                      <Tag color={SEV_COLOR[it.severity]}>{SEV_LABEL[it.severity]}</Tag>
                      <span style={{ fontSize: 13 }}>{it.message}</span>
                      {it.line != null && <Typography.Text type="secondary" style={{ fontSize: 12 }}>（第{it.line}行）</Typography.Text>}
                    </div>
                  ))}
                </div>
                {checkRes.changed && (
                  <Button style={{ marginTop: 8 }} type="primary" loading={norming} onClick={applyNorm}>
                    一键应用规范化排版
                  </Button>
                )}
              </Card>
            )}

            <Card size="small" title="版本历史（修改留痕）" style={{ marginTop: 12 }}>
              <Timeline items={detail.versions.slice().reverse().map((v) => ({
                color: v.version_no === 1 ? 'blue' : 'green',
                children: (
                  <div>
                    <b>v{v.version_no}</b>{' '}
                    {v.source && <Tag bordered={false} color={v.source === '智能体' ? 'gold' : v.source === '人工' ? 'blue' : 'default'}>{v.source}</Tag>}
                    {v.note || '无说明'} · {v.created_at}
                    {v.source === '智能体' && (
                      <Space size={4} style={{ marginLeft: 6 }}>
                        <Button size="small" type="link" icon={<DiffOutlined />} onClick={() => setCompareVer(v)}>对比</Button>
                        {isAdopted(v) ? <Tag color="green" bordered={false}>已采用</Tag>
                          : detail.status !== '已定稿' && <Button size="small" type="link" onClick={() => adopt(v)}>采用此版本</Button>}
                      </Space>
                    )}
                  </div>
                ),
              }))} />
            </Card>
          </>
        )}
      </Drawer>

      <DocReviewModal open={reviewOpen} doc={detail} onClose={() => setReviewOpen(false)}
        onSaved={async (vno, opts) => {
          if (!detail) return
          const d = await refreshDetail(detail.id)
          if (opts.compare && d) setCompareVer(d.versions.find((v) => v.version_no === vno) || null)
        }} />

      <Modal title={compareVer ? `对比：当前正文 ↔ v${compareVer.version_no}（${compareVer.note}）` : ''} open={!!compareVer}
        width={1100} onCancel={() => setCompareVer(null)}
        footer={[
          <Button key="c" onClick={() => setCompareVer(null)}>关闭</Button>,
          compareVer && detail && detail.status !== '已定稿' && !isAdopted(compareVer)
            ? <Popconfirm key="a" title="用该版本替换当前正文？原文仍保留在版本历史中" onConfirm={() => adopt(compareVer)}>
                <Button type="primary">采用此版本</Button>
              </Popconfirm>
            : null,
        ]}>
        {compareVer && detail && <LineDiff before={detail.content} after={compareVer.content} />}
      </Modal>
    </>
  )
}
