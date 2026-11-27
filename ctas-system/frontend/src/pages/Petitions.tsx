import { useEffect, useRef, useState } from 'react'
import {
  Card, Table, Button, Drawer, Form, Input, Select, Tag, Space, message,
  Descriptions, Modal, Typography, Alert, Upload, Steps, Row, Col,
  Empty, Segmented, Progress, Divider, Tooltip, Spin, Statistic, Radio,
} from 'antd'
import {
  PlusOutlined, ThunderboltOutlined, AuditOutlined, CopyOutlined, RobotOutlined,
  FileImageOutlined, LoadingOutlined, FilePdfOutlined, TagsOutlined,
  EditOutlined, InboxOutlined, ScanOutlined, HighlightOutlined,
  BarChartOutlined, SendOutlined, CheckCircleOutlined, CloseCircleOutlined,
  DownloadOutlined, EyeOutlined, PaperClipOutlined, WarningOutlined,
  BranchesOutlined, BellOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { useAuth } from '../auth'
import PageHeader from '../components/PageHeader'
import OcrPanel from '../components/OcrPanel'
import AiAssistDrawer from '../components/nexent/AiAssistDrawer'
import { pickReceiver } from '../components/ReceiverPicker'

interface Sla {
  petitioner_type: string; accept_decision: string
  needs_notify: boolean; notify_overdue: boolean; notify_due: string
  needs_feedback: boolean; feedback_overdue: boolean; feedback_due: string
  handle_overdue: boolean; handle_due: string; supervised_at: string
}
interface Suggestion { id: number; dept: string; reason: string; rule_hits: any[]; status: string; confirmed_at: string }
interface Petition {
  id: number; code: string; content: string; petitioner: string; channel: string
  status: string; elements: any; labels: string[]; urgency: string; duplicate_of: number | null
  biz_type?: string; summary?: string; summary_mode?: string
  has_material?: boolean; file_name?: string; file_type?: string
  created_by: string; created_at: string
  petitioner_type?: string; contact?: string; accept_decision?: string; accept_reason?: string
  notified_at?: string; due_at?: string; feedback_at?: string; feedback_note?: string
  transfer_to?: string; source_case_id?: number | null; supervised_at?: string; supervise_note?: string
  sla?: Sla; suggestion: Suggestion | null
}

const URGENCY_COLOR: Record<string, string> = { 高: 'red', 中: 'orange', 低: 'green' }
const STATUS_COLOR: Record<string, string> = {
  待登记: 'default', 已抽取: 'blue', 已建议: 'gold', 已分办: 'green',
  不予受理: 'red', 重复留存: 'default', 已办结: 'cyan',
}
const BIZ_COLOR: Record<string, string> = { 检举控告: 'volcano', 申诉: 'blue', 批评建议: 'green' }
const ACCEPT_COLOR: Record<string, string> = { 受理: 'green', 不予受理: 'red', 重复留存: 'orange', 待甄别: 'default' }

/** 计算距今天数（用于临期提示，仅按日历日粗算） */
function daysTo(dateStr?: string): number | null {
  if (!dateStr) return null
  const d = new Date(dateStr.replace(/-/g, '/')).getTime()
  if (isNaN(d)) return null
  return Math.ceil((d - Date.now()) / 86400000)
}

function SlaBadges({ sla }: { sla?: Sla }) {
  if (!sla) return null
  const tags: JSX.Element[] = []
  if (sla.petitioner_type === '实名') {
    if (sla.notify_overdue) tags.push(<Tag key="n" color="red" icon={<WarningOutlined />}>告知逾期</Tag>)
    else if (sla.needs_notify) {
      const dd = daysTo(sla.notify_due)
      tags.push(<Tag key="n" color={dd !== null && dd <= 3 ? 'volcano' : 'gold'}>待告知{dla(sla.notify_due)}</Tag>)
    } else tags.push(<Tag key="n" color="green">已告知</Tag>)
    if (sla.feedback_overdue) tags.push(<Tag key="f" color="red" icon={<WarningOutlined />}>反馈逾期</Tag>)
    else if (sla.needs_feedback) {
      const dd = daysTo(sla.feedback_due)
      tags.push(<Tag key="f" color={dd !== null && dd <= 3 ? 'volcano' : 'gold'}>待反馈{dla(sla.feedback_due)}</Tag>)
    } else tags.push(<Tag key="f" color="green">已反馈</Tag>)
  }
  if (sla.handle_overdue) tags.push(<Tag key="h" color="red" icon={<WarningOutlined />}>办理超期</Tag>)
  else if (sla.handle_due) {
    const dd = daysTo(sla.handle_due)
    if (dd !== null && dd <= 7) tags.push(<Tag key="h" color="volcano">办理临期{dla(sla.handle_due)}</Tag>)
  }
  if (sla.supervised_at) tags.push(<Tag key="s" color="blue">已督办</Tag>)
  return <Space size={[2, 2]} wrap>{tags}</Space>
}
function dla(due?: string): string {
  const dd = daysTo(due)
  if (dd === null) return ''
  if (dd < 0) return `(逾期${-dd}天)`
  if (dd === 0) return '(今日)'
  return `(剩${dd}天)`
}

export default function Petitions() {
  const { user } = useAuth()
  const canDispose = user?.role === 'admin' || user?.role === 'xinfang'
  const [rows, setRows] = useState<Petition[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(10)
  const [q, setQ] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [fStatus, setFStatus] = useState('')
  const [fUrgency, setFUrgency] = useState('')
  const [fBiz, setFBiz] = useState('')
  const [fAccept, setFAccept] = useState('')
  const [overdueOnly, setOverdueOnly] = useState(false)

  const [newOpen, setNewOpen] = useState(false)
  const [detail, setDetail] = useState<Petition | null>(null)
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [ocrLoading, setOcrLoading] = useState(false)
  const [step, setStep] = useState(0)
  const [view, setView] = useState<'list' | 'work' | 'stats'>('list')
  const [aiOpen, setAiOpen] = useState(false)
  // 工作台待办 / 深链接：/petitions?open=<id> 直达单件办理
  useEffect(() => {
    const op = Number(new URLSearchParams(window.location.search).get('open') || 0)
    if (!op) return
    window.history.replaceState(null, '', '/petitions')
    api.get(`/petitions/${op}`).then(({ data }) => { setDetail(data); setView('work') })
      .catch((e) => message.error(errMsg(e)))
  }, [])
  const [ocrOpen, setOcrOpen] = useState(false)
  const [stats, setStats] = useState<any>(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const materialRef = useRef<File | null>(null)
  const [hasMaterial, setHasMaterial] = useState(false)

  const loadStats = async () => {
    setStatsLoading(true)
    try {
      const { data } = await api.get('/petitions/stats')
      setStats(data)
    } catch (e) { message.error(errMsg(e)) } finally { setStatsLoading(false) }
  }

  const load = async () => {
    const params: any = { page, per_page: perPage }
    if (q) params.q = q
    if (fStatus) params.status = fStatus
    if (fUrgency) params.urgency = fUrgency
    if (fBiz) params.biz_type = fBiz
    if (fAccept) params.accept = fAccept
    if (overdueOnly) params.overdue = 1
    const { data } = await api.get('/petitions', { params })
    const items = data.items || []
    setRows(items)
    setTotal(data.total || 0)
    setDetail((prev) => prev ? (items.find((p: Petition) => p.id === prev.id) || prev) : prev)
    return items
  }
  useEffect(() => { load() }, [page, perPage, q, fStatus, fUrgency, fBiz, fAccept, overdueOnly])
  useEffect(() => { if (view === 'stats' && !stats) loadStats() }, [view])

  const resetFilters = () => {
    setQ(''); setSearchInput(''); setFStatus(''); setFUrgency(''); setFBiz(''); setFAccept(''); setOverdueOnly(false); setPage(1)
  }

  const exportLedger = async () => {
    try {
      const { data } = await api.get('/petitions/export', { responseType: 'blob', timeout: 120000 })
      const url = window.URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = url
      a.download = `信访台账_${new Date().toISOString().slice(0, 10)}.xlsx`
      a.click()
      window.URL.revokeObjectURL(url)
      message.success('信访台账已导出')
    } catch (e) { message.error(errMsg(e)) }
  }

  const parseMaterial = async (file: File) => {
    setOcrLoading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const { data } = await api.post('/petitions/parse', fd, { timeout: 600000 })
      const prev = form.getFieldValue('content') || ''
      form.setFieldsValue({ content: prev ? `${prev}\n${data.text}` : data.text })
      materialRef.current = file
      setHasMaterial(true)
      setStep(1)
      message.success(`材料识别完成（${data.chars} 字），原件将在登记时留存，请人工核对后修订`)
    } catch (e) { message.error(errMsg(e)) } finally { setOcrLoading(false) }
    return false
  }

  const create = async (v: any) => {
    try {
      const { data } = await api.post('/petitions', v)
      if (materialRef.current) {
        try {
          const fd = new FormData()
          fd.append('file', materialRef.current)
          const { data: up } = await api.post(`/petitions/${data.id}/material`, fd, { timeout: 120000 })
          message.success('材料原件已留存')
          data.has_material = up.has_material
        } catch (e) {
          message.warning('登记成功，但材料原件留存失败：' + errMsg(e))
        }
      }
      message.success('信访件已登记')
      setNewOpen(false); form.resetFields(); setStep(0)
      materialRef.current = null; setHasMaterial(false)
      await load()
      setDetail(data); setView('work')
    } catch (e) { message.error(errMsg(e)) }
  }

  const runExtract = async (p: Petition) => {
    setLoading(true)
    try {
      const { data } = await api.post(`/petitions/${p.id}/extract`)
      message.success(`一键整理完成（要素 + 分类 + 摘要，${data.mode === 'llm' ? '智能体' : '本机规则'}）`)
      await load()
    } catch (e) { message.error(errMsg(e)) } finally { setLoading(false) }
  }

  const checkDup = async (p: Petition) => {
    try {
      const { data } = await api.post(`/petitions/${p.id}/check-duplicate`)
      if (data.similar.length) {
        Modal.info({ title: '疑似重复举报', width: 640, content: (
          <div>
            <Typography.Paragraph>{data.hint}</Typography.Paragraph>
            {data.similar.map((s: any) => (
              <div key={s.id} style={{ marginBottom: 4 }}>· {s.code}（相似度 {s.score}）：{s.excerpt}…</div>
            ))}
          </div>
        ) })
      } else message.info('未发现相似举报')
      await load()
    } catch (e) { message.error(errMsg(e)) }
  }

  const runSuggest = async (p: Petition) => {
    setLoading(true)
    try {
      const { data } = await api.post(`/petitions/${p.id}/suggest`)
      message.success(`分办建议：${data.dept}`)
      await load()
    } catch (e) { message.error(errMsg(e)) } finally { setLoading(false) }
  }

  const runConfirm = (p: Petition) => {
    Modal.confirm({
      title: '确认分办',
      content: `确认按建议分办至「${p.suggestion?.dept}」？分办属业务流转，一经确认将留痕。`,
      okText: '确认分办', cancelText: '取消',
      onOk: async () => {
        try {
          await api.post(`/petitions/${p.id}/confirm`)
          message.success('已确认分办（已留痕）')
          await load()
        } catch (e) { message.error(errMsg(e)) }
      },
    })
  }

  const runReject = (p: Petition) => {
    let reason = ''
    Modal.confirm({
      title: '驳回分办建议',
      okText: '驳回', cancelText: '取消',
      content: (
        <div>
          <div style={{ marginBottom: 8 }}>驳回「{p.suggestion?.dept}」这一建议，可补充理由（选填）。</div>
          <Input.TextArea rows={3} onChange={(e) => { reason = e.target.value }} placeholder="如：应由审理室牵头办理" />
        </div>
      ),
      onOk: async () => {
        try {
          await api.post(`/petitions/${p.id}/reject`, null, { params: { reason } })
          message.success('已驳回分办建议（已留痕）')
          await load()
        } catch (e) { message.error(errMsg(e)) }
      },
    })
  }

  // —— 受理法治化：甄别（受理 / 不予受理 / 重复留存）——
  const runScreen = (p: Petition) => {
    let decision = p.accept_decision || '受理'
    let reason = p.accept_reason || ''
    Modal.confirm({
      title: '研判甄别（受理 / 不予受理 / 重复留存）',
      icon: <AuditOutlined />,
      width: 560,
      content: (
        <div>
          <Radio.Group value={decision} onChange={(e) => { decision = e.target.value }}>
            <Radio.Button value="受理">受理</Radio.Button>
            <Radio.Button value="不予受理">不予受理</Radio.Button>
            <Radio.Button value="重复留存">重复留存</Radio.Button>
          </Radio.Group>
          <div style={{ marginTop: 12, color: '#64748b', fontSize: 12 }}>
            受理：进入办理流程（默认 60 日办理期限，实名须告知+反馈）；不予受理：涉法涉诉/无实质内容/不属职权；重复留存：重复举报留存备查。
          </div>
          <Input.TextArea rows={3} defaultValue={reason} style={{ marginTop: 8 }}
            placeholder="不予受理原因 / 重复留存说明（选填）" onChange={(e) => { reason = e.target.value }} />
        </div>
      ),
      okText: '确认甄别', cancelText: '取消',
      onOk: async () => {
        try {
          await api.post(`/petitions/${p.id}/screen`, { decision, reason })
          message.success(`甄别结论：${decision}`)
          await load()
        } catch (e) { message.error(errMsg(e)) }
      },
    })
  }

  const runNotify = (p: Petition) => {
    Modal.confirm({
      title: '受理告知（实名举报）',
      icon: <BellOutlined />,
      content: '确认已按《纪检监察机关处理检举控告工作规则》向实名举报人书面告知受理情况？',
      okText: '确认告知', cancelText: '取消',
      onOk: async () => {
        try {
          const { data } = await api.post(`/petitions/${p.id}/notify`)
          if (data.was_overdue) message.warning('受理告知已补登，但已超过 15 个工作日期限，请注意督办')
          else message.success('受理告知已记录（实名 15 工作日）')
          await load()
        } catch (e) { message.error(errMsg(e)) }
      },
    })
  }

  const runFeedback = (p: Petition) => {
    let note = ''
    Modal.confirm({
      title: '办结反馈（实名举报）',
      icon: <CheckCircleOutlined />,
      width: 560,
      content: (
        <div>
          <div style={{ marginBottom: 8 }}>向实名举报人反馈核查处理结果（办结之日起 15 个工作日内）。</div>
          <Input.TextArea rows={4} onChange={(e) => { note = e.target.value }} placeholder="反馈内容 / 处理结果（选填）" />
        </div>
      ),
      okText: '确认反馈办结', cancelText: '取消',
      onOk: async () => {
        try {
          const { data } = await api.post(`/petitions/${p.id}/feedback`, { note })
          if (data.was_overdue) message.warning('反馈已补登，但已超过 15 个工作日期限，请注意督办')
          else message.success('办结反馈已记录，状态置「已办结」')
          await load()
        } catch (e) { message.error(errMsg(e)) }
      },
    })
  }

  const runSupervise = (p: Petition) => {
    let note = ''
    Modal.confirm({
      title: '督办',
      icon: <WarningOutlined />,
      width: 560,
      content: (
        <div>
          <div style={{ marginBottom: 8 }}>对推诿拖延、超期未办件发函督办并留痕。</div>
          <Input.TextArea rows={3} onChange={(e) => { note = e.target.value }} placeholder="督办意见（选填）" />
        </div>
      ),
      okText: '确认督办', cancelText: '取消',
      onOk: async () => {
        try {
          await api.post(`/petitions/${p.id}/supervise`, { note })
          message.success('已督办（已留痕）')
          await load()
        } catch (e) { message.error(errMsg(e)) }
      },
    })
  }

  const runTransfer = (p: Petition) => {
    Modal.confirm({
      title: '转案管线索',
      icon: <BranchesOutlined />,
      content: '将本检举控告一键转入案管（问题线索），生成线索台账并双向关联，避免重复录入。',
      okText: '确认转入', cancelText: '取消',
      onOk: async () => {
        const dept = await pickReceiver('clue', '转案管线索')
        if (dept === undefined) return
        try {
          const { data } = await api.post(`/petitions/${p.id}/transfer-case`, { dept_id: dept })
          message.success(`已转入案管线索（${data.case_code}${data.dept_name ? `，承办部门：${data.dept_name}` : ''}）`)
          await load()
        } catch (e) { message.error(errMsg(e)) }
      },
    })
  }

  const uploadMaterial = async (p: Petition, file: File) => {
    try {
      const fd = new FormData()
      fd.append('file', file)
      await api.post(`/petitions/${p.id}/material`, fd, { timeout: 120000 })
      message.success('材料原件已留存')
      await load()
    } catch (e) { message.error(errMsg(e)) }
    return false
  }

  const columns = [
    { title: '编号', dataIndex: 'code', width: 150 },
    { title: '类别', width: 110, render: (_: any, r: Petition) =>
      r.biz_type ? <Tag color={BIZ_COLOR[r.biz_type]}>{r.biz_type}</Tag> : '-' },
    { title: '问题分类', dataIndex: 'labels', width: 170, render: (l: string[]) =>
      (l || []).slice(0, 2).map((x) => <Tag key={x} color="geekblue">{x}</Tag>) },
    { title: '摘要', dataIndex: 'summary', ellipsis: true,
      render: (s: string) => s ? <Typography.Text type="secondary" style={{ fontSize: 12 }}>{s}</Typography.Text> : '-' },
    { title: '实名', width: 64, render: (_: any, r: Petition) =>
      r.petitioner_type === '实名' ? <Tag color="purple">实名</Tag> : <Tag>匿名</Tag> },
    { title: '受理', width: 92, render: (_: any, r: Petition) =>
      <Tag color={ACCEPT_COLOR[r.accept_decision || '待甄别']}>{r.accept_decision || '待甄别'}</Tag> },
    { title: '紧急度', dataIndex: 'urgency', width: 72,
      render: (u: string) => <Tag color={URGENCY_COLOR[u]}>{u}</Tag> },
    { title: '状态', dataIndex: 'status', width: 86,
      render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
    { title: '时限/告知', width: 150, render: (_: any, r: Petition) => <SlaBadges sla={r.sla} /> },
    { title: '原件', width: 60, render: (_: any, r: Petition) =>
      r.has_material ? <Tag color="cyan" icon={<PaperClipOutlined />}>有</Tag> : '-' },
    { title: '操作', width: 200, fixed: 'right' as const, render: (_: any, r: Petition) => (
      <Space>
        <Button size="small" type="primary" ghost icon={<ThunderboltOutlined />} loading={loading}
          onClick={() => { setDetail(r); setView('work'); runExtract(r) }}>一键整理</Button>
        <Button size="small" icon={<AuditOutlined />} onClick={() => { setDetail(r); setView('work') }}>办理</Button>
      </Space>
    ) },
  ]

  const cur = detail ? rows.find((r) => r.id === detail.id) || detail : null

  return (
    <>
      <PageHeader
        icon={<InboxOutlined />}
        title="信访件智能辅助"
        subtitle="登记识别 · 要素抽取 · 分类摘要 · 研判甄别 · 受理告知 · 分办建议 · 办结反馈 · 督办 · 转线索"
        extra={
          <Space>
            <Segmented value={view} onChange={(v) => setView(v as any)}
              options={[{ label: '信访件清单', value: 'list' }, { label: '单件办理辅助', value: 'work' }, { label: '效能统计', value: 'stats' }]} />
            <Button icon={<DownloadOutlined />} onClick={exportLedger}>导出台账</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setNewOpen(true)}>登记信访件</Button>
          </Space>
        } />
      <Card>
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="本模块覆盖信访举报全流程：登记→研判甄别（受理/不予受理/重复留存）→实名告知（15日）→分办建议（人工确认）→办理处置→办结反馈（实名15日）→超期督办→转案管线索。AI 仅辅助，流转与甄别须人工确认并留痕。" />

        {view === 'list' ? (
          <>
            <Space wrap style={{ marginBottom: 12 }}>
              <Input.Search allowClear placeholder="编号 / 内容 / 摘要 / 举报人"
                value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
                onSearch={(v) => { setQ(v); setPage(1) }} style={{ width: 260 }} />
              <Select allowClear placeholder="状态" value={fStatus || undefined} style={{ width: 120 }}
                onChange={(v) => { setFStatus(v || ''); setPage(1) }}
                options={['待登记', '已抽取', '已建议', '已分办', '不予受理', '重复留存', '已办结'].map((x) => ({ value: x, label: x }))} />
              <Select allowClear placeholder="受理" value={fAccept || undefined} style={{ width: 120 }}
                onChange={(v) => { setFAccept(v || ''); setPage(1) }}
                options={['受理', '不予受理', '重复留存', '待甄别'].map((x) => ({ value: x, label: x }))} />
              <Select allowClear placeholder="紧急度" value={fUrgency || undefined} style={{ width: 110 }}
                onChange={(v) => { setFUrgency(v || ''); setPage(1) }}
                options={['高', '中', '低'].map((x) => ({ value: x, label: x }))} />
              <Select allowClear placeholder="业务类别" value={fBiz || undefined} style={{ width: 130 }}
                onChange={(v) => { setFBiz(v || ''); setPage(1) }}
                options={['检举控告', '申诉', '批评建议'].map((x) => ({ value: x, label: x }))} />
              <Button type={overdueOnly ? 'primary' : 'default'} danger={overdueOnly} icon={<WarningOutlined />}
                onClick={() => { setOverdueOnly(!overdueOnly); setPage(1) }}>仅看超期</Button>
              <Button onClick={resetFilters}>重置</Button>
              <Typography.Text type="secondary">共 {total} 件</Typography.Text>
            </Space>
            <Table rowKey="id" size="middle" columns={columns} dataSource={rows}
              scroll={{ x: 1180 }}
              pagination={{ current: page, pageSize: perPage, total, showSizeChanger: true,
                showTotal: (t) => `共 ${t} 件`,
                onChange: (p, ps) => { setPage(p); setPerPage(ps) } }}
              locale={{ emptyText: <Empty description="暂无信访件，点击「登记信访件」或上传材料识别" /> }} />
          </>
        ) : view === 'stats' ? (
          <PetitionStats stats={stats} loading={statsLoading} onRefresh={loadStats} />
        ) : !cur ? (
          <Empty description="请从「信访件清单」中选择一件，或登记新信访件" />
        ) : (
          <>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
            <Button type="primary" ghost icon={<RobotOutlined />} onClick={() => setAiOpen(true)}>AI 研判</Button>
          </div>
          <WorkBench
            p={cur} loading={loading} canDispose={!!canDispose}
            onExtract={() => runExtract(cur)}
            onDup={() => checkDup(cur)}
            onSuggest={() => runSuggest(cur)}
            onConfirm={() => runConfirm(cur)}
            onReject={() => runReject(cur)}
            onScreen={() => runScreen(cur)}
            onNotify={() => runNotify(cur)}
            onFeedback={() => runFeedback(cur)}
            onSupervise={() => runSupervise(cur)}
            onTransfer={() => runTransfer(cur)}
            onUpload={(f) => uploadMaterial(cur, f)}
          />
          <AiAssistDrawer open={aiOpen} onClose={() => setAiOpen(false)} user={user}
            subject={{ kind: 'petition', id: cur.id, title: `信访件 ${cur.code}` }} />
          </>
        )}
      </Card>

      {/* 登记：材料识别 → 内容核对 → 登记 */}
      <Drawer title="登记信访件（支持 PDF / 图片材料识别）" open={newOpen} width={620}
        onClose={() => setNewOpen(false)}>
        <Steps size="small" current={step} style={{ marginBottom: 16 }}
          items={[{ title: '材料识别' }, { title: '内容核对' }, { title: '登记' }]} />
        <Card size="small" style={{ marginBottom: 12 }}>
          <Upload.Dragger accept=".pdf,image/*,.txt,.md" showUploadList={false} beforeUpload={parseMaterial}>
            <p className="ant-upload-drag-icon"><ScanOutlined style={{ fontSize: 30, color: '#1d4ed8' }} /></p>
            <p className="ant-upload-text">点击或拖入信访材料（PDF / 图片 / 文本）</p>
            <p className="ant-upload-hint">PDF 走本机版面解析，图片走本机 OCR，全程离线不上传；原件随登记留存</p>
          </Upload.Dragger>
          <Space style={{ marginTop: 8 }}>
            <Tooltip title="图片/扫描件">
              <Upload accept="image/*" showUploadList={false} beforeUpload={parseMaterial}>
                <Button size="small" icon={ocrLoading ? <LoadingOutlined /> : <FileImageOutlined />}>图片识别</Button>
              </Upload>
            </Tooltip>
            <Tooltip title="PDF 文档（含扫描版）">
              <Upload accept=".pdf" showUploadList={false} beforeUpload={parseMaterial}>
                <Button size="small" icon={ocrLoading ? <LoadingOutlined /> : <FilePdfOutlined />}>PDF 解析</Button>
              </Upload>
            </Tooltip>
            <Tooltip title="原文对照 · 逐行核验（图片/PDF 均可）">
              <Button size="small" type="primary" ghost icon={<HighlightOutlined />}
                onClick={() => setOcrOpen(true)}>OCR 图片识别</Button>
            </Tooltip>
            {ocrLoading && <Tag color="processing" icon={<LoadingOutlined />}>识别中…</Tag>}
            {hasMaterial && <Tag color="cyan" icon={<PaperClipOutlined />}>原件待留存</Tag>}
          </Space>
        </Card>
        <Form form={form} layout="vertical" onFinish={create}
          initialValues={{ petitioner: '匿名', channel: '网络', petitioner_type: '匿名' }}>
          <Form.Item label="信访件内容（识别结果可人工修订）" name="content"
            rules={[{ required: true, message: '请输入或识别内容' }]}>
            <Input.TextArea rows={10} onChange={() => setStep(1)}
              placeholder="上传材料自动填充，或手工录入：反映某单位某某同志……" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="petitioner_type" label="举报人类型">
                <Select options={[{ value: '实名', label: '实名' }, { value: '匿名', label: '匿名' }]} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="petitioner" label="举报人">
                <Input placeholder="匿名 / 姓名" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="channel" label="渠道">
                <Select options={['网络', '来信', '来访', '电话'].map((x) => ({ value: x, label: x }))} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item noStyle dependencies={['petitioner_type']}>
            {({ getFieldValue }) => getFieldValue('petitioner_type') === '实名' ? (
              <Form.Item name="contact" label="联系方式（实名告知 / 办结反馈用）">
                <Input placeholder="电话 / 地址，仅承办人可见" />
              </Form.Item>
            ) : null}
          </Form.Item>
          <Button type="primary" htmlType="submit" block icon={<AuditOutlined />}
            onClick={() => setStep(2)}>登记并进入办理辅助</Button>
        </Form>
      </Drawer>

      {/* 信访业务 OCR 识别入口：原文对照 · 逐行核验，结果可一键填入登记内容 */}
      <Modal title={<Space><HighlightOutlined style={{ color: '#1d4ed8' }} />OCR 图片识别（信访材料）</Space>}
        open={ocrOpen} onCancel={() => setOcrOpen(false)} footer={null} width={960}
        destroyOnClose>
        <OcrPanel accept="all" insertLabel="填入登记内容"
          onInsert={(t) => {
            const prev = form.getFieldValue('content') || ''
            form.setFieldsValue({ content: prev ? `${prev}\n${t}` : t })
            setStep(1)
            setOcrOpen(false)
            message.success('OCR 结果已填入登记内容，请人工核对后修订')
          }} />
      </Modal>
    </>
  )
}

/** 材料原件预览 */
function MaterialPreview({ pid, fileType, fileName }: { pid: number; fileType: string; fileName: string }) {
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  useEffect(() => {
    let objectUrl = ''
    ;(async () => {
      try {
        const { data } = await api.get(`/petitions/${pid}/material`, { responseType: 'blob', timeout: 120000 })
        objectUrl = window.URL.createObjectURL(data)
        setUrl(objectUrl)
      } catch (e) { setErr(errMsg(e)) } finally { setLoading(false) }
    })()
    return () => { if (objectUrl) window.URL.revokeObjectURL(objectUrl) }
  }, [pid])

  if (loading) return <div style={{ textAlign: 'center', padding: 30 }}><Spin /></div>
  if (err) return <Alert type="error" showIcon message={err} />
  const isImg = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'].includes(fileType)
  if (isImg) return <img src={url} alt={fileName} style={{ maxWidth: '100%', borderRadius: 6 }} />
  if (fileType === 'pdf') return <iframe src={url} title={fileName} style={{ width: '100%', height: 520, border: 0 }} />
  return <Alert type="info" showIcon message="该格式不支持在线预览，请下载后查看" />
}

/** 单件办理辅助：识别 / 甄别 / 告知 / 分办 / 反馈 / 督办 / 转线索（AI 仅辅助，流转须人工确认） */
function WorkBench({ p, loading, canDispose, onExtract, onDup,
                     onSuggest, onConfirm, onReject, onScreen, onNotify, onFeedback, onSupervise, onTransfer,
                     onUpload }: {
  p: Petition; loading: boolean; canDispose: boolean
  onExtract: () => void; onDup: () => void
  onSuggest: () => void; onConfirm: () => void; onReject: () => void
  onScreen: () => void; onNotify: () => void; onFeedback: () => void
  onSupervise: () => void; onTransfer: () => void; onUpload: (f: File) => void
}) {
  const [editing, setEditing] = useState('')
  const [matOpen, setMatOpen] = useState(false)
  const elements = p.elements && typeof p.elements === 'object' ? p.elements : null
  const person = elements?.被反映人 || {}
  const sugg = p.suggestion
  const isReal = p.petitioner_type === '实名'

  const saveSummary = async () => {
    try {
      await api.put(`/petitions/${p.id}`, { summary: editing })
      message.success('摘要已保存')
      setEditing('')
      onExtract()
    } catch (e) { message.error(errMsg(e)) }
  }

  const downloadMaterial = async () => {
    try {
      const { data } = await api.get(`/petitions/${p.id}/material`, { responseType: 'blob', timeout: 120000 })
      const url = window.URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = url; a.download = p.file_name || '材料原件'
      a.click(); window.URL.revokeObjectURL(url)
    } catch (e) { message.error(errMsg(e)) }
  }

  return (
    <Row gutter={16}>
      <Col xs={24} lg={14}>
        <Card size="small" title="信访件原文" style={{ marginBottom: 12 }}
          extra={<Space>
            <Tag>{p.code}</Tag>
            <Tag color={ACCEPT_COLOR[p.accept_decision || '待甄别']}>{p.accept_decision || '待甄别'}</Tag>
          </Space>}>
          <Typography.Paragraph className="answer-block"
            style={{ padding: 12, background: '#f6f8fa', borderRadius: 6, maxHeight: 220, overflowY: 'auto' }}>
            {p.content}
          </Typography.Paragraph>
          <Space style={{ marginTop: 8 }} wrap>
            <Button type="primary" icon={<ThunderboltOutlined />} loading={loading} onClick={onExtract}>
              一键整理（要素+分类+摘要）
            </Button>
            <Button icon={<CopyOutlined />} onClick={onDup}>查重</Button>
            {canDispose && (
              <Button icon={<AuditOutlined />} onClick={onScreen}
                disabled={p.accept_decision === '不予受理' || p.accept_decision === '重复留存'}>
                研判甄别
              </Button>
            )}
          </Space>
        </Card>

        {/* 受理法治化 / 办理法治化 流程动作条 */}
        {canDispose && (
          <Card size="small" title="受理 · 办理 · 督办" style={{ marginBottom: 12 }}
            extra={<SlaBadges sla={p.sla} />}>
            <Space wrap>
              {isReal && (
                <Button size="small" icon={<BellOutlined />} onClick={onNotify}
                  disabled={!!p.notified_at || p.accept_decision === '不予受理'}>受理告知</Button>
              )}
              {isReal && (
                <Button size="small" icon={<CheckCircleOutlined />} onClick={onFeedback}
                  disabled={!!p.feedback_at || p.status === '已办结' || p.accept_decision === '不予受理'}>办结反馈</Button>
              )}
              <Button size="small" icon={<WarningOutlined />} onClick={onSupervise}>督办</Button>
              <Button size="small" icon={<BranchesOutlined />} type="primary" ghost onClick={onTransfer}
                disabled={!!p.source_case_id}>转案管线索</Button>
              {p.source_case_id && (
                <Tag color="green">已转线索 #{p.source_case_id}{p.transfer_to ? `（${p.transfer_to}）` : ''}</Tag>
              )}
            </Space>
          </Card>
        )}

        {/* 分办建议：AI 只到建议为止，确认/驳回必须人工 */}
        <Card size="small" title={<Space><SendOutlined />分办建议（AI 建议 · 人工确认）</Space>}
          style={{ marginBottom: 12 }}
          extra={sugg ? <Tag color={sugg.status === '已确认' ? 'green' : sugg.status === '已驳回' ? 'red' : 'gold'}>
            {sugg.status}</Tag> : null}>
          {!sugg ? (
            <div>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
                尚未生成分办建议。需先完成「一键整理」（要素抽取）后生成。
              </Typography.Paragraph>
              <Button size="small" type="primary" ghost icon={<SendOutlined />} loading={loading}
                onClick={onSuggest} disabled={!elements}>生成分办建议</Button>
            </div>
          ) : (
            <div>
              <div style={{ marginBottom: 6 }}>
                <Typography.Text type="secondary">建议承办部门：</Typography.Text>{' '}
                <Tag color="blue" style={{ fontSize: 14 }}>{sugg.dept}</Tag>
              </div>
              <div style={{ marginBottom: 6 }}>
                <Typography.Text type="secondary">建议理由：</Typography.Text>
                <Typography.Paragraph style={{ margin: '4px 0 0', lineHeight: 1.8 }}>{sugg.reason || '-'}</Typography.Paragraph>
              </div>
              {(sugg.rule_hits || []).length > 0 && (
                <div style={{ marginBottom: 6 }}>
                  <Typography.Text type="secondary">命中规则（可解释性）：</Typography.Text>
                  <div style={{ marginTop: 4 }}>
                    {(sugg.rule_hits || []).map((h: any, i: number) => (
                      <Tag key={i} color="geekblue" style={{ marginBottom: 4 }}>{h.label || h.rule || JSON.stringify(h)}</Tag>
                    ))}
                  </div>
                </div>
              )}
              {sugg.confirmed_at && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>处理时间：{sugg.confirmed_at}</Typography.Text>
              )}
              <Divider style={{ margin: '10px 0' }} />
              {sugg.status === '待确认' ? (
                canDispose ? (
                  <Space>
                    <Button size="small" type="primary" icon={<CheckCircleOutlined />} onClick={onConfirm}>确认分办</Button>
                    <Button size="small" danger icon={<CloseCircleOutlined />} onClick={onReject}>驳回建议</Button>
                    <Button size="small" onClick={onSuggest} loading={loading}>重新生成</Button>
                  </Space>
                ) : (
                  <Alert type="warning" showIcon message="当前账号无分办权限（需信访岗或管理员）进行确认/驳回" />
                )
              ) : (
                <Space>
                  <Typography.Text type="secondary">该建议已{sugg.status}。</Typography.Text>
                  <Button size="small" onClick={onSuggest} loading={loading}>重新生成建议</Button>
                </Space>
              )}
            </div>
          )}
        </Card>

        <Card size="small" title={<Space><EditOutlined />信访件摘要（可人工修订）</Space>}
          extra={<Space>
            {p.summary_mode && <Tag color={p.summary_mode === 'llm' ? 'green' : p.summary_mode === 'manual' ? 'blue' : 'orange'}>
              {p.summary_mode === 'llm' ? '大模型润色' : p.summary_mode === 'manual' ? '人工修订' : '本机规则生成'}</Tag>}
            <Button size="small" onClick={onExtract} loading={loading}>生成摘要</Button>
            {p.summary && <Button size="small" icon={<EditOutlined />}
              onClick={() => setEditing(p.summary || '')}>修订</Button>}
          </Space>}>
          {editing === '' ? (
            p.summary
              ? <Typography.Paragraph style={{ margin: 0, lineHeight: 1.9 }}>{p.summary}</Typography.Paragraph>
              : <Empty description="点击「生成摘要」自动编写摘要" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ) : (
            <div>
              <Input.TextArea rows={6} value={editing} onChange={(e) => setEditing(e.target.value)} />
              <Space style={{ marginTop: 8 }}>
                <Button size="small" type="primary" onClick={saveSummary}>保存摘要</Button>
                <Button size="small" onClick={() => setEditing('')}>取消</Button>
              </Space>
            </div>
          )}
        </Card>
      </Col>

      <Col xs={24} lg={10}>
        <Card size="small" title="分类结果" style={{ marginBottom: 12 }}>
          <div style={{ marginBottom: 8 }}>
            <Typography.Text type="secondary">业务类别：</Typography.Text>{' '}
            {p.biz_type ? <Tag color={BIZ_COLOR[p.biz_type]} style={{ fontSize: 13 }}>{p.biz_type}</Tag> : '-'}
          </div>
          <div>
            <Typography.Text type="secondary">问题类别：</Typography.Text>
            <div style={{ marginTop: 6 }}>
              {(p.labels || []).length
                ? (p.labels || []).map((x) => <Tag key={x} color="geekblue" style={{ marginBottom: 4 }}>{x}</Tag>)
                : '-'}
            </div>
          </div>
          <Divider style={{ margin: '10px 0' }} />
          <Descriptions column={1} size="small">
            <Descriptions.Item label="紧急程度"><Tag color={URGENCY_COLOR[p.urgency]}>{p.urgency}</Tag></Descriptions.Item>
            <Descriptions.Item label="状态"><Tag color={STATUS_COLOR[p.status]}>{p.status}</Tag></Descriptions.Item>
            <Descriptions.Item label="举报人/渠道">{p.petitioner} · {p.channel}</Descriptions.Item>
            <Descriptions.Item label="甄别结论">{p.accept_decision || '待甄别'}{p.accept_reason ? `（${p.accept_reason}）` : ''}</Descriptions.Item>
            {p.notified_at && <Descriptions.Item label="受理告知">{p.notified_at}</Descriptions.Item>}
            {p.feedback_at && <Descriptions.Item label="办结反馈">{p.feedback_at}</Descriptions.Item>}
            {p.supervised_at && <Descriptions.Item label="督办">{p.supervised_at}{p.supervise_note ? `（${p.supervise_note}）` : ''}</Descriptions.Item>}
          </Descriptions>
        </Card>

        <Card size="small" title={<Space><PaperClipOutlined />材料原件</Space>} style={{ marginBottom: 12 }}
          extra={p.has_material ? <Space>
            <Button size="small" icon={<EyeOutlined />} onClick={() => setMatOpen(true)}>查看</Button>
            <Button size="small" icon={<DownloadOutlined />} onClick={downloadMaterial}>下载</Button>
          </Space> : null}>
          {p.has_material ? (
            <div>
              <Typography.Text>{p.file_name || '材料原件'}</Typography.Text>
              <div><Typography.Text type="secondary" style={{ fontSize: 12 }}>登记时留存，可用于回溯核对</Typography.Text></div>
            </div>
          ) : (
            <div>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 8, fontSize: 12 }}>
                未留存材料原件。登记时上传的材料会自动留存，也可在此补传。
              </Typography.Paragraph>
              <Upload accept=".pdf,image/*,.txt,.md" showUploadList={false}
                beforeUpload={(f) => { onUpload(f as File); return false }}>
                <Button size="small" icon={<PaperClipOutlined />}>补传材料原件</Button>
              </Upload>
            </div>
          )}
        </Card>

        {elements && (
          <Card size="small" title="要素抽取" style={{ marginBottom: 12 }}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="被反映人">
                {person?.姓名 || '-'} {person?.单位 ? `· ${person.单位}` : ''} {person?.职务 ? `· ${person.职务}` : ''}
              </Descriptions.Item>
              <Descriptions.Item label="涉及金额">{elements?.涉及金额 || '-'}</Descriptions.Item>
              <Descriptions.Item label="时间">{elements?.时间 || '-'}</Descriptions.Item>
              <Descriptions.Item label="举报分类">{elements?.举报分类 || '-'}</Descriptions.Item>
            </Descriptions>
          </Card>
        )}

        {p.duplicate_of && (
          <Alert type="warning" showIcon style={{ marginTop: 12 }}
            message={`疑似与 #${p.duplicate_of} 重复，是否合并由人工决定`} />
        )}
      </Col>

      <Modal title={`材料原件 · ${p.file_name || ''}`} open={matOpen}
        onCancel={() => setMatOpen(false)} footer={null} width={860} destroyOnClose>
        <MaterialPreview pid={p.id} fileType={p.file_type || ''} fileName={p.file_name || ''} />
      </Modal>
    </Row>
  )
}

