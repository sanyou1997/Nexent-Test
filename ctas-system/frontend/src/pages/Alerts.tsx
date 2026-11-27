import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Modal, Form, Input, Select, Tag, Space, message,
  Typography, Row, Col, Segmented, Empty, Statistic, Popconfirm, Tooltip, Descriptions,
} from 'antd'
import {
  AlertOutlined, ReloadOutlined, CheckCircleOutlined, StopOutlined,
  SendOutlined, BellOutlined, ThunderboltOutlined, CheckOutlined,
} from '@ant-design/icons'
import { api } from '../api/client'
import { useAuth } from '../auth'
import AlertFeedback from '../components/AlertFeedback'
import PageHeader from '../components/PageHeader'

const LEVEL_COLOR: Record<string, string> = { 高: 'red', 中: 'orange', 低: 'green', 提示: 'blue' }
const STATUS_COLOR: Record<string, string> = { 待处理: 'gold', 已确认: 'green', 已分办: 'blue', 已销号: 'default', 已忽略: 'default' }
const SRC_COLOR: Record<string, string> = { 案管: 'blue', 党风: 'green', 巡察: 'purple', 信访: 'cyan' }
const HUB_LEVEL_COLOR: Record<string, string> = { 超期: 'red', 临期: 'orange' }
const DISP_STATUS_COLOR: Record<string, string> = { 已下发: 'gold', 已反馈: 'blue', 已闭环: 'green' }
// 时限状态色板（预警 SLA 与督办办理期限共用）
const SLA_COLOR: Record<string, string> = { 超期: 'red', 已逾期: 'red', 临期: 'orange', 正常: 'blue' }
const SLA_PLAIN = ['无时限', '已终结', '已完成']

// 时限徽标（含剩余天数）
function SlaTag({ state, days, due }: { state: string; days: any; due?: string }) {
  if (SLA_PLAIN.includes(state)) return <Tag>{state === '无时限' ? '无限期' : state}</Tag>
  const extra = typeof days === 'number'
    ? (days < 0 ? `超${Math.abs(Math.round(days))}天` : `剩${Math.round(days)}天`)
    : ''
  return (
    <Tooltip title={due ? `到期 ${due}（逾期未处置将升级）` : ''}>
      <Tag color={SLA_COLOR[state] || 'default'}>{state}{extra ? `（${extra}）` : ''}</Tag>
    </Tooltip>
  )
}

// 超期/临期徽标（含剩余天数）
function HubLevel({ level, days }: { level: string; days: any }) {
  const c = HUB_LEVEL_COLOR[level] || 'default'
  const extra = typeof days === 'number'
    ? (days < 0 ? `超${Math.abs(days)}天` : `剩${days}天`)
    : ''
  return <Tag color={c}>{level}{extra ? `（${extra}）` : ''}</Tag>
}

