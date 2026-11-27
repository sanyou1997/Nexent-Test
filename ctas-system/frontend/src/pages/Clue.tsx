import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Tag, Space, message, Drawer, Typography, Descriptions, Empty,
  Progress, List, Input, Select, DatePicker, Modal, Form, InputNumber, Timeline, Alert as AntAlert,
} from 'antd'
import {
  AlertOutlined, ReloadOutlined, EyeOutlined, EditOutlined, FileAddOutlined,
  PlusOutlined, ExportOutlined, RobotOutlined,
} from '@ant-design/icons'
import AiAssistDrawer from '../components/nexent/AiAssistDrawer'
import { useAuth } from '../auth'
import dayjs from 'dayjs'
import { api, errMsg } from '../api/client'
import PageHeader from '../components/PageHeader'

const LEVEL_COLOR: Record<string, string> = { 高: 'red', 中: 'orange', 低: 'green', 提示: 'blue' }
const DISPOSITIONS = ['谈话函询', '初步核实', '暂存待查', '予以了结']
const SOURCES = ['信访', '巡视', '审计', '上级交办', '主动发现']
const STATUSES = ['待研判', '已分办', '初核中', '已立案', '已处置', '已了结']

function scoreColor(s: number) {
  if (s >= 70) return '#b91c1c'
  if (s >= 40) return '#b45309'
  return '#15803d'
}

