import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Drawer, Form, Input, Select, Tag, Space, message,
  Descriptions, Modal, Typography, Row, Col, Segmented, Empty, Statistic, Spin,
  Breadcrumb, Timeline, Popconfirm, Alert,
} from 'antd'
import {
  PlusOutlined, FileSearchOutlined, BarChartOutlined, SwapOutlined,
  NotificationOutlined, DownloadOutlined, EditOutlined, HistoryOutlined,
  SearchOutlined, ClockCircleOutlined, SafetyOutlined, CheckCircleOutlined,
  RedoOutlined, SendOutlined, WarningOutlined,
} from '@ant-design/icons'
import { api } from '../api/client'
import { useAuth } from '../auth'
import RegRefDrawer from './RegRefDrawer'
import { pickReceiver } from '../components/ReceiverPicker'

const STATUS_COLOR: Record<string, string> = {
  待处置: 'default', 整改中: 'blue', 已通报: 'orange', 已办结: 'green',
}
const RECTIFY_STAGE_COLOR: Record<string, string> = {
  待整改: 'default', 整改中: 'blue', 申请销号: 'gold', 已销号: 'green',
}

/** 整改时限合规徽标（复用案管 SLA 范式） */
function SlaBadge({ sla }: { sla: any }) {
  if (!sla || !sla.issued) return <Tag color="default">未下达</Tag>
  if (sla.overdue) return <Tag color="red"><ClockCircleOutlined /> 超期 {Math.abs(sla.days_left)} 天</Tag>
  if (sla.warn) return <Tag color="orange"><ClockCircleOutlined /> 剩 {sla.days_left} 天</Tag>
  return <Tag color="green"><ClockCircleOutlined /> 剩 {sla.days_left} 天</Tag>
}