/** 效能统计面板：受理法治化 / 办理法治化 / 监督追责法治化 指标 */
function PetitionStats({ stats, loading, onRefresh }: { stats: any; loading: boolean; onRefresh: () => void }) {
  if (loading && !stats) return <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
  if (!stats) return <Empty description="暂无统计数据" />

  const maxCount = (obj: Record<string, number>) => Math.max(1, ...Object.values(obj || {}))
  const U_COLOR: Record<string, string> = { 高: 'red', 中: 'orange', 低: 'green' }
  const B_COLOR: Record<string, string> = { 检举控告: 'volcano', 申诉: 'blue', 批评建议: 'green' }
  const A_COLOR: Record<string, string> = { 受理: 'green', 不予受理: 'red', 重复留存: 'orange', 待甄别: 'default' }

  const rate = (n: number) => stats.total ? Math.round((n / stats.total) * 100) : 0

  const BarList = ({ data, colorMap }: { data: Record<string, number>; colorMap?: Record<string, string> }) => {
    const mx = maxCount(data)
    return (
      <div>
        {Object.entries(data).length === 0 && <Typography.Text type="secondary">暂无数据</Typography.Text>}
        {Object.entries(data).map(([k, v]) => (
          <div key={k} style={{ marginBottom: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <Tag color={colorMap?.[k] || 'blue'}>{k}</Tag>
              <Typography.Text type="secondary">{v} 件</Typography.Text>
            </div>
            <div style={{ background: '#f3f5f9', borderRadius: 4, height: 10 }}>
              <div style={{ width: `${(v / mx) * 100}%`, height: '100%', borderRadius: 4,
                background: 'linear-gradient(90deg,#1677ff99,#1d4ed8)' }} />
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <Spin spinning={loading}>
      <Row gutter={16}>
        <Col xs={12} md={6}><Card><Statistic title="信访件总数" value={stats.total} suffix="件" valueStyle={{ color: '#1d4ed8' }} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="实名举报" value={stats.realname_total} suffix={`件 / ${rate(stats.realname_total)}%`} valueStyle={{ color: '#4f46e5' }} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="超期预警" value={stats.overdue_total} suffix="件" valueStyle={{ color: stats.overdue_total ? '#b91c1c' : '#15803d' }} /></Card></Col>
        <Col xs={12} md={6}><Card><Button icon={<BarChartOutlined />} onClick={onRefresh} block>刷新统计</Button></Card></Col>
      </Row>
      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col xs={24} lg={8}>
          <Card size="small" title="受理决定分布" style={{ marginBottom: 16 }}>
            <BarList data={stats.by_accept} colorMap={A_COLOR} />
          </Card>
          <Card size="small" title="紧急程度分布">
            <BarList data={stats.by_urgency} colorMap={U_COLOR} />
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card size="small" title="效能指标" style={{ marginBottom: 16 }}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="受理告知率">{stats.realname_total ? `${rate(stats.notified_total)}%（${stats.notified_total}/${stats.realname_total}）` : '—'}</Descriptions.Item>
              <Descriptions.Item label="办结反馈率">{stats.realname_total ? `${rate(stats.feedback_total)}%（${stats.feedback_total}/${stats.realname_total}）` : '—'}</Descriptions.Item>
              <Descriptions.Item label="实名未办结反馈">{stats.unresolved_total} 件</Descriptions.Item>
              <Descriptions.Item label="转案管线索">{stats.transferred_total} 件</Descriptions.Item>
              <Descriptions.Item label="已识别整理">{stats.extracted} 件</Descriptions.Item>
            </Descriptions>
          </Card>
          <Card size="small" title="业务类别分布">
            <BarList data={stats.by_biz_type} colorMap={B_COLOR} />
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card size="small" title="来源渠道分布" style={{ marginBottom: 16 }}>
            <BarList data={stats.by_channel} />
          </Card>
          <Card size="small" title="近 6 个月登记趋势">
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, height: 160, padding: '8px 0' }}>
              {stats.monthly.map((m: any) => {
                const h = Math.round((m.count / Math.max(1, ...stats.monthly.map((x: any) => x.count))) * 120) + (m.count ? 6 : 0)
                return (
                  <div key={m.month} style={{ flex: 1, textAlign: 'center' }}>
                    <div style={{ fontSize: 12, color: '#1d4ed8', fontWeight: 600 }}>{m.count}</div>
                    <div style={{ width: '70%', margin: '0 auto', background: 'linear-gradient(180deg,#1d4ed8,#69b1ff)',
                      borderRadius: 4, height: h, minHeight: 2, transition: 'height .4s' }} />
                    <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>{m.month.slice(2)}</div>
                  </div>
                )
              })}
            </div>
          </Card>
        </Col>
      </Row>
    </Spin>
  )
}
