import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Drawer, Form, Input, Select, Tag, Space, message,
  Descriptions, Modal, Typography, Row, Col, Segmented, Empty, Statistic, Spin,
  Breadcrumb, Timeline, Alert,
} from 'antd'
import {
  PlusOutlined, FileSearchOutlined, BarChartOutlined, SwapOutlined,
  CheckCircleOutlined, DownloadOutlined, EditOutlined, HistoryOutlined,
  SearchOutlined, ArrowRightOutlined, SafetyOutlined, RiseOutlined,
  ClockCircleOutlined, WarningOutlined,
} from '@ant-design/icons'
import { api } from '../api/client'
import { useAuth } from '../auth'
import { pickReceiver } from '../components/ReceiverPicker'

const INSPECT_COLOR: Record<string, string> = { 准备中: 'default', 进驻中: 'blue', 反馈中: 'orange', 整改中: 'processing', 已结束: 'green' }
const FIX_COLOR: Record<string, string> = { 待整改: 'default', 整改中: 'blue', 已销号: 'green' }
const SEV_COLOR: Record<string, string> = { 高: 'red', 中: 'orange', 低: 'green' }
const EFFECT_COLOR: Record<string, string> = { 未评估: 'default', 好: 'green', 中: 'orange', 差: 'red' }
const INSPECT_FLOW = ['准备中', '进驻中', '反馈中', '整改中', '已结束']
const CAT_OPTIONS = ['违规吃喝', '公车私用', '收送礼品礼金', '滥发津补贴', '形式主义官僚主义', '其他', '未分类']
const FIX_EFFECT_OPTS = ['未评估', '好', '中', '差']
const SLA_MAP: Record<string, { c: string; t: string }> = {
  ok: { c: 'green', t: '正常' }, warn: { c: 'orange', t: '临期' },
  overdue: { c: 'red', t: '超期' }, none: { c: 'default', t: '未设时限' },
}

function SlaBadge({ sla }: { sla: any }) {
  if (!sla) return null
  const m = SLA_MAP[sla.level] || { c: 'default', t: sla.stage || '' }
  const extra = sla.level === 'overdue' ? `（${sla.overdue_days}天）`
    : sla.level === 'warn' ? `（${sla.days_left}天）` : ''
  return <Tag color={m.c}>{m.t}{extra}</Tag>
}

