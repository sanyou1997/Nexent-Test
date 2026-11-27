import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Drawer, Form, Input, Select, Tag, Space, message,
  Descriptions, Modal, Typography, Row, Col, Segmented, Empty, Statistic, Spin,
  DatePicker, Breadcrumb, Timeline, Divider, Checkbox, Popconfirm, Alert,
} from 'antd'
import {
  PlusOutlined, FileSearchOutlined, SwapOutlined, CheckCircleOutlined,
  WarningOutlined, BarChartOutlined, DownloadOutlined, FileTextOutlined,
  HistoryOutlined, SafetyOutlined, FileProtectOutlined, SolutionOutlined,
  ClockCircleOutlined, RobotOutlined,
} from '@ant-design/icons'
import { api } from '../api/client'
import AiAssistDrawer from '../components/nexent/AiAssistDrawer'
import { useAuth } from '../auth'
import { pickReceiver } from '../components/ReceiverPicker'

const { TextArea } = Input

const STATUS_COLOR: Record<string, string> = {
  待研判: 'default', 已分办: 'blue', 初核中: 'cyan', 已立案: 'orange',
  审查调查中: 'purple', 已移送审理: 'geekblue', 已处置: 'green', 已了结: 'gray',
}
const LIUZHI_STATUS_COLOR: Record<string, string> = {
  '': 'default', 已采取: 'red', 已延长: 'volcano', 已解除: 'green',
}
const URGENCY_COLOR: Record<string, string> = { 高: 'red', 中: 'orange', 低: 'green' }
const STATUS_FLOW = ['待研判', '已分办', '初核中', '已立案', '审查调查中', '已移送审理', '已处置']
// 已立案及之后：线索处置、立案按钮不再适用（与后端 FILED_STAGES 一致）
const FILED = ['已立案', '审查调查中', '已移送审理', '已处置', '已了结']
const DISPOSITIONS = ['谈话函询', '初步核实', '暂存待查', '予以了结']
const SOURCES = ['信访', '巡视', '审计', '上级交办', '主动发现']

const errMsg = (e: any) => e?.response?.data?.detail || '操作失败'

function MiniBar({ data }: { data: Record<string, number> }) {
  const max = Math.max(1, ...Object.values(data))
  return (
    <div>
      {Object.entries(data).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
        <div key={k} style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
          <div style={{ width: 110, fontSize: 12, color: '#475569' }}>{k}</div>
          <div style={{ flex: 1, background: '#eef1f5', borderRadius: 3, height: 16, margin: '0 8px' }}>
            <div style={{ width: `${v / max * 100}%`, background: '#1d4ed8', height: 16, borderRadius: 3 }} />
          </div>
          <div style={{ width: 30, fontSize: 12, textAlign: 'right' }}>{v}</div>
        </div>
      ))}
    </div>
  )
}

function SlaBadge({ sla }: { sla: any }) {
  if (!sla || !sla.stage) return <span style={{ color: '#b6c0cf' }}>—</span>
  if (sla.overdue) return <Tag color="red">超期 {Math.abs(sla.days_left)}天</Tag>
  if (sla.warn) return <Tag color="orange">{sla.stage} 剩 {sla.days_left}天</Tag>
  return <span style={{ color: '#94a3b8' }}>{sla.stage} 剩 {sla.days_left}天</span>
}

function LiuzhiBadge({ c }: { c: any }) {
  if (!c.liuzhi_status) return <span style={{ color: '#b6c0cf' }}>—</span>
  if (c.liuzhi_status === '已解除') return <Tag color="green">已解除</Tag>
  const ls = c.liuzhi_sla
  if (ls?.overdue) return <Tag color="red">留置超期 {Math.abs(ls.days_left)}天</Tag>
  if (ls?.warn) return <Tag color="orange">留置剩 {ls.days_left}天</Tag>
  return <Tag color="red">留置中 剩 {ls?.days_left}天</Tag>
}