export default function Conduct() {
  const { user } = useAuth()
  const [view, setView] = useState<'list' | 'stats'>('list')
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [fCat, setFCat] = useState('')
  const [fStatus, setFStatus] = useState('')
  const [fStage, setFStage] = useState('')
  const [fOverdue, setFOverdue] = useState(false)
  const [stats, setStats] = useState<any>(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [detail, setDetail] = useState<any>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [timeline, setTimeline] = useState<any>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [issueOpen, setIssueOpen] = useState(false)
  const [reportOpen, setReportOpen] = useState(false)
  const [iform] = Form.useForm()
  const [pform] = Form.useForm()
  const [form] = Form.useForm()
  // —— 相关依据检索（内嵌法规知识库） ——
  const [regRefOpen, setRegRefOpen] = useState(false)
  const [regRefSeed, setRegRefSeed] = useState('')

  const load = (p = 1) => {
    setLoading(true)
    const qs = new URLSearchParams()
    if (fCat) qs.set('category', fCat)
    if (fStatus) qs.set('status', fStatus)
    if (fStage) qs.set('rectify_stage', fStage)
    if (fOverdue) qs.set('overdue', '1')
    if (search) qs.set('q', search)
    qs.set('page', String(p))
    api.get(`/conduct?${qs.toString()}`).then(({ data }) => {
      setRows(data.items || [])
      setTotal(data.total || 0)
      setPage(p)
    }).catch(() => { setRows([]); setTotal(0) }).finally(() => setLoading(false))
  }
  const loadStats = () => { setStatsLoading(true); api.get('/conduct/stats').then(({ data }) => setStats(data)).catch(() => setStats(null)).finally(() => setStatsLoading(false)) }
  useEffect(() => { load(1) }, [fCat, fStatus, fStage, fOverdue])
  useEffect(() => { if (view === 'stats') loadStats() }, [view])

  const openDetail = (r: any) => {
    setTimeline(null)
    api.get(`/conduct/${r.id}`).then(({ data }) => { setDetail(data); setDetailOpen(true) }).catch(() => {})
    api.get(`/conduct/${r.id}/timeline`).then(({ data }) => setTimeline(data)).catch(() => setTimeline(null))
  }
  const act = async (r: any, path: string, body: any, okMsg: string) => {
    if (path === 'to-case') {   // 跨部门移交：确定承办部门
      const dept = await pickReceiver('clue', '移交案管线索')
      if (dept === undefined) return
      body = { ...body, dept_id: dept }
    }
    api.post(`/conduct/${r.id}/${path}`, body).then(() => { message.success(okMsg); load(page); if (detailOpen) openDetail(r) }).catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const submitCreate = async () => {
    const v = await form.validateFields()
    api.post('/conduct', v).then(() => { message.success('四风问题已登记'); setCreateOpen(false); form.resetFields(); load(1) }).catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const submitIssue = async () => {
    const v = await iform.validateFields()
    if (!detail) return
    api.post(`/conduct/${detail.id}/rectify-issue`, { req: v.req, due_days: v.due_days, note: v.note || '' }).then(() => {
      message.success('已下达整改（启动时限倒计时）')
      setIssueOpen(false); iform.resetFields(); openDetail(detail); load(page)
    }).catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const submitReport = async () => {
    const v = await pform.validateFields()
    if (!detail) return
    api.post(`/conduct/${detail.id}/rectify-report`, { note: v.note }).then(() => {
      message.success('整改进展已报送')
      setReportOpen(false); pform.resetFields(); openDetail(detail); load(page)
    }).catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const downloadExport = async () => {
    const qs = new URLSearchParams()
    if (fCat) qs.set('category', fCat)
    if (fStatus) qs.set('status', fStatus)
    if (fStage) qs.set('rectify_stage', fStage)
    if (search) qs.set('q', search)
    try {
      const { data } = await api.get(`/conduct/export?${qs.toString()}`, { responseType: 'blob' } as any)
      const d = new Date()
      const ymd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
      const url = URL.createObjectURL(new Blob([data]))
      const a = document.createElement('a')
      a.href = url
      a.download = `四风问题台账_${ymd}.xlsx`
      a.click()
      URL.revokeObjectURL(url)
      message.success('台账已导出')
    } catch { message.error('导出失败') }
  }

  const columns = [
    { title: '编号', dataIndex: 'code', width: 130 },
    { title: '涉及人员/单位', dataIndex: 'subject', width: 110 },
    { title: '单位', dataIndex: 'unit', width: 130, ellipsis: true },
    { title: '问题类别', dataIndex: 'category', width: 120, render: (s: string) => <Tag color="volcano">{s}</Tag> },
    { title: '来源', dataIndex: 'source', width: 90 },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
    { title: '整改阶段', dataIndex: 'rectify_stage', width: 90, render: (s: string) => <Tag color={RECTIFY_STAGE_COLOR[s]}>{s || '待整改'}</Tag> },
    { title: '整改时限合规', key: 'sla', width: 130, render: (_: any, r: any) => <SlaBadge sla={r.rectify_sla} /> },
    { title: '通报', dataIndex: 'exposure', width: 70, render: (s: string) => <Tag color={s === '是' ? 'red' : 'default'}>{s}</Tag> },
    {
      title: '操作', key: 'op', width: 210, fixed: 'right' as const,
      render: (_: any, r: any) => (
        <Space size={2}>
          <Button size="small" onClick={() => openDetail(r)}>详情</Button>
          <Button size="small" type="link" icon={<NotificationOutlined />} onClick={() => act(r, 'expose', {}, r.exposure === '是' ? '已取消通报' : '已通报')}>通报</Button>
          <Button size="small" type="link" icon={<SwapOutlined />} onClick={() => act(r, 'to-case', {}, '已转案管线索')} disabled={!!r.linked_case_id}>转线索</Button>
          <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openDetail(r)}>整改</Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Breadcrumb items={[{ title: '业务办理' }, { title: '党风（党风政风监督）' }]} />
        <Space>
          <Segmented value={view} onChange={(v) => setView(v as any)} options={[
            { label: '四风台账', value: 'list', icon: <FileSearchOutlined /> },
            { label: '数据统计', value: 'stats', icon: <BarChartOutlined /> },
          ]} />
          <Button icon={<DownloadOutlined />} onClick={downloadExport}>导出台账</Button>
          <Button icon={<FileSearchOutlined />} onClick={() => { setRegRefSeed(''); setRegRefOpen(true) }}>相关依据检索</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>登记问题</Button>
        </Space>
      </div>

      {view === 'list' && (
        <Card size="small">
          <Space style={{ marginBottom: 10 }} wrap>
            <Input allowClear placeholder="搜索人员/单位/编号/问题" style={{ width: 240 }}
              prefix={<SearchOutlined />} value={search}
              onChange={(e) => setSearch(e.target.value)}
              onPressEnter={() => load(1)}
              onClear={() => load(1)} />
            <Button icon={<SearchOutlined />} onClick={() => load(1)}>搜索</Button>
            <Select placeholder="问题类别" allowClear style={{ width: 160 }} value={fCat || undefined} onChange={(v) => setFCat(v || '')}
              options={['违规吃喝', '公车私用', '收送礼品礼金', '滥发津补贴', '形式主义官僚主义', '其他'].map((s) => ({ label: s, value: s }))} />
            <Select placeholder="状态" allowClear style={{ width: 120 }} value={fStatus || undefined} onChange={(v) => setFStatus(v || '')}
              options={Object.keys(STATUS_COLOR).map((s) => ({ label: s, value: s }))} />
            <Select placeholder="整改阶段" allowClear style={{ width: 130 }} value={fStage || undefined} onChange={(v) => setFStage(v || '')}
              options={Object.keys(RECTIFY_STAGE_COLOR).map((s) => ({ label: s, value: s }))} />
            <Button type={fOverdue ? 'primary' : 'default'} danger={fOverdue} icon={<WarningOutlined />} onClick={() => setFOverdue(!fOverdue)}>
              超期&nbsp;{fOverdue ? '✓' : ''}
            </Button>
          </Space>
          <Table rowKey="id" size="small" loading={loading} columns={columns} dataSource={rows}
            scroll={{ x: 1080 }} pagination={{
              current: page, pageSize: 10, total, showTotal: (t) => `共 ${t} 条`,
              onChange: (p) => load(p),
            }} />
        </Card>
      )}

      {view === 'stats' && (
        <Spin spinning={statsLoading}>
          {stats && (
            <Row gutter={12}>
              <Col span={6}><Card><Statistic title="问题总数" value={stats.total} /></Card></Col>
              <Col span={6}><Card><Statistic title="已通报" value={stats.exposed} valueStyle={{ color: (stats.exposed) ? '#b91c1c' : undefined }} /></Card></Col>
              <Col span={6}><Card><Statistic title="已转线索" value={stats.transferred} /></Card></Col>
              <Col span={6}><Card><Statistic title="办结率" value={stats.total ? Math.round((stats.by_status['已办结'] || 0) / stats.total * 100) : 0} suffix="%" /></Card></Col>
              <Col span={24}><Divider4 text="整改预警看板" /></Col>
              <Col span={6}><Card><Statistic title="在整改" value={stats.rectify_active} valueStyle={{ color: '#1d4ed8' }} /></Card></Col>
              <Col span={6}><Card><Statistic title="临期(≤7天)" value={stats.rectify_due_soon} valueStyle={{ color: (stats.rectify_due_soon) ? '#b45309' : undefined }} /></Card></Col>
              <Col span={6}><Card><Statistic title="超期未销号" value={stats.rectify_overdue} valueStyle={{ color: (stats.rectify_overdue) ? '#b91c1c' : undefined }} /></Card></Col>
              <Col span={6}><Card><Statistic title="销号率" value={stats.rectify_rate} suffix="%" /></Card></Col>
              <Col span={12}><Card size="small" title="按问题类别"><BarListMini data={stats.by_cat} /></Card></Col>
              <Col span={12}><Card size="small" title="按整改阶段"><BarListMini data={stats.by_rectify_stage} /></Card></Col>
              <Col span={12}><Card size="small" title="按状态"><BarListMini data={stats.by_status} /></Card></Col>
              <Col span={12}><Card size="small" title="按来源"><BarListMini data={stats.by_source} /></Card></Col>
            </Row>
          )}
        </Spin>
      )}

      <Drawer width={640} open={detailOpen} onClose={() => setDetailOpen(false)} title={`党风问题 ${detail?.code || ''}`}>
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="涉及人员/单位">{detail.subject}</Descriptions.Item>
              <Descriptions.Item label="单位">{detail.unit}</Descriptions.Item>
              <Descriptions.Item label="问题类别"><Tag color="volcano">{detail.category}</Tag></Descriptions.Item>
              <Descriptions.Item label="来源">{detail.source}</Descriptions.Item>
              <Descriptions.Item label="状态"><Tag color={STATUS_COLOR[detail.status]}>{detail.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="整改阶段"><Tag color={RECTIFY_STAGE_COLOR[detail.rectify_stage]}>{detail.rectify_stage || '待整改'}</Tag></Descriptions.Item>
              <Descriptions.Item label="通报曝光">{detail.exposure}</Descriptions.Item>
              <Descriptions.Item label="转案管线索">{detail.linked_case_code || '—'}</Descriptions.Item>
              <Descriptions.Item label="问题概述"><Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{detail.content}</Typography.Paragraph></Descriptions.Item>
              <Descriptions.Item label="整改要求">{detail.rectify_req || '—'}</Descriptions.Item>
              <Descriptions.Item label="整改下达">{detail.rectify_issued_at || '—'}</Descriptions.Item>
              <Descriptions.Item label="整改期限">{detail.rectify_due_at || '—'}</Descriptions.Item>
              <Descriptions.Item label="时限合规"><SlaBadge sla={detail.rectify_sla} /></Descriptions.Item>
              <Descriptions.Item label="审核销号">{detail.rectify_closed_at ? `${detail.rectify_closed_at} · ${detail.rectify_closed_by}` : '—'}</Descriptions.Item>
            </Descriptions>

            {/* —— 党风整改预警闭环 —— */}
            <Card size="small" style={{ marginTop: 12 }}
              title={<span><SafetyOutlined /> 整改预警闭环</span>}>
              {!detail.rectify_issued_at && (
                <Alert type="info" showIcon style={{ marginBottom: 10 }}
                  message="尚未下达整改" description="点击「下达整改」设定整改要求与期限，系统将启动时限倒计时与临期/超期预警。" />
              )}
              <Space wrap>
                {!detail.rectify_issued_at && (
                  <Button type="primary" icon={<EditOutlined />} onClick={() => { iform.setFieldsValue({ req: detail.rectify_req || '', due_days: 60, note: '' }); setIssueOpen(true) }}>下达整改</Button>
                )}
                {detail.rectify_stage === '整改中' && (
                  <>
                    <Button icon={<SendOutlined />} onClick={() => { pform.setFieldsValue({ note: '' }); setReportOpen(true) }}>报送进展</Button>
                    <Button icon={<CheckCircleOutlined />} onClick={() => act(detail, 'rectify-apply', {}, '已申请销号')}>申请销号</Button>
                  </>
                )}
                {detail.rectify_stage === '申请销号' && (
                  <>
                    <Popconfirm title="确认整改到位、审核销号？" onConfirm={() => act(detail, 'rectify-close', {}, '已审核销号')}>
                      <Button type="primary" icon={<CheckCircleOutlined />}>审核销号</Button>
                    </Popconfirm>
                    <Button icon={<RedoOutlined />} onClick={() => act(detail, 'rectify-reopen', {}, '已退回整改')}>退回整改</Button>
                  </>
                )}
                {detail.rectify_stage === '已销号' && (
                  <Tag color="green" style={{ fontSize: 13 }}>✓ 已销号归档</Tag>
                )}
              </Space>
              {detail.rectify_progress?.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <Typography.Text type="secondary">整改进展历史</Typography.Text>
                  <Timeline style={{ marginTop: 8 }} items={detail.rectify_progress.map((p: any) => ({
                    color: 'blue',
                    children: (<div><div style={{ fontSize: 13 }}>{p.note}</div><div style={{ fontSize: 12, color: '#94a3b8' }}>{p.by} · {p.at}</div></div>),
                  }))} />
                </div>
              )}
            </Card>

            <Space style={{ marginTop: 12 }}>
              <Button icon={<FileSearchOutlined />} onClick={() => { setRegRefSeed(`${detail.category || ''} ${detail.subject || ''}`.trim()); setRegRefOpen(true) }}>相关依据检索</Button>
              <Button icon={<NotificationOutlined />} onClick={() => act(detail, 'expose', {}, '已切换通报')}>通报曝光</Button>
              <Button type="primary" icon={<SwapOutlined />} disabled={!!detail.linked_case_id} onClick={() => act(detail, 'to-case', {}, '已转案管线索')}>移交案管</Button>
            </Space>

            {timeline && (
              <Card size="small" title={<span><HistoryOutlined /> 流程轨迹</span>} style={{ marginTop: 16 }}>
                {timeline.items?.length ? (
                  <Timeline items={timeline.items.map((it: any) => ({
                    color: it.action === 'conduct_to_case' ? 'green' : it.action === 'conduct_expose' ? 'red' : it.action?.startsWith('conduct_rectify') ? 'blue' : 'blue',
                    children: (
                      <div>
                        <div style={{ fontWeight: 600 }}>{it.label}</div>
                        <div style={{ fontSize: 12, color: '#475569' }}>{it.desc}</div>
                        <div style={{ fontSize: 12, color: '#94a3b8' }}>{it.username} · {it.created_at}</div>
                      </div>
                    ),
                  }))} />
                ) : <Empty description="暂无轨迹" />}
              </Card>
            )}
          </>
        )}
      </Drawer>

      <Modal title="登记四风问题" open={createOpen} onOk={submitCreate} onCancel={() => setCreateOpen(false)} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item label="涉及人员/单位" name="subject"><Input /></Form.Item>
          <Form.Item label="单位" name="unit"><Input /></Form.Item>
          <Form.Item label="问题类别" name="category" rules={[{ required: true }]}>
            <Select options={['违规吃喝', '公车私用', '收送礼品礼金', '滥发津补贴', '形式主义官僚主义', '其他'].map((s) => ({ label: s, value: s }))} />
          </Form.Item>
          <Form.Item label="问题概要" name="title"><Input /></Form.Item>
          <Form.Item label="问题概述" name="content"><Input.TextArea rows={3} /></Form.Item>
          <Form.Item label="来源" name="source" initialValue="日常监督">
            <Select options={['日常监督', '节点监督', '巡视巡察', '信访', '媒体'].map((s) => ({ label: s, value: s }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`下达整改 · ${detail?.code || ''}`} open={issueOpen} onOk={submitIssue} onCancel={() => setIssueOpen(false)} destroyOnClose>
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message="整改期限默认 60 日" description="系统将按下达日 + 期限自动倒计时，临期（≤7天）与超期将置红预警；整改完成后须申请销号并经审核方可闭环。" />
        <Form form={iform} layout="vertical">
          <Form.Item label="整改要求" name="req" rules={[{ required: true, message: '请填写整改要求' }]}><Input.TextArea rows={2} placeholder="向被监督单位提出的整改要求" /></Form.Item>
          <Form.Item label="整改期限" name="due_days" initialValue={60}>
            <Select options={[{ label: '30 日', value: 30 }, { label: '60 日（默认）', value: 60 }, { label: '90 日', value: 90 }]} />
          </Form.Item>
          <Form.Item label="首期整改进展（可选）" name="note"><Input.TextArea rows={2} placeholder="可一并填写首期进展" /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`报送整改进展 · ${detail?.code || ''}`} open={reportOpen} onOk={submitReport} onCancel={() => setReportOpen(false)} destroyOnClose>
        <Form form={pform} layout="vertical">
          <Form.Item label="整改进展" name="note" rules={[{ required: true, message: '请填写进展' }]}><Input.TextArea rows={3} placeholder="填写本次整改进展，系统将追加至进展历史" /></Form.Item>
        </Form>
      </Modal>

      <RegRefDrawer open={regRefOpen} onClose={() => setRegRefOpen(false)} seed={regRefSeed} title="党风监督·相关依据" />
    </div>
  )
}

function Divider4({ text }: { text: string }) {
  return (
    <div style={{ fontSize: 13, fontWeight: 600, color: '#1d4ed8', borderLeft: '3px solid #1d4ed8', paddingLeft: 8, margin: '4px 0' }}>
      {text}
    </div>
  )
}

function BarListMini({ data }: { data: Record<string, number> }) {
  const max = Math.max(1, ...Object.values(data || {}))
  return (
    <div>
      {Object.entries(data || {}).map(([k, v]) => (
        <div key={k} style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
          <div style={{ width: 110, fontSize: 12 }}>{k}</div>
          <div style={{ flex: 1, background: '#eef1f5', borderRadius: 3, height: 14, margin: '0 8px' }}>
            <div style={{ width: `${v / max * 100}%`, background: '#1d4ed8', height: 14, borderRadius: 3 }} />
          </div>
          <div style={{ width: 28, fontSize: 12, textAlign: 'right' }}>{v}</div>
        </div>
      ))}
    </div>
  )
}