export default function Alerts() {
  const { user } = useAuth()
  const [view, setView] = useState<'hub' | 'dispatch' | 'model'>('hub')

  // ---------- 时限预警中枢 ----------
  const [hub, setHub] = useState<any>({ items: [], stats: {} })
  const [hLoading, setHLoading] = useState(false)
  const [fSource, setFSource] = useState('')
  const [fLevel, setFLevel] = useState('')
  const [fQ, setFQ] = useState('')
  const [dispOpen, setDispOpen] = useState(false)
  const [dispTarget, setDispTarget] = useState<any>(null)
  const [dispForm, setDispForm] = useState({ urge_to: '', note: '' })

  // ---------- 督办记录 ----------
  const [dispRows, setDispRows] = useState<any[]>([])
  const [dLoading, setDLoading] = useState(false)
  const [dSource, setDSource] = useState('')
  const [dStatus, setDStatus] = useState('')
  const [fbOpen, setFbOpen] = useState(false)
  const [fbTarget, setFbTarget] = useState<any>(null)
  const [fbForm, setFbForm] = useState({ feedback: '', status: '已反馈' })

  // ---------- 模型预警（原有） ----------
  const [rows, setRows] = useState<any[]>([])
  const [mLoading, setMLoading] = useState(false)
  const [mStats, setMStats] = useState<any>(null)
  const [mLevel, setMLevel] = useState('')
  const [mStatus, setMStatus] = useState('')
  const [mSource, setMSource] = useState('')
  const [mSla, setMSla] = useState('')
  // 模型预警 → 分办/销号
  const [aDispOpen, setADispOpen] = useState(false)
  const [aDispTarget, setADispTarget] = useState<any>(null)
  const [aDispForm, setADispForm] = useState({ urge_to: '', note: '' })

  const loadHub = () => {
    setHLoading(true)
    const qs = new URLSearchParams()
    if (fSource) qs.set('source', fSource)
    if (fLevel) qs.set('level', fLevel)
    if (fQ) qs.set('q', fQ)
    api.get(`/alerts/hub?${qs.toString()}`).then(({ data }) => setHub(data)).catch(() => setHub({ items: [], stats: {} })).finally(() => setHLoading(false))
  }
  const loadDispatches = () => {
    setDLoading(true)
    const qs = new URLSearchParams()
    if (dSource) qs.set('source', dSource)
    if (dStatus) qs.set('status', dStatus)
    api.get(`/alerts/dispatches?${qs.toString()}`).then(({ data }) => setDispRows(data)).catch(() => setDispRows([])).finally(() => setDLoading(false))
  }
  const loadModel = () => {
    setMLoading(true)
    const qs = new URLSearchParams()
    if (mLevel) qs.set('level', mLevel)
    if (mStatus) qs.set('status', mStatus)
    if (mSource) qs.set('source', mSource)
    if (mSla) qs.set('sla', mSla)
    api.get(`/alerts?${qs.toString()}`).then(({ data }) => setRows(data)).catch(() => setRows([])).finally(() => setMLoading(false))
  }
  const loadModelStats = () => api.get('/alerts/stats').then(({ data }) => setMStats(data)).catch(() => setMStats(null))

  useEffect(() => {
    if (view === 'hub') loadHub()
    else if (view === 'dispatch') loadDispatches()
    else { loadModel(); loadModelStats() }
  }, [view, fSource, fLevel, fQ, dSource, dStatus, mLevel, mStatus, mSource, mSla])

  // 模型预警操作
  const doAck = (r: any) => {
    api.post(`/alerts/${r.id}/ack`).then(() => { message.success('已确认'); loadModel(); loadModelStats() })
      .catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const doIgnore = (r: any) => {
    api.post(`/alerts/${r.id}/ignore`).then(() => { message.success('已忽略'); loadModel(); loadModelStats() })
      .catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }
  const doScan = () => {
    api.post('/alerts/scan').then(() => { message.success('扫描完成'); loadModel(); loadModelStats() })
      .catch((e) => message.error(e?.response?.data?.detail || '失败'))
  }

  // 模型预警 → 一键分办（打通 预警→分办→整改→销号 闭环）
  const openAlertDispatch = (r: any) => {
    setADispTarget(r)
    setADispForm({ urge_to: r.ref_id || '', note: '' })
    setADispOpen(true)
  }
  const submitAlertDispatch = () => {
    if (!aDispTarget) return
    api.post(`/alerts/${aDispTarget.id}/dispatch`, aDispForm).then(() => {
      message.success('已分办，预警进入督办闭环')
      setADispOpen(false); loadModel(); loadModelStats(); loadDispatches()
    }).catch((e) => message.error(e?.response?.data?.detail || '分办失败'))
  }
  const doCloseAlert = (r: any) => {
    api.post(`/alerts/${r.id}/close`).then(() => {
      message.success('预警已销号'); loadModel(); loadModelStats()
    }).catch((e) => message.error(e?.response?.data?.detail || '销号失败'))
  }

  // 一键督办
  const openDispatch = (r: any) => {
    setDispTarget(r)
    setDispForm({ urge_to: r.owner || r.ref_unit || '', note: '' })
    setDispOpen(true)
  }
  const submitDispatch = () => {
    if (!dispTarget) return
    const body = {
      source: dispTarget.source, ref_type: dispTarget.ref_type, ref_id: dispTarget.ref_id,
      ref_code: dispTarget.ref_code || '', ref_title: dispTarget.ref_title || '',
      ref_unit: dispTarget.ref_unit || '', level: dispTarget.level,
      urge_to: dispForm.urge_to, note: dispForm.note,
    }
    api.post('/alerts/dispatch', body).then(() => {
      message.success('督办已下发')
      setDispOpen(false)
      loadHub(); loadDispatches()
    }).catch((e) => message.error(e?.response?.data?.detail || '下发失败'))
  }

  // 督办反馈闭环
  const openFeedback = (r: any) => {
    setFbTarget(r)
    setFbForm({ feedback: '', status: '已反馈' })
    setFbOpen(true)
  }
  const submitFeedback = () => {
    if (!fbTarget) return
    api.post(`/alerts/dispatches/${fbTarget.id}/feedback`, fbForm).then(() => {
      message.success('反馈已提交')
      setFbOpen(false)
      loadDispatches(); loadHub()
    }).catch((e) => message.error(e?.response?.data?.detail || '提交失败'))
  }

  // ====== 时限预警中枢 表格 ======
  const hubColumns = [
    { title: '来源', dataIndex: 'source', width: 80, render: (s: string) => <Tag color={SRC_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '业务编号', dataIndex: 'ref_code', width: 130, ellipsis: true },
    { title: '事项', dataIndex: 'ref_title', ellipsis: true, render: (t: string) => <Typography.Text>{t || '—'}</Typography.Text> },
    { title: '责任单位/对象', dataIndex: 'ref_unit', width: 130, ellipsis: true },
    { title: '承办人', dataIndex: 'owner', width: 90 },
    { title: '阶段', dataIndex: 'stage', width: 90 },
    { title: '时限', key: 'level', width: 120, render: (_: any, r: any) => <HubLevel level={r.level} days={r.days_left} /> },
    { title: '到期日', dataIndex: 'deadline', width: 110 },
    { title: '督办状态', key: 'disp', width: 90, render: (_: any, r: any) => r.dispatched
      ? <Tag color="processing">督办中</Tag>
      : <Tag>未督办</Tag> },
    {
      title: '操作', key: 'op', width: 90, fixed: 'right' as const,
      render: (_: any, r: any) => (
        <Button size="small" type="primary" icon={<SendOutlined />} onClick={() => openDispatch(r)}>督办</Button>
      ),
    },
  ]

  const modelColumns = [
    { title: '级别', dataIndex: 'level', width: 70, render: (s: string) => <Tag color={LEVEL_COLOR[s]}>{s}</Tag> },
    { title: '来源', dataIndex: 'source', width: 110, render: (s: string) => <Tag>{s}</Tag> },
    { title: '处置时限', key: 'sla', width: 130, render: (_: any, r: any) => <SlaTag state={r.sla_state} days={r.days_left} due={r.due_at} /> },
    { title: '预警内容', dataIndex: 'title', ellipsis: true },
    { title: '说明', dataIndex: 'detail', width: 240, ellipsis: true, render: (d: string) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{d}</Typography.Text> },
    { title: '对象', key: 'ref', width: 110, render: (_: any, r: any) => r.ref_type ? `${r.ref_type}:${r.ref_id}` : '—' },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
    { title: '误报反馈', key: 'fb', width: 130, render: (_: any, r: any) => (
      <AlertFeedback row={r} onDone={() => { loadModel(); loadModelStats() }} />) },
    {
      title: '操作', key: 'op', width: 170,
      render: (_: any, r: any) => (
        <Space size={2}>
          {r.status === '待处理' || r.status === '已确认' ? (
            <>
              <Button size="small" type="link" icon={<CheckCircleOutlined />} onClick={() => doAck(r)}>确认</Button>
              <Button size="small" type="link" icon={<SendOutlined />} onClick={() => openAlertDispatch(r)}>分办</Button>
              <Popconfirm title="确认忽略该预警？" onConfirm={() => doIgnore(r)}>
                <Button size="small" type="link" icon={<StopOutlined />}>忽略</Button>
              </Popconfirm>
            </>
          ) : r.status === '已分办' ? (
            <Popconfirm title="确认将该预警销号？" onConfirm={() => doCloseAlert(r)}>
              <Button size="small" type="link" icon={<CheckOutlined />}>销号</Button>
            </Popconfirm>
          ) : <Typography.Text type="secondary">已处理</Typography.Text>}
        </Space>
      ),
    },
  ]

  const dispColumns = [
    { title: '来源', dataIndex: 'source', width: 80, render: (s: string) => <Tag color={SRC_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '业务编号', dataIndex: 'ref_code', width: 120, ellipsis: true },
    { title: '事项', dataIndex: 'ref_title', ellipsis: true },
    { title: '责任对象', dataIndex: 'urge_to', width: 110 },
    { title: '级别', dataIndex: 'level', width: 70, render: (s: string) => <Tag color={HUB_LEVEL_COLOR[s]}>{s}</Tag> },
    { title: '办理时限', key: 'dsla', width: 120, render: (_: any, r: any) => <SlaTag state={r.sla_state} days={r.days_left} due={r.due_at} /> },
    { title: '督办人', dataIndex: 'created_by', width: 90 },
    {
      title: '状态', dataIndex: 'status', width: 110,
      render: (s: string, r: any) => (
        <Space size={2}>
          <Tag color={DISP_STATUS_COLOR[s]}>{s}</Tag>
          {r.escalated && <Tooltip title={`督办逾期未反馈，已自动升级 ${r.escalate_count || 1} 次${r.ref_type === 'alert' ? '，关联预警已提级' : ''}`}><Tag color="red">已升级×{r.escalate_count || 1}</Tag></Tooltip>}
        </Space>
      ),
    },
    {
      title: '反馈', dataIndex: 'feedback', width: 180, ellipsis: true,
      render: (f: string, r: any) => f ? <Typography.Text type="secondary" style={{ fontSize: 12 }}>{f}</Typography.Text>
        : <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: '操作', key: 'op', width: 90, fixed: 'right' as const,
      render: (_: any, r: any) => (
        <Button size="small" icon={<ReloadOutlined />} onClick={() => openFeedback(r)}
          disabled={r.status === '已闭环'}>反馈</Button>
      ),
    },
  ]

  const hs = hub?.stats || {}
  const bySrc = hs.by_source || {}

  return (
    <div>
      <PageHeader
        icon={<AlertOutlined />}
        title="智能督办预警中枢"
        subtitle="信访 / 案管 / 党风 / 巡察时限统一预警 · 一键督办与反馈闭环 · 监督模型碰撞预警与误报反馈"
        extra={
          <Segmented
            value={view}
            onChange={(v) => setView(v as any)}
            options={[
              { label: '时限预警', value: 'hub', icon: <ThunderboltOutlined /> },
              { label: '督办记录', value: 'dispatch', icon: <BellOutlined /> },
              { label: '模型预警', value: 'model', icon: <AlertOutlined /> },
            ]}
          />
        } />

      {/* ===== 时限预警中枢 ===== */}
      {view === 'hub' && (
        <>
          <Row gutter={12} style={{ marginBottom: 12 }}>
            <Col span={6}><Card size="small"><Statistic title="超期未办结" value={hs.overdue || 0} valueStyle={{ color: (hs.overdue || 0) ? '#b91c1c' : undefined }} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="临期预警" value={hs.due_soon || 0} valueStyle={{ color: (hs.due_soon || 0) ? '#b45309' : undefined }} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="案管超期" value={bySrc['案管']?.overdue || 0} valueStyle={{ color: (bySrc['案管']?.overdue || 0) ? '#b91c1c' : undefined }} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="党风/巡察超期" value={(bySrc['党风']?.overdue || 0) + (bySrc['巡察']?.overdue || 0)} valueStyle={{ color: ((bySrc['党风']?.overdue || 0) + (bySrc['巡察']?.overdue || 0)) ? '#b91c1c' : undefined }} /></Card></Col>
          </Row>
          <Row gutter={12} style={{ marginBottom: 12 }}>
            <Col span={6}><Card size="small"><Statistic title="信访超期" value={bySrc['信访']?.overdue || 0} valueStyle={{ color: (bySrc['信访']?.overdue || 0) ? '#b91c1c' : undefined }} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="案管临期" value={bySrc['案管']?.due_soon || 0} valueStyle={{ color: (bySrc['案管']?.due_soon || 0) ? '#b45309' : undefined }} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="党风临期" value={bySrc['党风']?.due_soon || 0} valueStyle={{ color: (bySrc['党风']?.due_soon || 0) ? '#b45309' : undefined }} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="巡察临期" value={bySrc['巡察']?.due_soon || 0} valueStyle={{ color: (bySrc['巡察']?.due_soon || 0) ? '#b45309' : undefined }} /></Card></Col>
          </Row>
          <Space style={{ marginBottom: 10 }} wrap>
            <Select placeholder="来源" allowClear style={{ width: 120 }} value={fSource || undefined} onChange={(v) => setFSource(v || '')}
              options={['案管', '党风', '巡察', '信访'].map((s) => ({ label: s, value: s }))} />
            <Select placeholder="时限" allowClear style={{ width: 110 }} value={fLevel || undefined} onChange={(v) => setFLevel(v || '')}
              options={[{ label: '超期', value: '超期' }, { label: '临期', value: '临期' }]} />
            <Input.Search placeholder="编号/事项/单位/承办人" allowClear style={{ width: 240 }} onSearch={(v) => setFQ(v)} />
            <Button icon={<ReloadOutlined />} onClick={loadHub}>刷新</Button>
          </Space>
          <Card size="small">
            <Table rowKey={(r: any) => `${r.ref_type}:${r.ref_id}`} size="small" loading={hLoading} columns={hubColumns}
              dataSource={hub.items || []} scroll={{ x: 1080 }}
              locale={{ emptyText: <Empty description="四路暂无超期/临期事项，督办中枢安静" /> }}
              pagination={{ pageSize: 10 }} />
          </Card>
        </>
      )}

      {/* ===== 督办记录 ===== */}
      {view === 'dispatch' && (
        <>
          <Space style={{ marginBottom: 10 }} wrap>
            <Select placeholder="来源" allowClear style={{ width: 120 }} value={dSource || undefined} onChange={(v) => setDSource(v || '')}
              options={['案管', '党风', '巡察', '信访'].map((s) => ({ label: s, value: s }))} />
            <Select placeholder="状态" allowClear style={{ width: 120 }} value={dStatus || undefined} onChange={(v) => setDStatus(v || '')}
              options={[{ label: '已下发', value: '已下发' }, { label: '已反馈', value: '已反馈' }, { label: '已闭环', value: '已闭环' }]} />
            <Button icon={<ReloadOutlined />} onClick={loadDispatches}>刷新</Button>
          </Space>
          <Card size="small">
            <Table rowKey="id" size="small" loading={dLoading} columns={dispColumns} dataSource={dispRows}
              scroll={{ x: 980 }}
              locale={{ emptyText: <Empty description="暂无督办记录，去「时限预警中枢」一键督办" /> }}
              pagination={{ pageSize: 10 }} />
          </Card>
        </>
      )}

      {/* ===== 模型预警（原有监督模型扫描） ===== */}
      {view === 'model' && (
        <>
          <Row gutter={12} style={{ marginBottom: 12 }}>
            <Col span={4}><Card size="small"><Statistic title="预警总数" value={mStats?.total || 0} /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="待处理" value={mStats?.pending || 0} valueStyle={{ color: (mStats?.pending || 0) ? '#b45309' : undefined }} /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="超期未处置" value={mStats?.overdue || 0} valueStyle={{ color: mStats?.overdue ? '#b91c1c' : undefined }} /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="临期预警" value={mStats?.due_soon || 0} valueStyle={{ color: (mStats?.due_soon || 0) ? '#b45309' : undefined }} /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="高级" value={mStats?.by_level?.高 || 0} valueStyle={{ color: (mStats?.by_level?.高 || 0) ? '#b91c1c' : undefined }} /></Card></Col>
            <Col span={4}><Card size="small"><Statistic title="中/低/提示" value={(mStats?.by_level?.中 || 0) + (mStats?.by_level?.低 || 0) + (mStats?.by_level?.提示 || 0)} /></Card></Col>
          </Row>
          <Space style={{ marginBottom: 10 }} wrap>
            <Select placeholder="级别" allowClear style={{ width: 110 }} value={mLevel || undefined} onChange={(v) => setMLevel(v || '')}
              options={['高', '中', '低', '提示'].map((s) => ({ label: s, value: s }))} />
            <Select placeholder="状态" allowClear style={{ width: 120 }} value={mStatus || undefined} onChange={(v) => setMStatus(v || '')}
              options={['待处理', '已确认', '已分办', '已销号', '已忽略'].map((s) => ({ label: s, value: s }))} />
            <Select placeholder="来源" allowClear style={{ width: 140 }} value={mSource || undefined} onChange={(v) => setMSource(v || '')}
              options={['案件超期', '巡察整改超期', '重复被反映', '关联穿透', '长期未处置', '巡察未移交', '资金异常'].map((s) => ({ label: s, value: s }))} />
            <Select placeholder="时限" allowClear style={{ width: 110 }} value={mSla || undefined} onChange={(v) => setMSla(v || '')}
              options={[{ label: '超期', value: '超期' }, { label: '临期', value: '临期' }, { label: '正常', value: '正常' }]} />
            <Button type="primary" icon={<ThunderboltOutlined />} onClick={doScan}>一键扫描</Button>
            <Button icon={<ReloadOutlined />} onClick={() => { loadModel(); loadModelStats() }}>刷新</Button>
          </Space>
          <Card size="small">
            <Table rowKey="id" size="small" loading={mLoading} columns={modelColumns} dataSource={rows}
              locale={{ emptyText: <Empty description="暂无预警，点击「一键扫描」生成" /> }}
              pagination={{ pageSize: 10 }} />
          </Card>
        </>
      )}

      {/* 一键督办弹窗 */}
      <Modal title="一键督办下发" open={dispOpen} onOk={submitDispatch} onCancel={() => setDispOpen(false)} okText="下发督办" destroyOnClose>
        {dispTarget && (
          <Descriptions size="small" column={1} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="来源">{dispTarget.source}</Descriptions.Item>
            <Descriptions.Item label="事项">{dispTarget.ref_title || dispTarget.ref_code || '—'}</Descriptions.Item>
            <Descriptions.Item label="时限">{dispTarget.level}（到期 {dispTarget.deadline}）</Descriptions.Item>
            <Descriptions.Item label="责任单位/对象">{dispTarget.ref_unit || '—'}</Descriptions.Item>
            <Descriptions.Item label="办理期限">
              超期事项 3 个自然日内、临期事项 7 个自然日内反馈；逾期未反馈将自动升级并提级关联预警
            </Descriptions.Item>
          </Descriptions>
        )}
        <Form layout="vertical">
          <Form.Item label="督办对象（承办人/责任单位）" required>
            <Input value={dispForm.urge_to} placeholder="请填写承办人姓名或责任单位" onChange={(e) => setDispForm({ ...dispForm, urge_to: e.target.value })} />
          </Form.Item>
          <Form.Item label="督办要求/说明">
            <Input.TextArea rows={3} value={dispForm.note} placeholder="如：请于X日前办结并反馈，逾期将追责。" onChange={(e) => setDispForm({ ...dispForm, note: e.target.value })} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 模型预警 → 分办弹窗（打通 预警→分办→整改→销号 闭环） */}
      <Modal title="预警分办 / 督办下发" open={aDispOpen} onOk={submitAlertDispatch} onCancel={() => setADispOpen(false)} okText="分办并下发" destroyOnClose>
        {aDispTarget && (
          <Descriptions size="small" column={1} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="预警">{aDispTarget.title}</Descriptions.Item>
            <Descriptions.Item label="对象">{aDispTarget.ref_id || '—'}</Descriptions.Item>
            <Descriptions.Item label="说明">{aDispTarget.detail}</Descriptions.Item>
            <Descriptions.Item label="处置时限">
              <SlaTag state={aDispTarget.sla_state} days={aDispTarget.days_left} due={aDispTarget.due_at} />
              <span style={{ marginLeft: 8, fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>
                高 24 小时 / 中 72 小时 / 低 7 天；分办后 3 个自然日内反馈，逾期自动升级
              </span>
            </Descriptions.Item>
          </Descriptions>
        )}
        <Form layout="vertical">
          <Form.Item label="分办对象（承办人/责任单位）" required>
            <Input value={aDispForm.urge_to} placeholder="请填写承办人姓名或责任单位" onChange={(e) => setADispForm({ ...aDispForm, urge_to: e.target.value })} />
          </Form.Item>
          <Form.Item label="分办要求/说明">
            <Input.TextArea rows={3} value={aDispForm.note} placeholder="如：请于X日前核查资金去向并反馈。" onChange={(e) => setADispForm({ ...aDispForm, note: e.target.value })} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 督办反馈弹窗 */}
      <Modal title="督办反馈 / 闭环" open={fbOpen} onOk={submitFeedback} onCancel={() => setFbOpen(false)} okText="提交反馈" destroyOnClose>
        {fbTarget && (
          <Descriptions size="small" column={1} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="来源">{fbTarget.source}</Descriptions.Item>
            <Descriptions.Item label="事项">{fbTarget.ref_title || fbTarget.ref_code || '—'}</Descriptions.Item>
            <Descriptions.Item label="当前状态">{fbTarget.status}</Descriptions.Item>
          </Descriptions>
        )}
        <Form layout="vertical">
          <Form.Item label="反馈说明">
            <Input.TextArea rows={3} value={fbForm.feedback} placeholder="填写整改/办结情况" onChange={(e) => setFbForm({ ...fbForm, feedback: e.target.value })} />
          </Form.Item>
          <Form.Item label="推进状态" required>
            <Select value={fbForm.status} onChange={(v) => setFbForm({ ...fbForm, status: v })}
              options={[{ label: '已反馈', value: '已反馈' }, { label: '已闭环', value: '已闭环' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