export default function Inspections() {
  const { user } = useAuth()
  const [view, setView] = useState<'list' | 'findings' | 'stats'>('list')
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [fStatus, setFStatus] = useState('')
  const [stats, setStats] = useState<any>(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [detail, setDetail] = useState<any>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [timeline, setTimeline] = useState<any>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [advanceOpen, setAdvanceOpen] = useState(false)
  const [transferOpen, setTransferOpen] = useState(false)
  const [transferF, setTransferF] = useState<any>(null)
  const [fixOpen, setFixOpen] = useState(false)
  const [fixF, setFixF] = useState<any>(null)
  const [traceOpen, setTraceOpen] = useState(false)
  const [traceData, setTraceData] = useState<any>(null)
  const [form] = Form.useForm()
  const [findingForm] = Form.useForm()
  const [aform] = Form.useForm()
  const [tform] = Form.useForm()
  const [fform] = Form.useForm()
  // 问题库 / 成果运用
  const [fRows, setFRows] = useState<any[]>([])
  const [fLoading, setFLoading] = useState(false)
  const [fTotal, setFTotal] = useState(0)
  const [fPage, setFPage] = useState(1)
  const [fSearch, setFSearch] = useState('')
  const [fCat, setFCat] = useState('')
  const [fSev, setFSev] = useState('')
  const [fFix, setFFix] = useState('')
  const [fOverdue, setFOverdue] = useState(false)
  const [common, setCommon] = useState<any>(null)
  const [commonLoading, setCommonLoading] = useState(false)

  const load = (p = 1) => {
    setLoading(true)
    const qs = new URLSearchParams()
    if (fStatus) qs.set('status', fStatus)
    if (search) qs.set('q', search)
    qs.set('page', String(p))
    api.get(`/inspections?${qs.toString()}`).then(({ data }) => {
      setRows(data.items || [])
      setTotal(data.total || 0)
      setPage(p)
    }).catch(() => { setRows([]); setTotal(0) }).finally(() => setLoading(false))
  }
  const loadFindings = (p = 1) => {
    setFLoading(true)
    const qs = new URLSearchParams()
    if (fCat) qs.set('category', fCat)
    if (fSev) qs.set('severity', fSev)
    if (fFix) qs.set('fix_status', fFix)
    if (fOverdue) qs.set('overdue', '1')
    if (fSearch) qs.set('q', fSearch)
    qs.set('page', String(p))
    api.get(`/inspections/findings?${qs.toString()}`).then(({ data }) => {
      setFRows(data.items || [])
      setFTotal(data.total || 0)
      setFPage(p)
    }).catch(() => { setFRows([]); setFTotal(0) }).finally(() => setFLoading(false))
  }
  const loadStats = () => {
    setStatsLoading(true)
    api.get('/inspections/stats').then(({ data }) => setStats(data)).catch(() => setStats(null)).finally(() => setStatsLoading(false))
  }
  const loadCommon = () => {
    setCommonLoading(true)
    api.get('/inspections/common-problems').then(({ data }) => setCommon(data)).catch(() => setCommon(null)).finally(() => setCommonLoading(false))
  }
  useEffect(() => { load(1) }, [fStatus])
  useEffect(() => {
    if (view === 'stats') { loadStats(); loadCommon() }
    if (view === 'findings') loadFindings(1)
  }, [view])

  const openDetail = (r: any) => {
    setTimeline(null)
    api.get(`/inspections/${r.id}`).then(({ data }) => { setDetail(data); setDetailOpen(true) }).catch(() => {})
    api.get(`/inspections/${r.id}/timeline`).then(({ data }) => setTimeline(data)).catch(() => setTimeline(null))
  }
  const submitCreate = async () => {
    const v = await form.validateFields()
    api.post('/inspections', v).then(() => { message.success('巡察任务已创建'); setCreateOpen(false); form.resetFields(); load(1) }).catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const submitFinding = async () => {
    if (!detail) return
    const v = await findingForm.validateFields()
    api.post(`/inspections/${detail.id}/findings`, v).then(() => { message.success('问题已录入'); findingForm.resetFields(); openDetail(detail) }).catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const openAdvance = () => { aform.setFieldsValue({ status: detail?.status, note: '' }); setAdvanceOpen(true) }
  const submitAdvance = async () => {
    const v = await aform.validateFields()
    if (!detail) return
    api.post(`/inspections/${detail.id}/advance`, v).then(() => {
      message.success(`状态已置为 ${v.status}`); setAdvanceOpen(false); aform.resetFields(); openDetail(detail)
    }).catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const openTransfer = (f: any) => {
    setTransferF(f)
    tform.setFieldsValue({ transfer_to: '案管', subject: '', unit: '', title: '', content: f.content })
    setTransferOpen(true)
  }
  const submitTransfer = async () => {
    const v = await tform.validateFields()
    if (!transferF) return
    const mod = ({ 案管: 'clue', 党风: 'conduct', 信访: 'petitions' } as Record<string, string>)[v.transfer_to] || 'clue'
    const dept = await pickReceiver(mod, `移交${v.transfer_to}`)
    if (dept === undefined) return
    api.post(`/inspections/findings/${transferF.id}/transfer`, { ...v, dept_id: dept }).then(() => {
      message.success(`已移交${v.transfer_to}`); setTransferOpen(false); tform.resetFields()
      if (view === 'findings') loadFindings(fPage); else if (detail) openDetail(detail)
    }).catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const openFix = (f: any) => {
    setFixF(f)
    fform.setFieldsValue({ fix_status: f.fix_status, req: f.fix_req || '', due: f.due_at || '', note: f.fix_note || '', fix_effect: f.fix_effect || '未评估' })
    setFixOpen(true)
  }
  const submitFix = async () => {
    const v = await fform.validateFields()
    if (!fixF) return
    api.post(`/inspections/findings/${fixF.id}/fix`, v).then(() => {
      message.success('整改记录已保存'); setFixOpen(false); fform.resetFields()
      if (view === 'findings') loadFindings(fPage); else if (detail) openDetail(detail)
    }).catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const openTrace = (f: any) => {
    api.get(`/inspections/findings/${f.id}/trace`).then(({ data }) => { setTraceData(data); setTraceOpen(true) }).catch(() => message.error('追溯失败'))
  }
  const downloadExport = async () => {
    const qs = new URLSearchParams()
    if (fStatus) qs.set('status', fStatus)
    if (search) qs.set('q', search)
    try {
      const { data } = await api.get(`/inspections/export?${qs.toString()}`, { responseType: 'blob' } as any)
      const d = new Date()
      const ymd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
      const url = URL.createObjectURL(new Blob([data]))
      const a = document.createElement('a')
      a.href = url
      a.download = `巡察台账_${ymd}.xlsx`
      a.click()
      URL.revokeObjectURL(url)
      message.success('台账已导出')
    } catch { message.error('导出失败') }
  }

  const columns = [
    { title: '巡字编号', dataIndex: 'code', width: 140 },
    { title: '轮次', dataIndex: 'round_name', width: 140, ellipsis: true },
    { title: '巡察对象', dataIndex: 'target', width: 160, ellipsis: true },
    { title: '巡察组', dataIndex: 'group_name', width: 100 },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={INSPECT_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '问题数', dataIndex: 'finding_count', width: 70 },
    { title: '已移交', dataIndex: 'transferred', width: 70 },
    { title: '操作', key: 'op', width: 100, fixed: 'right' as const, render: (_: any, r: any) => <Button size="small" onClick={() => openDetail(r)}>详情</Button> },
  ]

  const fColumns = [
    { title: '问题摘要', dataIndex: 'content', width: 240, ellipsis: true, render: (t: string) => <span style={{ fontSize: 13 }}>{t}</span> },
    { title: '类别', dataIndex: 'category', width: 120, render: (c: string) => <Tag>{c || '未分类'}</Tag> },
    { title: '程度', dataIndex: 'severity', width: 70, render: (s: string) => <Tag color={SEV_COLOR[s]}>{s}</Tag> },
    { title: '整改状态', dataIndex: 'fix_status', width: 90, render: (s: string) => <Tag color={FIX_COLOR[s]}>{s}</Tag> },
    { title: '时限合规', key: 'sla', width: 110, render: (_: any, r: any) => <SlaBadge sla={r.sla} /> },
    { title: '整改成效', dataIndex: 'fix_effect', width: 90, render: (e: string) => <Tag color={EFFECT_COLOR[e] || 'default'}>{e || '未评估'}</Tag> },
    { title: '移交', dataIndex: 'transfer_to', width: 90, render: (t: string) => t && t !== '未移交' ? <Tag color="blue">{t}</Tag> : <span style={{ color: '#b6c0cf' }}>未移交</span> },
    { title: '操作', key: 'op', width: 160, fixed: 'right' as const, render: (_: any, r: any) => (
      <Space>
        <Button size="small" type="link" icon={<SwapOutlined />} disabled={!!r.transfer_to && r.transfer_to !== '未移交'} onClick={() => openTransfer(r)}>移交</Button>
        <Button size="small" type="link" icon={<CheckCircleOutlined />} onClick={() => openFix(r)}>整改</Button>
        {r.transfer_to && r.transfer_to !== '未移交' && <Button size="small" type="link" icon={<ArrowRightOutlined />} onClick={() => openTrace(r)}>成效</Button>}
      </Space>
    ) },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Breadcrumb items={[{ title: '业务办理' }, { title: '巡察（巡视巡察监督）' }]} />
        <Space>
          <Segmented value={view} onChange={(v) => setView(v as any)} options={[
            { label: '巡察台账', value: 'list', icon: <FileSearchOutlined /> },
            { label: '问题库', value: 'findings', icon: <SafetyOutlined /> },
            { label: '数据统计', value: 'stats', icon: <BarChartOutlined /> },
          ]} />
          <Button icon={<DownloadOutlined />} onClick={downloadExport}>导出台账</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建巡察</Button>
        </Space>
      </div>

      {view === 'list' && (
        <Card size="small">
          <Space style={{ marginBottom: 10 }} wrap>
            <Input allowClear placeholder="搜索对象/轮次/巡察组/编号" style={{ width: 240 }}
              prefix={<SearchOutlined />} value={search}
              onChange={(e) => setSearch(e.target.value)}
              onPressEnter={() => load(1)} onClear={() => load(1)} />
            <Button icon={<SearchOutlined />} onClick={() => load(1)}>搜索</Button>
            <Select placeholder="状态" allowClear style={{ width: 120 }} value={fStatus || undefined} onChange={(v) => setFStatus(v || '')}
              options={INSPECT_FLOW.map((s) => ({ label: s, value: s }))} />
          </Space>
          <Table rowKey="id" size="small" loading={loading} columns={columns} dataSource={rows}
            scroll={{ x: 900 }} pagination={{
              current: page, pageSize: 10, total, showTotal: (t) => `共 ${t} 条`,
              onChange: (p) => load(p),
            }} />
        </Card>
      )}

      {view === 'findings' && (
        <Card size="small">
          <Space style={{ marginBottom: 10 }} wrap>
            <Input allowClear placeholder="搜索问题/类别" style={{ width: 220 }} prefix={<SearchOutlined />}
              value={fSearch} onChange={(e) => setFSearch(e.target.value)} onPressEnter={() => loadFindings(1)} onClear={() => loadFindings(1)} />
            <Select placeholder="类别" allowClear style={{ width: 140 }} value={fCat || undefined} onChange={(v) => { setFCat(v || ''); loadFindings(1) }}
              options={CAT_OPTIONS.map((s) => ({ label: s, value: s }))} />
            <Select placeholder="程度" allowClear style={{ width: 90 }} value={fSev || undefined} onChange={(v) => { setFSev(v || ''); loadFindings(1) }}
              options={['高', '中', '低'].map((s) => ({ label: s, value: s }))} />
            <Select placeholder="整改状态" allowClear style={{ width: 110 }} value={fFix || undefined} onChange={(v) => { setFFix(v || ''); loadFindings(1) }}
              options={['待整改', '整改中', '已销号'].map((s) => ({ label: s, value: s }))} />
            <Button danger={fOverdue} icon={<WarningOutlined />} onClick={() => { setFOverdue(!fOverdue); loadFindings(1) }}>
              {fOverdue ? '取消超期筛选' : '仅看超期'}
            </Button>
            <Button icon={<SearchOutlined />} onClick={() => loadFindings(1)}>搜索</Button>
          </Space>
          <Alert type="info" showIcon style={{ marginBottom: 10 }}
            message="巡察问题库：跨任务汇聚全部发现问题，可一键筛选超期未整改、按类别/程度穿透，支撑共性问题分析与整改成效评估。" />
          <Table rowKey="id" size="small" loading={fLoading} columns={fColumns} dataSource={fRows}
            scroll={{ x: 1000 }} pagination={{
              current: fPage, pageSize: 10, total: fTotal, showTotal: (t) => `共 ${t} 条`,
              onChange: (p) => loadFindings(p),
            }} />
        </Card>
      )}

      {view === 'stats' && (
        <Spin spinning={statsLoading}>
          {stats && (
            <Row gutter={12}>
              <Col span={6}><Card><Statistic title="巡察任务" value={stats.inspection_total} /></Card></Col>
              <Col span={6}><Card><Statistic title="发现问题" value={stats.finding_total} /></Card></Col>
              <Col span={6}><Card><Statistic title="超期未销号" value={stats.finding_overdue} valueStyle={{ color: stats.finding_overdue ? '#b91c1c' : undefined }} /></Card></Col>
              <Col span={6}><Card><Statistic title="销号率" value={stats.sealed_rate} suffix="%" valueStyle={{ color: '#15803d' }} /></Card></Col>
              <Col span={6}><Card><Statistic title="临期(≤7天)" value={stats.finding_due_soon} valueStyle={{ color: stats.finding_due_soon ? '#b45309' : undefined }} /></Card></Col>
              <Col span={6}><Card><Statistic title="已移交" value={stats.transferred} valueStyle={{ color: '#1d4ed8' }} /></Card></Col>
              <Col span={6}><Card><Statistic title="已销号" value={stats.by_fix['已销号'] || 0} valueStyle={{ color: '#15803d' }} /></Card></Col>
              <Col span={6}><Card><Statistic title="待整改" value={stats.by_fix['待整改'] || 0} /></Card></Col>
              <Col span={12}><Card size="small" title="问题整改进度"><BarListMini data={stats.by_fix} /></Card></Col>
              <Col span={12}><Card size="small" title="整改成效分布"><BarListMini data={stats.by_fix_effect} /></Card></Col>
              <Col span={12}><Card size="small" title="移交去向"><BarListMini data={stats.by_transfer} /></Card></Col>
              <Col span={12}><Card size="small" title="问题程度分布"><BarListMini data={stats.by_sev} /></Card></Col>
            </Row>
          )}
          <Card size="small" title={<span><RiseOutlined /> 共性问题分析（成果运用）</span>} style={{ marginTop: 12 }} loading={commonLoading}>
            {common && (
              <Alert type="info" showIcon style={{ marginBottom: 10 }}
                message={`共识别出 ${common.common_count} 类共性问题（跨单位反复出现或同类高频），占全部 ${common.total} 类问题类别。`} />
            )}
            {common?.items?.length ? (
              <div>
                {common.items.map((r: any) => (
                  <div key={r.category} style={{ display: 'flex', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #f6f8fb' }}>
                    <Tag color={r.is_common ? 'red' : 'default'}>{r.is_common ? '共性问题' : '一般'}</Tag>
                    <b style={{ width: 140 }}>{r.category}</b>
                    <span style={{ width: 120 }}>出现 {r.count} 次</span>
                    <span style={{ width: 130 }}>涉及 {r.unit_count} 个单位</span>
                    <span style={{ width: 110 }}>销号率 {r.sealed_rate}%</span>
                    {r.sev_high > 0 && <Tag color="red">高风险 {r.sev_high}</Tag>}
                  </div>
                ))}
              </div>
            ) : <Empty description="暂无问题数据" />}
          </Card>
        </Spin>
      )}

      <Drawer width={680} open={detailOpen} onClose={() => setDetailOpen(false)} title={`巡察任务 ${detail?.code || ''}`}
        extra={<Space>
          <Button icon={<ArrowRightOutlined />} onClick={openAdvance}>状态流转</Button>
          <Button icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>录入问题</Button>
        </Space>}>
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="轮次">{detail.round_name}</Descriptions.Item>
              <Descriptions.Item label="巡察对象">{detail.target}</Descriptions.Item>
              <Descriptions.Item label="巡察组">{detail.group_name}</Descriptions.Item>
              <Descriptions.Item label="起止">{detail.start_date} ~ {detail.end_date}</Descriptions.Item>
              <Descriptions.Item label="状态"><Tag color={INSPECT_COLOR[detail.status] || 'default'}>{detail.status}</Tag></Descriptions.Item>
            </Descriptions>

            {timeline && (
              <Card size="small" title={<span><HistoryOutlined /> 流程轨迹</span>} style={{ marginTop: 12 }}>
                {timeline.items?.length ? (
                  <Timeline items={timeline.items.map((it: any) => ({
                    color: it.action === 'inspection_advance' ? 'blue' : it.action.startsWith('inspection_to_') ? 'green' : 'gray',
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

            <Card size="small" title="问题清单" style={{ marginTop: 12 }}>
              {detail.findings?.length ? detail.findings.map((f: any) => (
                <div key={f.id} style={{ padding: '8px 0', borderBottom: '1px solid #f6f8fb' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <Space>
                      <Tag color={SEV_COLOR[f.severity]}>{f.severity}</Tag>
                      <Tag>{f.category || '未分类'}</Tag>
                      <span style={{ fontSize: 13 }}>{f.content}</span>
                    </Space>
                    <Space>
                      <Tag color={FIX_COLOR[f.fix_status]}>{f.fix_status}</Tag>
                      <Tag color={EFFECT_COLOR[f.fix_effect] || 'default'}>{f.fix_effect || '未评估'}</Tag>
                    </Space>
                  </div>
                  <div style={{ marginTop: 2 }}>
                    <SlaBadge sla={f.sla} />
                    {f.fix_req || f.fix_note ? (
                      <span style={{ fontSize: 12, color: '#64748b' }}>
                        整改要求：{f.fix_req || '—'}｜落实：{f.fix_note || '—'}{f.due_at ? `｜时限：${f.due_at}` : ''}
                      </span>
                    ) : null}
                  </div>
                  <Space style={{ marginTop: 4 }}>
                    <Button size="small" type="link" icon={<SwapOutlined />} disabled={!!f.transfer_to && f.transfer_to !== '未移交'} onClick={() => openTransfer(f)}>移交</Button>
                    <Button size="small" type="link" icon={<CheckCircleOutlined />} onClick={() => openFix(f)}>整改</Button>
                    {f.transfer_to && f.transfer_to !== '未移交' && <Button size="small" type="link" icon={<ArrowRightOutlined />} onClick={() => openTrace(f)}>移交成效</Button>}
                    {f.transfer_to && f.transfer_to !== '未移交' && <Tag color="blue">已移交{f.transfer_to}</Tag>}
                  </Space>
                </div>
              )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无问题" />}
            </Card>
          </>
        )}
      </Drawer>

      <Modal title="新建巡察任务" open={createOpen} onOk={submitCreate} onCancel={() => setCreateOpen(false)} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item label="轮次" name="round_name"><Input placeholder="如：一届市委第五轮巡察" /></Form.Item>
          <Form.Item label="巡察对象" name="target"><Input /></Form.Item>
          <Form.Item label="巡察组" name="group_name"><Input /></Form.Item>
          <Space>
            <Form.Item label="开始" name="start_date"><Input placeholder="2026-03-01" /></Form.Item>
            <Form.Item label="结束" name="end_date"><Input placeholder="2026-04-30" /></Form.Item>
          </Space>
        </Form>
      </Modal>

      <Modal title="录入巡察发现问题" open={addOpen} onOk={submitFinding} onCancel={() => setAddOpen(false)} destroyOnClose>
        <Form form={findingForm} layout="vertical">
          <Form.Item label="问题类别" name="category"><Input placeholder="如：违规吃喝" /></Form.Item>
          <Form.Item label="紧急程度" name="severity" initialValue="中">
            <Select options={['高', '中', '低'].map((s) => ({ label: s, value: s }))} />
          </Form.Item>
          <Form.Item label="问题摘要" name="content" rules={[{ required: true }]}><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`状态流转 · ${detail?.code || ''}`} open={advanceOpen} onOk={submitAdvance} onCancel={() => setAdvanceOpen(false)} destroyOnClose>
        <Form form={aform} layout="vertical">
          <Form.Item label="目标状态" name="status" rules={[{ required: true }]}>
            <Select options={INSPECT_FLOW.map((s) => ({ label: s, value: s }))} />
          </Form.Item>
          <Form.Item label="备注" name="note"><Input.TextArea rows={2} placeholder="流转说明（可选）" /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`移交问题 · ${transferF?.content?.slice(0, 12) || ''}`} open={transferOpen} onOk={submitTransfer} onCancel={() => setTransferOpen(false)} destroyOnClose>
        <Form form={tform} layout="vertical">
          <Form.Item label="移交去向" name="transfer_to" rules={[{ required: true }]}>
            <Select options={['案管', '党风', '信访'].map((s) => ({ label: s, value: s }))} />
          </Form.Item>
          <Form.Item label="涉及人员/单位" name="subject"><Input /></Form.Item>
          <Form.Item label="单位" name="unit"><Input /></Form.Item>
          <Form.Item label="问题概要" name="title"><Input /></Form.Item>
          <Form.Item label="问题详情" name="content" rules={[{ required: true }]}><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="整改记录" open={fixOpen} onOk={submitFix} onCancel={() => setFixOpen(false)} destroyOnClose>
        <Form form={fform} layout="vertical">
          <Form.Item label="整改状态" name="fix_status">
            <Select options={['待整改', '整改中', '已销号'].map((s) => ({ label: s, value: s }))} />
          </Form.Item>
          <Form.Item label="整改要求" name="req"><Input.TextArea rows={2} placeholder="向被巡察单位提出的整改要求" /></Form.Item>
          <Form.Item label="整改时限" name="due"><Input placeholder="如 2026-12-31" /></Form.Item>
          <Form.Item label="整改落实情况" name="note"><Input.TextArea rows={3} placeholder="填写落实情况后状态将推进/销号" /></Form.Item>
          <Form.Item label="整改成效评估" name="fix_effect">
            <Select options={FIX_EFFECT_OPTS.map((s) => ({ label: s, value: s }))} />
          </Form.Item>
          <Alert type="info" showIcon message="整改成效（好/中/差）用于巡察成果运用评估，销号后建议据实评定。" />
        </Form>
      </Modal>

      <Modal title="移交成效追溯" open={traceOpen} onCancel={() => setTraceOpen(false)} footer={null} destroyOnClose>
        {traceData && (
          <div>
            {!traceData.transferred && <Alert type="warning" showIcon message="该问题尚未移交，无后续链路成效可追。" />}
            {traceData.transferred && (
              <>
                <Descriptions column={1} size="small" bordered>
                  <Descriptions.Item label="移交去向">{traceData.to}</Descriptions.Item>
                  <Descriptions.Item label="关联编号">{traceData.ref_code || '—'}</Descriptions.Item>
                  <Descriptions.Item label="当前状态"><Tag color={traceData.ref_status === '已不存在' ? 'default' : 'blue'}>{traceData.ref_status}</Tag></Descriptions.Item>
                </Descriptions>
                <Alert type="success" showIcon style={{ marginTop: 10 }}
                  message={`巡察问题已通过"移交${traceData.to}"进入${traceData.to}办理链路，当前状态：${traceData.ref_status}（成果运用闭环）。`} />
              </>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}

function BarListMini({ data }: { data: Record<string, number> }) {
  const max = Math.max(1, ...Object.values(data || {}))
  return (
    <div>
      {Object.entries(data || {}).map(([k, v]) => (
        <div key={k} style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
          <div style={{ width: 100, fontSize: 12 }}>{k}</div>
          <div style={{ flex: 1, background: '#eef1f5', borderRadius: 3, height: 14, margin: '0 8px' }}>
            <div style={{ width: `${v / max * 100}%`, background: '#1d4ed8', height: 14, borderRadius: 3 }} />
          </div>
          <div style={{ width: 28, fontSize: 12, textAlign: 'right' }}>{v}</div>
        </div>
      ))}
    </div>
  )
}
