import { useCallback, useEffect, useState } from 'react'
import {
  Card, Table, Button, Drawer, Form, Input, Select, Tag, Space, message,
  Descriptions, Modal, Typography, Row, Col, Segmented, Empty, Statistic, Spin,
  Timeline, Divider, Radio, Switch, Alert, DatePicker, Upload, Checkbox, Steps, Tabs,
} from 'antd'
import dayjs from 'dayjs'
import {
  PlusOutlined, FileSearchOutlined, BarChartOutlined, SafetyCertificateOutlined,
  AuditOutlined, DownloadOutlined, FileTextOutlined, HistoryOutlined,
  CheckCircleOutlined, WarningOutlined, RedoOutlined, StopOutlined,
  FileProtectOutlined, UsergroupAddOutlined, SearchOutlined, UploadOutlined,
  FileDoneOutlined, EditOutlined, DeleteOutlined, ProfileOutlined,
  RobotOutlined, ArrowLeftOutlined, CloseOutlined, RightOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth, ROLE_NAMES } from '../auth'
import RegRefDrawer from './RegRefDrawer'
import HighlightText from '../components/HighlightText'
import ChatPanel, { NexentEmpty } from '../components/nexent/ChatPanel'
import { NexentConversation, listConversations, useNexentAgents, useNexentStatus } from '../api/nexent'

const { TextArea } = Input

const STATUS_COLOR: Record<string, string> = {
  待阅卷: 'default', 审理中: 'blue', 已报告: 'orange', 已定稿: 'green',
  暂缓受理: 'gold', 不予受理: 'red', 退回补充调查: 'volcano', 已执行: 'cyan',
}
const STATUS_FLOW = ['待阅卷', '审理中', '已报告', '已定稿', '暂缓受理', '不予受理', '退回补充调查', '已执行']
const ACCEPT_COLOR: Record<string, string> = {
  受理: 'green', 暂缓受理: 'gold', 不予受理: 'red',
}
const errMsg = (e: any) => e?.response?.data?.detail || '操作失败'

function SlaBadges({ sla }: { sla: any }) {
  if (!sla || !sla.accepted) return <Tag color="default">待受理</Tag>
  if (sla.overdue) return <Tag color="red"><WarningOutlined /> 已超期 · 应 {sla.due_at}</Tag>
  if (sla.needs_attention) return <Tag color="orange">临期 · {sla.due_at}</Tag>
  return <Tag color="green">时限内 · {sla.due_at}</Tag>
}