export default function Clue() {
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(10)
  const [filters, setFilters] = useState<{ q: string; status: string; source: string; urgency: string }>(
    { q: '', status: '待研判', source: '', urgency: '' })
  const [users, setUsers] = useState<any[]>([])

  // 研判 / 立案 / 登记 弹窗 + 画像抽屉
  const [review, setReview] = useState<{ open: boolean; cid: number; rec: any }>({ open: false, cid: 0, rec: null })
  const [openCase, setOpenCase] = useState<{ open: boolean; cid: number; rec: any }>({ open: false, cid: 0, rec: null })
  const [createOpen, setCreateOpen] = useState(false)
  const [profile, setProfile] = useState<any>(null)
  const [timeline, setTimeline] = useState<any[]>([])
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [aiOpen, setAiOpen] = useState(false)
  const { user } = useAuth()

  const [reviewForm] = Form.useForm()
  const [openForm] = Form.useForm()
  const [createForm] = Form.useForm()

  const load = async (p = page) => {
    setLoading(true)
    try {
      const { data } = await api.get('/clue', {
        params: { q: filters.q, status: filters.status, source: filters.source,
                  urgency: filters.urgency, page: p, per_page: pageSize },
      })
      setRows(data.items || [])
      setTotal(data.total || 0)
      setPage(p)
    } catch (e) { message.error(errMsg(e)) } finally { setLoading(false) }
  }
  const loadUsers = async () => {
    try { const { data } = await api.get('/users'); setUsers(data || []) } catch { /* 非 admin 忽略 */ }
  }
  useEffect(() => { loadUsers() }, [])
  useEffect(() => { load(1) }, []) // eslint-disable-line

  const openReview = (r: any) => {
    setReview({ open: true, cid: r.id, rec: r })
    reviewForm.setFieldsValue({
      disposition: r.disposition || undefined,
      note: r.disposition_note || '',
      owner_id: r.owner_id || undefined,
      due_at: r.due_at ? dayjs(r.due_at) : null,
    })
  }
  const submitReview = async () => {
    const v = await reviewForm.validateFields()
    try {
      await api.post(`/cases/${review.cid}/dispose`, {
        disposition: v.disposition, note: v.note || '', meeting_note: '',
        owner_id: v.owner_id ?? null, due_at: v.due_at ? v.due_at.format('YYYY-MM-DD') : null,
      })
      message.success('研判处置已记录，流程轨迹已留痕')
      setReview({ open: false, cid: 0, rec: null })
      load(page)
    } catch (e) { message.error(errMsg(e)) }
  }

  const submitOpen = async () => {
    const v = await openForm.validateFields()
    try {
      const { data } = await api.post(`/cases/${openCase.cid}/open`, { basis: v.basis || '', measure: v.measure || '' })
      message.success('已立案，立案决定书（AI 初稿）已生成')
      setOpenCase({ open: false, cid: 0, rec: null })
      load(page)
      // 展示立案决定书
      if (data?.document) {
        Modal.info({
          title: `立案决定书 · ${openCase.rec?.code}`,
          width: 680,
          content: <pre style={{ maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 13 }}>{data.document.content}</pre>,
        })
      }
    } catch (e) { message.error(errMsg(e)) }
  }

  const submitCreate = async () => {
    const v = await createForm.validateFields()
    try {
      await api.post('/cases', {
        subject: v.subject, unit: v.unit || '', title: v.title, content: v.content || '',
        problem_type: v.problem_type || '', urgency: v.urgency || '中', source: v.source || '信访',
      })
      message.success('线索已登记')
      setCreateOpen(false)
      createForm.resetFields()
      load(1)
    } catch (e) { message.error(errMsg(e)) }
  }

  const openProfile = async (r: any) => {
    try {
      const [p, t] = await Promise.all([api.get(`/clue/${r.id}`), api.get(`/cases/${r.id}/timeline`)])
      setProfile(p.data); setTimeline(t.data.items || []); setDrawerOpen(true)
    } catch (e) { message.error(errMsg(e)) }
  }

  const columns = [
    { title: '风险', dataIndex: 'risk_score', width: 64, fixed: 'left' as const,
      render: (s: number) => (
        <Progress type="circle" size={34} percent={s} strokeColor={scoreColor(s)} format={() => s} />
      ) },
    { title: '案号', dataIndex: 'code', width: 130 },
    { title: '被反映人', dataIndex: 'subject', width: 100 },
    { title: '单位/职务', dataIndex: 'unit', width: 130, ellipsis: true },
    { title: '案由', dataIndex: 'title', ellipsis: true },
    { title: '问题类型', dataIndex: 'problem_type', width: 110, ellipsis: true },
    { title: '来源', dataIndex: 'source', width: 80, render: (s: string) => <Tag>{s}</Tag> },
    { title: '紧急度', dataIndex: 'urgency', width: 70, render: (s: string) => <Tag color={LEVEL_COLOR[s]}>{s}</Tag> },
    { title: '状态', dataIndex: 'status', width: 90,
      render: (s: string) => <Tag color={s === '已了结' ? 'default' : s === '已立案' ? 'blue' : 'processing'}>{s}</Tag> },
    { title: '承办人', dataIndex: 'owner', width: 90, render: (s: string) => s || <span style={{ color: '#b6c0cf' }}>未分办</span> },
    { title: '操作', key: 'op', width: 200, fixed: 'right' as const,
      render: (_: any, r: any) => (
        <Space size={4}>
          <Button size="small" icon={<EditOutlined />} onClick={() => openReview(r)} disabled={['已处置', '已了结'].includes(r.status)}>研判</Button>
          <Button size="small" type="primary" icon={<FileAddOutlined />} onClick={() => { setOpenCase({ open: true, cid: r.id, rec: r }); openForm.resetFields() }}
            disabled={['已立案', '审查调查中', '已移送审理', '已处置', '已了结'].includes(r.status)}>立案</Button>
          <Button size="small" icon={<EyeOutlined />} onClick={() => openProfile(r)}>画像</Button>
        </Space>
      ) },
  ]

  return (
    <div>
      <PageHeader
        icon={<AlertOutlined />}
        title="问题线索研判工作台"
        subtitle="线索登记 · 风险研判 · 处置分办 · 立案流转（与案管同源台账）"
        extra={
          <Space>
            <Button icon={<PlusOutlined />} type="primary" onClick={() => { setCreateOpen(true); createForm.resetFields() }}>登记线索</Button>
            <Button icon={<ReloadOutlined />} onClick={() => load(1)}>刷新</Button>
          </Space>
        } />

      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap>
          <Input.Search placeholder="搜索被反映人 / 案由 / 案号" allowClear style={{ width: 240 }}
            value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })}
            onSearch={() => load(1)} enterButton />
          <Select placeholder="状态" style={{ width: 120 }} allowClear
            value={filters.status || undefined} onChange={(v) => { setFilters({ ...filters, status: v || '' }); }}>
            {STATUSES.map((s) => <Select.Option key={s} value={s}>{s}</Select.Option>)}
          </Select>
          <Select placeholder="来源" style={{ width: 120 }} allowClear
            value={filters.source || undefined} onChange={(v) => { setFilters({ ...filters, source: v || '' }); }}>
            {SOURCES.map((s) => <Select.Option key={s} value={s}>{s}</Select.Option>)}
          </Select>
          <Select placeholder="紧急度" style={{ width: 100 }} allowClear
            value={filters.urgency || undefined} onChange={(v) => { setFilters({ ...filters, urgency: v || '' }); }}>
            {['高', '中', '低'].map((s) => <Select.Option key={s} value={s}>{s}</Select.Option>)}
          </Select>
          <Button onClick={() => load(1)}>查询</Button>
        </Space>
      </Card>

      <Card size="small">
        <Table rowKey="id" size="small" loading={loading} columns={columns} dataSource={rows}
          scroll={{ x: 1180 }}
          pagination={{ current: page, pageSize, total, showTotal: (t) => `共 ${t} 条线索`,
            onChange: (p) => load(p) }} />
      </Card>

      {/* 研判处置 */}
      <Modal title={`研判处置 · ${review.rec?.code || ''}`} open={review.open}
        onCancel={() => setReview({ ...review, open: false })} onOk={submitReview} okText="记录研判"
        destroyOnClose>
        <Form form={reviewForm} layout="vertical">
          <Form.Item name="disposition" label="处置方式" rules={[{ required: true, message: '请选择处置方式' }]}>
            <Select placeholder="谈话函询 / 初步核实 / 暂存待查 / 予以了结" options={DISPOSITIONS.map((d) => ({ label: d, value: d }))} />
          </Form.Item>
          <Form.Item name="note" label="研判意见 / 处置依据">
            <Input.TextArea rows={3} placeholder="填写研判意见、处置依据（将写入流程轨迹留痕）" />
          </Form.Item>
          <Form.Item name="owner_id" label="承办人（分办到人）">
            <Select placeholder="选择承办人" allowClear options={users.map((u) => ({ label: u.real_name || u.username, value: u.id }))} />
          </Form.Item>
          <Form.Item name="due_at" label="办结时限">
            <DatePicker style={{ width: '100%' }} placeholder="选择时限（逾期将触发预警）" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 立案 */}
      <Modal title={`立案审查调查 · ${openCase.rec?.code || ''}`} open={openCase.open}
        onCancel={() => setOpenCase({ ...openCase, open: false })} onOk={submitOpen} okText="立案并生成决定书"
        destroyOnClose>
        <AntAlert type="info" showIcon style={{ marginBottom: 12 }}
          message="立案后状态置「已立案」，系统自动套模板生成《立案决定书》AI 初稿（可在文书模块定稿）。" />
        <Form form={openForm} layout="vertical">
          <Form.Item name="basis" label="立案依据">
            <Input.TextArea rows={3} placeholder="如：经初步核实，发现涉嫌 XX 违纪违法问题" />
          </Form.Item>
          <Form.Item name="measure" label="拟采取措施">
            <Input.TextArea rows={2} placeholder="如：依规依纪依法采取相应审查调查措施" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 登记线索 */}
      <Modal title="登记问题线索" open={createOpen}
        onCancel={() => setCreateOpen(false)} onOk={submitCreate} okText="登记"
        destroyOnClose>
        <Form form={createForm} layout="vertical">
          <Form.Item name="subject" label="被反映人" rules={[{ required: true, message: '请输入被反映人' }]}>
            <Input placeholder="姓名" />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="source" label="来源" style={{ flex: 1 }} initialValue="信访">
              <Select options={SOURCES.map((s) => ({ label: s, value: s }))} />
            </Form.Item>
            <Form.Item name="urgency" label="紧急度" style={{ flex: 1 }} initialValue="中">
              <Select options={['高', '中', '低'].map((s) => ({ label: s, value: s }))} />
            </Form.Item>
          </Space>
          <Form.Item name="unit" label="单位/职务">
            <Input placeholder="单位及职务" />
          </Form.Item>
          <Form.Item name="title" label="案由" rules={[{ required: true, message: '请输入案由' }]}>
            <Input placeholder="问题概要" />
          </Form.Item>
          <Form.Item name="problem_type" label="问题类型">
            <Input placeholder="如：违反廉洁纪律 / 职务违法" />
          </Form.Item>
          <Form.Item name="content" label="反映内容">
            <Input.TextArea rows={3} placeholder="主要问题反映" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 画像 + 轨迹 */}
      <AiAssistDrawer open={aiOpen && !!profile} onClose={() => setAiOpen(false)} user={user}
        subject={profile ? { kind: 'case', id: profile.id, title: `${profile.code} ${profile.subject || ''}：${profile.title || ''}` } : null} />
      <Drawer width={600} open={drawerOpen} onClose={() => setDrawerOpen(false)}
        title={`线索研判画像 ${profile?.code || ''}`}
        extra={profile && <Button type="primary" ghost icon={<RobotOutlined />} onClick={() => setAiOpen(true)}>AI 研判</Button>}>
        {profile && (
          <>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="被反映人">{profile.subject}</Descriptions.Item>
              <Descriptions.Item label="单位/职务">{profile.unit}</Descriptions.Item>
              <Descriptions.Item label="案由">{profile.title}</Descriptions.Item>
              <Descriptions.Item label="风险分">
                <Progress percent={profile.risk_score} strokeColor={scoreColor(profile.risk_score)} />
              </Descriptions.Item>
              <Descriptions.Item label="风险标签">{(profile.tags || []).map((t: string) => <Tag key={t} color="processing">{t}</Tag>)}</Descriptions.Item>
            </Descriptions>
            <AntAlert type="warning" showIcon style={{ margin: '12px 0' }}
              message="处置建议" description={profile.suggestion} />
            <Card size="small" title="研判依据" style={{ marginBottom: 12 }}>
              <List size="small" dataSource={profile.reasons || []} renderItem={(r: string) => <List.Item>• {r}</List.Item>} />
            </Card>
            {profile.bank_flags?.length > 0 && (
              <Card size="small" title={`关联资金异常（${profile.bank_flags.length}）`} style={{ marginBottom: 12 }}>
                {profile.bank_flags.map((b: any, i: number) => (
                  <div key={i}>· {b.type}：{b.reason}</div>
                ))}
              </Card>
            )}
            <Card size="small" title={`关联记录（${profile.related?.length || 0}）`} style={{ marginBottom: 12 }}>
              {profile.related?.length ? profile.related.map((r: any, i: number) => (
                <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid #f6f8fb' }}>
                  <Tag color="blue">{r.type}</Tag> {r.title || r.code} <Tag>{r.status}</Tag>
                </div>
              )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无跨域关联" />}
            </Card>
            <Card size="small" title={`流程轨迹（${timeline.length}）`}>
              {timeline.length ? (
                <Timeline items={timeline.map((t: any) => ({
                  color: t.action === 'case_open' ? 'green' : t.action === 'case_dispose' ? 'blue' : 'gray',
                  children: <div>
                    <div><b>{t.label}</b> <Tag>{t.username}</Tag> <span style={{ color: '#94a3b8' }}>{t.created_at}</span></div>
                    <div style={{ color: '#475569' }}>{t.desc}</div>
                  </div>,
                }))} />
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无轨迹" />}
            </Card>
          </>
        )}
      </Drawer>
    </div>
  )
}