export default function Cases() {
  const { user } = useAuth()
  const [view, setView] = useState<'list' | 'supervise' | 'stats'>('list')
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(10)
  const [loading, setLoading] = useState(false)
  const [stats, setStats] = useState<any>(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [fSource, setFSource] = useState('')
  const [fStatus, setFStatus] = useState('')
  const [fUrg, setFUrg] = useState('')
  const [fOverdue, setFOverdue] = useState(false)
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')
  const [detail, setDetail] = useState<any>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [timeline, setTimeline] = useState<any[]>([])
  const [interros, setInterros] = useState<any[]>([])
  const [docData, setDocData] = useState<any>(null)
  const [docOpen, setDocOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [petitions, setPetitions] = useState<any[]>([])
  const [adv, setAdv] = useState<{ open: boolean; row: any; to: string; note: string }>({ open: false, row: null, to: '', note: '' })
  const [disp, setDisp] = useState<{ open: boolean; row: any; disposition: string; note: string; meeting_note: string }>({ open: false, row: null, disposition: '谈话函询', note: '', meeting_note: '' })
  const [liuzhi, setLiuzhi] = useState<{ open: boolean; row: any; approved_by: string; start: any }>({ open: false, row: null, approved_by: '', start: null })
  const [interro, setInterro] = useState<{ open: boolean; row: any; interrogated: string; interrogator: string; at: any; location: string; duration_hours: number; rights_informed: boolean; rest_meal_ok: boolean; note: string }>({ open: false, row: null, interrogated: '', interrogator: '', at: null, location: '', duration_hours: 2, rights_informed: true, rest_meal_ok: true, note: '' })
  const [transfer, setTransfer] = useState<{ open: boolean; row: any; title: string; summary: string }>({ open: false, row: null, title: '', summary: '' })
  const [form] = Form.useForm()

  const load = () => {
    setLoading(true)
    const qs = new URLSearchParams({ page: String(page), per_page: String(perPage) })
    if (fSource) qs.set('source', fSource)
    if (fStatus) qs.set('status', fStatus)
    if (fUrg) qs.set('urgency', fUrg)
    if (q) qs.set('q', q)
    if (fOverdue) qs.set('overdue', '1')
    api.get(`/cases?${qs.toString()}`)
      .then(({ data }) => { setRows(data.items || []); setTotal(data.total || 0) })
      .catch(() => { setRows([]); setTotal(0) })
      .finally(() => setLoading(false))
  }
  const loadStats = () => {
    setStatsLoading(true)
    api.get('/cases/stats').then(({ data }) => setStats(data)).catch(() => setStats(null)).finally(() => setStatsLoading(false))
  }
  useEffect(() => { load() }, [page, perPage, fSource, fStatus, fUrg, q, fOverdue])
  useEffect(() => { if (view === 'stats') loadStats() }, [view])

  const [allRows, setAllRows] = useState<any[]>([])
  useEffect(() => {
    if (view !== 'supervise') return
    const qs = new URLSearchParams({ per_page: '200' })
    if (fSource) qs.set('source', fSource)
    api.get(`/cases?${qs.toString()}`).then(({ data }) => setAllRows(data.items || [])).catch(() => setAllRows([]))
  }, [view, fSource, rows])

  const [aiOpen, setAiOpen] = useState(false)
  // 工作台待办 / 深链接：/cases?open=<id> 直接打开详情
  useEffect(() => {
    const op = Number(new URLSearchParams(window.location.search).get('open') || 0)
    if (op) { openDetail({ id: op }); window.history.replaceState(null, '', '/cases') }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openDetail = (r: any) => {
    api.get(`/cases/${r.id}`).then(({ data }) => {
      setDetail(data)
      setDetailOpen(true)
      api.get(`/cases/${r.id}/timeline`).then(({ data: t }) => setTimeline(t.items || [])).catch(() => setTimeline([]))
      api.get(`/cases/${r.id}/interrogations`).then(({ data: i }) => setInterros(i.items || [])).catch(() => setInterros([]))
    }).catch(() => {})
  }

  const submitAdvance = () => {
    if (!adv.row) return
    api.post(`/cases/${adv.row.id}/advance`, { status: adv.to, note: adv.note })
      .then(() => { message.success('已流转'); setAdv({ ...adv, open: false }); load(); if (detail?.id === adv.row.id) openDetail(adv.row) })
      .catch((e) => message.error(errMsg(e)))
  }
  const submitDispose = () => {
    if (!disp.row) return
    api.post(`/cases/${disp.row.id}/dispose`, {
      disposition: disp.disposition, note: disp.note, meeting_note: disp.meeting_note,
    })
      .then(() => { message.success('已登记处置'); setDisp({ ...disp, open: false }); load(); if (detail?.id === disp.row.id) openDetail(disp.row) })
      .catch((e) => message.error(errMsg(e)))
  }
  const doOpen = (r: any) => {
    Modal.confirm({
      title: `对「${r.subject || r.code}」立案并生成立案决定书？`,
      content: '立案后系统自动生成《立案决定书》AI 初稿，须经人工审核定稿后使用。',
      onOk: () => api.post(`/cases/${r.id}/open`, {}).then(() => {
        message.success('已立案，立案决定书已生成（AI初稿）')
        load()
        if (detail?.id === r.id) openDetail(r)
      }).catch((e) => message.error(errMsg(e))),
    })
  }
  const showDoc = (r: any) => {
    api.get(`/cases/${r.id}/doc`).then(({ data }) => { setDocData(data); setDocOpen(true) }).catch((e) => message.error(errMsg(e)))
  }
  const exportLedger = async () => {
    try {
      const qs = new URLSearchParams()
      if (fSource) qs.set('source', fSource)
      if (fStatus) qs.set('status', fStatus)
      if (fUrg) qs.set('urgency', fUrg)
      if (q) qs.set('q', q)
      const { data } = await api.get(`/cases/export?${qs.toString()}`, { responseType: 'blob', timeout: 120000 })
      const url = window.URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = url
      a.download = `线索台账_${new Date().toISOString().slice(0, 10)}.xlsx`
      a.click()
      window.URL.revokeObjectURL(url)
      message.success('线索台账已导出')
    } catch (e) { message.error(errMsg(e)) }
  }

  const submitCreate = async () => {
    const v = await form.validateFields()
    const body: any = { ...v }
    if (v.due_at) body.due_at = v.due_at.format('YYYY-MM-DD')
    api.post('/cases', body).then(() => { message.success('线索已登记'); setCreateOpen(false); form.resetFields(); load() })
      .catch((e) => message.error(errMsg(e)))
  }

  // —— 案管全流程合规动作 ——
  const submitLiuzhi = () => {
    if (!liuzhi.row) return
    const body: any = { approved_by: liuzhi.approved_by }
    if (liuzhi.start) body.start = liuzhi.start.format('YYYY-MM-DD')
    api.post(`/cases/${liuzhi.row.id}/liuzhi`, body)
      .then(() => { message.success('已采取留置（期限 3 个月）'); setLiuzhi({ ...liuzhi, open: false }); if (detail?.id === liuzhi.row.id) openDetail(liuzhi.row) })
      .catch((e) => message.error(errMsg(e)))
  }
  const doLiuzhiExtend = (r: any) => Modal.confirm({
    title: '留置延长一次？', content: '自原届满日再延长 3 个月（法定最长 6 个月，仅可延长一次）。',
    onOk: () => api.post(`/cases/${r.id}/liuzhi/extend`, {}).then(() => { message.success('已延长留置'); if (detail?.id === r.id) openDetail(r) }).catch((e) => message.error(errMsg(e))),
  })
  const doLiuzhiNotify = (r: any) => Modal.confirm({
    title: '登记留置后 24 小时通知？', content: '确认已通知被留置人所在单位/家属（合规留痕）。',
    onOk: () => api.post(`/cases/${r.id}/liuzhi/notify`, {}).then(() => { message.success('已登记通知'); if (detail?.id === r.id) openDetail(r) }).catch((e) => message.error(errMsg(e))),
  })
  const doLiuzhiRelease = (r: any) => Modal.confirm({
    title: '解除留置？', content: '审查调查结束或变更措施时解除留置。',
    onOk: () => api.post(`/cases/${r.id}/liuzhi/release`, {}).then(() => { message.success('已解除留置'); if (detail?.id === r.id) openDetail(r) }).catch((e) => message.error(errMsg(e))),
  })
  const submitInterro = () => {
    if (!interro.row) return
    const body: any = {
      interrogated: interro.interrogated, interrogator: interro.interrogator, location: interro.location,
      duration_hours: interro.duration_hours, rights_informed: interro.rights_informed,
      rest_meal_ok: interro.rest_meal_ok, note: interro.note,
    }
    if (interro.at) body.at = interro.at.format('YYYY-MM-DDTHH:mm')
    api.post(`/cases/${interro.row.id}/interrogations`, body)
      .then(() => { message.success('讯问记录已登记'); setInterro({ ...interro, open: false }); if (detail?.id === interro.row.id) openDetail(interro.row) })
      .catch((e) => message.error(errMsg(e)))
  }
  const delInterro = (r: any, iid: number) => {
    api.delete(`/cases/${r.id}/interrogations/${iid}`).then(() => { message.success('已删除'); if (detail?.id === r.id) openDetail(r) }).catch((e) => message.error(errMsg(e)))
  }
  const submitTransfer = async () => {
    if (!transfer.row) return
    const dept = await pickReceiver('trials', '移送审理')
    if (dept === undefined) return
    api.post(`/cases/${transfer.row.id}/transfer-trial`, { title: transfer.title, summary: transfer.summary, dept_id: dept })
      .then(({ data }) => {
        const to = data?.trial?.dept_name
        message.success(`已移送审理（生成关联审理事项${to ? `，承办部门：${to}` : ''}）`)
        setTransfer({ ...transfer, open: false }); load(); if (detail?.id === transfer.row.id) openDetail(transfer.row)
      })
      .catch((e) => message.error(errMsg(e)))
  }

  const columns = [
    { title: '案号', dataIndex: 'code', width: 120 },
    { title: '被反映人', dataIndex: 'subject', width: 90 },
    { title: '单位/职务', dataIndex: 'unit', width: 130, ellipsis: true },
    { title: '问题类型', dataIndex: 'problem_type', width: 110, ellipsis: true },
    { title: '来源', dataIndex: 'source', width: 70, render: (s: string) => <Tag>{s}</Tag> },
    { title: '紧急度', dataIndex: 'urgency', width: 60, render: (u: string) => <Tag color={URGENCY_COLOR[u]}>{u}</Tag> },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
    { title: '时限合规', dataIndex: ['sla', 'stage'], width: 130, render: (_: any, r: any) => <SlaBadge sla={r.sla} /> },
    { title: '留置', dataIndex: 'liuzhi_status', width: 90, render: (_: any, r: any) => <LiuzhiBadge c={r} /> },
    {
      title: '操作', key: 'op', width: 250, fixed: 'right' as const,
      render: (_: any, r: any) => (
        <Space size={2}>
          <Button size="small" onClick={() => openDetail(r)}>详情</Button>
          {!['已处置', '已了结'].includes(r.status) && (
            <Button size="small" type="link" icon={<SwapOutlined />}
              onClick={() => {
                const cur = STATUS_FLOW.indexOf(r.status)
                setAdv({ open: true, row: r, to: cur >= 0 && cur < STATUS_FLOW.length - 1 ? STATUS_FLOW[cur + 1] : STATUS_FLOW[0], note: '' })
              }}>流转</Button>
          )}
          {!FILED.includes(r.status) && (
            <Button size="small" type="link" icon={<WarningOutlined />}
              onClick={() => setDisp({ open: true, row: r, disposition: '谈话函询', note: '', meeting_note: '' })}>处置</Button>
          )}
          {r.opened_doc_id ? (
            <Button size="small" type="link" icon={<FileTextOutlined />} onClick={() => showDoc(r)}>决定书</Button>
          ) : !FILED.includes(r.status) && (
            <Button size="small" type="link" icon={<CheckCircleOutlined />} onClick={() => doOpen(r)}>立案</Button>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Breadcrumb items={[{ title: '业务办理' }, { title: '案管（案件监督管理）' }]} />
        <Space>
          <Segmented value={view} onChange={(v) => setView(v as any)} options={[
            { label: '线索台账', value: 'list', icon: <FileSearchOutlined /> },
            { label: '督办看板', value: 'supervise', icon: <WarningOutlined /> },
            { label: '数据统计', value: 'stats', icon: <BarChartOutlined /> },
          ]} />
          <Button icon={<DownloadOutlined />} onClick={exportLedger}>导出台账</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => {
            api.get('/petitions?per_page=200').then(({ data }) => setPetitions(data.items || data)).catch(() => setPetitions([]))
            setCreateOpen(true)
          }}>新建线索</Button>
        </Space>
      </div>

      {view === 'list' && (
        <Card size="small">
          <Space style={{ marginBottom: 10 }} wrap>
            <Input.Search placeholder="案号/被反映人/单位/案由/内容" allowClear style={{ width: 240 }}
              value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
              onSearch={(v) => { setQ(v || ''); setPage(1) }} />
            <Select placeholder="来源" allowClear style={{ width: 120 }} value={fSource || undefined} onChange={(v) => { setFSource(v || ''); setPage(1) }}
              options={SOURCES.map((s) => ({ label: s, value: s }))} />
            <Select placeholder="状态" allowClear style={{ width: 140 }} value={fStatus || undefined} onChange={(v) => { setFStatus(v || ''); setPage(1) }}
              options={Object.keys(STATUS_COLOR).map((s) => ({ label: s, value: s }))} />
            <Select placeholder="紧急度" allowClear style={{ width: 100 }} value={fUrg || undefined} onChange={(v) => { setFUrg(v || ''); setPage(1) }}
              options={['高', '中', '低'].map((s) => ({ label: s, value: s }))} />
            <Button danger={fOverdue} type={fOverdue ? 'primary' : 'default'} icon={<ClockCircleOutlined />}
              onClick={() => { setFOverdue(!fOverdue); setPage(1) }}>超期</Button>
          </Space>
          <Table rowKey="id" size="small" loading={loading} columns={columns} dataSource={rows}
            scroll={{ x: 1280 }}
            pagination={{
              current: page, pageSize: perPage, total,
              showSizeChanger: true, showTotal: (t) => `共 ${t} 条`,
              onChange: (p, ps) => { setPage(p); setPerPage(ps) },
            }} />
        </Card>
      )}

      {view === 'supervise' && (
        <Row gutter={12}>
          {[
            { key: 'normal', title: '正常在办', color: '#15803d', filter: (r: any) => !r.sla?.stage || (!r.sla.overdue && !r.sla.warn) },
            { key: 'soon', title: '临期（≤7天）', color: '#b45309', filter: (r: any) => r.sla?.warn && !r.sla.overdue },
            { key: 'over', title: '超期', color: '#b91c1c', filter: (r: any) => r.sla?.overdue },
            { key: 'liuzhi', title: '留置在册', color: '#b91c1c', filter: (r: any) => r.liuzhi_status === '已采取' || r.liuzhi_status === '已延长' },
          ].map((g) => (
            <Col span={6} key={g.key}>
              <Card size="small" title={<span style={{ color: g.color }}>{g.title}</span>} style={{ minHeight: 320 }}>
                {allRows.filter(g.filter).length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}
                {allRows.filter(g.filter).map((r) => (
                  <div key={r.id} style={{ padding: '6px 0', borderBottom: '1px solid #f6f8fb', cursor: 'pointer' }}
                    onClick={() => openDetail(r)}>
                    <div style={{ fontWeight: 500 }}>{r.subject || r.code}
                      <Tag color={URGENCY_COLOR[r.urgency]} style={{ marginLeft: 6 }}>{r.urgency}</Tag></div>
                    <div style={{ fontSize: 12, color: '#64748b' }}>
                      {g.key === 'liuzhi'
                        ? `留置${r.liuzhi_sla ? '剩 ' + r.liuzhi_sla.days_left + '天' : ''} · ${r.liuzhi_approved_by || ''}`
                        : `${r.sla?.stage || '—'} · 时限 ${r.sla?.deadline || '—'}`}
                    </div>
                  </div>
                ))}
              </Card>
            </Col>
          ))}
        </Row>
      )}

      {view === 'stats' && (
        <Spin spinning={statsLoading}>
          {stats && (
            <Row gutter={12}>
              <Col span={6}><Card><Statistic title="线索总数" value={stats.total} /></Card></Col>
              <Col span={6}><Card><Statistic title="在办（受时限约束）" value={stats.active_total} /></Card></Col>
              <Col span={6}><Card><Statistic title="临期" value={stats.due_soon} valueStyle={{ color: (stats.due_soon) ? '#b45309' : undefined }} /></Card></Col>
              <Col span={6}><Card><Statistic title="超期" value={stats.overdue} valueStyle={{ color: (stats.overdue) ? '#b91c1c' : undefined }} /></Card></Col>
              <Col span={6}><Card><Statistic title="留置在册" value={stats.liuzhi_active} valueStyle={{ color: (stats.liuzhi_active) ? '#b91c1c' : undefined }} /></Card></Col>
              <Col span={6}><Card><Statistic title="留置超期" value={stats.liuzhi_overdue} valueStyle={{ color: (stats.liuzhi_overdue) ? '#b91c1c' : undefined }} /></Card></Col>
              <Col span={6}><Card><Statistic title="留置已延长" value={stats.liuzhi_extended} /></Card></Col>
              <Col span={6}><Card><Statistic title="移送审理" value={stats.transferred} /></Card></Col>
              <Col span={8}><Card><Statistic title="时限合规率" value={stats.compliance_rate} suffix="%" valueStyle={{ color: stats.compliance_rate >= 90 ? '#15803d' : '#b91c1c' }} /></Card></Col>
              <Col span={8}><Card size="small" title="按来源"><MiniBar data={stats.by_source} /></Card></Col>
              <Col span={8}><Card size="small" title="按状态"><MiniBar data={stats.by_status} /></Card></Col>
              <Col span={24}><Card size="small" title="按问题类型"><MiniBar data={stats.by_type} /></Card></Col>
            </Row>
          )}
        </Spin>
      )}

      <AiAssistDrawer open={aiOpen && !!detail} onClose={() => setAiOpen(false)} user={user}
        subject={detail ? { kind: 'case', id: detail.id, title: `${detail.code} ${detail.subject || ''}：${detail.title || ''}` } : null} />
      <Drawer width={760} open={detailOpen} onClose={() => setDetailOpen(false)} title={`线索详情 ${detail?.code || ''}`}
        extra={detail && <Button type="primary" ghost icon={<RobotOutlined />} onClick={() => setAiOpen(true)}>AI 研判</Button>}>
        {detail && (
          <>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="被反映人">{detail.subject}</Descriptions.Item>
              <Descriptions.Item label="单位/职务">{detail.unit}</Descriptions.Item>
              <Descriptions.Item label="案由" span={2}>{detail.title}</Descriptions.Item>
              <Descriptions.Item label="问题类型">{detail.problem_type}</Descriptions.Item>
              <Descriptions.Item label="来源">{detail.source}</Descriptions.Item>
              <Descriptions.Item label="状态"><Tag color={STATUS_COLOR[detail.status]}>{detail.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="紧急度"><Tag color={URGENCY_COLOR[detail.urgency]}>{detail.urgency}</Tag></Descriptions.Item>
              <Descriptions.Item label="处置方式">{detail.disposition || '—'}</Descriptions.Item>
              <Descriptions.Item label="关联信访">{detail.linked_petition_code || '—'}</Descriptions.Item>
              <Descriptions.Item label="时限合规" span={2}>
                <SlaBadge sla={detail.sla} />
                {detail.sla?.deadline && <span style={{ color: '#94a3b8', marginLeft: 8 }}>届满 {detail.sla.deadline}</span>}
              </Descriptions.Item>
              <Descriptions.Item label="初核开始">{detail.chuhe_at || '—'}</Descriptions.Item>
              <Descriptions.Item label="立案时间">{detail.opened_at || '—'}</Descriptions.Item>
              <Descriptions.Item label="留置状态"><Tag color={LIUZHI_STATUS_COLOR[detail.liuzhi_status]}>{detail.liuzhi_status || '未采取'}</Tag></Descriptions.Item>
              <Descriptions.Item label="留置期限">{detail.liuzhi_end || '—'}{detail.liuzhi_extended ? '（已延长）' : ''}</Descriptions.Item>
              <Descriptions.Item label="移送审理">{detail.trial_id ? `审理事项 #${detail.trial_id}` : '—'}</Descriptions.Item>
              <Descriptions.Item label="移送时间">{detail.transferred_at || '—'}</Descriptions.Item>
              <Descriptions.Item label="反映内容" span={2}>
                <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{detail.content}</Typography.Paragraph>
              </Descriptions.Item>
              <Descriptions.Item label="处置依据" span={2}>{detail.disposition_note || '—'}</Descriptions.Item>
              <Descriptions.Item label="会议结论" span={2}>{detail.meeting_note || '—'}</Descriptions.Item>
            </Descriptions>

            <Space style={{ marginTop: 12 }} wrap>
              {!['已处置', '已了结'].includes(detail.status) && (
                <Button size="small" icon={<SwapOutlined />} onClick={() => {
                  const cur = STATUS_FLOW.indexOf(detail.status)
                  setAdv({ open: true, row: detail, to: cur >= 0 && cur < STATUS_FLOW.length - 1 ? STATUS_FLOW[cur + 1] : STATUS_FLOW[0], note: '' })
                }}>状态流转</Button>
              )}
              {!FILED.includes(detail.status) && (
                <Button size="small" icon={<WarningOutlined />}
                  onClick={() => setDisp({ open: true, row: detail, disposition: detail.disposition || '谈话函询', note: detail.disposition_note || '', meeting_note: detail.meeting_note || '' })}>分办处置</Button>
              )}
              {detail.opened_doc_id
                ? <Button size="small" icon={<FileTextOutlined />} onClick={() => showDoc(detail)}>查看立案决定书</Button>
                : !FILED.includes(detail.status) && <Button size="small" type="primary" icon={<CheckCircleOutlined />} onClick={() => doOpen(detail)}>立案并生成决定书</Button>}
              {(detail.status === '已立案' || detail.status === '审查调查中') && !detail.trial_id && (
                <Button size="small" icon={<SolutionOutlined />}
                  onClick={() => setTransfer({ open: true, row: detail, title: '', summary: '' })}>移送审理</Button>
              )}
              {detail.trial_id && <Tag color="geekblue">已移送审理（#{detail.trial_id}）</Tag>}
            </Space>

            {/* —— 留置措施合规 —— */}
            <Divider orientation="left" plain><SafetyOutlined /> 留置措施合规</Divider>
            {!detail.liuzhi_status && (
              <Alert type="info" showIcon message="本案尚未采取留置措施" action={
                <Button size="small" type="primary" onClick={() => setLiuzhi({ open: true, row: detail, approved_by: '', start: null })}>采取留置</Button>
              } />
            )}
            {detail.liuzhi_status && (
              <Card size="small">
                <Descriptions column={2} size="small">
                  <Descriptions.Item label="留置状态"><Tag color={LIUZHI_STATUS_COLOR[detail.liuzhi_status]}>{detail.liuzhi_status}</Tag></Descriptions.Item>
                  <Descriptions.Item label="审批单位/人">{detail.liuzhi_approved_by || '—'}</Descriptions.Item>
                  <Descriptions.Item label="开始">{detail.liuzhi_start || '—'}</Descriptions.Item>
                  <Descriptions.Item label="届满">{detail.liuzhi_end || '—'}{detail.liuzhi_extended ? '（已延长一次）' : ''}</Descriptions.Item>
                  <Descriptions.Item label="期限倒计时" span={2}>
                    <LiuzhiBadge c={detail} />
                    {detail.liuzhi_notify_at ? <span style={{ color: '#15803d', marginLeft: 8 }}>已通知（{detail.liuzhi_notify_at}）</span>
                      : <span style={{ color: '#b45309', marginLeft: 8 }}>未登记 24h 通知</span>}
                  </Descriptions.Item>
                </Descriptions>
                <Space style={{ marginTop: 8 }} wrap>
                  {detail.liuzhi_status === '已采取' && <Button size="small" onClick={() => doLiuzhiExtend(detail)}>延长一次</Button>}
                  {!detail.liuzhi_notify_at && <Button size="small" onClick={() => doLiuzhiNotify(detail)}>登记 24h 通知</Button>}
                  {detail.liuzhi_status !== '已解除' && <Button size="small" danger onClick={() => doLiuzhiRelease(detail)}>解除留置</Button>}
                  {detail.liuzhi_status === '已解除' && <span style={{ color: '#15803d' }}>已于 {detail.liuzhi_release_at} 解除</span>}
                </Space>
              </Card>
            )}

            {/* —— 讯问合规台账 —— */}
            <Divider orientation="left" plain><FileProtectOutlined /> 讯问合规台账</Divider>
            <Card size="small">
              <Space style={{ marginBottom: 8 }}>
                <Button size="small" type="primary" icon={<PlusOutlined />}
                  disabled={!['已立案', '审查调查中', '已移送审理'].includes(detail.status)}
                  onClick={() => setInterro({ open: true, row: detail, interrogated: detail.subject || '', interrogator: '', at: null, location: '', duration_hours: 2, rights_informed: true, rest_meal_ok: true, note: '' })}>
                  新增讯问
                </Button>
                {interros.length > 0 && <span style={{ color: '#64748b', fontSize: 12 }}>累计 {interros.reduce((s: number, x: any) => s + (x.duration_hours || 0), 0)} 小时</span>}
              </Space>
              {interros.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无讯问记录" />}
              {interros.map((it) => (
                <div key={it.id} style={{ padding: '6px 0', borderBottom: '1px solid #f6f8fb' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <b>{it.interrogated}</b> <span style={{ color: '#94a3b8', fontSize: 12 }}>被讯问 · {it.at}</span>
                      <Tag style={{ marginLeft: 6 }}>{it.duration_hours}小时</Tag>
                      {it.rights_informed ? <Tag color="green">已告知权利</Tag> : <Tag color="red">未告知权利</Tag>}
                      {it.rest_meal_ok ? <Tag color="green">饮食休息保障</Tag> : <Tag color="red">未保障</Tag>}
                    </div>
                    <Popconfirm title="删除该讯问记录？" onConfirm={() => delInterro(detail, it.id)}>
                      <Button size="small" type="link" danger>删除</Button>
                    </Popconfirm>
                  </div>
                  {it.compliance_issues?.length > 0 && (
                    <div style={{ marginTop: 4 }}>
                      {it.compliance_issues.map((x: string, i: number) => <Tag key={i} color="red">{x}</Tag>)}
                    </div>
                  )}
                  {it.note && <div style={{ fontSize: 12, color: '#475569', marginTop: 2 }}>{it.note}</div>}
                </div>
              ))}
            </Card>

            <Divider orientation="left" plain><HistoryOutlined /> 流程轨迹</Divider>
            {timeline.length ? (
              <Timeline
                items={timeline.map((t) => ({
                  color: ['case_open', 'case_liuzhi'].includes(t.action) ? 'red'
                    : t.action === 'case_dispose' ? 'orange' : 'blue',
                  children: (
                    <div>
                      <div><b>{t.label}</b> <span style={{ color: '#94a3b8', fontSize: 12 }}>{t.created_at} · {t.username}</span></div>
                      <div style={{ color: '#475569', fontSize: 12 }}>{t.desc}</div>
                    </div>
                  ),
                }))}
              />
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无流转记录" />}

            <Card size="small" title="关联文书" style={{ marginTop: 12 }}>
              {detail.documents?.length ? detail.documents.map((d: any) => (
                <div key={d.id}>{d.title} <Tag>{d.status}</Tag></div>
              )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无" />}
            </Card>
          </>
        )}
      </Drawer>

      <Modal title="立案决定书（AI 初稿，须人工审核）" open={docOpen} onCancel={() => setDocOpen(false)} width={760} footer={[
        <Button key="close" onClick={() => setDocOpen(false)}>关闭</Button>,
        <Button key="dl" type="primary" icon={<DownloadOutlined />} onClick={async () => {
          if (!detail?.id) return
          try {
            const { data } = await api.get(`/cases/${detail.id}/doc/export`, { responseType: 'blob', timeout: 60000 })
            const url = window.URL.createObjectURL(data)
            const a = document.createElement('a')
            a.href = url
            a.download = `${docData?.case_code || detail.code}_立案决定书.txt`
            a.click()
            window.URL.revokeObjectURL(url)
          } catch (e) { message.error(errMsg(e)) }
        }}>下载</Button>,
      ]}>
        <Descriptions column={2} size="small" style={{ marginBottom: 8 }}>
          <Descriptions.Item label="案号">{docData?.case_code}</Descriptions.Item>
          <Descriptions.Item label="立案时间">{docData?.opened_at || '—'}</Descriptions.Item>
        </Descriptions>
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', maxHeight: 420, overflow: 'auto', background: '#f8fafc', padding: 12 }}>
          {docData?.content}
        </Typography.Paragraph>
      </Modal>

      <Modal title={`状态流转 · ${adv.row?.subject || adv.row?.code || ''}`} open={adv.open}
        onOk={submitAdvance} onCancel={() => setAdv({ ...adv, open: false })} okText="确认流转" destroyOnClose>
        <div style={{ marginBottom: 8, color: '#64748b' }}>
          当前状态：<Tag color={STATUS_COLOR[adv.row?.status]}>{adv.row?.status}</Tag>
          （状态不可回退，仅可流转到当前阶段及之后）
        </div>
        <Form layout="vertical">
          <Form.Item label="流转到">
            <Select value={adv.to} onChange={(v) => setAdv({ ...adv, to: v })}
              options={STATUS_FLOW.map((s, i) => ({
                label: s, value: s,
                disabled: STATUS_FLOW.indexOf(adv.row?.status) >= 0 && i < STATUS_FLOW.indexOf(adv.row?.status),
              }))} />
          </Form.Item>
          <Form.Item label="备注（记入会议结论）">
            <TextArea rows={3} value={adv.note} onChange={(e) => setAdv({ ...adv, note: e.target.value })}
              placeholder="如：经2026年第X次专题会议研究同意" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`分办处置 · ${disp.row?.subject || disp.row?.code || ''}`} open={disp.open}
        onOk={submitDispose} onCancel={() => setDisp({ ...disp, open: false })} okText="确认处置" destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="处置方式">
            <Select value={disp.disposition} onChange={(v) => setDisp({ ...disp, disposition: v })}
              options={DISPOSITIONS.map((s) => ({ label: s, value: s }))} />
          </Form.Item>
          <Form.Item label="处置依据">
            <TextArea rows={3} value={disp.note} onChange={(e) => setDisp({ ...disp, note: e.target.value })}
              placeholder="如：依据《监督执纪工作规则》第X条，反映问题具体、可查性较强" />
          </Form.Item>
          <Form.Item label="会议结论">
            <TextArea rows={2} value={disp.meeting_note} onChange={(e) => setDisp({ ...disp, meeting_note: e.target.value })}
              placeholder="如：经XX会议研究决定" />
          </Form.Item>
          {disp.disposition === '予以了结' && (
            <div style={{ color: '#b91c1c' }}>选择「予以了结」后，该线索状态将置为「已了结」。</div>
          )}
        </Form>
      </Modal>

      <Modal title={`采取留置措施 · ${liuzhi.row?.subject || liuzhi.row?.code || ''}`} open={liuzhi.open}
        onOk={submitLiuzhi} onCancel={() => setLiuzhi({ ...liuzhi, open: false })} okText="确认采取" destroyOnClose>
        <Alert type="warning" showIcon style={{ marginBottom: 10 }}
          message="留置须严格审批，期限 3 个月；特殊情况可延长一次（再 3 个月，法定最长 6 个月）；采取后 24 小时内通知被留置人所在单位/家属。" />
        <Form layout="vertical">
          <Form.Item label="审批单位/审批人">
            <Input value={liuzhi.approved_by} onChange={(e) => setLiuzhi({ ...liuzhi, approved_by: e.target.value })}
              placeholder="如：经XX监委审批" />
          </Form.Item>
          <Form.Item label="留置开始日期">
            <DatePicker style={{ width: '100%' }} value={liuzhi.start} onChange={(v) => setLiuzhi({ ...liuzhi, start: v })} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`讯问记录 · ${interro.row?.subject || interro.row?.code || ''}`} open={interro.open}
        onOk={submitInterro} onCancel={() => setInterro({ ...interro, open: false })} okText="登记" destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="被讯问人"><Input value={interro.interrogated} onChange={(e) => setInterro({ ...interro, interrogated: e.target.value })} /></Form.Item>
          <Form.Item label="讯问人"><Input value={interro.interrogator} onChange={(e) => setInterro({ ...interro, interrogator: e.target.value })} /></Form.Item>
          <Form.Item label="讯问时间"><DatePicker showTime style={{ width: '100%' }} value={interro.at} onChange={(v) => setInterro({ ...interro, at: v })} /></Form.Item>
          <Form.Item label="讯问地点"><Input value={interro.location} onChange={(e) => setInterro({ ...interro, location: e.target.value })} /></Form.Item>
          <Form.Item label="单次讯问时长（小时）"><Input type="number" value={interro.duration_hours} onChange={(e) => setInterro({ ...interro, duration_hours: Number(e.target.value) })} /></Form.Item>
          <Form.Item label="合规要点">
            <Checkbox checked={interro.rights_informed} onChange={(e) => setInterro({ ...interro, rights_informed: e.target.checked })}>已告知权利义务</Checkbox>
            <Checkbox checked={interro.rest_meal_ok} onChange={(e) => setInterro({ ...interro, rest_meal_ok: e.target.checked })}>保障饮食与休息</Checkbox>
          </Form.Item>
          <Form.Item label="备注"><TextArea rows={2} value={interro.note} onChange={(e) => setInterro({ ...interro, note: e.target.value })} /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`移送审理 · ${transfer.row?.subject || transfer.row?.code || ''}`} open={transfer.open}
        onOk={submitTransfer} onCancel={() => setTransfer({ ...transfer, open: false })} okText="确认移送" destroyOnClose>
        <Alert type="info" showIcon style={{ marginBottom: 10 }}
          message="审查调查结束后移送审理室；系统将自动生成关联审理事项，案件置「已移送审理」并阻断再次处置。" />
        <Form layout="vertical">
          <Form.Item label="审理事项标题"><Input value={transfer.title} onChange={(e) => setTransfer({ ...transfer, title: e.target.value })} placeholder="留空则按案由自动生成" /></Form.Item>
          <Form.Item label="阅卷摘要（可选）"><TextArea rows={3} value={transfer.summary} onChange={(e) => setTransfer({ ...transfer, summary: e.target.value })} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="新建线索" open={createOpen} onOk={submitCreate} onCancel={() => setCreateOpen(false)} width={640} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item label="从信访件转入" name="from_petition">
            <Select placeholder="可选：关联已有信访件（自动带入要素）" allowClear showSearch optionFilterProp="label"
              options={petitions.map((p) => ({ label: `${p.code} ${p.petitioner}`, value: p.id }))} />
          </Form.Item>
          <Form.Item label="被反映人" name="subject"><Input placeholder="姓名" /></Form.Item>
          <Form.Item label="单位/职务" name="unit"><Input /></Form.Item>
          <Form.Item label="案由" name="title"><Input /></Form.Item>
          <Form.Item label="问题类型" name="problem_type"><Input placeholder="如：违反廉洁纪律/职务违法" /></Form.Item>
          <Form.Item label="反映内容" name="content"><TextArea rows={4} /></Form.Item>
          <Space>
            <Form.Item label="来源" name="source" initialValue="信访">
              <Select style={{ width: 140 }} options={SOURCES.map((s) => ({ label: s, value: s }))} />
            </Form.Item>
            <Form.Item label="紧急度" name="urgency" initialValue="中">
              <Select style={{ width: 100 }} options={['高', '中', '低'].map((s) => ({ label: s, value: s }))} />
            </Form.Item>
            <Form.Item label="审查调查时限" name="due_at"><DatePicker /></Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  )
}