function BarListMini({ data }: { data: Record<string, number> }) {
  const max = Math.max(1, ...Object.values(data))
  return (
    <div>
      {Object.entries(data).map(([k, v]) => (
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

// ===================== 审理申诉复查 =====================
const APPEAL_STATUS_COLOR: Record<string, string> = {
  待受理: 'default', 复查中: 'blue', 不予受理: 'red',
  已维持: 'green', 已变更: 'gold', 已撤销: 'volcano', 已终结: 'default',
}
const APPEAL_STATUS_FLOW = ['待受理', '复查中', '不予受理', '已维持', '已变更', '已撤销', '已终结']

function AppealSlaBadge({ sla }: { sla: any }) {
  if (!sla) return <Tag>—</Tag>
  if (sla.overdue) return <Tag color="red"><WarningOutlined /> 时限超期</Tag>
  if (sla.accept_needs || sla.review_needs) return <Tag color="orange">临期预警</Tag>
  if (sla.accepted) return <Tag color="blue">复查时限 {sla.review_due_at}</Tag>
  return <Tag color="default">受理时限 {sla.accept_due_at}</Tag>
}

function AppealsPanel({ pendingTrial, onClearPending }: { pendingTrial: number | null; onClearPending: () => void }) {
  const { user } = useAuth()
  const canConclude = ['shenli', 'admin'].includes(user?.role || '')
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(10)
  const [fStatus, setFStatus] = useState('')
  const [overdueOnly, setOverdueOnly] = useState(false)
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [reviewers, setReviewers] = useState<any[]>([])
  const [trials, setTrials] = useState<any[]>([])
  const [detail, setDetail] = useState<any>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [timeline, setTimeline] = useState<any[]>([])
  const [decisionData, setDecisionData] = useState<any>(null)
  const [decisionOpen, setDecisionOpen] = useState(false)
  const [registerOpen, setRegisterOpen] = useState(false)
  const [form] = Form.useForm()

  const [acceptOpen, setAcceptOpen] = useState(false)
  const [acceptDecision, setAcceptDecision] = useState('受理')
  const [acceptReason, setAcceptReason] = useState('')
  const [assignOpen, setAssignOpen] = useState(false)
  const [assignReviewer, setAssignReviewer] = useState<number | undefined>()
  const [assignRecusal, setAssignRecusal] = useState('')
  const [concludeOpen, setConcludeOpen] = useState(false)
  const [conclusion, setConclusion] = useState('维持')
  const [concludeReason, setConcludeReason] = useState('')
  const [concludeNote, setConcludeNote] = useState('')
  const [serveOpen, setServeOpen] = useState(false)
  const [serveNote, setServeNote] = useState('')
  const [correctOpen, setCorrectOpen] = useState(false)
  const [correctNote, setCorrectNote] = useState('')
  const [reappealOpen, setReappealOpen] = useState(false)
  const [reappealNote, setReappealNote] = useState('')
  const [finalizeOpen, setFinalizeOpen] = useState(false)
  const [finalizeNote, setFinalizeNote] = useState('')

  const load = () => {
    setLoading(true)
    const qs = new URLSearchParams({ page: String(page), per_page: String(perPage) })
    if (fStatus) qs.set('status', fStatus)
    if (q) qs.set('q', q)
    if (overdueOnly) qs.set('overdue', '1')
    api.get(`/appeals?${qs.toString()}`).then(({ data }: any) => { setRows(data.items || []); setTotal(data.total || 0) })
      .catch(() => { setRows([]); setTotal(0) }).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [page, perPage, fStatus, q, overdueOnly])
  useEffect(() => {
    api.get('/appeals/reviewers').then(({ data }: any) => setReviewers(data || [])).catch(() => setReviewers([]))
    api.get('/trials?status=' + encodeURIComponent('已定稿') + '&per_page=200').then(({ data }: any) => {
      const list = Array.isArray(data) ? data : (data.items || []); setTrials(list)
    }).catch(() => setTrials([]))
  }, [])
  useEffect(() => {
    if (pendingTrial) {
      setRegisterOpen(true)
      form.setFieldsValue({ trial_id: pendingTrial })
      onClearPending()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingTrial])

  const openDetail = (r: any) => {
    api.get(`/appeals/${r.id}`).then(({ data }: any) => {
      setDetail(data); setDetailOpen(true)
      api.get(`/appeals/${r.id}/timeline`).then(({ data: t }: any) => setTimeline(t.items || [])).catch(() => setTimeline([]))
    }).catch(() => {})
  }
  const aact = (r: any, path: string, body: any, okMsg: string, onDone?: () => void) => {
    api.post(`/appeals/${r.id}/${path}`, body).then(() => {
      message.success(okMsg); load(); if (detailOpen && detail?.id === r.id) openDetail(r); onDone && onDone()
    }).catch((e: any) => message.error(e?.response?.data?.detail || '操作失败'))
  }
  const submitRegister = async () => {
    const v = await form.validateFields()
    api.post('/appeals', v).then(() => { message.success('申诉已登记'); setRegisterOpen(false); form.resetFields(); load() })
      .catch((e: any) => message.error(e?.response?.data?.detail || '登记失败'))
  }
  const downloadDecision = async (r: any) => {
    try {
      const { data } = await api.get(`/appeals/${r.id}/decision/export`, { responseType: 'blob', timeout: 60000 })
      const url = window.URL.createObjectURL(data)
      const a = document.createElement('a'); a.href = url; a.download = `${detail?.code || r.id}_复查决定书.txt`; a.click()
      window.URL.revokeObjectURL(url)
    } catch (e: any) { message.error(e?.response?.data?.detail || '下载失败') }
  }
  const showDecision = (r: any) => {
    api.get(`/appeals/${r.id}/decision`).then(({ data }: any) => { if (data?.content) { setDecisionData(data); setDecisionOpen(true) } else message.warning('尚未生成复查决定书') })
      .catch((e: any) => message.warning(e?.response?.data?.detail || '尚未生成复查决定书'))
  }

  const columns = [
    { title: '申诉编号', dataIndex: 'code', width: 130 },
    { title: '关联案号', dataIndex: 'case_code', width: 120 },
    { title: '被处分人', dataIndex: 'case_subject', width: 90 },
    { title: '申诉人', dataIndex: 'appellant', width: 90, ellipsis: true },
    { title: '来源', dataIndex: 'source', width: 80 },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={APPEAL_STATUS_COLOR[s]}>{s}</Tag> },
    {
      title: '受理', dataIndex: 'accept_decision', width: 88,
      render: (v: string) => v ? <Tag color={v === '受理' ? 'green' : v === '不予受理' ? 'red' : 'gold'}>{v}</Tag> : <Tag>待受理</Tag>,
    },
    { title: '时限合规', key: 'sla', width: 140, render: (_: any, r: any) => <AppealSlaBadge sla={r.sla} /> },
    { title: '操作', key: 'op', width: 90, fixed: 'right' as const, render: (_: any, r: any) => <Button size="small" onClick={() => openDetail(r)}>详情</Button> },
  ]

  const term = detail?.reappealed || detail?.finalized_at || detail?.status === '不予受理'

  return (
    <div>
      <Card size="small">
        <Space style={{ marginBottom: 10 }} wrap>
          <Input.Search placeholder="编号/案号/被处分人/申诉人" allowClear style={{ width: 240 }}
            value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
            onSearch={(v) => { setQ(v || ''); setPage(1) }} />
          <Select placeholder="状态" allowClear style={{ width: 120 }} value={fStatus || undefined}
            onChange={(v) => { setFStatus(v || ''); setPage(1) }}
            options={APPEAL_STATUS_FLOW.map((s) => ({ label: s, value: s }))} />
          <Button type={overdueOnly ? 'primary' : 'default'} danger={overdueOnly} icon={<WarningOutlined />}
            onClick={() => { setOverdueOnly(!overdueOnly); setPage(1) }}>超期预警{overdueOnly ? '（已筛）' : ''}</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setRegisterOpen(true) }}>登记申诉</Button>
        </Space>
        <Table rowKey="id" size="small" loading={loading} columns={columns} dataSource={rows} scroll={{ x: 1000 }} pagination={{
          current: page, pageSize: perPage, total, showSizeChanger: true, showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => { setPage(p); setPerPage(ps) },
        }} />
      </Card>

      <Drawer width={780} open={detailOpen} onClose={() => setDetailOpen(false)} title={`申诉复查详情 ${detail?.code || ''}`}>
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="申诉编号">{detail.code}</Descriptions.Item>
              <Descriptions.Item label="关联原处分决定">{detail.case_code}（{detail.case_subject}）· {detail.trial_title}</Descriptions.Item>
              <Descriptions.Item label="申诉人">{detail.appellant}{detail.appellant_unit ? `（${detail.appellant_unit}）` : ''}</Descriptions.Item>
              <Descriptions.Item label="申诉来源">{detail.source}</Descriptions.Item>
              <Descriptions.Item label="申诉理由/诉求"><div style={{ whiteSpace: 'pre-wrap' }}>{detail.reason}</div></Descriptions.Item>
              <Descriptions.Item label="状态"><Tag color={APPEAL_STATUS_COLOR[detail.status]}>{detail.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="受理决定">
                {detail.accept_decision ? <Tag color={detail.accept_decision === '受理' ? 'green' : detail.accept_decision === '不予受理' ? 'red' : 'gold'}>{detail.accept_decision}</Tag> : <Tag>待受理</Tag>}
                {detail.accepted_at ? ` · ${detail.accepted_at}` : ''}{detail.accept_reason ? <div style={{ color: '#94a3b8', fontSize: 12 }}>{detail.accept_reason}</div> : null}
              </Descriptions.Item>
              <Descriptions.Item label="复查承办人">{detail.reviewer_name || '—'}{detail.recusal_note ? <div style={{ color: '#94a3b8', fontSize: 12 }}>回避：{detail.recusal_note}</div> : null}</Descriptions.Item>
              <Descriptions.Item label="复核意见"><div style={{ whiteSpace: 'pre-wrap' }}>{detail.review_note || '—'}</div></Descriptions.Item>
              <Descriptions.Item label="复查结论">
                {detail.conclusion ? <Tag color={detail.conclusion === '维持' ? 'green' : detail.conclusion === '变更' ? 'gold' : 'volcano'}>{detail.conclusion}</Tag> : '—'}
                {detail.reviewed_at ? ` · ${detail.reviewed_at}` : ''}{detail.conclusion_reason ? <div style={{ color: '#94a3b8', fontSize: 12 }}>{detail.conclusion_reason}</div> : null}
              </Descriptions.Item>
              <Descriptions.Item label="送达">{detail.served_at || '—'}{detail.served_note ? <div style={{ color: '#94a3b8', fontSize: 12 }}>{detail.served_note}</div> : null}</Descriptions.Item>
              <Descriptions.Item label="纠错执行">{detail.correction_note || '—'}</Descriptions.Item>
              <Descriptions.Item label="再申诉">{detail.reappealed ? '已再申诉' : '未再申诉'}{detail.reappeal_note ? <div style={{ color: '#94a3b8', fontSize: 12 }}>{detail.reappeal_note}</div> : null}</Descriptions.Item>
              <Descriptions.Item label="终结">{detail.finalized_at || '—'}</Descriptions.Item>
              <Descriptions.Item label="时限合规"><AppealSlaBadge sla={detail.sla} /></Descriptions.Item>
              <Descriptions.Item label="登记人">{detail.created_name} · {detail.created_at}</Descriptions.Item>
            </Descriptions>

            <Card size="small" title="申诉复查办理" style={{ marginTop: 12 }}>
              <Space wrap>
                {detail.status === '待受理' && (
                  <Button icon={<CheckCircleOutlined />} onClick={() => { setAcceptDecision(detail.accept_decision || '受理'); setAcceptReason(detail.accept_reason || ''); setAcceptOpen(true) }}>受理审核</Button>
                )}
                {detail.status === '复查中' && !detail.reviewer_id && (
                  <Button icon={<UsergroupAddOutlined />} onClick={() => { setAssignReviewer(undefined); setAssignRecusal(''); setAssignOpen(true) }}>指定复查承办人</Button>
                )}
                {detail.status === '复查中' && detail.reviewer_id && (
                  <Button type="primary" icon={<AuditOutlined />}
                    disabled={!canConclude} onClick={() => { if (!canConclude) { message.warning('须审理室或管理员作复查结论'); return } setConclusion('维持'); setConcludeReason(''); setConcludeNote(''); setConcludeOpen(true) }}>复查结论</Button>
                )}
                {['已维持', '已变更', '已撤销'].includes(detail.status) && !detail.served_at && (
                  <Button icon={<FileProtectOutlined />} onClick={() => { setServeNote(''); setServeOpen(true) }}>送达备案</Button>
                )}
                {detail.status === '已撤销' || detail.status === '已变更' ? !detail.correction_note && (
                  <Button icon={<SafetyCertificateOutlined />} onClick={() => { setCorrectNote(''); setCorrectOpen(true) }}>纠错执行</Button>
                ) : null}
                {['已维持', '已变更', '已撤销'].includes(detail.status) && !detail.reappealed && !detail.finalized_at && (
                  <>
                    <Button icon={<RedoOutlined />} onClick={() => { setReappealNote(''); setReappealOpen(true) }}>再申诉（二级终结）</Button>
                    <Button icon={<StopOutlined />} onClick={() => { setFinalizeNote(''); setFinalizeOpen(true) }}>复查终结</Button>
                  </>
                )}
                {detail.decision_doc_id && (
                  <Button icon={<FileTextOutlined />} onClick={() => showDecision(detail)}>复查决定书</Button>
                )}
              </Space>
              {term && <div style={{ marginTop: 8, color: '#94a3b8', fontSize: 12 }}>该申诉复查已终结，流程不可再变更。</div>}
            </Card>

            <Divider orientation="left" plain><HistoryOutlined /> 流程轨迹</Divider>
            {timeline.length ? (
              <Timeline items={timeline.map((t: any) => ({
                color: t.action === 'appeal_conclude' ? 'green' : t.action === 'appeal_accept' ? 'blue'
                  : (t.action === 'appeal_reappeal' || t.action === 'appeal_finalize' ? 'gray' : 'blue'),
                children: (
                  <div>
                    <div><b>{t.label}</b> <span style={{ color: '#94a3b8', fontSize: 12 }}>{t.created_at} · {t.username}</span></div>
                    {t.desc && <div style={{ color: '#475569', fontSize: 12 }}>{t.desc}</div>}
                  </div>
                ),
              }))} />
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无操作记录" />}
          </>
        )}
      </Drawer>

      <Modal title="复查决定书（AI 初稿，须人工审核后印发）" open={decisionOpen} onCancel={() => setDecisionOpen(false)} width={760} footer={[
        <Button key="close" onClick={() => setDecisionOpen(false)}>关闭</Button>,
        <Button key="dl" type="primary" icon={<DownloadOutlined />} onClick={() => detail && downloadDecision(detail)}>下载</Button>,
      ]}>
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', maxHeight: 420, overflow: 'auto', background: '#f8fafc', padding: 12 }}>
          {decisionData?.content}
        </Typography.Paragraph>
      </Modal>

      {/* 登记申诉 */}
      <Modal title="登记申诉复查" open={registerOpen} onOk={submitRegister} onCancel={() => setRegisterOpen(false)} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item label="原处分决定（须已定稿的审理事项）" name="trial_id" rules={[{ required: true, message: '请选择已定稿审理事项' }]}>
            <Select showSearch optionFilterProp="label" placeholder="仅显示已定稿事项"
              options={trials.map((c: any) => ({ label: `${c.case_code || ''} ${c.case_subject || c.title}`, value: c.id }))} />
          </Form.Item>
          <Form.Item label="申诉人" name="appellant"><Input placeholder="被处分人姓名" /></Form.Item>
          <Form.Item label="单位/职务" name="appellant_unit"><Input placeholder="申诉人单位/职务" /></Form.Item>
          <Form.Item label="申诉来源" name="source" initialValue="本人书面">
            <Select options={[{ label: '本人书面', value: '本人书面' }, { label: '上级交办', value: '上级交办' }, { label: '其他', value: '其他' }]} />
          </Form.Item>
          <Form.Item label="申诉理由/主要诉求" name="reason" rules={[{ required: true, message: '请填写申诉理由' }]}>
            <TextArea rows={4} placeholder="如对事实认定、证据、量纪档次、程序等有异议，逐条说明。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 受理审核 */}
      <Modal title={`受理审核 · ${detail?.code || ''}`} open={acceptOpen}
        onOk={() => aact(detail, 'accept', { decision: acceptDecision, reason: acceptReason }, '已记录受理决定', () => setAcceptOpen(false))}
        onCancel={() => setAcceptOpen(false)} okText="确定" destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="受理决定">
            <Radio.Group value={acceptDecision} onChange={(e) => setAcceptDecision(e.target.value)}>
              <Radio value="受理">受理</Radio>
              <Radio value="暂缓受理">暂缓受理</Radio>
              <Radio value="不予受理">不予受理</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item label="理由 / 依据">
            <TextArea rows={3} value={acceptReason} onChange={(e) => setAcceptReason(e.target.value)} placeholder="受理/不予受理/暂缓原因。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 指定复查承办人 */}
      <Modal title={`指定复查承办人（落实回避） · ${detail?.code || ''}`} open={assignOpen}
        onOk={() => aact(detail, 'assign', { reviewer_id: assignReviewer, recusal_note: assignRecusal }, '已指定复查承办人', () => setAssignOpen(false))}
        onCancel={() => setAssignOpen(false)} okText="确定" destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="复查承办人" required>
            <Select placeholder="选择承办人（不得为原审理承办人）" value={assignReviewer} onChange={(v) => setAssignReviewer(v)}
              options={reviewers.map((u: any) => ({ label: `${u.name}（${u.role}）`, value: u.id }))} />
          </Form.Item>
          <Form.Item label="回避说明">
            <TextArea rows={2} value={assignRecusal} onChange={(e) => setAssignRecusal(e.target.value)} placeholder="如：非原案承办人，已落实回避。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 复查结论 */}
      <Modal title={`复查结论（须审理室/管理员） · ${detail?.code || ''}`} open={concludeOpen}
        onOk={() => aact(detail, 'conclude', { conclusion, reason: concludeReason, review_note: concludeNote }, '已作出复查结论并生成决定书', () => setConcludeOpen(false))}
        onCancel={() => setConcludeOpen(false)} okText="确定" okButtonProps={{ disabled: !canConclude }} destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="复查结论">
            <Radio.Group value={conclusion} onChange={(e) => setConclusion(e.target.value)}>
              <Radio value="维持">维持</Radio>
              <Radio value="变更">变更</Radio>
              <Radio value="撤销">撤销</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item label="复核意见">
            <TextArea rows={3} value={concludeNote} onChange={(e) => setConcludeNote(e.target.value)} placeholder="复查组调卷复核意见。" />
          </Form.Item>
          <Form.Item label="结论理由">
            <TextArea rows={3} value={concludeReason} onChange={(e) => setConcludeReason(e.target.value)} placeholder="如：事实清楚、证据确凿，量纪适当 / 量纪偏重应予变更 / 原处分事实不清应予撤销。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 送达备案 */}
      <Modal title={`复查决定书送达备案 · ${detail?.code || ''}`} open={serveOpen}
        onOk={() => aact(detail, 'serve', { note: serveNote }, '已送达备案', () => setServeOpen(false))}
        onCancel={() => setServeOpen(false)} okText="确定" destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="送达 / 备案说明">
            <TextArea rows={3} value={serveNote} onChange={(e) => setServeNote(e.target.value)} placeholder="如：已送达申诉人本人，并报上级备案。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 纠错执行 */}
      <Modal title={`纠错执行（变更/撤销） · ${detail?.code || ''}`} open={correctOpen}
        onOk={() => aact(detail, 'correct', { note: correctNote }, '已记录纠错执行', () => setCorrectOpen(false))}
        onCancel={() => setCorrectOpen(false)} okText="确定" destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="纠错执行说明（恢复权利/待遇/名誉）">
            <TextArea rows={3} value={correctNote} onChange={(e) => setCorrectNote(e.target.value)} placeholder="如：恢复党员权利、恢复公职待遇、消除影响、恢复名誉。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 再申诉 */}
      <Modal title={`再申诉（二级终结） · ${detail?.code || ''}`} open={reappealOpen}
        onOk={() => aact(detail, 'reappeal', { note: reappealNote }, '已进入二级终结', () => setReappealOpen(false))}
        onCancel={() => setReappealOpen(false)} okText="确定" okButtonProps={{ danger: true }} destroyOnClose>
        <div style={{ marginBottom: 8, color: '#64748b' }}>申诉人对复查决定仍不服，可向上一级提出再申诉，进入二级终结。原处分决定不停止执行。</div>
        <Form layout="vertical">
          <Form.Item label="再申诉说明">
            <TextArea rows={3} value={reappealNote} onChange={(e) => setReappealNote(e.target.value)} placeholder="再申诉理由与请求。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 复查终结 */}
      <Modal title={`复查终结 · ${detail?.code || ''}`} open={finalizeOpen}
        onOk={() => aact(detail, 'finalize', { note: finalizeNote }, '已终结', () => setFinalizeOpen(false))}
        onCancel={() => setFinalizeOpen(false)} okText="确定" destroyOnClose>
        <div style={{ marginBottom: 8, color: '#64748b' }}>维持且无再申诉、或纠错执行完毕的，予以终结。</div>
        <Form layout="vertical">
          <Form.Item label="终结说明">
            <TextArea rows={3} value={finalizeNote} onChange={(e) => setFinalizeNote(e.target.value)} placeholder="终结说明。" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default function Trials() {
  const { user } = useAuth()
  const canConfirm = ['shenli', 'admin'].includes(user?.role || '')
  const [view, setView] = useState<'list' | 'stats' | 'appeal'>('list')
  const [appealPendingTrial, setAppealPendingTrial] = useState<number | null>(null)
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(10)
  const [loading, setLoading] = useState(false)
  const [stats, setStats] = useState<any>(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [cases, setCases] = useState<any[]>([])
  const [fStatus, setFStatus] = useState('')
  const [overdueOnly, setOverdueOnly] = useState(false)
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')
  const [detail, setDetail] = useState<any>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [timeline, setTimeline] = useState<any[]>([])
  const [reportData, setReportData] = useState<any>(null)
  const [reportOpen, setReportOpen] = useState(false)
  // 类案参考（历史报告库）：检索问题表述 → 选用 → 生成报告时附于文末并留痕
  const [refQuery, setRefQuery] = useState('')
  const [refHits, setRefHits] = useState<any[]>([])
  const [refTerms, setRefTerms] = useState<string[]>([])
  const [refSearching, setRefSearching] = useState(false)
  const [pickedRefs, setPickedRefs] = useState<any[]>([])
  const [citedRefs, setCitedRefs] = useState<any[]>([])
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmNote, setConfirmNote] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()
  // 新建项目：导入审查调查报告识别回填 + 报告生成选用模板
  const [intakeBusy, setIntakeBusy] = useState(false)
  const [intakeHint, setIntakeHint] = useState('')
  const [reportTplId, setReportTplId] = useState<number | null>(null)
  const [docTpls, setDocTpls] = useState<any[]>([])
  // —— 量纪说理（结构化辅助引擎） ——
  const [reasoning, setReasoning] = useState<any>(null)
  const [reasoningLoading, setReasoningLoading] = useState(false)
  const [reasoningTextOpen, setReasoningTextOpen] = useState(false)
  // 审理组人工认定的从轻/从重情节（标准情节目录 key）
  const [manualLight, setManualLight] = useState<string[]>([])
  const [manualHeavy, setManualHeavy] = useState<string[]>([])

  // —— 真实工作流动作弹窗状态 ——
  const [acceptOpen, setAcceptOpen] = useState(false)
  const [acceptDecision, setAcceptDecision] = useState('受理')
  const [acceptReason, setAcceptReason] = useState('')
  const [interviewOpen, setInterviewOpen] = useState(false)
  const [interviewAttitude, setInterviewAttitude] = useState('认错悔错')
  const [interviewNote, setInterviewNote] = useState('')
  // —— 见面谈话专业化（《监督执纪工作规则》第五十一~五十三条）——
  const [interviewInterviewers, setInterviewInterviewers] = useState('')
  const [interviewRecorder, setInterviewRecorder] = useState('')
  const [interviewPlace, setInterviewPlace] = useState('')
  const [interviewRights, setInterviewRights] = useState(false)
  const [interviewSummary, setInterviewSummary] = useState('')
  const [defenseItems, setDefenseItems] = useState<any[]>([])
  const [returnOpen, setReturnOpen] = useState(false)
  const [returnReason, setReturnReason] = useState('')
  const [returnTo, setReturnTo] = useState('审查调查组')
  const [executeOpen, setExecuteOpen] = useState(false)
  const [executeNote, setExecuteNote] = useState('')
  // —— P3 处分决定书 + 执行回证 ——
  const [decisionOpen, setDecisionOpen] = useState(false)
  const [decisionNo, setDecisionNo] = useState('')
  const [decisionResult, setDecisionResult] = useState('')
  const [decisionDate, setDecisionDate] = useState('')
  const [decisionApprover, setDecisionApprover] = useState('')
  const [decisionViewOpen, setDecisionViewOpen] = useState(false)
  const [decisionView, setDecisionView] = useState<any>(null)
  const [reportViewOpen, setReportViewOpen] = useState(false)
  const [reportView, setReportView] = useState<any>(null)
  const [executeAnnouncer, setExecuteAnnouncer] = useState('')
  const [executeScope, setExecuteScope] = useState('')
  const [executeDelivery, setExecuteDelivery] = useState('')
  const [executeSigned, setExecuteSigned] = useState(false)
  const [itemEditOpen, setItemEditOpen] = useState(false)
  const [itemEditKey, setItemEditKey] = useState('')
  const [itemEditStatus, setItemEditStatus] = useState('pending')
  const [itemEditAt, setItemEditAt] = useState('')
  const [itemEditNote, setItemEditNote] = useState('')
  const [propertyOpen, setPropertyOpen] = useState(false)
  const [propertyDisposal, setPropertyDisposal] = useState('')
  // —— 物证情况审查 ——
  const [physOpen, setPhysOpen] = useState(false)
  const [physForm] = Form.useForm()
  const [physEditing, setPhysEditing] = useState<number | null>(null)
  // —— 相关依据检索（内嵌法规知识库） ——
  const [regRefOpen, setRegRefOpen] = useState(false)
  const [regRefSeed, setRegRefSeed] = useState('')
  // —— 案件工作台 / AI 协审（Nexent） ——
  const navigate = useNavigate()
  const [wbTab, setWbTab] = useState('overview')
  const [aiOpen, setAiOpen] = useState<boolean>(() => {
    try { return localStorage.getItem('ctas.wb.ai') !== '0' } catch { return true }
  })
  const [aiAgent, setAiAgent] = useState('')
  const [aiCid, setAiCid] = useState<number | null>(null)
  const [trialConvs, setTrialConvs] = useState<NexentConversation[]>([])
  const { status: nxStatus, ready: nxReady } = useNexentStatus()
  const { agents: nxAgents, defaultAgent: nxDefault } = useNexentAgents(nxReady)
  useEffect(() => { try { localStorage.setItem('ctas.wb.ai', aiOpen ? '1' : '0') } catch { /* ignore */ } }, [aiOpen])
  useEffect(() => {
    if (aiAgent || !nxAgents.length) return
    // 审理协审默认交给「研判分析」场景智能体（带平台 MCP 工具），其次是系统默认智能体
    const pick = [nxStatus?.scene_agents?.analysis, nxDefault].find((n) => n && nxAgents.some((a) => a.name === n))
    setAiAgent(pick || nxAgents[0].name)
  }, [nxAgents, nxDefault, aiAgent, nxStatus?.scene_agents?.analysis])
  const loadTrialConvs = useCallback(() => {
    if (!nxReady || !detail?.id) { setTrialConvs([]); return }
    listConversations(detail.id).then(setTrialConvs).catch(() => setTrialConvs([]))
  }, [nxReady, detail?.id])
  useEffect(() => { setAiCid(null); loadTrialConvs() }, [detail?.id, loadTrialConvs])

  const load = () => {
    setLoading(true)
    const qs = new URLSearchParams({ page: String(page), per_page: String(perPage) })
    if (fStatus) qs.set('status', fStatus)
    if (q) qs.set('q', q)
    if (overdueOnly) qs.set('overdue', '1')
    api.get(`/trials?${qs.toString()}`)
      .then(({ data }) => { setRows(data.items || []); setTotal(data.total || 0) })
      .catch(() => { setRows([]); setTotal(0) })
      .finally(() => setLoading(false))
  }
  const loadStats = () => {
    setStatsLoading(true)
    api.get('/trials/stats').then(({ data }) => setStats(data)).catch(() => setStats(null)).finally(() => setStatsLoading(false))
  }
  // 接收驾驶舱下钻：审理状态饼图 / 超期 KPI 带参进入
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search)
    const st = sp.get('status'); const od = sp.get('overdue')
    if (st) setFStatus(st)
    if (od === '1') setOverdueOnly(true)
    const vw = sp.get('view')
    if (vw === 'appeal' || vw === 'stats') setView(vw)
    const op = Number(sp.get('open') || 0)
    if (op) openDetail({ id: op })
    if (sp.get('create') === '1') openCreate()
    if (op || sp.get('create')) window.history.replaceState(null, '', '/trials')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { load() }, [page, perPage, fStatus, q, overdueOnly])
  useEffect(() => { loadStats() }, [view])

  const openDetail = (r: any) => {
    api.get(`/trials/${r.id}`).then(({ data }) => {
      if (detail?.id !== data.id) setWbTab('overview')
      setDetail(data)
      setDetailOpen(true)
      // 切换审理事项时清空上一条的类案选用与量纪说理，避免串用
      setPickedRefs([]); setRefHits([]); setRefTerms([]); setCitedRefs([])
      setReasoning(null); setReasoningTextOpen(false)
      setPropertyOpen(false)
      setInterviewOpen(false); setDefenseItems([])
      setManualLight([]); setManualHeavy([])
      // P3：重置处分决定/执行/回证弹窗状态防串案
      setDecisionOpen(false); setDecisionViewOpen(false); setDecisionView(null)
      setExecuteOpen(false); setItemEditOpen(false)
      loadReasoning(r.id)
      api.get(`/trials/${r.id}/timeline`).then(({ data: t }) => setTimeline(t.items || [])).catch(() => setTimeline([]))
    }).catch(() => {})
  }
  /** 量纪说理（结构化辅助引擎）：定性→真实法条→量纪区间→从轻从重，离线确定性生成。 */
  const loadReasoning = (tid: number) => {
    setReasoningLoading(true)
    api.get(`/trials/${tid}/sentencing-reasoning`)
      .then(({ data }) => setReasoning(data))
      .catch(() => setReasoning(null))
      .finally(() => setReasoningLoading(false))
  }
  /** 审理组人工认定从轻/从重情节后重算说理稿（POST，人工项与自动项去重合并）。 */
  const recomputeReasoning = (tid: number, lightKeys: string[], heavyKeys: string[]) => {
    setReasoningLoading(true)
    api.post(`/trials/${tid}/sentencing-reasoning`, { light_keys: lightKeys, heavy_keys: heavyKeys })
      .then(({ data }) => setReasoning(data))
      .catch(() => {})
      .finally(() => setReasoningLoading(false))
  }
  /** 勾选/取消某条人工认定情节。 */
  const toggleManual = (kind: 'light' | 'heavy', key: string, checked: boolean) => {
    if (!detail) return
    if (kind === 'light') {
      const next = checked ? [...new Set([...manualLight, key])] : manualLight.filter(k => k !== key)
      setManualLight(next); recomputeReasoning(detail.id, next, manualHeavy)
    } else {
      const next = checked ? [...new Set([...manualHeavy, key])] : manualHeavy.filter(k => k !== key)
      setManualHeavy(next); recomputeReasoning(detail.id, manualLight, next)
    }
  }
  /** P3：生成/重新生成处分决定书（裁量事项人工填，系统不预设）。 */
  const submitDecision = () => {
    if (!detail) return
    api.post(`/trials/${detail.id}/decision`, {
      decision_no: decisionNo, decision_result: decisionResult,
      decision_date: decisionDate, decision_approver: decisionApprover,
    }).then(({ data }) => {
      message.success(data.decision?.needs_manual ? '已生成初稿（含〔待填〕裁量事项，须补全后定稿）' : '处分决定书已生成')
      setDecisionOpen(false)
      if (detailOpen && detail?.id === detail.id) openDetail(detail)
    }).catch((e) => message.error(errMsg(e)))
  }
  /** P3：查看处分决定书全文。 */
  const viewDecision = (tid: number) => {
    api.get(`/trials/${tid}/decision`).then(({ data }) => {
      setDecisionView(data.decision); setDecisionViewOpen(true)
    }).catch((e) => message.error(errMsg(e)))
  }
  /** P3：生成《处分决定执行情况报告》（执行回证）并查看。 */
  const genExecutionReport = (tid: number) => {
    api.post(`/trials/${tid}/execution-report`, {}).then(({ data }) => {
      setReportView(data); setReportViewOpen(true)
      message.success('执行回证报告已生成入库')
      if (detailOpen && detail?.id === tid) openDetail(detail)
    }).catch((e) => message.error(errMsg(e)))
  }
  /** P3：宣布执行（结构化 body）。 */
  const submitExecute = () => {
    if (!detail) return
    api.post(`/trials/${detail.id}/execute`, {
      note: executeNote, announcer: executeAnnouncer, scope: executeScope,
      delivery: executeDelivery, signed: executeSigned,
    }).then(({ data }) => {
      message.success('已记录宣布执行')
      if ((data.warnings || []).length) {
        Modal.info({ title: '执行合规提示（须复核补正）', content: (data.warnings || []).map((w: string) => <div key={w}>· {w}</div>) })
      }
      setExecuteOpen(false)
      if (detailOpen && detail?.id === detail.id) openDetail(detail)
    }).catch((e) => message.error(errMsg(e)))
  }
  /** P3：执行回证单项回填。 */
  const submitExecutionItem = () => {
    if (!detail || !itemEditKey) return
    api.post(`/trials/${detail.id}/execution-items`, {
      key: itemEditKey, status: itemEditStatus, at: itemEditAt, note: itemEditNote,
    }).then(({ data }) => {
      message.success('回证已回填')
      setItemEditOpen(false)
      if ((data.blockers || []).length) {
        message.warning(`尚有 ${data.blockers.length} 项未落实，不得报结`)
      }
      if (detailOpen && detail?.id === detail.id) openDetail(detail)
    }).catch((e) => message.error(errMsg(e)))
  }
  /** P3：回证报结（有未落实项时后端 400 拦截）。 */
  const closeExecution = () => {
    if (!detail) return
    api.post(`/trials/${detail.id}/execution-close`, {}).then(() => {
      message.success('执行回证已报结归档')
      if (detailOpen && detail?.id === detail.id) openDetail(detail)
    }).catch((e) => message.error(errMsg(e)))
  }
  const act = (r: any, path: string, body: any, okMsg: string, reload = true, onDone?: () => void) => {
    api.post(`/trials/${r.id}/${path}`, body).then(() => {
      message.success(okMsg)
      if (reload) { load(); if (detailOpen && detail?.id === r.id) openDetail(r) }
      onDone && onDone()
    }).catch((e) => message.error(errMsg(e)))
  }
  const submitConfirm = () => {
    if (!detail) return
    api.post(`/trials/${detail.id}/confirm`, { note: confirmNote }).then(() => {
      message.success('已定稿（审议结论已留痕）')
      setConfirmOpen(false); setConfirmNote('')
      load(); openDetail(detail)
    }).catch((e) => message.error(errMsg(e)))
  }
  /** 见面谈话（专业化）：保存结构化谈话要素与逐条申辩，呈现程序合规提示（不阻断）。 */
  const saveInterview = async () => {
    if (!detail) return
    const body = {
      attitude: interviewAttitude,
      note: interviewNote,
      interviewers: interviewInterviewers,
      recorder: interviewRecorder,
      place: interviewPlace,
      rights_notified: interviewRights,
      defense_items: defenseItems,
      summary: interviewSummary,
    }
    try {
      const { data } = await api.post(`/trials/${detail.id}/interview`, body)
      const warns: string[] = data?.warnings || []
      if (warns.length) {
        Modal.info({
          title: '谈话已保存 · 程序合规提示',
          content: <div>{warns.map((w, i) => <div key={i} style={{ marginBottom: 6 }}>· {w}</div>)}
            <div style={{ color: '#94a3b8', fontSize: 12, marginTop: 8 }}>提示仅用于复核补正，系统不替代人工认定。</div></div>,
          okText: '知道了',
        })
      } else {
        message.success('已记录见面谈话（要素齐全，符合程序要求）')
      }
      setInterviewOpen(false)
      load(); openDetail(detail)
    } catch (e) { message.error(errMsg(e)) }
  }
  /** 打开谈话弹窗：预填已有谈话要素；无申辩记录时给一条空行便于直接录入。 */
  const openInterviewEditor = () => {
    if (!detail) return
    setInterviewAttitude(detail.interview_attitude || '认错悔错')
    setInterviewNote(detail.interview_note || '')
    setInterviewInterviewers(detail.interviewers || '')
    setInterviewRecorder(detail.recorder || '')
    setInterviewPlace(detail.interview_place || '')
    setInterviewRights(!!detail.rights_notified)
    setInterviewSummary(detail.interview_summary || '')
    setDefenseItems((detail.defense_items || []).length
      ? (detail.defense_items as any[]).map((d: any) => ({ ...d }))
      : [{ content: '', stance: '提出申辩', adopt: '待审议', reason: '' }])
    setInterviewOpen(true)
  }
  /** 涉案财物处置意见：打开填写弹窗。已有意见预填原文；无意见预填系统建议方向作模板（可改可删）。 */
  const openPropertyEditor = () => {
    const plan = reasoning?.property_plan
    setPropertyDisposal(
      plan?.raw
      || detail?.property_disposal
      || (plan?.suggestions?.length ? plan.suggestions.map((s: string) => '· ' + s).join('\n') : '')
    )
    setPropertyOpen(true)
  }
  /** 类案参考：检索历史报告库中与本案问题对口的问题表述。 */
  const searchRefs = async () => {
    const q = refQuery.trim()
    if (!q) { message.warning('请输入问题关键词'); return }
    setRefSearching(true)
    try {
      const { data } = await api.post('/historical-reports/search', { q, top_k: 6 })
      setRefHits(data.hits || []); setRefTerms(data.terms || [])
      if (!(data.hits || []).length) message.info('未命中历史报告，换个关键词试试')
    } catch (e) { message.error(errMsg(e)) } finally { setRefSearching(false) }
  }

  const pickRef = (h: any, s: any) => {
    const item = {
      report_id: h.id, finding_id: s.finding_id || 0,
      title: h.title, year: h.year,
      problem_type: s.problem_type || h.problem_category || '',
      content: s.content, query: refQuery.trim(),
    }
    setPickedRefs((prev) => (
      prev.some((p) => p.report_id === item.report_id && p.finding_id === item.finding_id)
        ? prev : [...prev, item]
    ))
    message.success('已选用，生成报告时将附于文末「八、类案参考」并留痕')
  }

  // 文书模板库（审理报告套模板生成用）
  useEffect(() => {
    api.get('/documents/templates').then(({ data }: any) => setDocTpls(data || [])).catch(() => setDocTpls([]))
  }, [])

  /** 生成审理报告：可选用文书模板；携带已选类案引用（后端附于文末并逐条留痕）。 */
  const genReport = async () => {
    if (!detail) return
    try {
      const { data } = await api.post(`/trials/${detail.id}/report`, { note: '', template_id: reportTplId || null, refs: pickedRefs, light_keys: manualLight, heavy_keys: manualHeavy })
      message.success(pickedRefs.length
        ? `已生成审理报告（附 ${pickedRefs.length} 条类案参考，引用已留痕）`
        : '已生成审理报告（AI初稿）')
      setReportData(data.document); setCitedRefs(data.cited || []); setReportOpen(true)
      setPickedRefs([]); setRefHits([])
      load(); openDetail(detail)
    } catch (e) { message.error(errMsg(e)) }
  }

  /** 导入审查调查报告/移送材料 → 识别审理建档字段 → 回填新建表单（人工核对后提交）。 */
  const onIntakeFile = async (info: any) => {
    const f = info.file
    if (!f || intakeBusy) return
    if (f.size > 30 * 1024 * 1024) { message.error('文件过大（≤30MB）'); return }
    setIntakeBusy(true)
    try {
      const fd = new FormData(); fd.append('file', f)
      const { data } = await api.post('/trials/extract', fd, { timeout: 300000 })
      const fields = data.fields || {}
      const fv: any = { ...fields }
      if (fv.opened_at) fv.opened_at = dayjs(fv.opened_at)   // DatePicker 需 dayjs 对象
      form.setFieldsValue(fv)
      const miss: string[] = data.missing || []
      const conf = Math.round((data.confidence || 0) * 100)
      message.success(`识别完成：回填 ${Object.keys(fields).length} 个字段（置信度 ${conf}%）${miss.length ? `，${miss.length} 项未识别` : ''}`)
      setIntakeHint(miss.length ? `未识别字段：${miss.join('、')}，请人工补填后再提交。` : '字段已全部识别，请核对后提交。')
    } catch (e) { message.error(errMsg(e)) }
    finally { setIntakeBusy(false) }
  }

  const showReport = async (r: any) => {
    try {
      const { data } = await api.get(`/trials/${r.id}/report`)
      if (data?.content) { setReportData(data); setReportOpen(true) }
      else message.warning('尚未生成审理报告')
    } catch (e) {
      message.warning((e as any)?.response?.data?.detail || '尚未生成审理报告')
    }
  }
  const downloadReport = async (r: any) => {
    try {
      const { data } = await api.get(`/trials/${r.id}/report/export`, { responseType: 'blob', timeout: 60000 })
      const url = window.URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${detail?.case_code || r.case_code || r.id}_审理报告.txt`
      a.click()
      window.URL.revokeObjectURL(url)
    } catch (e) { message.error(errMsg(e)) }
  }
  const submitCreate = async () => {
    const v = await form.validateFields()
    const body: any = { ...v }
    if (body.opened_at && dayjs.isDayjs(body.opened_at)) body.opened_at = body.opened_at.format('YYYY-MM-DD')
    if (body.case_id === undefined || body.case_id === null || body.case_id === '') delete body.case_id
    api.post('/trials', body).then(() => { message.success('审理事项已创建'); setCreateOpen(false); form.resetFields(); load() })
      .catch((e) => message.error(errMsg(e)))
  }

  const savePe = (v: any) => {
    if (!detail) return
    const body = { ...v, has_photo: !!v.has_photo }
    const done = () => { physForm.resetFields(); setPhysEditing(null); openDetail(detail) }
    if (physEditing != null) {
      api.put(`/trials/${detail.id}/physical-evidence/${physEditing}`, body)
        .then(() => { message.success('已保存物证'); done() }).catch((e) => message.error(errMsg(e)))
    } else {
      api.post(`/trials/${detail.id}/physical-evidence`, body)
        .then(() => { message.success('已登记物证'); done() }).catch((e) => message.error(errMsg(e)))
    }
  }
  const delPe = (eid: number) => {
    if (!detail) return
    api.delete(`/trials/${detail.id}/physical-evidence/${eid}`)
      .then(() => { message.success('已删除物证'); openDetail(detail) }).catch((e) => message.error(errMsg(e)))
  }

  function openCreate() {
    api.get('/cases?per_page=200').then(({ data }) => {
      const list = Array.isArray(data) ? data : (data.items || [])
      setCases(list.filter((c: any) => ['已立案', '初核中', '已处置'].includes(c.status)))
    }).catch(() => setCases([]))
    setCreateOpen(true)
  }

  const columns = [
    {
      title: '事项', dataIndex: 'title', width: 260, ellipsis: true,
      render: (v: string) => <span style={{ fontWeight: 600, color: '#0f172a' }}>{v}</span>,
    },
    { title: '关联案件', dataIndex: 'case_code', width: 120 },
    {
      title: '被审查人', dataIndex: 'case_subject', width: 120,
      render: (v: string) => v ? <span className="cx-subject"><span className="cx-subject-avatar">{v.slice(0, 1)}</span>{v}</span> : '—',
    },
    {
      title: '状态', dataIndex: 'status', width: 96,
      render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
    },
    {
      title: '受理', dataIndex: 'accept_decision', width: 88,
      render: (v: string) => v ? <Tag color={ACCEPT_COLOR[v]}>{v}</Tag> : <Tag>待受理</Tag>,
    },
    {
      title: '审理时限', key: 'sla', width: 150,
      render: (_: any, r: any) => <SlaBadges sla={r.sla} />,
    },
    {
      title: '操作', key: 'op', width: 110, fixed: 'right' as const,
      render: (_: any, r: any) => <Button size="small" type="link" onClick={(e) => { e.stopPropagation(); openDetail(r) }}>打开工作台 <RightOutlined /></Button>,
    },
  ]

  const CheckList = ({ title, payload, showFacts }: { title: string; payload: any; showFacts?: boolean }) => {
    const data = Array.isArray(payload) ? payload : (payload || {})
    const checks = data.checks || (Array.isArray(payload) ? payload : [])
    const verdict = data.verdict
    const facts = data.facts || []
    const gaps = data.gaps || []
    const vColor = verdict === '闭环' || verdict === '合规' ? 'green'
      : verdict === '断裂' || verdict === '不合规' ? 'red'
      : verdict === '待补强' || verdict === '待完善' ? 'orange' : 'default'
    return (
      <Card size="small" title={title} style={{ marginTop: 12 }}
        extra={verdict ? <Tag color={vColor}>{verdict}</Tag> : null}>
        {!checks.length && !verdict ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未执行" />
        ) : (
          <>
            {checks.map((c: any, i: number) => (
              <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid #f6f8fb' }}>
                <Tag color={c.level === 'error' ? 'red' : c.level === 'warn' ? 'orange'
                  : c.level === 'info' ? 'blue' : 'default'}>{c.status || c.item}</Tag>
                <span style={{ fontSize: 13 }}>{c.point || c.item}</span>
                <div style={{ fontSize: 12, color: '#94a3b8' }}>{c.note}</div>
              </div>
            ))}
            {showFacts && facts.length ? (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 12, color: '#475569', marginBottom: 2 }}>事实-证据对应</div>
                {facts.map((f: any, i: number) => (
                  <div key={i} style={{ fontSize: 12, padding: '2px 0' }}>
                    <Tag color={f.level === 'warn' ? 'orange' : 'blue'}>{f.status}</Tag>
                    <span style={{ color: '#475569' }}>{f.fact}</span>
                  </div>
                ))}
              </div>
            ) : null}
            {gaps.length ? (
              <div style={{ marginTop: 8, fontSize: 12, color: '#b91c1c' }}>
                待补正：{gaps.join('；')}
              </div>
            ) : null}
          </>
        )}
      </Card>
    )
  }

  // ===================== 物证情况审查（直观可视化） =====================
  function Donut({ value, total }: { value: number; total: number }) {
    const pct = total ? Math.round((value / total) * 100) : 0
    const r = 26, c = 2 * Math.PI * r
    const off = c * (1 - pct / 100)
    const color = pct === 100 ? '#15803d' : pct >= 60 ? '#1d4ed8' : '#b91c1c'
    return (
      <svg width="72" height="72" viewBox="0 0 72 72">
        <circle cx="36" cy="36" r={r} fill="none" stroke="#eef1f5" strokeWidth="8" />
        <circle cx="36" cy="36" r={r} fill="none" stroke={color} strokeWidth="8"
          strokeDasharray={c} strokeDashoffset={off} strokeLinecap="round" transform="rotate(-90 36 36)" />
        <text x="36" y="40" textAnchor="middle" fontSize="15" fontWeight="700" fill={color}>{pct}%</text>
      </svg>
    )
  }
  function MiniBar({ value, total }: { value: number; total: number }) {
    const pct = total ? Math.round((value / total) * 100) : 0
    const color = pct === 100 ? '#15803d' : pct >= 60 ? '#1d4ed8' : '#b91c1c'
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ flex: 1, background: '#eef1f5', borderRadius: 4, height: 10, overflow: 'hidden' }}>
          <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4 }} />
        </div>
        <span style={{ fontSize: 12, width: 36, textAlign: 'right' }}>{value}/{total}</span>
      </div>
    )
  }
  function PhysicalCard({ title, list }: { title: string; list: any[] }) {
    return (
      <div style={{ flex: 1, border: '1px solid #e9edf3', borderRadius: 8, padding: 10, background: '#fafcff' }}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>{title}</span>
          <Tag color={list.length ? 'green' : 'red'}>{list.length ? `${list.length} 项` : '待登记'}</Tag>
        </div>
        {list.length ? list.map((e: any, j: number) => (
          <div key={j} style={{ fontSize: 12, padding: '4px 0', borderTop: '1px dashed #e9edf3' }}>
            <div><b>{e.name || '(未命名)'}</b> {e.has_photo
              ? <Tag color="blue" style={{ marginLeft: 4 }}>有照片</Tag>
              : <Tag>无照片</Tag>}</div>
            <div style={{ color: '#64748b' }}>
              {[e.qty && `数量:${e.qty}`, e.feature && `特征:${e.feature}`, e.storage && `存放:${e.storage}`].filter(Boolean).join(' · ') || '—'}
            </div>
            {e.photo_note && <div style={{ color: '#94a3b8', fontSize: 11 }}>照片说明: {e.photo_note}</div>}
          </div>
        )) : (
          <div style={{ fontSize: 12, color: '#b6c0cf', lineHeight: 1.6 }}>文章要求：{title}及相关照片，请登记查封/扣押或购买信息并附照片。</div>
        )}
      </div>
    )
  }
  function EvidenceReviewMatrix({ review, physical }: { review: any; physical: any[] }) {
    if (!review || !review.aspects) {
      return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未生成审查（点击右上角「生成审查」）" />
    }
    const vColor = review.verdict === '闭环' ? 'green' : review.verdict === '待补强' ? 'orange' : 'red'
    const pe = physical || []
    const seized = pe.filter((e: any) => (e.category || '').includes('查封') || (e.category || '').includes('扣押'))
    const purchased = pe.filter((e: any) => (e.category || '').includes('购买'))
    return (
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 10 }}>
          <Donut value={review.covered} total={review.total} />
          <div style={{ flex: 1 }}>
            <div style={{ marginBottom: 4 }}>整体取证完整度：<b>{review.covered}/{review.total}</b> · <Tag color={vColor}>{review.verdict}</Tag></div>
            <div style={{ fontSize: 12, color: '#94a3b8' }}>物证条目 {review.physical_total || 0} 项，其中附照片 {review.photos_total || 0} 项</div>
          </div>
        </div>
        {review.aspects.map((a: any, i: number) => (
          <div key={i} style={{ marginBottom: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
              {a.name} <span style={{ color: '#94a3b8', fontWeight: 400 }}>（{a.covered}/{a.total}）</span>
            </div>
            <MiniBar value={a.covered} total={a.total} />
            <div style={{ marginTop: 4 }}>
              {a.items.map((it: any, j: number) => (
                <div key={j} style={{ padding: '3px 0', borderBottom: '1px dashed #eef1f5' }}>
                  <Tag color={it.status === '已覆盖' ? 'green' : it.status === '缺失' ? 'red' : 'default'}>{it.status}</Tag>
                  <span style={{ fontSize: 12.5 }}>{it.point}</span>
                  {it.physical && <Tag color="purple" style={{ marginLeft: 4 }}>物证</Tag>}
                  <div style={{ fontSize: 11.5, color: '#94a3b8', marginLeft: 2 }}>{it.note}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
        <div style={{ marginTop: 8 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6, color: '#4f46e5' }}>物证专项（按文章要求：须附照片）</div>
          <div style={{ display: 'flex', gap: 10 }}>
            <PhysicalCard title="查封、扣押的赃款赃物及照片" list={seized} />
            <PhysicalCard title="用赃款购买的物品及照片" list={purchased} />
          </div>
        </div>
        {review.note && <div style={{ fontSize: 11.5, color: '#94a3b8', marginTop: 8 }}>{review.note}</div>}
      </div>
    )
  }


  /* ================= 案件工作台（全屏）：分区内容 =================
     原 760px 抽屉里自上而下堆叠的 10 张卡片，按办理阶段拆到 6 个页签；
     业务逻辑、弹窗与接口调用保持不变。右侧为 Nexent 智能体「AI 协审」。 */
  const wbStages = detail ? [
    { t: '受理', done: detail.accept_decision === '受理', bad: detail.accept_decision === '不予受理' },
    { t: '阅卷审查', done: !!detail.summary || !!detail.evidence_check?.verdict },
    { t: '见面谈话', done: !!detail.interview_at },
    { t: '审理报告', done: !!detail.report_doc_id },
    { t: '定稿', done: !!detail.confirmed_at || ['已定稿', '已执行'].includes(detail.status) },
    { t: '处分决定', done: !!detail.decision_doc_id },
    { t: '宣布执行', done: !!detail.executed_at },
    { t: '回证报结', done: !!detail.execution_closed },
  ] : []
  const wbCurrent = (() => { const i = wbStages.findIndex((s) => !s.done); return i < 0 ? wbStages.length - 1 : i })()
  const verdictColor = (v?: string) => v === '闭环' || v === '合规' ? 'green' : v === '断裂' || v === '不合规' ? 'red'
    : v === '待补强' || v === '待完善' ? 'orange' : 'default'
  const tierText = (x: any) => Array.isArray(x) ? x.join('、') : (x || '')

  const wbOverview = detail ? (
    <Row gutter={16}>
      <Col xs={24} xl={14}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="事项">{detail.title}</Descriptions.Item>
              <Descriptions.Item label="关联案件">{detail.case_code}（{detail.case_subject}）</Descriptions.Item>
              <Descriptions.Item label="状态"><Tag color={STATUS_COLOR[detail.status]}>{detail.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="受理决定">
                {detail.accept_decision ? <Tag color={ACCEPT_COLOR[detail.accept_decision]}>{detail.accept_decision}</Tag> : <Tag>待受理</Tag>}
                {detail.accepted_at ? ` · ${detail.accepted_at}` : ''}
                {detail.accept_reason ? <div style={{ color: '#94a3b8', fontSize: 12 }}>{detail.accept_reason}</div> : null}
              </Descriptions.Item>
              <Descriptions.Item label="审理时限"><SlaBadges sla={detail.sla} /></Descriptions.Item>
              <Descriptions.Item label="见面谈话">
                {detail.interview_at ? (
                  <>
                    <div>{detail.interview_at}（{detail.interview_attitude || '—'}）</div>
                    {(detail.interviewers || detail.interview_place || detail.recorder) && (
                      <div style={{ color: '#475569', fontSize: 12 }}>
                        {[
                          detail.interview_place ? `地点：${detail.interview_place}` : '',
                          detail.interviewers ? `谈话人：${detail.interviewers}` : '',
                          detail.recorder ? `记录人：${detail.recorder}` : '',
                        ].filter(Boolean).join('；')}
                      </div>
                    )}
                    <div style={{ marginTop: 2 }}>
                      <Tag color={detail.rights_notified ? 'green' : 'orange'} style={{ fontSize: 11 }}>
                        {detail.rights_notified ? '已告知申辩权利' : '未记录权利告知'}
                      </Tag>
                      {(detail.defense_items || []).length > 0 && (
                        <Tag color="blue" style={{ fontSize: 11 }}>申辩 {detail.defense_items.length} 条</Tag>
                      )}
                    </div>
                  </>
                ) : '—'}
                {detail.interview_note ? <div style={{ color: '#94a3b8', fontSize: 12 }}>{detail.interview_note}</div> : null}
                {(detail.defense_items || []).map((d: any, i: number) => (
                  <div key={i} style={{ color: '#475569', fontSize: 12, marginTop: 2 }}>
                    · {d.stance}：{d.content}
                    <Tag color={d.adopt === '采纳' ? 'green' : d.adopt === '不采纳' ? 'red' : d.adopt === '部分采纳' ? 'blue' : 'orange'}
                      style={{ fontSize: 11, marginLeft: 4 }}>{d.adopt}</Tag>
                    {d.reason ? `（${d.reason}）` : ''}
                  </div>
                ))}
                {detail.interview_summary ? <div style={{ color: '#94a3b8', fontSize: 12 }}>说明：{detail.interview_summary}</div> : null}
              </Descriptions.Item>
              <Descriptions.Item label="退回补证">
                {detail.returned_at ? `${detail.returned_at} → ${detail.return_to}` : '—'}
                {detail.return_reason ? <div style={{ color: '#94a3b8', fontSize: 12 }}>{detail.return_reason}</div> : null}
              </Descriptions.Item>
              <Descriptions.Item label="宣布执行">
                {detail.executed_at ? detail.executed_at : '—'}
                {detail.execute_announcer ? <div style={{ color: '#94a3b8', fontSize: 12 }}>宣布人：{detail.execute_announcer}{detail.execute_delivery ? ` · ${detail.execute_delivery}` : ''}{detail.execute_signed ? ' · 本人已签收' : ' · 未签收'}</div> : null}
                {detail.execute_scope ? <div style={{ color: '#94a3b8', fontSize: 12 }}>范围：{detail.execute_scope}</div> : null}
              </Descriptions.Item>
              <Descriptions.Item label="涉案财物处置">{detail.property_disposal || '—'}</Descriptions.Item>
              <Descriptions.Item label="创建人">{detail.created_by}</Descriptions.Item>
              <Descriptions.Item label="定稿时间">{detail.confirmed_at || '—'}</Descriptions.Item>
              {detail.confirmed_note && (
                <Descriptions.Item label="审议结论">{detail.confirmed_note}</Descriptions.Item>
              )}
            </Descriptions>
      </Col>
      <Col xs={24} xl={10}>
        <Card size="small" title="审查要点速览">
          <div className="cx-ov-list">
            <div className="cx-ov-item"><span>送审材料</span>
              {detail.materials ? <Tag color={detail.materials.complete ? 'green' : 'gold'}>{detail.materials.complete ? '齐全' : `缺 ${detail.materials.required_missing?.length || 0} 项`}</Tag> : <Tag>未登记</Tag>}
            </div>
            <div className="cx-ov-item"><span>证据链校验</span>
              <Tag color={verdictColor(detail.evidence_check?.verdict)}>{detail.evidence_check?.verdict || '未校验'}</Tag>
              <a onClick={() => setWbTab('review')}>查看</a>
            </div>
            <div className="cx-ov-item"><span>程序合规检查</span>
              <Tag color={verdictColor(detail.procedure_check?.verdict)}>{detail.procedure_check?.verdict || '未检查'}</Tag>
              <a onClick={() => setWbTab('review')}>查看</a>
            </div>
            <div className="cx-ov-item"><span>定性量纪</span>
              {reasoning ? <span className="cx-ov-text">{reasoning.category} · 党纪区间 {tierText(reasoning.tier_range) || '—'}{tierText(reasoning.gov_tier_range) ? ` · 政务 ${tierText(reasoning.gov_tier_range)}` : ''}</span> : <Tag>未生成</Tag>}
              <a onClick={() => setWbTab('sentencing')}>查看</a>
            </div>
            <div className="cx-ov-item"><span>审理报告</span>
              {detail.report_doc_id ? <Tag color="orange">已生成 · 待人工审核</Tag> : <Tag>未生成</Tag>}
              <a onClick={() => setWbTab('report')}>前往</a>
            </div>
          </div>
        </Card>
        <Card size="small" title="阅卷摘要" style={{ marginTop: 12 }}
          extra={<Button size="small" icon={<FileSearchOutlined />} onClick={() => act(detail, 'summarize', {}, '已生成阅卷摘要')}>{detail.summary ? '重新生成' : '生成摘要'}</Button>}>
          {detail.summary
            ? <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }} ellipsis={{ rows: 9, expandable: true, symbol: '展开' }}>{detail.summary}</Typography.Paragraph>
            : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未生成阅卷摘要" />}
        </Card>
      </Col>
    </Row>
  ) : null

  const wbFlow = detail ? (
    <>
            {/* —— 真实工作流：受理 / 见面谈话 / 退回 / 宣布执行 / 涉案财物 —— */}
            <Card size="small" title="受理 · 谈话 · 退回 · 执行" style={{ marginTop: 12 }}>
              <Space wrap>
                <Button icon={<CheckCircleOutlined />}
                  onClick={() => { setAcceptDecision(detail.accept_decision || '受理'); setAcceptReason(detail.accept_reason || ''); setAcceptOpen(true) }}>
                  受理审核
                </Button>
                <Button icon={<AuditOutlined />}
                  onClick={openInterviewEditor}>
                  见面谈话
                </Button>
                <Button icon={<FileSearchOutlined />}
                  onClick={openPropertyEditor}>
                  涉案财物处置
                </Button>
                <Button danger icon={<WarningOutlined />}
                  onClick={() => { setReturnReason(detail.return_reason || ''); setReturnTo(detail.return_to || '审查调查组'); setReturnOpen(true) }}>
                  退回补证
                </Button>
                <Button type="primary" icon={<ProfileOutlined />}
                  disabled={!canConfirm || detail.status !== '已定稿'}
                  onClick={() => {
                    setDecisionNo(detail.decision_no || ''); setDecisionResult(detail.decision_result || '')
                    setDecisionDate(detail.decision_date || ''); setDecisionApprover(detail.decision_approver || '')
                    setDecisionOpen(true)
                  }}>
                  {detail.decision_doc_id ? '重新生成处分决定书' : '制作处分决定书'}
                </Button>
                {detail.decision_doc_id && (
                  <Button icon={<FileTextOutlined />} onClick={() => viewDecision(detail.id)}>查看决定书</Button>
                )}
                <Button type="primary" icon={<CheckCircleOutlined />}
                  disabled={!canConfirm || detail.status !== '已定稿'}
                  onClick={() => {
                    setExecuteNote(detail.execute_note || ''); setExecuteAnnouncer(detail.execute_announcer || '')
                    setExecuteScope(detail.execute_scope || ''); setExecuteDelivery(detail.execute_delivery || '')
                    setExecuteSigned(!!detail.execute_signed); setExecuteOpen(true)
                  }}>
                  宣布执行
                </Button>
              </Space>
            </Card>

            {/* —— P3 处分决定 · 执行回证 —— */}
            <Card size="small" title="处分决定 · 执行回证" style={{ marginTop: 12 }}
              extra={detail.execution_closed
                ? <Tag color="green">已报结（{detail.execution_closed_at}）</Tag>
                : detail.executed_at
                  ? <Tag color="orange">待报结{detail.execution_blockers?.length ? ` · ${detail.execution_blockers.length} 项未落实` : ''}</Tag>
                  : <Tag>未执行</Tag>}>
              {detail.decision_doc_id ? (
                <Descriptions size="small" column={2} style={{ marginBottom: 8 }}>
                  <Descriptions.Item label="处分决定">{detail.decision_result || '〔档次待填〕'}</Descriptions.Item>
                  <Descriptions.Item label="文号">{detail.decision_no || '〔待填〕'}</Descriptions.Item>
                  <Descriptions.Item label="批准机关">{detail.decision_approver || '〔待填〕'}</Descriptions.Item>
                  <Descriptions.Item label="决定日期">{detail.decision_date || '〔待填〕'}</Descriptions.Item>
                </Descriptions>
              ) : (
                <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                  审理定稿后，先由处分批准机关审定处分档次，再制作处分决定书。
                </Typography.Paragraph>
              )}
              {detail.executed_at ? (
                <>
                  <div style={{ margin: '8px 0 4px', fontWeight: 500 }}>执行回证清单</div>
                  {(detail.execution_items || []).map((it: any) => (
                    <div key={it.key} style={{ display: 'flex', alignItems: 'center', padding: '5px 0', borderBottom: '1px solid #f6f8fb' }}>
                      <Tag color={it.status === 'done' ? 'green' : it.status === 'na' ? 'default' : 'orange'}
                        style={{ minWidth: 64, textAlign: 'center' }}>
                        {it.status === 'done' ? '已落实' : it.status === 'na' ? '不适用' : '待落实'}
                      </Tag>
                      <div style={{ flex: 1, marginLeft: 8 }}>
                        <div style={{ fontSize: 13 }}>{it.label}</div>
                        <div style={{ fontSize: 11, color: '#94a3b8' }}>
                          {it.ref}{it.at ? ` · ${it.at}` : ''}{it.note ? ` · ${it.note}` : ''}
                        </div>
                      </div>
                      {detail.execution_closed ? null : (
                        <Button size="small" type="link" disabled={detail.execution_closed}
                          onClick={() => {
                            setItemEditKey(it.key); setItemEditStatus(it.status || 'pending')
                            setItemEditAt(it.at || ''); setItemEditNote(it.note || ''); setItemEditOpen(true)
                          }}>回填</Button>
                      )}
                    </div>
                  ))}
                  {detail.restore_rights_at && (
                    <div style={{ fontSize: 12, color: '#475569', marginTop: 6 }}>
                      恢复党员权利时间：{detail.restore_rights_at}
                    </div>
                  )}
                  {!detail.execution_closed && (
                    <div style={{ marginTop: 8 }}>
                      {(detail.execution_blockers || []).length > 0 ? (
                        <Tag color="red">尚有 {(detail.execution_blockers || []).length} 项未落实，不得报结</Tag>
                      ) : (
                        <Space>
                          <Button type="primary" size="small" icon={<CheckCircleOutlined />} onClick={closeExecution}>
                            回证报结归档
                          </Button>
                          {detail.executed_at && (
                            <Button size="small" icon={<FileDoneOutlined />} onClick={() => genExecutionReport(detail.id)}>
                              生成执行回证
                            </Button>
                          )}
                        </Space>
                      )}
                    </div>
                  )}
                  {detail.execution_closed && (
                    <div style={{ marginTop: 8 }}>
                      <Button size="small" icon={<FileDoneOutlined />} onClick={() => genExecutionReport(detail.id)}>
                        生成/查看执行回证
                      </Button>
                    </div>
                  )}
                </>
              ) : (
                <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                  宣布执行处分决定后，在此逐项回证（职务职级调整 / 工资待遇变更 / 涉案财物处置 / 材料归档 / 恢复党员权利），缺项不得报结。
                </Typography.Paragraph>
              )}
            </Card>
    </>
  ) : null

  const wbReview = detail ? (
    <>
            <Card size="small" title="审理阅卷摘要" style={{ marginTop: 12 }}
              extra={<Button size="small" icon={<FileSearchOutlined />} onClick={() => act(detail, 'summarize', {}, '已生成阅卷摘要')}>生成摘要</Button>}>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{detail.summary || '（未生成）'}</Typography.Paragraph>
            </Card>

            <CheckList title="证据链完整性校验" payload={detail.evidence_check} showFacts />
            <CheckList title="程序合规性检查" payload={detail.procedure_check} />

            <Card size="small" title="物证情况审查（受贿取证要点）" style={{ marginTop: 12 }}
              extra={<Space>
                <Button size="small" icon={<FileSearchOutlined />} onClick={() => act(detail, 'evidence-review', {}, '已生成物证审查')}>生成审查</Button>
                <Button size="small" icon={<FileProtectOutlined />} onClick={() => setPhysOpen(true)}>管理物证</Button>
              </Space>}>
              <EvidenceReviewMatrix review={detail.evidence_review} physical={detail.physical_evidence} />
            </Card>
    </>
  ) : null

  const wbSentencing = detail ? (
    <>
            <Card size="small" title="类案量纪参考" style={{ marginTop: 12 }}
              extra={<Button size="small" icon={<AuditOutlined />} onClick={() => act(detail, 'sentencing-ref', {}, '已检索类案')}>检索类案</Button>}>
              {detail.sentencing_ref?.refs?.length ? (
                <>
                  <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>{detail.sentencing_ref.note}</Typography.Paragraph>
                  {detail.sentencing_ref.refs.map((r: any, i: number) => (
                    <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid #f6f8fb' }}>
                      <Tag>{r.article}</Tag> {r.regulation}
                      <div style={{ fontSize: 12, color: '#64748b' }}>{r.content}</div>
                    </div>
                  ))}
                </>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未检索" />}
            </Card>

            <Card size="small" title="类案参考（历史报告库）" style={{ marginTop: 12 }}
              extra={<Tag color="blue">检索历史审计报告的问题表述，选用后附于审理报告文末并留痕</Tag>}>
              <Space wrap size={[8, 8]} style={{ marginBottom: refHits.length ? 8 : 0 }}>
                <Input
                  style={{ width: 300 }} placeholder="问题关键词，如：公务接待 无审批单"
                  prefix={<SearchOutlined />} allowClear
                  value={refQuery}
                  onChange={(e) => setRefQuery(e.target.value)}
                  onPressEnter={searchRefs}
                />
                <Button icon={<SearchOutlined />} onClick={searchRefs} loading={refSearching}>检索历史报告</Button>
              </Space>
              {refHits.length > 0 && (
                <div style={{ maxHeight: 240, overflow: 'auto', border: '1px solid #eef1f5', borderRadius: 6, padding: '6px 10px' }}>
                  {refHits.map((h: any) => (
                    <div key={h.id} style={{ padding: '6px 0', borderBottom: '1px solid #f6f8fb' }}>
                      <Space size={6} wrap>
                        <Tag color="blue">{Math.round((h.score || 0) * 100)}%</Tag>
                        <b>{h.title}</b>
                        <Tag>{h.year}</Tag>
                        <Tag>{h.problem_category}</Tag>
                        {h.cite_count ? <Tag color="gold">被引 {h.cite_count}</Tag> : null}
                      </Space>
                      {(h.snippets || []).map((s: any) => (
                        <div key={s.finding_id || s.content}
                          style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginTop: 4 }}>
                          <div style={{ fontSize: 12, flex: 1 }}>
                            <Tag color="blue">{s.problem_type || '问题'}</Tag>
                            <HighlightText text={s.content} terms={refTerms} />
                          </div>
                          <Button size="small" type="link" onClick={() => pickRef(h, s)}>选用</Button>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
              {pickedRefs.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ marginBottom: 4, fontSize: 12, color: '#475569' }}>
                    已选 {pickedRefs.length} 条引用（生成报告时附于文末「八、类案参考」，逐条留痕）：
                  </div>
                  <Space wrap size={[4, 4]}>
                    {pickedRefs.map((p: any, i: number) => (
                      <Tag key={`${p.report_id}-${p.finding_id}-${i}`} closable color="volcano"
                        onClose={() => setPickedRefs(pickedRefs.filter((_, j) => j !== i))}>
                        {String(p.title || '').slice(0, 16)}｜{String(p.content || '').slice(0, 16)}…
                      </Tag>
                    ))}
                  </Space>
                </div>
              )}
            </Card>

            <Card size="small" title={<span><FileDoneOutlined /> 量纪说理（结构化辅助引擎）</span>} style={{ marginTop: 12 }}
              extra={<Button size="small" icon={<RedoOutlined />} loading={reasoningLoading} onClick={() => detail && loadReasoning(detail.id)}>重新生成</Button>}>
              <Spin spinning={reasoningLoading}>
                {reasoning ? (
                  <>
                    <Space wrap style={{ marginBottom: 8 }}>
                      <Tag color="purple">定性：{reasoning.category}</Tag>
                      <Tag color={reasoning.severity_band === 'heavy' ? 'red' : reasoning.severity_band === 'mid' ? 'orange' : 'green'}>
                        情节：{reasoning.severity_band === 'heavy' ? '情节严重' : reasoning.severity_band === 'mid' ? '情节较重' : '情节较轻'}
                      </Tag>
                      <Tag color="blue">党纪区间：{reasoning.tier_range}</Tag>
                      {reasoning.gov_tier_range && !String(reasoning.gov_tier_range).includes('须结合') && (
                        <Tag color="cyan">政务区间：{reasoning.gov_tier_range}</Tag>
                      )}
                      {reasoning.amount_wan != null && <Tag>涉案约 {Number(reasoning.amount_wan).toFixed(2)} 万元</Tag>}
                    </Space>
                    <Divider orientation="left" plain style={{ margin: '8px 0' }}>法条依据（真实条号·条文）</Divider>
                    {(reasoning.substantive || []).map((a: any, i: number) => (
                      <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid #f6f8fb' }}>
                        <Tag color="geekblue">{a.no}</Tag>
                        <Typography.Paragraph style={{ fontSize: 12.5, margin: '4px 0 0 0', whiteSpace: 'pre-wrap', color: '#475569' }}>{a.text}</Typography.Paragraph>
                      </div>
                    ))}
                    {(reasoning.light?.length > 0 || reasoning.heavy?.length > 0) && (
                      <>
                        <Divider orientation="left" plain style={{ margin: '8px 0' }}>从轻 / 从重情节</Divider>
                        {(reasoning.light || []).map((f: any, i: number) => (
                          <div key={`l${i}`} style={{ fontSize: 12.5, padding: '3px 0' }}>
                            <Tag color="green">从轻/减轻</Tag> {f.point} <span style={{ color: '#94a3b8' }}>（{f.ref}）</span>
                          </div>
                        ))}
                        {(reasoning.heavy || []).map((f: any, i: number) => (
                          <div key={`h${i}`} style={{ fontSize: 12.5, padding: '3px 0' }}>
                            <Tag color="red">从重/加重</Tag> {f.point} <span style={{ color: '#94a3b8' }}>（{f.ref}）</span>
                          </div>
                        ))}
                      </>
                    )}
                    <Divider orientation="left" plain style={{ margin: '8px 0' }}>人工认定情节（审理组勾选）</Divider>
                    <div style={{ fontSize: 11.5, color: '#94a3b8', marginBottom: 6 }}>
                      勾选后自动并入上方说理稿与审理报告，并标注「经审理组人工认定」；与系统自动识别重复项自动去重。
                    </div>
                    <div style={{ marginBottom: 6 }}>
                      <div style={{ fontSize: 12, color: '#475569', marginBottom: 4 }}>从轻 / 减轻（《条例》第十七条等）：</div>
                      <Space size={[4, 6]} wrap>
                        {(reasoning.light_catalog || []).map((c: any) => {
                          const on = manualLight.includes(c.key)
                          return (
                            <Tag key={c.key} color={on ? 'green' : 'default'} style={{ cursor: 'pointer' }}
                              onClick={() => toggleManual('light', c.key, !on)}>
                              {(on ? '✓ ' : '') + (c.point.length > 20 ? c.point.slice(0, 20) + '…' : c.point)}
                            </Tag>
                          )
                        })}
                      </Space>
                    </div>
                    <div>
                      <div style={{ fontSize: 12, color: '#475569', marginBottom: 4 }}>从重 / 加重（《条例》第二十条等）：</div>
                      <Space size={[4, 6]} wrap>
                        {(reasoning.heavy_catalog || []).map((c: any) => {
                          const on = manualHeavy.includes(c.key)
                          return (
                            <Tag key={c.key} color={on ? 'red' : 'default'} style={{ cursor: 'pointer' }}
                              onClick={() => toggleManual('heavy', c.key, !on)}>
                              {(on ? '✓ ' : '') + (c.point.length > 20 ? c.point.slice(0, 20) + '…' : c.point)}
                            </Tag>
                          )
                        })}
                      </Space>
                    </div>
                    {reasoning.property_plan && (
                      <>
                        <Divider orientation="left" plain style={{ margin: '8px 0' }}>涉案财物处置</Divider>
                        <Space size={[4, 6]} wrap style={{ marginBottom: 4 }}>
                          {reasoning.property_plan.amount_wan != null
                            ? <Tag color="purple">涉案约 {Number(reasoning.property_plan.amount_wan).toFixed(2)} 万元</Tag>
                            : <Tag>数额待核（以查封扣押冻结清单为准）</Tag>}
                          {(reasoning.property_plan.actions || []).map((a: string, i: number) => (
                            <Tag key={i} color="blue">{a}</Tag>
                          ))}
                          {reasoning.property_plan.needs_confirm && <Tag color="orange">承办人尚未提处置意见·建议方向须审理组确认</Tag>}
                          {reasoning.property_plan.transfer_flag && <Tag color="red">在案材料提示涉嫌犯罪移送·请核对是否随案移送</Tag>}
                        </Space>
                        {reasoning.property_plan.needs_confirm && (reasoning.property_plan.suggestions || []).map((s: string, i: number) => (
                          <div key={i} style={{ fontSize: 12.5, padding: '2px 0', color: '#475569' }}>· {s}</div>
                        ))}
                        <div style={{ fontSize: 11.5, color: '#94a3b8', marginTop: 4 }}>
                          依据：{reasoning.property_plan.articles?.join('；') || '《中国共产党纪律处分条例》第四十三条；《政务处分法》第二十五条、第二十七条'}
                        </div>
                        <div style={{ marginTop: 6 }}>
                          <Button size="small" type="primary" ghost icon={<EditOutlined />} onClick={openPropertyEditor}>
                            {reasoning.property_plan.raw ? '修改处置意见' : '填写处置意见'}
                          </Button>
                          <span style={{ fontSize: 11.5, color: '#94a3b8', marginLeft: 8 }}>保存后写入报告（五）段并留痕于流程轨迹</span>
                        </div>
                      </>
                    )}
                    <Alert type="info" showIcon style={{ marginTop: 8 }}
                      message="系统离线辅助生成，仅供参考；处分档次须经审理组集体审议并按程序报批，系统不替代人工结论。"
                      description={<Typography.Link onClick={() => setReasoningTextOpen(true)}>查看完整说理稿</Typography.Link>} />
                  </>
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无量纪说理（点击右上角重新生成）" />
                )}
              </Spin>
            </Card>
    </>
  ) : null

  const wbReport = detail ? (
    <>
            <Card size="small" title="审理报告" style={{ marginTop: 12 }}>
              {detail.report_doc_id ? (
                <Space>
                  <Button size="small" icon={<FileTextOutlined />} onClick={() => showReport(detail)}>查看报告</Button>
                  <Button size="small" icon={<DownloadOutlined />} onClick={() => downloadReport(detail)}>下载</Button>
                  <Tag color="orange">AI 初稿，须人工审核后定稿</Tag>
                </Space>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未生成" />}
            </Card>
            <Card size="small" title="报告生成与定稿" style={{ marginTop: 12 }}>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12.5 }}>
                生成审理报告会自动汇入阅卷摘要、证据链与程序校验、量纪说理及选用的类案；报告为 AI 初稿，须经审理组集体审议后定稿。
              </Typography.Paragraph>
            <Space wrap>
              <Button icon={<FileSearchOutlined />} onClick={() => { setRegRefSeed(`${detail.case_subject || ''} ${detail.title || ''}`.trim()); setRegRefOpen(true) }}>相关依据检索</Button>
              <Button icon={<SafetyCertificateOutlined />} onClick={() => act(detail, 'evidence-check', {}, '已校验证据链')}>证据链校验</Button>
              <Button icon={<SafetyCertificateOutlined />} onClick={() => act(detail, 'procedure-check', {}, '已检查程序')}>程序合规检查</Button>
              <Select allowClear style={{ width: 210 }} placeholder="默认结构 / 选用文书模板" value={reportTplId ?? undefined}
                onChange={(v) => setReportTplId(v ?? null)}
                options={docTpls.map((t: any) => ({ label: t.name, value: t.id }))} />
              <Button type="primary" icon={<FileSearchOutlined />} onClick={genReport}>生成审理报告{pickedRefs.length ? `（含${pickedRefs.length}条类案）` : ''}</Button>
              <Button icon={<RedoOutlined />} onClick={() => { setAppealPendingTrial(detail.id); setView('appeal') }}>发起申诉复查</Button>
              <Button type="primary" icon={<AuditOutlined />}
                onClick={() => { if (!canConfirm) { message.warning('须审理室或管理员确认定稿'); return } setConfirmOpen(true) }}
                disabled={detail.status === '已定稿'}>定稿确认</Button>
            </Space>
            </Card>
    </>
  ) : null

  const wbTimeline = detail ? (
    <Card size="small" title="流程轨迹（审计留痕）">
            {timeline.length ? (
              <Timeline items={timeline.map((t) => ({
                color: t.action === 'trial_confirm' ? 'green' : t.action === 'trial_report' ? 'orange'
                  : (t.action === 'trial_return' ? 'red' : 'blue'),
                children: (
                  <div>
                    <div><b>{t.label}</b> <span style={{ color: '#94a3b8', fontSize: 12 }}>{t.created_at} · {t.username}</span></div>
                    {t.desc && <div style={{ color: '#475569', fontSize: 12 }}>{t.desc}</div>}
                  </div>
                ),
              }))} />
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无操作记录" />}
    </Card>
  ) : null

  const AI_PROMPTS = [
    '审查本案证据链，指出薄弱环节和需要补充的材料',
    '核对量纪说理引用的法条是否准确、有无遗漏',
    '根据在案事实起草审理报告「主要违纪违法事实」一节',
    '拟一份见面谈话提纲，围绕可能的申辩意见',
  ]
  const wbAi = detail ? (
    <>
      <div className="cx-wb-ai-head">
        <div className="cx-wb-ai-title"><RobotOutlined /> AI 协审 <Tag className="cx-tag-soft">Nexent</Tag></div>
        {nxReady && trialConvs.length > 0 && (
          <Select size="small" style={{ width: 190 }} value={aiCid ?? 0} popupMatchSelectWidth={false}
            onChange={(v) => {
              if (!v) { setAiCid(null); return }
              const c = trialConvs.find((x) => x.conversation_id === v)
              if (c) setAiAgent(c.agent_name)
              setAiCid(v)
            }}
            options={[{ label: '＋ 新会话', value: 0 }, ...trialConvs.map((c) => ({ label: `${c.title}（${c.updated_at.slice(5)}）`, value: c.conversation_id }))]} />
        )}
      </div>
      {nxReady ? (
        <ChatPanel
          variant="side"
          agents={nxAgents}
          agentName={aiAgent}
          onAgentChange={setAiAgent}
          conversationId={aiCid}
          onConversationChange={setAiCid}
          onConversationsChanged={loadTrialConvs}
          trialId={detail.id}
          trialTitle={detail.title}
          contextDefault={nxStatus?.attach_context_default ?? true}
          quickPrompts={AI_PROMPTS}
        />
      ) : (
        <div className="cx-wb-ai-empty">
          <NexentEmpty status={nxStatus} isAdmin={user?.role === 'admin'} onGoSettings={() => navigate('/nexent-settings')} />
        </div>
      )}
    </>
  ) : null


  return (
    <div>
      <div className="cx-page-head" style={{ marginBottom: 16 }}>
        <div>
          <div className="cx-page-title"><FileDoneOutlined /> 审理台账</div>
          <div className="cx-page-sub">受理 → 阅卷审查 → 见面谈话 → 审理报告 → 定稿 → 处分决定 → 执行回证，全流程在案件工作台内办理。</div>
        </div>
        <Space wrap>
          <Segmented value={view} onChange={(v) => setView(v as any)} options={[
            { label: '审理台账', value: 'list', icon: <FileSearchOutlined /> },
            { label: '效能统计', value: 'stats', icon: <BarChartOutlined /> },
            { label: '申诉复查', value: 'appeal', icon: <RedoOutlined /> },
          ]} />
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建审理</Button>
          <Button icon={<FileSearchOutlined />} onClick={() => { setRegRefSeed(''); setRegRefOpen(true) }}>相关依据检索</Button>
        </Space>
      </div>

      {view === 'list' && (
        <Card size="small">
          <div className="cx-filter-chips" style={{ marginBottom: 12 }}>
            <button className={`cx-chip${!fStatus ? ' active' : ''}`} onClick={() => { setFStatus(''); setPage(1) }}>
              全部<span className="cx-chip-count">{stats?.total ?? '·'}</span>
            </button>
            {STATUS_FLOW.filter((s) => fStatus === s || (stats?.by_status?.[s] ?? 0) > 0).map((s) => (
              <button key={s} className={`cx-chip${fStatus === s ? ' active' : ''}`} onClick={() => { setFStatus(s); setPage(1) }}>
                {s}<span className="cx-chip-count">{stats?.by_status?.[s] ?? 0}</span>
              </button>
            ))}
          </div>
          <Space style={{ marginBottom: 10 }} wrap>
            <Input.Search placeholder="事项/案号/被审查人" allowClear style={{ width: 220 }}
              value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
              onSearch={(v) => { setQ(v || ''); setPage(1) }} />
            <Select placeholder="状态" allowClear style={{ width: 120 }} value={fStatus || undefined}
              onChange={(v) => { setFStatus(v || ''); setPage(1) }}
              options={STATUS_FLOW.map((s) => ({ label: s, value: s }))} />
            <Button type={overdueOnly ? 'primary' : 'default'} danger={overdueOnly}
              icon={<WarningOutlined />} onClick={() => { setOverdueOnly(!overdueOnly); setPage(1) }}>
              超期预警{overdueOnly ? '（已筛）' : ''}
            </Button>
          </Space>
          <Table rowKey="id" size="middle" loading={loading} columns={columns} dataSource={rows}
            onRow={(r: any) => ({ onClick: () => openDetail(r), style: { cursor: 'pointer' } })}
            scroll={{ x: 900 }} pagination={{
              current: page, pageSize: perPage, total,
              showSizeChanger: true, showTotal: (t) => `共 ${t} 条`,
              onChange: (p, ps) => { setPage(p); setPerPage(ps) },
            }} />
        </Card>
      )}

      {view === 'stats' && (
        <Spin spinning={statsLoading}>
          {stats && (
            <Row gutter={12}>
              <Col span={6}><Card><Statistic title="审理事项总数" value={stats.total} /></Card></Col>
              <Col span={6}><Card><Statistic title="已定稿" value={stats.done} /></Card></Col>
              <Col span={6}><Card><Statistic title="超期预警" value={stats.overdue_total} valueStyle={{ color: stats.overdue_total ? '#b91c1c' : undefined }} /></Card></Col>
              <Col span={6}><Card><Statistic title="未办结" value={stats.unresolved_total} /></Card></Col>
              <Col span={8}><Card size="small" title="受理决定分布"><BarListMini data={stats.by_accept} /></Card></Col>
              <Col span={8}><Card size="small" title="按状态"><BarListMini data={stats.by_status} /></Card></Col>
              <Col span={8}><Card size="small" title="效能指标">
                <div style={{ fontSize: 13, lineHeight: 2 }}>
                  受理数：<b>{stats.accepted_total}</b> / 待受理：{stats.not_accepted_total}<br />
                  见面谈话：<b>{stats.interviewed_total}</b><br />
                  退回补证：<b>{stats.returned_total}</b> · 宣布执行：<b>{stats.executed_total}</b>
                </div>
              </Card></Col>
            </Row>
          )}
        </Spin>
      )}

      {view === 'appeal' && (
        <AppealsPanel pendingTrial={appealPendingTrial} onClearPending={() => setAppealPendingTrial(null)} />
      )}

      <Drawer
        open={detailOpen} onClose={() => setDetailOpen(false)} width="100%" placement="right"
        rootClassName={`cx-wb${aiOpen ? '' : ' ai-off'}`} closable={false}
        styles={{ body: { padding: 0 } }}
      >
        {detail && (
          <>
            <div className="cx-wb-head">
              <div className="cx-wb-row1">
                <div style={{ minWidth: 0 }}>
                  <div className="cx-wb-title">
                    <Button className="cx-wb-back" type="text" icon={<ArrowLeftOutlined />} onClick={() => setDetailOpen(false)} />
                    <span>{detail.title}</span>
                    <Tag color={STATUS_COLOR[detail.status]}>{detail.status}</Tag>
                    <SlaBadges sla={detail.sla} />
                  </div>
                  <div className="cx-wb-meta">
                    <span>案号 <b>{detail.case_code || '独立建档'}</b></span>
                    <span>被审查调查人 <b>{detail.case_subject || '—'}</b></span>
                    {detail.unit ? <span>单位职务 <b>{detail.unit}</b></span> : null}
                    {detail.problem_type ? <span>问题性质 <b>{detail.problem_type}</b></span> : null}
                    {detail.involved_amount ? <span>涉案金额 <b>{detail.involved_amount}</b></span> : null}
                  </div>
                </div>
                <div className="cx-wb-actions">
                  <Button icon={<FileSearchOutlined />} onClick={() => { setRegRefSeed(`${detail.case_subject || ''} ${detail.title || ''}`.trim()); setRegRefOpen(true) }}>相关依据</Button>
                  <Button icon={<FileTextOutlined />} onClick={() => setWbTab('report')}>报告与定稿</Button>
                  <Button type={aiOpen ? 'primary' : 'default'} icon={<RobotOutlined />} onClick={() => setAiOpen(!aiOpen)}>
                    {aiOpen ? '收起 AI 协审' : 'AI 协审'}
                  </Button>
                  <Button icon={<CloseOutlined />} onClick={() => setDetailOpen(false)}>关闭</Button>
                </div>
              </div>
              <Steps className="cx-wb-steps" size="small" current={wbCurrent}
                items={wbStages.map((s, i) => ({
                  title: s.t,
                  status: s.bad ? 'error' : s.done ? 'finish' : i === wbCurrent ? 'process' : 'wait',
                }))} />
              <Tabs className="cx-wb-tabs" activeKey={wbTab} onChange={setWbTab} items={[
                { key: 'overview', label: '概览' },
                { key: 'flow', label: '流程办理' },
                { key: 'review', label: '阅卷审查' },
                { key: 'sentencing', label: '定性量纪' },
                { key: 'report', label: '报告与定稿' },
                { key: 'timeline', label: `流程轨迹${timeline.length ? `（${timeline.length}）` : ''}` },
              ]} />
            </div>
            <div className="cx-wb-body">
              <div className="cx-wb-main">
                {wbTab === 'overview' && wbOverview}
                {wbTab === 'flow' && wbFlow}
                {wbTab === 'review' && wbReview}
                {wbTab === 'sentencing' && wbSentencing}
                {wbTab === 'report' && wbReport}
                {wbTab === 'timeline' && wbTimeline}
              </div>
              <div className="cx-wb-ai">{wbAi}</div>
            </div>
          </>
        )}
      </Drawer>

      <Modal title="审理报告（AI 初稿，须人工审核）" open={reportOpen} onCancel={() => setReportOpen(false)} width={760} footer={[
        <Button key="close" onClick={() => setReportOpen(false)}>关闭</Button>,
        <Button key="dl" type="primary" icon={<DownloadOutlined />} onClick={() => detail && downloadReport(detail)}>下载</Button>,
      ]}>
        {citedRefs.length > 0 && (
          <Alert type="info" showIcon style={{ marginBottom: 12 }}
            message={`本报告文末附「八、类案参考」共 ${citedRefs.length} 条历史类案表述，引用已留痕（历史报告库 → 引用记录可查）。`} />
        )}
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', maxHeight: 420, overflow: 'auto', background: '#f8fafc', padding: 12 }}>
          {reportData?.content}
        </Typography.Paragraph>
      </Modal>

      <Modal title="量纪说理稿（结构化·辅助参考）" open={reasoningTextOpen} onCancel={() => setReasoningTextOpen(false)} width={760} footer={[
        <Button key="close" onClick={() => setReasoningTextOpen(false)}>关闭</Button>,
      ]}>
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', maxHeight: 460, overflow: 'auto', background: '#f8fafc', padding: 12 }}>
          {reasoning?.text}
        </Typography.Paragraph>
      </Modal>

      {/* 受理审核 */}
      <Modal title={`受理审核（移送审理把关） · ${detail?.title || ''}`} open={acceptOpen}
        onOk={() => act(detail, 'accept', { decision: acceptDecision, reason: acceptReason }, '已记录受理决定', true, () => setAcceptOpen(false))}
        onCancel={() => setAcceptOpen(false)} okText="确定" destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="受理决定">
            <Radio.Group value={acceptDecision} onChange={(e) => setAcceptDecision(e.target.value)}>
              <Radio value="受理">受理</Radio>
              <Radio value="暂缓受理">暂缓受理</Radio>
              <Radio value="不予受理">不予受理</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item label="理由 / 依据">
            <TextArea rows={3} value={acceptReason} onChange={(e) => setAcceptReason(e.target.value)}
              placeholder="如：材料齐全、手续完备，符合移送审理条件；或不予受理/暂缓原因。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 见面谈话（专业化：对标《监督执纪工作规则》第五十一~五十三条） */}
      <Modal title={`与被审查调查人见面谈话 · ${detail?.title || ''}`} open={interviewOpen}
        onOk={saveInterview} onCancel={() => setInterviewOpen(false)} okText="保存" destroyOnClose width={680}>
        <Form layout="vertical">
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item label="谈话人（2 名以上审理人员，顿号分隔）" required>
                <Input value={interviewInterviewers} onChange={(e) => setInterviewInterviewers(e.target.value)}
                  placeholder="如：张明、李华" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="记录人">
                <Input value={interviewRecorder} onChange={(e) => setInterviewRecorder(e.target.value)}
                  placeholder="谈话记录制作人" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item label="谈话地点">
                <Input value={interviewPlace} onChange={(e) => setInterviewPlace(e.target.value)}
                  placeholder="如：审理室谈话室" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="已告知被审查调查人有申辩的权利（第五十二条）">
                <Switch checked={interviewRights} onChange={(v) => setInterviewRights(v)}
                  checkedChildren="已告知" unCheckedChildren="未记录" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="被审查调查人态度">
            <Select value={interviewAttitude} onChange={(v) => setInterviewAttitude(v)}
              options={[
                { label: '认错悔错', value: '认错悔错' },
                { label: '无申辩', value: '无申辩' },
                { label: '部分申辩', value: '部分申辩' },
                { label: '提出申辩', value: '提出申辩' },
              ]} />
          </Form.Item>
          <Form.Item label="谈话速记 / 申辩要点">
            <TextArea rows={2} value={interviewNote} onChange={(e) => setInterviewNote(e.target.value)}
              placeholder="简要记录谈话过程与核对情况。" />
          </Form.Item>
          <Divider orientation="left" plain style={{ margin: '8px 0' }}>逐条申辩及采纳情况（第五十三条：如实记录并写明采纳与否及理由）</Divider>
          {(defenseItems || []).map((d: any, i: number) => (
            <div key={i} style={{ border: '1px solid #eef1f5', borderRadius: 6, padding: 10, marginBottom: 8 }}>
              <TextArea rows={2} value={d.content}
                onChange={(e) => { const arr = [...defenseItems]; arr[i] = { ...arr[i], content: e.target.value }; setDefenseItems(arr) }}
                placeholder="申辩内容（如实记录被审查调查人原意）" style={{ marginBottom: 6 }} />
              <Space size={[4, 8]} wrap>
                <Select size="small" value={d.stance} style={{ width: 110 }}
                  onChange={(v) => { const arr = [...defenseItems]; arr[i] = { ...arr[i], stance: v }; setDefenseItems(arr) }}
                  options={[{ label: '认可事实', value: '认可事实' }, { label: '提出申辩', value: '提出申辩' }, { label: '异议', value: '异议' }]} />
                <Select size="small" value={d.adopt} style={{ width: 110 }}
                  onChange={(v) => { const arr = [...defenseItems]; arr[i] = { ...arr[i], adopt: v }; setDefenseItems(arr) }}
                  options={[{ label: '待审议', value: '待审议' }, { label: '采纳', value: '采纳' }, { label: '部分采纳', value: '部分采纳' }, { label: '不采纳', value: '不采纳' }]} />
                <Input size="small" style={{ width: 260 }} value={d.reason}
                  onChange={(e) => { const arr = [...defenseItems]; arr[i] = { ...arr[i], reason: e.target.value }; setDefenseItems(arr) }}
                  placeholder="采纳/不采纳理由" />
                <Button size="small" danger icon={<DeleteOutlined />}
                  onClick={() => setDefenseItems(defenseItems.filter((_: any, j: number) => j !== i))} />
              </Space>
            </div>
          ))}
          <Button size="small" type="dashed" icon={<PlusOutlined />} block
            onClick={() => setDefenseItems([...defenseItems, { content: '', stance: '提出申辩', adopt: '待审议', reason: '' }])}>
            添加一条申辩
          </Button>
          <Form.Item label="谈话情况说明（第五十三条）" style={{ marginTop: 10 }}>
            <TextArea rows={2} value={interviewSummary} onChange={(e) => setInterviewSummary(e.target.value)}
              placeholder="如：谈话过程规范，被审查调查人核对事实材料后提出申辩，审理组将如实呈报。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 涉案财物处置 */}
      <Modal title={`涉案财物处置意见 · ${detail?.title || ''}`} open={propertyOpen}
        onOk={() => act(detail, 'property', { disposal: propertyDisposal }, '处置意见已保存并留痕，量纪说理已重算', true,
          () => { setPropertyOpen(false); if (detail) loadReasoning(detail.id) })}
        onCancel={() => setPropertyOpen(false)} okText="保存" destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="处置意见（规则第五十八条）">
            <div style={{ fontSize: 11.5, color: '#94a3b8', marginBottom: 6 }}>点击快捷动作插入意见文本（可继续编辑）；无意见时打开会自动带入系统建议方向作模板。</div>
            <Space size={[4, 6]} wrap style={{ marginBottom: 8 }}>
              {['收缴', '责令退赔', '没收', '追缴', '退还原所有人', '登记上交', '上缴国库', '冻结', '查封', '扣押', '随案移送'].map(a => (
                <Tag key={a} style={{ cursor: 'pointer' }}
                  onClick={() => setPropertyDisposal(prev => prev ? prev + '；' + a : a)}>+ {a}</Tag>
              ))}
            </Space>
            <TextArea rows={3} value={propertyDisposal} onChange={(e) => setPropertyDisposal(e.target.value)}
              placeholder="如：违纪所得予以收缴；涉嫌职务犯罪所得随案移送司法机关。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 退回补证 */}
      <Modal title={`退回补充 / 重新审查调查 · ${detail?.title || ''}`} open={returnOpen}
        onOk={() => act(detail, 'return', { reason: returnReason, to: returnTo }, '已退回补充调查', true, () => setReturnOpen(false))}
        onCancel={() => setReturnOpen(false)} okText="退回" okButtonProps={{ danger: true }} destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="退回对象">
            <Input value={returnTo} onChange={(e) => setReturnTo(e.target.value)} placeholder="如：审查调查组" />
          </Form.Item>
          <Form.Item label="退回理由">
            <TextArea rows={3} value={returnReason} onChange={(e) => setReturnReason(e.target.value)}
              placeholder="如：主要事实不清、证据不足，须补充完善证据。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* P3：制作处分决定书（裁量事项人工填，系统不预设） */}
      <Modal title={`制作处分决定书 · ${detail?.title || ''}`} open={decisionOpen}
        onOk={submitDecision} onCancel={() => setDecisionOpen(false)} okText="生成初稿"
        okButtonProps={{ disabled: !canConfirm }} destroyOnClose width={560}>
        <div style={{ marginBottom: 8, color: '#64748b' }}>
          须审理室 / 管理员操作。处分档次、文号、批准机关、日期属批准机关裁量与程序事项，一律人工填写；
          系统不预设结论，缺项将以〔待填〕标注。
        </div>
        <Form layout="vertical">
          <Form.Item label="处分决定内容（档次）">
            <Select value={decisionResult} onChange={setDecisionResult} allowClear
              placeholder="以处分批准机关审定意见为准（如：党内警告处分 / 撤销党内职务处分 / 开除党籍处分）"
              options={[
                { label: '党内警告处分', value: '党内警告处分' },
                { label: '党内严重警告处分', value: '党内严重警告处分' },
                { label: '撤销党内职务处分', value: '撤销党内职务处分' },
                { label: '留党察看处分', value: '留党察看处分' },
                { label: '开除党籍处分', value: '开除党籍处分' },
              ]} />
          </Form.Item>
          <Form.Item label="处分决定文号">
            <Input value={decisionNo} onChange={(e) => setDecisionNo(e.target.value)}
              placeholder="如：×纪决〔2026〕×号（缺省标〔文号待填〕）" />
          </Form.Item>
          <Form.Item label="处分批准机关（有处分批准权限的党组织/监察机关）">
            <Input value={decisionApprover} onChange={(e) => setDecisionApprover(e.target.value)}
              placeholder="如：中共×区纪委（缺省标〔处分批准机关待填〕）" />
          </Form.Item>
          <Form.Item label="处分决定作出日期">
            <Input type="date" value={decisionDate} onChange={(e) => setDecisionDate(e.target.value)} />
          </Form.Item>
        </Form>
      </Modal>

      {/* P3：查看处分决定书 */}
      <Modal title={decisionView?.title || '处分决定书'} open={decisionViewOpen}
        footer={<Button type="primary" onClick={() => setDecisionViewOpen(false)}>关闭</Button>}
        onCancel={() => setDecisionViewOpen(false)} width={720}>
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{decisionView?.content}</Typography.Paragraph>
        {decisionView && /〔[^〕]*待[^〕]*〕/.test(decisionView.content) && (
          <div style={{ color: '#b45309', fontSize: 12 }}>注意：本文书含〔待填〕裁量事项，须补全并报批准机关审定后方可定稿。</div>
        )}
      </Modal>

      {/* P3：查看执行回证（处分决定执行情况报告） */}
      <Modal title={reportView?.title || '处分决定执行情况报告'} open={reportViewOpen}
        footer={<Button type="primary" onClick={() => setReportViewOpen(false)}>关闭</Button>}
        onCancel={() => setReportViewOpen(false)} width={720}>
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{reportView?.content}</Typography.Paragraph>
      </Modal>

      {/* 宣布执行（结构化） */}
      <Modal title={`处分决定宣布执行 · ${detail?.title || ''}`} open={executeOpen}
        onOk={submitExecute}
        onCancel={() => setExecuteOpen(false)} okText="确认执行" okButtonProps={{ disabled: !canConfirm }} destroyOnClose>
        <div style={{ marginBottom: 8, color: '#64748b' }}>须审理室 / 管理员操作；处分决定作出后应在 1 个月内宣布并执行，逾期系统将提示。</div>
        <Form layout="vertical">
          <Form.Item label="宣布人">
            <Input value={executeAnnouncer} onChange={(e) => setExecuteAnnouncer(e.target.value)}
              placeholder="如：张××、李××" />
          </Form.Item>
          <Form.Item label="宣布范围">
            <Input value={executeScope} onChange={(e) => setExecuteScope(e.target.value)}
              placeholder="如：已向被处分人本人及其所在党支部全体党员宣布" />
          </Form.Item>
          <Form.Item label="送达方式">
            <Select value={executeDelivery} onChange={setExecuteDelivery} allowClear
              options={[{ label: '直接送达', value: '直接送达' }, { label: '邮寄送达', value: '邮寄送达' },
                { label: '留置送达', value: '留置送达' }, { label: '转交送达', value: '转交送达' }]} />
          </Form.Item>
          <Form.Item label="本人签收">
            <Checkbox checked={executeSigned} onChange={(e) => setExecuteSigned(e.target.checked)}>
              被处分人本人已签收决定书（拒绝签收的应注明并见证）
            </Checkbox>
          </Form.Item>
          <Form.Item label="宣布执行情况 / 执行报告">
            <TextArea rows={3} value={executeNote} onChange={(e) => setExecuteNote(e.target.value)}
              placeholder="如：已向所在党支部全体党员及本人宣布，执行情况已报告。" />
          </Form.Item>
        </Form>
      </Modal>

      {/* P3：执行回证单项回填 */}
      <Modal title="执行回证回填" open={itemEditOpen}
        onOk={submitExecutionItem} onCancel={() => setItemEditOpen(false)} okText="保存回填" destroyOnClose>
        <Form layout="vertical">
          <Form.Item label="落实状态">
            <Select value={itemEditStatus} onChange={setItemEditStatus}
              options={[{ label: '待落实', value: 'pending' }, { label: '已落实', value: 'done' }, { label: '不适用', value: 'na' }]} />
          </Form.Item>
          <Form.Item label="完成日期（标记「已落实」必填）">
            <Input type="date" value={itemEditAt} onChange={(e) => setItemEditAt(e.target.value)} />
          </Form.Item>
          <Form.Item label="备注（落实文号/经办人/情况说明）">
            <TextArea rows={2} value={itemEditNote} onChange={(e) => setItemEditNote(e.target.value)}
              placeholder="如：已按×干字〔2026〕×号办理职级调整；如留党察看恢复党员权利，填写恢复时间。" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`审理定稿确认 · ${detail?.title || ''}`} open={confirmOpen}
        onOk={submitConfirm} onCancel={() => setConfirmOpen(false)} okText="确认定稿" okButtonProps={{ disabled: !canConfirm }} destroyOnClose>
        <div style={{ marginBottom: 8, color: '#64748b' }}>
          定稿为责任链末端。须审理室 / 管理员确认；系统将记录确认人与时间，作为定稿证据链。
        </div>
        <Form layout="vertical">
          <Form.Item label="集体审议结论">
            <TextArea rows={4} value={confirmNote} onChange={(e) => setConfirmNote(e.target.value)}
              placeholder="如：经2026年第X次审理小组会议审议，事实清楚、证据确凿，同意按拟定量纪档次报批。" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="新建审理项目" open={createOpen} onOk={submitCreate} onCancel={() => setCreateOpen(false)} destroyOnClose width={680}>
        <Form form={form} layout="vertical">
          <Alert type="info" showIcon style={{ marginBottom: 12 }}
            message="CTAS 可独立建档：直接填写被审查人等信息即可建项跑通全链条；如已接入全业务案件，可在底部关联。" />
          <div style={{ marginBottom: 12, padding: '10px 12px', border: '1px dashed #dde3ec', borderRadius: 8, background: '#f8fafc' }}>
            <div style={{ fontSize: 12, color: '#475569', marginBottom: 8 }}>
              快速建档：导入审查调查报告 / 移送材料（Word/PDF/TXT）自动识别字段回填，请核对后提交。
            </div>
            <Upload accept=".docx,.pdf,.txt" showUploadList={false} beforeUpload={() => false} onChange={onIntakeFile}>
              <Button icon={<UploadOutlined />} loading={intakeBusy}>选择文件识别回填</Button>
            </Upload>
            {intakeHint && <div style={{ fontSize: 12, color: '#64748b', marginTop: 6 }}>{intakeHint}</div>}
          </div>
          <Row gutter={12}>
            <Col span={12}><Form.Item label="被审查调查人" name="subject" rules={[{ required: true, message: '必填' }]}>
              <Input placeholder="如：张某" /></Form.Item></Col>
            <Col span={12}><Form.Item label="单位 / 职务" name="unit">
              <Input placeholder="如：某区财政局（副局长）" /></Form.Item></Col>
            <Col span={12}><Form.Item label="职级" name="rank"><Input placeholder="如：乡科级副职" /></Form.Item></Col>
            <Col span={12}><Form.Item label="政治面貌" name="political_status">
              <Select allowClear placeholder="选择" options={[{ label: '中共党员', value: '中共党员' }, { label: '非党员', value: '非党员' }]} /></Form.Item></Col>
            <Col span={12}><Form.Item label="问题性质" name="problem_type" rules={[{ required: true, message: '必填' }]}>
              <Select allowClear placeholder="选择" options={[
                { label: '违反政治纪律', value: '违反政治纪律' },
                { label: '违反组织纪律', value: '违反组织纪律' },
                { label: '违反廉洁纪律', value: '违反廉洁纪律' },
                { label: '违反群众纪律', value: '违反群众纪律' },
                { label: '违反工作纪律', value: '违反工作纪律' },
                { label: '违反生活纪律', value: '违反生活纪律' },
                { label: '职务违法', value: '职务违法' },
                { label: '职务犯罪', value: '职务犯罪' },
              ]} /></Form.Item></Col>
            <Col span={12}><Form.Item label="线索来源" name="source">
              <Select allowClear placeholder="选择" options={[
                { label: '信访', value: '信访' }, { label: '巡视', value: '巡视' },
                { label: '审计', value: '审计' }, { label: '上级交办', value: '上级交办' },
                { label: '主动发现', value: '主动发现' },
              ]} /></Form.Item></Col>
            <Col span={12}><Form.Item label="立案状态" name="case_status">
              <Select allowClear placeholder="选择" options={[
                { label: '初核中', value: '初核中' }, { label: '已立案', value: '已立案' },
                { label: '已处置', value: '已处置' }, { label: '直接了结', value: '直接了结' },
              ]} /></Form.Item></Col>
            <Col span={12}><Form.Item label="立案时间" name="opened_at">
              <DatePicker style={{ width: '100%' }} placeholder="YYYY-MM-DD" /></Form.Item></Col>
            <Col span={12}><Form.Item label="涉案金额" name="involved_amount">
              <Input placeholder="如：18.6万元" /></Form.Item></Col>
            <Col span={12}><Form.Item label="移送审查调查组" name="transfer_from">
              <Input placeholder="如：审查调查组" /></Form.Item></Col>
          </Row>
          <Form.Item label="简要案情" name="brief">
            <TextArea rows={3} placeholder="主要违纪事实概述" /></Form.Item>
          <Form.Item label="事项标题" name="title"><Input placeholder="留空则自动生成（被审查人+涉嫌违纪案审理）" /></Form.Item>
          {cases.length > 0 && (
            <Form.Item label="关联全业务案件（可选）" name="case_id">
              <Select showSearch allowClear optionFilterProp="label" placeholder="已接入全业务案件时可关联，否则留空独立建档"
                options={cases.map((c: any) => ({ label: `${c.code} ${c.subject}`, value: c.id }))} />
            </Form.Item>
          )}
        </Form>
      </Modal>

      {/* 物证登记管理 */}
      <Modal title={`物证登记管理 · ${detail?.title || ''}`} open={physOpen} onCancel={() => { setPhysOpen(false); setPhysEditing(null); physForm.resetFields() }} footer={null} width={680} destroyOnClose>
        <Table size="small" dataSource={detail?.physical_evidence || []} rowKey="id" pagination={false}
          style={{ marginBottom: 12 }}
          columns={[
            { title: '名称', dataIndex: 'name', render: (v: any) => v || '(未命名)' },
            { title: '类别', dataIndex: 'category', width: 92 },
            { title: '数量', dataIndex: 'qty', width: 80, ellipsis: true },
            { title: '照片', dataIndex: 'has_photo', width: 64, render: (v: any) => v ? <Tag color="blue">有</Tag> : <Tag>无</Tag> },
            {
              title: '操作', key: 'op', width: 120,
              render: (_: any, r: any) => (
                <Space>
                  <Button size="small" onClick={() => { setPhysEditing(r.id); physForm.setFieldsValue(r) }}>编辑</Button>
                  <Button size="small" danger onClick={() => delPe(r.id)}>删除</Button>
                </Space>
              ),
            },
          ]} />
        <Divider orientation="left" plain>{physEditing != null ? '修改物证' : '登记物证'}</Divider>
        <Form form={physForm} layout="vertical" onFinish={savePe}>
          <Row gutter={12}>
            <Col span={8}><Form.Item name="name" label="物证名称" rules={[{ required: true, message: '必填' }]}><Input placeholder="如：现金人民币" /></Form.Item></Col>
            <Col span={8}><Form.Item name="category" label="类别" rules={[{ required: true, message: '必选' }]}>
              <Select placeholder="选择" options={[{ label: '查封扣押', value: '查封扣押' }, { label: '用赃款购买', value: '用赃款购买' }]} />
            </Form.Item></Col>
            <Col span={8}><Form.Item name="qty" label="数量/规格"><Input placeholder="如：50万元" /></Form.Item></Col>
            <Col span={8}><Form.Item name="feature" label="特征/品牌/型号"><Input /></Form.Item></Col>
            <Col span={8}><Form.Item name="storage" label="存放地点/保管"><Input /></Form.Item></Col>
            <Col span={8}><Form.Item name="has_photo" label="有无照片" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col span={24}><Form.Item name="photo_note" label="照片说明/档号"><Input placeholder="照片编号、档号或说明" /></Form.Item></Col>
            <Col span={24}><Form.Item name="remark" label="备注"><TextArea rows={2} /></Form.Item></Col>
          </Row>
          <Space>
            <Button type="primary" htmlType="submit">{physEditing != null ? '保存修改' : '添加物证'}</Button>
            {physEditing != null && <Button onClick={() => { setPhysEditing(null); physForm.resetFields() }}>取消编辑</Button>}
          </Space>
        </Form>
      </Modal>

      <RegRefDrawer open={regRefOpen} onClose={() => setRegRefOpen(false)} seed={regRefSeed} title="审理办案·相关依据" />
    </div>
  )
}
