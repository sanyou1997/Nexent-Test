import { useEffect, useRef, useState } from 'react'
import {
  Card, Button, message, Typography, Tag, Space, Empty, Alert, Spin, Upload,
  Tooltip, Segmented, Progress, Switch,
} from 'antd'
import {
  FileImageOutlined, FilePdfOutlined, CopyOutlined, DownloadOutlined, UploadOutlined,
  DatabaseOutlined, FieldTimeOutlined, FontSizeOutlined, BlockOutlined,
  SwapOutlined, HighlightOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'

interface Box { box: [number, number][]; text: string; score: number }
interface PageData {
  page: number; text: string; boxes: Box[]; ocr: boolean
  page_image: string | null; width: number; height: number
}

interface OcrPanelProps {
  accept?: 'image' | 'all'        // 'all' 同时支持 图片 + PDF
  onInsert?: (text: string) => void   // 提供时显示"填入"按钮（信访登记等场景）
  insertLabel?: string
}

export default function OcrPanel({ accept = 'all', onInsert, insertLabel = '填入登记内容' }: OcrPanelProps) {
  const [loading, setLoading] = useState(false)
  const [errTip, setErrTip] = useState('')
  const [source, setSource] = useState<'image' | 'pdf' | null>(null)
  const [objUrl, setObjUrl] = useState('')      // 图片源：本地预览
  const [pages, setPages] = useState<PageData[]>([])
  const [engine, setEngine] = useState('')
  const [note, setNote] = useState('')
  const [latency, setLatency] = useState(0)
  const [curPage, setCurPage] = useState(0)
  const [selIdx, setSelIdx] = useState(-1)      // 原文对照：当前高亮行/框
  const [view, setView] = useState<'text' | 'compare'>('compare')
  const [enhanced, setEnhanced] = useState(true)   // 手写增强预处理（去噪/二值化/纠偏 + 纪检纠错）
  const objRef = useRef<string>('')
  const imgDims = useRef<{ w: number; h: number }>({ w: 0, h: 0 })

  const reset = () => {
    if (objRef.current) URL.revokeObjectURL(objRef.current)
    objRef.current = ''
    setObjUrl(''); setPages([]); setSource(null); setEngine(''); setNote(''); setLatency(0)
    setCurPage(0); setSelIdx(-1); setErrTip('')
  }

  useEffect(() => () => { if (objRef.current) URL.revokeObjectURL(objRef.current) }, [])

  const doOcr = async (file: File) => {
    setLoading(true); setErrTip('')
    const isPdf = file.name.toLowerCase().endsWith('.pdf')
    if (accept === 'image' && isPdf) {
      message.error('当前入口仅支持图片，请使用 PDF 解析入口'); setLoading(false); return
    }
    const url = URL.createObjectURL(file)
    objRef.current = url
    setObjUrl(url); setSelIdx(-1); setCurPage(0)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const qs = enhanced ? '?preprocess=1&correct=1' : '?preprocess=0&correct=0'
      const { data } = await api.post('/ocr' + qs, fd, { timeout: 180000 })
      if (data?.mode !== 'ocr') throw new Error('返回格式异常')
      setPages(data.pages || [])
      setSource(data.source === 'pdf' ? 'pdf' : 'image')
      setEngine(data.engine || '')
      setNote(data.note || '')
      setLatency(data.latency_ms || 0)
      const total = (data.pages || []).reduce((s: number, p: PageData) => s + (p.boxes || []).length, 0)
      if (total === 0 && (data.pages || []).every((p: PageData) => !p.text.trim())) {
        message.warning('未识别出文字，可尝试更清晰的图片或文本版 PDF')
      } else {
        message.success(`识别完成：${(data.pages || []).length} 页，共 ${total} 个识别区域`)
      }
    } catch (e: any) {
      const code = e?.response?.status
      const detail = errMsg(e)
      if (code === 501) setErrTip('OCR 引擎未安装：请在后端执行 pip install rapidocr-onnxruntime 后重启服务。')
      else if (code === 400) setErrTip(detail)
      else if (code === 422) setErrTip(`解析失败：${detail}`)
      else setErrTip(detail || '识别失败')
      message.error(detail)
      reset()
    } finally {
      setLoading(false)
    }
  }

  const beforeUpload = (file: File) => {
    if (accept === 'image' && !file.type.startsWith('image/')) { message.error('仅支持图片文件'); return Upload.LIST_IGNORE }
    const max = file.name.toLowerCase().endsWith('.pdf') ? 30 : 15
    if (file.size > max * 1024 * 1024) { message.error(`文件超过 ${max}MB 限制`); return Upload.LIST_IGNORE }
    doOcr(file)
    return false
  }

  const allText = pages.map((p) => p.text).join('\n\n')
  const copy = () => { if (!allText) return; navigator.clipboard?.writeText(allText).then(() => message.success('已复制识别文本')) }
  const download = () => {
    if (!allText) return
    const body = `OCR 识别结果\n引擎：${engine}\n耗时：${latency}ms\n页数：${pages.length}\n字数：${[...allText].length}\n\n${allText}\n`
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain;charset=utf-8' }))
    const a = document.createElement('a'); a.href = url
    a.download = `OCR识别结果-${new Date().toLocaleDateString('zh-CN')}.txt`; a.click(); URL.revokeObjectURL(url)
  }

  const cur = pages[curPage] || null
  const displayImg = source === 'image' ? objUrl : (cur?.page_image || '')
  const dims = source === 'image' ? imgDims.current : { w: cur?.width || 0, h: cur?.height || 0 }
  const showOverlay = !!cur && cur.boxes.length > 0 && !!displayImg && dims.w > 0
  // 原文对照的行：有识别框则用框，否则按文本换行（无框联动）
  const lines: { text: string; idx: number }[] = cur
    ? (cur.boxes.length
        ? cur.boxes.map((b, i) => ({ text: b.text, idx: i }))
        : cur.text.split('\n').map((t, i) => ({ text: t, idx: -1 })))
    : []

  return (
    <div>
      {errTip && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={errTip} />}

      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap>
          <Upload accept={accept === 'all' ? 'image/*,.jpg,.jpeg,.png,.bmp,.pdf' : 'image/*,.jpg,.jpeg,.png,.bmp'}
            showUploadList={false} beforeUpload={beforeUpload}>
            <Button type="primary" icon={<UploadOutlined />} loading={loading}>
              {accept === 'all' ? '上传图片 / PDF' : '上传图片'}
            </Button>
          </Upload>
          <Tooltip title="开启后先做去噪/二值化/倾斜纠偏再识别，并套用纪检纠错词典；对手写与拍照材料识别率提升明显。关闭可对比原链路。">
            <Space size={6}>
              <Switch size="small" checked={enhanced} onChange={setEnhanced} />
              <Typography.Text style={{ fontSize: 13 }}>手写增强</Typography.Text>
            </Space>
          </Tooltip>
          {source === 'pdf' && pages.length > 1 && (
            <Space.Compact>
              <Button disabled={curPage === 0} onClick={() => { setCurPage(0); setSelIdx(-1) }}>首页</Button>
              <Button disabled={curPage === 0} icon={<span>‹</span>} onClick={() => { setCurPage((p) => Math.max(0, p - 1)); setSelIdx(-1) }}>上一页</Button>
              <Button disabled={curPage >= pages.length - 1} onClick={() => { setCurPage((p) => Math.min(pages.length - 1, p + 1)); setSelIdx(-1) }}>下一页›</Button>
              <Button disabled={curPage >= pages.length - 1} onClick={() => { setCurPage(pages.length - 1); setSelIdx(-1) }}>末页</Button>
            </Space.Compact>
          )}
          {source && (
            <Tooltip title={accept === 'all' ? '本机轻量模型识别，离线不占大模型资源' : '本机轻量模型识别，离线不占大模型资源'}>
              <Tag icon={<DatabaseOutlined />} color="green">{engine || '离线·小模型'}</Tag>
            </Tooltip>
          )}
          {note && <Tag color="blue">{note}</Tag>}
          {source && (
            <Button icon={<CopyOutlined />} disabled={!allText} onClick={copy}>复制</Button>
          )}
          {source && (
            <Button icon={<DownloadOutlined />} disabled={!allText} onClick={download}>下载 .txt</Button>
          )}
          {onInsert && source && (
            <Button type="primary" ghost icon={<HighlightOutlined />} disabled={!allText} onClick={() => onInsert(allText)}>
              {insertLabel}
            </Button>
          )}
        </Space>
        {source === 'pdf' && pages.length > 1 && (
          <div style={{ marginTop: 8, color: '#64748b', fontSize: 12 }}>
            当前第 {curPage + 1} / {pages.length} 页
            {cur?.ocr ? '（扫描页 · OCR 识别）' : '（数字文本抽取）'}
          </div>
        )}
      </Card>

      {!source ? (
        <Empty description={accept === 'all'
          ? '上传一张含文字的图片或扫描件 PDF，本机离线识别'
          : '上传一张含文字的图片，本机离线识别'} style={{ padding: '40px 0' }} />
      ) : (
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          {/* 左：原图 / 页面 + 识别框叠加 */}
          <Card title={<Space>{source === 'pdf' ? <FilePdfOutlined /> : <FileImageOutlined />}原图与识别区域</Space>}
            style={{ flex: '1 1 460px', minWidth: 340 }}>
            {displayImg ? (
              <div style={{ position: 'relative', background: '#000', borderRadius: 8, overflow: 'hidden' }}>
                <img src={displayImg} alt="待识别" style={{ width: '100%', display: 'block', opacity: 0.96 }}
                  onLoad={(e) => { if (source === 'image') { const el = e.currentTarget; imgDims.current = { w: el.naturalWidth, h: el.naturalHeight } } }} />
                {showOverlay && (
                  <svg viewBox={`0 0 ${dims.w} ${dims.h}`}
                    style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'auto', cursor: 'pointer' }}>
                    {cur!.boxes.map((b, i) => {
                      const xs = b.box.map((p) => p[0]); const ys = b.box.map((p) => p[1])
                      const x = Math.min(...xs); const y = Math.min(...ys)
                      const w = Math.max(...xs) - x; const h = Math.max(...ys) - y
                      const sel = i === selIdx
                      return (
                        <rect key={i} x={x} y={y} width={w} height={h}
                          fill={sel ? 'rgba(250,84,28,0.18)' : 'rgba(22,119,255,0.12)'}
                          stroke={sel ? '#c2410c' : '#1d4ed8'}
                          strokeWidth={sel ? Math.max(3, dims.w / 260) : Math.max(2, dims.w / 400)}
                          onClick={() => setSelIdx(i === selIdx ? -1 : i)} />
                      )
                    })}
                  </svg>
                )}
                {loading && (
                  <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,.45)' }}>
                    <Spin tip="识别中…"><div style={{ width: 1, height: 1 }} /></Spin>
                  </div>
                )}
              </div>
            ) : (
              <Alert type="info" showIcon message="本页为数字文本抽取页（无可定位图片），右侧为抽取文本" />
            )}
            {showOverlay && (
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
                提示：点击右侧任一行，左侧对应识别框会高亮；点击图片中的框也可联动右侧。
              </Typography.Paragraph>
            )}
          </Card>

          {/* 右：识别文本 / 原文对照 */}
          <Card title={<Space><SwapOutlined />识别结果</Space>} style={{ flex: '1 1 380px', minWidth: 320 }}
            extra={
              <Segmented value={view} onChange={(v) => setView(v as any)}
                options={[{ label: '原文对照', value: 'compare' }, { label: '纯文本', value: 'text' }]} />
            }>
            <Space wrap style={{ marginBottom: 12 }}>
              <Tag icon={<FontSizeOutlined />} color="blue">字数 {[...allText].length}</Tag>
              <Tag icon={<BlockOutlined />} color="cyan">识别框 {(cur?.boxes.length) || 0}</Tag>
              {latency > 0 && <Tooltip title="本机推理耗时"><Tag icon={<FieldTimeOutlined />} color="default">{latency} ms</Tag></Tooltip>}
            </Space>

            {view === 'text' ? (
              <div style={{ minHeight: 200, maxHeight: 460, overflowY: 'auto', padding: 12, background: '#f8fafc', borderRadius: 8, border: '1px solid #eef1f5', whiteSpace: 'pre-wrap', lineHeight: 1.8, fontSize: 14, userSelect: 'text' }}>
                {allText || <Typography.Text type="secondary">识别结果将显示在此，可全选复制或下载。</Typography.Text>}
              </div>
            ) : (
              <div style={{ maxHeight: 460, overflowY: 'auto' }}>
                {lines.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无识别文本" />}
                {lines.map((ln, i) => {
                  const sel = ln.idx >= 0 && ln.idx === selIdx
                  return (
                    <div key={i} onClick={() => ln.idx >= 0 && setSelIdx(ln.idx === selIdx ? -1 : ln.idx)}
                      style={{
                        display: 'flex', gap: 8, padding: '7px 8px', cursor: ln.idx >= 0 ? 'pointer' : 'default',
                        borderBottom: '1px dashed #eef1f5', borderRadius: 4, lineHeight: 1.8, fontSize: 14,
                        background: sel ? '#fef2f2' : undefined,
                        boxShadow: sel ? 'inset 3px 0 0 #c2410c' : undefined, transition: 'background .15s',
                      }}>
                      <Tag color={sel ? 'error' : 'blue'} style={{ height: 'fit-content', flexShrink: 0 }}>
                        {ln.idx >= 0 ? ln.idx + 1 : '·'}
                      </Tag>
                      <span>{ln.text || ' '}</span>
                    </div>
                  )
                })}
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
