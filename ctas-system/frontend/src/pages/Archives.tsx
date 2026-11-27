import { useState, useEffect, useCallback } from 'react'
import { Card, Upload, Button, Select, Input, Table, Tag, Drawer, Descriptions, Typography,
  Space, message as antdMessage, Statistic, Row, Col, Progress, Popconfirm } from 'antd'
import { DatabaseOutlined, UploadOutlined, InboxOutlined, SaveOutlined, CopyOutlined,
  DownloadOutlined, EyeOutlined, FileImageOutlined, FilePdfOutlined, ExportOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'

const CATS = ['文书档案', '案件档案', '财务档案', '其他']
const STATUSES = ['已入库', '待编目', '已归档']
const SECRET_LEVELS = ['公开', '内部', '秘密', '机密']
const RETENTIONS = ['永久', '30年', '10年']
const STATUS_COLOR: Record<string, string> = { '已归档': 'green', '待编目': 'orange', '已入库': 'default' }
const SECRET_COLOR: Record<string, string> = { '公开': 'default', '内部': 'blue', '秘密': 'gold', '机密': 'red' }
const IMG_TYPES = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']

interface Arc {
  id: number; archive_no: string; title: string; source: string; category: string
  ocr_text: string; page_count: number; char_count: number; status: string
  file_name: string; file_type: string; has_file: boolean; uploaded_by_name: string; created_at: string
  secret_level: string; retention: string; has_boxes: boolean; duplicate_of?: number | null
}

interface BoxPage { page: number; w: number; h: number; boxes: { box: number[][]; text: string; score: number }[] }

/** 按检索词高亮 OCR 正文 */
function hl(text: string, q: string) {
  if (!q) return text
  const idx: number[] = []
  const lower = text.toLowerCase(); const k = q.toLowerCase()
  let p = lower.indexOf(k)
  while (p >= 0) { idx.push(p); p = lower.indexOf(k, p + k.length) }
  if (!idx.length) return text
  const out: React.ReactNode[] = []
  let cur = 0
  idx.forEach((i, n) => {
    if (i > cur) out.push(text.slice(cur, i))
    out.push(<mark key={n} style={{ background: '#fff7ed', padding: 0 }}>{text.slice(i, i + k.length)}</mark>)
    cur = i + k.length
  })
  if (cur < text.length) out.push(text.slice(cur))
  return out
}

export default function Archives() {
  const [files, setFiles] = useState<File[]>([])
  const [title, setTitle] = useState('')
  const [source, setSource] = useState('')
  const [category, setCategory] = useState('文书档案')
  const [secretLevel, setSecretLevel] = useState('内部')
  const [retention, setRetention] = useState('30年')
  const [ingesting, setIngesting] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number; cur: string }>({ done: 0, total: 0, cur: '' })
  const [list, setList] = useState<Arc[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(10)
  const [stats, setStats] = useState<any>(null)
  const [q, setQ] = useState('')
  const [fCat, setFCat] = useState('')
  const [fStatus, setFStatus] = useState('')
  const [drawer, setDrawer] = useState<Arc | null>(null)
  const [edit, setEdit] = useState<Partial<Arc>>({})
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [cmpImg, setCmpImg] = useState<string | null>(null)
  const [previewKind, setPreviewKind] = useState<'image' | 'pdf' | 'other'>('other')
  const [boxesData, setBoxesData] = useState<{ pages: BoxPage[] } | null>(null)
  const [viewPage, setViewPage] = useState(1)
  const [selBox, setSelBox] = useState(-1)

  const load = useCallback(() => {
    const p = new URLSearchParams()
    if (q) p.set('q', q); if (fCat) p.set('category', fCat); if (fStatus) p.set('status', fStatus)
    p.set('page', String(page)); p.set('per_page', String(perPage))
    api.get(`/archives?${p.toString()}`).then(({ data }) => { setList(data.items || []); setTotal(data.total || 0) })
    api.get('/archives/stats/summary').then(({ data }) => setStats(data))
  }, [q, fCat, fStatus, page, perPage])
  useEffect(load, [load])

  const removeFile = (i: number) => setFiles((f) => f.filter((_, k) => k !== i))

  const ingestAll = async () => {
    if (!files.length) { antdMessage.warning('请先选择扫描文件'); return }
    setIngesting(true)
    setProgress({ done: 0, total: files.length, cur: '' })
    let ok = 0
    const fails: string[] = []
    for (let i = 0; i < files.length; i++) {
      const f = files[i]
      setProgress((s) => ({ ...s, cur: f.name }))
      try {
        const fd = new FormData()
        fd.append('file', f)
        fd.append('title', title); fd.append('source', source); fd.append('category', category)
        fd.append('secret_level', secretLevel); fd.append('retention', retention)
        const { data } = await api.post('/archives/ingest', fd)
        ok++
        if (data.duplicate_of) antdMessage.warning(`检测到重复原件，已入库为 ${data.archive_no}（原件内容一致）`)
        else antdMessage.success(`入库成功：${data.archive_no}（${data.file_name || f.name}）`)
      } catch (e: any) {
        fails.push(`${f.name}：${e?.response?.data?.detail || '失败'}`)
      }
      setProgress((s) => ({ ...s, done: i + 1 }))
    }
    setIngesting(false)
    setFiles([]); setTitle(''); setSource('')
    if (fails.length) antdMessage.error(`失败 ${fails.length} 件：${fails[0]}`)
    setPage(1); load()
  }

  const openDetail = async (a: Arc) => {
    setDrawer(a)
    setEdit({ title: a.title, source: a.source, category: a.category, status: a.status, secret_level: a.secret_level, retention: a.retention })
    setSelBox(-1); setBoxesData(null); setViewPage(1); setCmpImg(null)
    if (a.has_file) {
      try {
        const { data } = await api.get(`/archives/${a.id}/file`, { responseType: 'blob' })
        const url = URL.createObjectURL(data)
        setPreviewUrl(url)
        setPreviewKind(a.file_type === 'pdf' ? 'pdf' : IMG_TYPES.includes(a.file_type) ? 'image' : 'other')
        if (a.has_boxes && a.file_type !== 'pdf') setCmpImg(url)
      } catch { setPreviewUrl(null); setPreviewKind('other') }
    } else { setPreviewUrl(null); setPreviewKind('other') }
    if (a.has_boxes) {
      try { const { data } = await api.get(`/archives/${a.id}/boxes`); setBoxesData(data) }
      catch { setBoxesData(null) }
    }
  }
  useEffect(() => {
    if (drawer?.has_boxes && drawer.file_type === 'pdf' && boxesData) {
      let url: string | null = null
      api.get(`/archives/${drawer.id}/page/${viewPage}`, { responseType: 'blob' })
        .then(({ data }) => { url = URL.createObjectURL(data); setCmpImg(url) })
        .catch(() => setCmpImg(null))
      return () => { if (url) URL.revokeObjectURL(url) }
    }
  }, [drawer?.id, viewPage, boxesData, drawer?.has_boxes, drawer?.file_type])
  const closeDrawer = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    if (cmpImg && cmpImg !== previewUrl) URL.revokeObjectURL(cmpImg)
    setPreviewUrl(null); setCmpImg(null); setDrawer(null); setBoxesData(null); setSelBox(-1)
  }
  const save = async () => {
    if (!drawer) return
    try {
      await api.put(`/archives/${drawer.id}`, edit)
      antdMessage.success('编目已保存'); closeDrawer(); setPage(1); load()
    } catch { antdMessage.error('保存失败') }
  }
  const setStatus = async (s: string) => {
    if (!drawer) return
    try {
      await api.put(`/archives/${drawer.id}`, { status: s })
      setDrawer({ ...drawer, status: s }); setEdit({ ...edit, status: s }); setPage(1); load()
      antdMessage.success(`状态已置为「${s}」`)
    } catch { antdMessage.error('更新失败') }
  }
  const copyText = () => {
    if (!drawer) return
    navigator.clipboard?.writeText(drawer.ocr_text).then(
      () => antdMessage.success('OCR 正文已复制'),
      () => antdMessage.error('复制失败'))
  }
  const download = () => {
    if (previewUrl && drawer) {
      const a = document.createElement('a')
      a.href = previewUrl; a.download = drawer.file_name || `原件.${drawer.file_type}`
      a.click()
    }
  }
  const exportXlsx = async () => {
    try {
      const { data } = await api.get('/archives/export', { responseType: 'blob' })
      const url = URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = url; a.download = `档案目录_${new Date().toISOString().slice(0, 10)}.xlsx`
      a.click(); URL.revokeObjectURL(url)
      antdMessage.success('目录已导出')
    } catch { antdMessage.error('导出失败') }
  }

  const curPage: BoxPage | undefined = boxesData?.pages?.[viewPage - 1]
  const showCmp = !!drawer?.has_boxes && !!boxesData && !!cmpImg && !!curPage

  const cols = [
    { title: '档号', dataIndex: 'archive_no', key: 'no', width: 120 },
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    { title: '来源', dataIndex: 'source', key: 'source', width: 90, ellipsis: true },
    { title: '类别', dataIndex: 'category', key: 'category', width: 84,
      render: (c: string) => <Tag color="blue">{c}</Tag> },
    { title: '密级', dataIndex: 'secret_level', key: 'secret', width: 70,
      render: (s: string) => <Tag color={SECRET_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 84,
      render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '原件', dataIndex: 'has_file', key: 'file', width: 70,
      render: (h: boolean, r: Arc) => (
        <Space size={2}>
          {h ? <Button type="link" size="small" icon={r.file_type === 'pdf' ? <FilePdfOutlined /> : <FileImageOutlined />}
            onClick={(e) => { e.stopPropagation(); openDetail(r) }}>预览</Button> : <Typography.Text type="secondary">—</Typography.Text>}
          {r.has_boxes && <Tag color="green" style={{ marginInlineEnd: 0 }}>对照</Tag>}
        </Space>
      ) },
    { title: '页数', dataIndex: 'page_count', key: 'pages', width: 56 },
    { title: '入库人', dataIndex: 'uploaded_by_name', key: 'by', width: 84 },
    { title: '时间', dataIndex: 'created_at', key: 't', width: 120 },
    { title: '操作', key: 'op', width: 64,
      render: (_: any, r: Arc) => <Button type="link" onClick={(e) => { e.stopPropagation(); openDetail(r) }}>查看</Button> },
  ]

  return (
    <div className="page-wrap">
      <PageHeader icon={<DatabaseOutlined />} title="档案数字化 OCR 入库"
        subtitle="扫描件（图片/PDF）识别文字后入库 · 原件可回溯 · 原文对照 · 检索编目 · 目录导出" />

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card><Statistic title="入库档案" value={stats?.total || 0} suffix="件" /></Card></Col>
        <Col span={6}><Card><Statistic title="OCR 文字量" value={stats?.total_chars || 0} suffix="字" /></Card></Col>
        <Col span={6}><Card><Statistic title="扫描页数" value={stats?.total_pages || 0} suffix="页" /></Card></Col>
        <Col span={6}><Card><Statistic title="待编目" value={stats?.by_status?.['待编目'] || 0} suffix="件" /></Card></Col>
      </Row>

      {stats?.by_category && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space wrap size={[6, 6]}>
            <Typography.Text type="secondary">类别分布：</Typography.Text>
            {CATS.map((c) => (<Tag key={c}>{c} {stats.by_category[c] || 0}</Tag>))}
            <Typography.Text type="secondary" style={{ marginLeft: 8 }}>密级：</Typography.Text>
            {SECRET_LEVELS.map((c) => (<Tag key={c} color={SECRET_COLOR[c]}>{c} {stats.by_secret?.[c] || 0}</Tag>))}
          </Space>
        </Card>
      )}

      <Card title="扫描件批量入库" style={{ marginBottom: 16 }}>
        <Space wrap size={12} align="center">
          <Upload multiple beforeUpload={(f) => { setFiles((p) => [...p, f as unknown as File]); return false }} showUploadList={false}>
            <Button icon={<UploadOutlined />}>{files.length ? `已选 ${files.length} 件` : '选择文件（可多选）'}</Button>
          </Upload>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="标题前缀（留空用文件名）" style={{ width: 180 }} />
          <Input value={source} onChange={(e) => setSource(e.target.value)} placeholder="来源/移交单位" style={{ width: 140 }} />
          <Select value={category} onChange={setCategory} style={{ width: 120 }} options={CATS.map((c) => ({ label: c, value: c }))} />
          <Select value={secretLevel} onChange={setSecretLevel} style={{ width: 100 }} options={SECRET_LEVELS.map((c) => ({ label: `密级·${c}`, value: c }))} />
          <Select value={retention} onChange={setRetention} style={{ width: 100 }} options={RETENTIONS.map((c) => ({ label: `保管·${c}`, value: c }))} />
          <Button type="primary" icon={<InboxOutlined />} loading={ingesting} onClick={ingestAll}>OCR 批量入库</Button>
        </Space>
        {files.length > 0 && (
          <div style={{ marginTop: 10 }}>
            {files.map((f, i) => (
              <Tag key={i} closable onClose={() => removeFile(i)} style={{ marginBottom: 4 }}>
                {f.name}（{(f.size / 1024).toFixed(0)} KB）
              </Tag>
            ))}
            {ingesting && <Progress percent={Math.round((progress.done / (progress.total || 1)) * 100)}
              status="active" style={{ marginTop: 6 }}
              format={() => `正在处理 ${progress.done}/${progress.total}：${progress.cur}`} />}
          </div>
        )}
      </Card>

      <Card title="档案检索" extra={<Button icon={<ExportOutlined />} onClick={exportXlsx}>导出目录</Button>}>
        <Space wrap size={8} style={{ marginBottom: 12 }}>
          <Input.Search value={q} onChange={(e) => setQ(e.target.value)} onSearch={() => { setPage(1); load() }}
            placeholder="搜索档号/标题/来源/正文" style={{ width: 260 }} allowClear />
          <Select value={fCat} onChange={(v) => { setFCat(v || ''); setPage(1) }} style={{ width: 120 }} allowClear placeholder="类别"
            options={CATS.map((c) => ({ label: c, value: c }))} />
          <Select value={fStatus} onChange={(v) => { setFStatus(v || ''); setPage(1) }} style={{ width: 120 }} allowClear placeholder="状态"
            options={STATUSES.map((s) => ({ label: s, value: s }))} />
        </Space>
        <Table rowKey="id" size="small" columns={cols} dataSource={list} pagination={{
          current: page, pageSize: perPage, total, showSizeChanger: true, showTotal: (t) => `共 ${t} 件`,
          onChange: (p, ps) => { setPage(p); setPerPage(ps) },
        }}
          onRow={(r) => ({ onClick: () => openDetail(r as Arc), style: { cursor: 'pointer' } })} />
      </Card>

      <Drawer title={drawer ? `档案详情 · ${drawer.archive_no}` : ''} width={720}
        open={!!drawer} onClose={closeDrawer}>
        {drawer && (
          <>
            <Descriptions column={1} size="small" bordered style={{ marginBottom: 12 }}>
              <Descriptions.Item label="标题">
                <Input value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} />
              </Descriptions.Item>
              <Descriptions.Item label="来源">
                <Input value={edit.source} onChange={(e) => setEdit({ ...edit, source: e.target.value })} />
              </Descriptions.Item>
              <Descriptions.Item label="类别">
                <Select value={edit.category} onChange={(v) => setEdit({ ...edit, category: v })}
                  style={{ width: '100%' }} options={CATS.map((c) => ({ label: c, value: c }))} />
              </Descriptions.Item>
              <Descriptions.Item label="密级 / 保管期限">
                <Space>
                  <Select value={edit.secret_level} onChange={(v) => setEdit({ ...edit, secret_level: v })} style={{ width: 110 }}
                    options={SECRET_LEVELS.map((c) => ({ label: c, value: c }))} />
                  <Select value={edit.retention} onChange={(v) => setEdit({ ...edit, retention: v })} style={{ width: 100 }}
                    options={RETENTIONS.map((c) => ({ label: c, value: c }))} />
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Space wrap>
                  {STATUSES.map((s) => (
                    <Tag key={s} color={STATUS_COLOR[s]} style={{ cursor: 'pointer' }}
                      onClick={() => setStatus(s)}>{drawer.status === s ? `● ${s}` : s}</Tag>
                  ))}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="页数/字数/类型">
                {drawer.page_count} 页 · {drawer.char_count} 字 · {drawer.file_type || '—'}
                {drawer.has_boxes && <Tag color="green" style={{ marginLeft: 8 }}>支持原文对照</Tag>}
              </Descriptions.Item>
            </Descriptions>

            {showCmp ? (
              <>
                <Typography.Text strong>原文对照（点右栏文字行 / 左图框互高亮）</Typography.Text>
                {drawer.file_type === 'pdf' && boxesData!.pages.length > 1 && (
                  <Space style={{ margin: '6px 0' }}>
                    <Button size="small" disabled={viewPage <= 1} onClick={() => setViewPage((p) => p - 1)}>上一页</Button>
                    <Typography.Text>{viewPage} / {boxesData!.pages.length}</Typography.Text>
                    <Button size="small" disabled={viewPage >= boxesData!.pages.length} onClick={() => setViewPage((p) => p + 1)}>下一页</Button>
                  </Space>
                )}
                <div style={{ position: 'relative', marginTop: 8, border: '1px solid #e9edf3', borderRadius: 6, overflow: 'hidden', background: '#000' }}>
                  <img src={cmpImg!} alt="原件" style={{ width: '100%', display: 'block', opacity: 0.96 }} />
                  <svg viewBox="0 0 100 100" preserveAspectRatio="none"
                    style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'auto' }}>
                    {curPage!.boxes.map((b, i) => {
                      const xs = b.box.map((p) => p[0] * 100); const ys = b.box.map((p) => p[1] * 100)
                      const x = Math.min(...xs); const y = Math.min(...ys)
                      const w = Math.max(...xs) - x; const h = Math.max(...ys) - y
                      const sel = i === selBox
                      return (<rect key={i} x={x} y={y} width={w} height={h}
                        fill={sel ? 'rgba(250,84,28,0.20)' : 'rgba(22,119,255,0.14)'}
                        stroke={sel ? '#c2410c' : '#1d4ed8'} strokeWidth={sel ? 1 : 0.5}
                        style={{ cursor: 'pointer' }} onClick={() => setSelBox(i)} />)
                    })}
                  </svg>
                </div>
                <div style={{ marginTop: 8, maxHeight: 220, overflow: 'auto', border: '1px solid #eef1f5', borderRadius: 6, padding: 4 }}>
                  {curPage!.boxes.map((b, i) => (
                    <div key={i} onClick={() => setSelBox(i)} style={{
                      padding: '4px 8px', borderRadius: 4, cursor: 'pointer', fontSize: 13, lineHeight: 1.8,
                      background: i === selBox ? '#fff7ed' : 'transparent',
                    }}>{b.text}</div>
                  ))}
                </div>
                {drawer.has_file && <Button icon={<DownloadOutlined />} style={{ marginTop: 8 }} onClick={download}>下载原件</Button>}
              </>
            ) : (
              <>
                <Typography.Text strong>扫描原件</Typography.Text>
                <div style={{ marginTop: 8, marginBottom: 8 }}>
                  {!drawer.has_file && <Typography.Text type="secondary">无原件（仅文字入库）</Typography.Text>}
                  {drawer.has_file && previewKind === 'image' && previewUrl && (
                    <img src={previewUrl} alt="原件" style={{ maxWidth: '100%', border: '1px solid #e9edf3', borderRadius: 6 }} />
                  )}
                  {drawer.has_file && previewKind === 'pdf' && previewUrl && (
                    <iframe src={previewUrl} title="原件PDF" style={{ width: '100%', height: 420, border: '1px solid #e9edf3', borderRadius: 6 }} />
                  )}
                  {drawer.has_file && previewKind === 'other' && <Typography.Text>该类型仅支持下载查看。</Typography.Text>}
                  {drawer.has_file && <div style={{ marginTop: 8 }}><Button icon={<DownloadOutlined />} onClick={download}>下载原件</Button></div>}
                </div>
              </>
            )}

            <Space style={{ margin: '10px 0 6px' }}>
              <Typography.Text strong>OCR 正文</Typography.Text>
              {q && <Typography.Text type="secondary">（已高亮检索词）</Typography.Text>}
              <Button size="small" icon={<CopyOutlined />} onClick={copyText}>复制</Button>
            </Space>
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', background: '#f8fafc', padding: 12,
              borderRadius: 8, marginTop: 4, lineHeight: 1.8, maxHeight: 280, overflow: 'auto' }}>
              {hl(drawer.ocr_text, q)}
            </Typography.Paragraph>

            <Popconfirm title="保存编目修改？" onConfirm={save}>
              <Button type="primary" icon={<SaveOutlined />}>保存编目</Button>
            </Popconfirm>
          </>
        )}
      </Drawer>
    </div>
  )
}
