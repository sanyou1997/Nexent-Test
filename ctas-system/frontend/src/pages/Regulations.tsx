import { useEffect, useRef, useState } from 'react'
import { Card, Table, Input, Tag, Upload, Button, message, Typography, Space, Alert, Descriptions, Select, Modal, Form, Switch } from 'antd'
import { UploadOutlined, SearchOutlined, BookOutlined, SafetyCertificateOutlined, DownloadOutlined, PlusOutlined } from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { useAuth } from '../auth'
import PageHeader from '../components/PageHeader'
import KbSyncBar, { KbSync } from '../components/KbSyncBar'

const CATEGORIES = ['中央党内法规', '国家法律法规', '纪检监察法规制度', '业务工作规范', '其他']

interface Reg {
  id: number; title: string; version: string; effective_date: string
  status: string; source: string; article_count: number; is_official?: boolean; category: string
}
interface Article { id: number; regulation: string; chapter: string; article_no: string; content: string }

export default function Regulations() {
  const { user } = useAuth()
  const canEdit = user?.role === 'admin' || user?.role === 'xinfang'
  const [regs, setRegs] = useState<Reg[]>([])
  const [kb, setKb] = useState<KbSync | null>(null)
  const loadKb = () => api.get('/regulations/kb-sync').then(({ data }) => setKb(data)).catch(() => setKb(null))
  const kbStatus: Record<number, string> = Object.fromEntries((kb?.items || []).map((i) => [i.regulation_id, i.status]))
  const [articles, setArticles] = useState<Article[]>([])
  const [total, setTotal] = useState(0)
  const [regId, setRegId] = useState<number | null>(null)
  const [cat, setCat] = useState<string>('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const debRef = useRef<number>()
  const [createForm] = Form.useForm()

  const loadRegs = async () => {
    const { data } = await api.get('/regulations', { params: { category: cat || undefined } })
    setRegs(data)
    loadKb()  // 新增 / 修改法规后，联动状态随之变为「待同步」
  }
  useEffect(() => { loadRegs() }, [cat])

  const loadArticles = async () => {
    setLoading(true)
    try {
      const { data } = await api.get('/regulations/articles', {
        params: { regulation_id: regId || undefined, q: q || undefined, page, per_page: pageSize },
      })
      setArticles(data.items)
      setTotal(data.total)
    } finally { setLoading(false) }
  }
  useEffect(() => { loadArticles() }, [regId, q, page])

  const onSearch = (v: string) => {
    setPage(1)
    if (debRef.current) window.clearTimeout(debRef.current)
    debRef.current = window.setTimeout(() => setQ(v), 300)
  }

  const [uploading, setUploading] = useState(false)
  const upload = async (file: File, official: boolean) => {
    setUploading(true)
    const fd = new FormData()
    fd.append('file', file)
    fd.append('official', official ? 'true' : 'false')
    try {
      message.loading({ content: '解析入库中，PDF 较大时约需 2-5 秒/页…', key: 'up', duration: 0 })
      const { data } = await api.post('/regulations/ingest', fd, { timeout: 600000 })
      message.destroy('up')
      if (data.ok) {
        message.success(official
          ? `正式文档录入完成：${data.article_count} 条（只读，标注来源）`
          : `入库成功：${data.article_count} 条`)
        loadRegs()
      } else message.error(data.error)
    } catch (e) { message.destroy('up'); message.error(errMsg(e)) }
    finally { setUploading(false) }
    return false
  }

  const exportArticles = async () => {
    try {
      const { data } = await api.get('/regulations/export', {
        params: { regulation_id: regId || undefined, q: q || undefined },
        responseType: 'blob', timeout: 120000,
      })
      const url = window.URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = url
      const scope = regId ? (regs.find((r) => r.id === regId)?.title || '全部') : '全库'
      a.download = `法规条款_${scope}_${new Date().toISOString().slice(0, 10)}.xlsx`
      a.click()
      window.URL.revokeObjectURL(url)
      message.success('条款已导出')
    } catch (e) { message.error(errMsg(e)) }
  }

  const onEnsure = async () => {
    try {
      const vals = await createForm.validateFields()
      setCreating(true)
      const { data } = await api.post('/regulations/create', {
        text: vals.text, official: vals.official, category: vals.category, source: vals.source || '在线录入（粘贴语料）',
      })
      if (data.ok) {
        message.success(`已建库：${data.title}（${data.article_count} 条）`)
        setCreateOpen(false)
        createForm.resetFields()
        setCat('')
        loadRegs()
      } else message.error(data.error || '建库失败')
    } catch (e: any) { if (e?.response) message.error(errMsg(e)) }
    finally { setCreating(false) }
  }

  const cur = regs.find((r) => r.id === regId)

  return (
    <>
      <PageHeader
        icon={<BookOutlined />}
        title="法规知识库"
        subtitle="条款级切块 · 本地检索即时溯源 · 同步为 Nexent 知识库供智能体检索"
        extra={canEdit ? (
          <Space>
            <Button icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>粘贴语料建库</Button>
            <Upload accept=".txt,.md,.pdf" showUploadList={false}
              beforeUpload={(f) => upload(f as unknown as File, true)}>
              <Button type="primary" icon={<SafetyCertificateOutlined />} loading={uploading}>
                正式文档录入（原件导入 · 只读）
              </Button>
            </Upload>
            <Upload accept=".txt,.md,.pdf" showUploadList={false}
              beforeUpload={(f) => upload(f as unknown as File, false)}>
              <Button icon={<UploadOutlined />}>导入业务资料</Button>
            </Upload>
          </Space>
        ) : null} />
      <KbSyncBar data={kb} isAdmin={user?.role === 'admin'} onRefresh={loadKb} />
      <Card>
        <Alert type="info" showIcon style={{ marginBottom: 10 }}
          message="「正式文档录入」= 以发布机关公开原文整篇入库，自动标注来源与发布机关，入库后只读（不可编辑/改写）；「导入业务资料」用于自有材料（须为经授权使用的资料）。「粘贴语料建库」支持离线直接粘贴 txt/md 格式语料（无需 PDF/MinerU 即可录入）。带「正式」徽标的是官方原文条目。" />
        <Space wrap style={{ marginBottom: 8 }}>
          <span>分类：</span>
          <Select value={cat} style={{ width: 180 }} allowClear placeholder="全部分类"
            onChange={(v) => { setCat(v || ''); setRegId(null) }}
            options={CATEGORIES.map((c) => ({ value: c, label: c }))} />
        </Space>
        <Typography.Paragraph type="secondary" style={{ margin: '4px 0' }}>
          语料格式（txt/md）：首行 <code>法规名|版本|生效日期</code>，<code>## 章名</code> 标示章节，<code>【第X条】条款内容</code> 逐条切分。
        </Typography.Paragraph>
        <Space wrap size={[4, 6]}>
          <Tag.CheckableTag checked={regId === null} onChange={() => setRegId(null)}
            style={{ border: '1px dashed #b6c0cf' }}>
            全库（跨法规搜索）
          </Tag.CheckableTag>
          {regs.map((r) => (
            <Tag.CheckableTag key={r.id} checked={regId === r.id} onChange={() => setRegId(r.id)}
              style={{ border: r.is_official ? '1px solid #b7d0ff' : undefined }}>
              {r.is_official && <Tag color="blue" style={{ marginRight: 4, lineHeight: '16px' }}>正式</Tag>}
              {r.title}（{r.article_count} 条）
              {kb?.configured && kbStatus[r.id] && kbStatus[r.id] !== 'synced' && (
                <span style={{ marginLeft: 4, color: '#b45309', fontSize: 11 }}>· 待同步</span>
              )}
            </Tag.CheckableTag>
          ))}
        </Space>
        {cur && (
          <Descriptions size="small" column={3} bordered style={{ marginTop: 10 }}
            items={[
              { key: '1', label: '分类', children: cur.category || '其他' },
              { key: '2', label: '版本', children: cur.version || '—' },
              { key: '3', label: '施行日期', children: cur.effective_date || '—' },
              { key: '4', label: '文档性质', children: cur.is_official
                ? <Tag color="blue">正式文档（只读）</Tag> : <Tag>业务资料</Tag> },
              { key: '5', label: '状态', children: cur.status || '—' },
              { key: '6', label: '来源', span: 3, children: cur.source || '—' },
            ]} />
        )}
      </Card>
      <Card style={{ marginTop: 12 }}
        title={`条款列表（${total} 条${regId ? '· 当前法规' : '· 全库'}）`}
        extra={<Space>
          <Input allowClear prefix={<SearchOutlined />} placeholder="搜索条款内容/条款号/章节"
            onChange={(e) => onSearch(e.target.value)} style={{ width: 260 }} />
          <Button icon={<DownloadOutlined />} onClick={exportArticles}>导出</Button>
        </Space>}>
        <Table rowKey="id" size="small" loading={loading}
          dataSource={articles} pagination={{ current: page, pageSize, total, onChange: (p) => setPage(p) }}
          columns={[
            { title: '法规', dataIndex: 'regulation', width: 220, ellipsis: true },
            { title: '章节', dataIndex: 'chapter', width: 180, ellipsis: true },
            { title: '条款', dataIndex: 'article_no', width: 90 },
            { title: '内容', dataIndex: 'content' },
          ]} />
      </Card>

      <Modal title="粘贴语料建库（离线零依赖）" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={onEnsure} confirmLoading={creating} okText="建库" width={680} destroyOnClose>
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="无需 PDF/MinerU，直接粘贴 txt/md 语料即可建库。离线使用本地向量检索，入库即生效。" />
        <Form form={createForm} layout="vertical" initialValues={{ official: false, category: '其他' }}>
          <Form.Item name="text" label="语料文本" rules={[{ required: true, message: '请粘贴语料文本' }]}>
            <Input.TextArea rows={10} placeholder={'首行：法规名|版本|生效日期\n## 第一章 总则\n【第一条】……\n【第二条】……'} />
          </Form.Item>
          <Space size="large" wrap>
            <Form.Item name="category" label="分类" style={{ width: 220 }}>
              <Select options={CATEGORIES.map((c) => ({ value: c, label: c }))} />
            </Form.Item>
            <Form.Item name="source" label="来源说明" style={{ width: 240 }}>
              <Input placeholder="在线录入（粘贴语料）" />
            </Form.Item>
            <Form.Item name="official" label="标记为正式文档（只读）" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </>
  )
}
