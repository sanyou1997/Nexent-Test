import { useEffect, useRef, useState } from 'react'
import {
  Card, Table, Button, Drawer, Tag, Space, message, Typography, Input,
  Popconfirm, Upload, Alert, Descriptions, Tooltip, Empty, Modal, Badge,
  Select, InputNumber,
} from 'antd'
import {
  AudioOutlined, StopOutlined, LoadingOutlined, UploadOutlined,
  FileTextOutlined, DeleteOutlined, DownloadOutlined, SoundOutlined,
  CopyOutlined, ClearOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { useRealtimeASR } from '../hooks/useRealtimeASR'
import PageHeader from '../components/PageHeader'

interface Segment { start: number; end: number; text: string; ts: string; speaker?: number }
interface Rec {
  id: number; filename: string; duration_s: number; language: string
  status: string; error: string; segments: Segment[]; text: string
  lang?: string; speaker_count?: number; speaker_names?: Record<number, string>
  minutes?: string; minutes_status?: string; minutes_model?: string; minutes_error?: string
  has_audio?: boolean
  created_by: string; created_at: string
}

// 说话人配色（启发式分离，按索引取色）
const SPEAKER_COLORS = ['#1d4ed8', '#b91c1c', '#15803d', '#b45309', '#4f46e5', '#08979c']
function spkColor(i: number) {
  return SPEAKER_COLORS[((i || 0) % SPEAKER_COLORS.length + SPEAKER_COLORS.length) % SPEAKER_COLORS.length]
}
const LANG_LABELS: Record<string, string> = { zh: '中文', yue: '粤语', en: '英文', auto: '自动检测' }
const langLabel = (l?: string) => LANG_LABELS[l || 'zh'] || l || '中文'

// 关键词高亮：纪检监察谈话/会议常见关注点（可自定义增删）
const DEFAULT_KEYWORDS = ['受贿', '礼金', '礼卡', '消费卡', '宴请', '工程项目', '招标',
  '打招呼', '请托', '回扣', '好处费', '报销', '挪用', '私分', '串供', '特定关系人',
  '干股', '违规', '违纪', '隐瞒', '旅游']

function fmtDur(s: number) {
  const m = Math.floor(s / 60), ss = Math.floor(s % 60)
  return `${m}分${ss}秒`
}

function fmtSec(s: number) {
  const m = Math.floor(s / 60), ss = Math.floor(s % 60)
  return `${m}:${String(ss).padStart(2, '0')}`
}

/** 文本关键词高亮渲染 */
function highlight(text: string, kws: string[]) {
  if (!kws.length) return <span>{text}</span>
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(`(${kws.filter(Boolean).map(esc).join('|')})`, 'g')
  const parts = text.split(re)
  return <>{parts.map((p, i) => (kws.includes(p)
    ? <mark key={i} className="kw-hit">{p}</mark>
    : <span key={i}>{p}</span>))}</>
}

export default function Transcripts() {
  const [rows, setRows] = useState<Rec[]>([])
  const [detail, setDetail] = useState<Rec | null>(null)
  const [editText, setEditText] = useState('')
  const [uploading, setUploading] = useState(false)
  const [recording, setRecording] = useState(false)
  const [recSec, setRecSec] = useState(0)
  const [liveOpen, setLiveOpen] = useState(false)
  // ---- ASR 深化：语种 / 说话人数 / 纪要 ----
  const [lang, setLang] = useState('zh')
  const [spkCount, setSpkCount] = useState(2)
  const [minutesText, setMinutesText] = useState('')
  const [genMinutes, setGenMinutes] = useState(false)
  const [savingMinutes, setSavingMinutes] = useState(false)
  const [minutesTouched, setMinutesTouched] = useState(false)
  const [spkEdit, setSpkEdit] = useState<Record<number, string>>({})

  const spkLabel = (i?: number) => {
    const idx = i || 0
    const names = detail?.speaker_names || {}
    return names[idx] || `说话人${String.fromCharCode(65 + idx)}`
  }
  const mediaRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<number | null>(null)
  const live = useRealtimeASR((m) => message.error(m, 6))

  // ---- 音文对照状态 ----
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [audioUrl, setAudioUrl] = useState('')
  const [curSeg, setCurSeg] = useState(-1)
  const [kws, setKws] = useState<string[]>(DEFAULT_KEYWORDS)
  const [kwInput, setKwInput] = useState('')
  const segRefs = useRef<(HTMLDivElement | null)[]>([])

  // 打开记录时加载本机音频（带鉴权拉取为 blob，供音文对照回放）
  useEffect(() => {
    setAudioUrl(''); setCurSeg(-1)
    if (!detail?.has_audio) return
    let revoked = false
    api.get(`/transcripts/${detail.id}/audio`, { responseType: 'blob', timeout: 120000 })
      .then(({ data }) => { if (!revoked) setAudioUrl(URL.createObjectURL(data)) })
      .catch(() => message.warning('音频文件不可用，仅显示文本'))
    return () => { revoked = true }
  }, [detail?.id, detail?.has_audio])

  const seek = (sec: number, play = true, idx?: number) => {
    const a = audioRef.current
    if (a) {
      a.currentTime = sec
      if (play) a.play().catch(() => {})
    }
    if (idx != null) {
      setCurSeg(idx)
      segRefs.current[idx]?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  }

  // 关键词命中：定位到段 + 时间点
  const hits = detail
    ? detail.segments.flatMap((s, i) => kws.filter((k) => k && s.text.includes(k))
      .map((k) => ({ word: k, idx: i, ts: s.ts, start: s.start })))
    : []

  const load = async () => {
    const { data } = await api.get('/transcripts')
    setRows(data)
    // 函数式更新：抽屉/对照面板关闭后（prev 为 null）不会被轮询重新打开
    setDetail((prev) => prev ? (data.find((r: Rec) => r.id === prev.id) || prev) : prev)
    return data
  }
  // 轮询仅在存在"转录中"任务时进行；依赖置空避免反复重建定时器
  const rowsRef = useRef<Rec[]>([])
  useEffect(() => { rowsRef.current = rows }, [rows])
  useEffect(() => {
    load()
    const t = setInterval(() => {
      if (rowsRef.current.some((r) => r.status === '转录中' || r.minutes_status === '生成中')) load()
    }, 4000)
    return () => clearInterval(t)
  }, [])

  const submitAudio = async (blob: Blob, filename: string) => {
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', new File([blob], filename, { type: blob.type }))
      fd.append('lang', lang)
      fd.append('speaker_count', String(spkCount))
      const { data } = await api.post('/transcripts/upload', fd)
      message.success(`已提交转录（任务 #${data.id}），完成后自动刷新`)
      await load()
    } catch (e) { message.error(errMsg(e)) } finally { setUploading(false) }
  }

  const uploadFile = async (file: File) => {
    await submitAudio(file, file.name)
    return false
  }

  const startRec = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunksRef.current = []
      mr.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data) }
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || 'audio/webm' })
        if (blob.size < 5000) { setRecording(false); message.warning('录音太短'); return }
        setRecording(false)
        await submitAudio(blob, `现场录音-${new Date().toLocaleString('zh-CN').replace(/[/: ]/g, '-')}.webm`)
      }
      mediaRef.current = mr
      mr.start()
      setRecording(true); setRecSec(0)
      timerRef.current = window.setInterval(() => setRecSec((s) => s + 1), 1000)
    } catch { message.error('无法访问麦克风，请检查浏览器权限（需通过 localhost 访问）') }
  }

  const save = async () => {
    if (!detail) return
    try {
      const { data } = await api.put(`/transcripts/${detail.id}`, { text: editText })
      setDetail(data); message.success('校对结果已保存（留痕）')
      load()
    } catch (e) { message.error(errMsg(e)) }
  }

  // ---- 会议纪要 ----
  const genMeeting = async () => {
    if (!detail) return
    setGenMinutes(true); setMinutesTouched(false)
    try {
      await api.post(`/transcripts/${detail.id}/minutes`)
      message.success('纪要生成中（本机/在线模型），完成后自动刷新')
    } catch (e) { message.error(errMsg(e)); setGenMinutes(false) }
  }
  const saveMinutes = async () => {
    if (!detail) return
    setSavingMinutes(true)
    try {
      await api.put(`/transcripts/${detail.id}/minutes`, { minutes: minutesText })
      message.success('纪要已保存（留痕）')
      setMinutesTouched(false)
      load()
    } catch (e) { message.error(errMsg(e)) } finally { setSavingMinutes(false) }
  }
  // 生成完成（status 由轮询刷新）且用户未手动编辑时，自动载入纪要文本
  useEffect(() => {
    if (detail && detail.minutes_status === '完成' && !minutesTouched) {
      setMinutesText(detail.minutes || '')
      setGenMinutes(false)
    }
    if (detail && detail.minutes_status === '失败') setGenMinutes(false)
  }, [detail?.id, detail?.minutes_status, detail?.minutes, minutesTouched])

  // ---- 说话人重命名 ----
  const renameSpeakers = async () => {
    if (!detail) return
    try {
      const names = { ...(detail.speaker_names || {}), ...spkEdit }
      await api.put(`/transcripts/${detail.id}/speakers`, { speaker_names: names })
      message.success('说话人名称已更新')
      setDetail((d) => (d ? { ...d, speaker_names: names } : d))
      setSpkEdit({})
      load()
    } catch (e) { message.error(errMsg(e)) }
  }

  const exportTxt = () => {
    if (!detail) return
    const segs = detail.segments.map((s) => `[${s.ts}] ${spkLabel(s.speaker)}：${s.text}`).join('\n')
    const minutes = detail.minutes ? `\n\n== 会议纪要 ==\n${detail.minutes}\n` : ''
    const body = `转录记录：${detail.filename}\n时长：${fmtDur(detail.duration_s)}\n语种：${detail.lang || 'zh'}\n生成时间：${detail.created_at}\n\n== 分段原文（机器转录·说话人分离为启发式标注） ==\n${segs}\n\n== 校对后全文 ==\n${detail.text}\n${minutes}`
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${(detail.filename || 'transcript').replace(/\.[^.]+$/, '')}-转录.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const liveText = live.segments.map((s) => s.text).join('')

  const exportLive = () => {
    const body = live.segments.map((s) => `[${s.ts}] ${s.text}`).join('\n')
    const url = URL.createObjectURL(new Blob([body || '（无识别结果）'], { type: 'text/plain;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `实时转录-${new Date().toLocaleDateString('zh-CN')}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  // 实时转录弹窗关闭时自动停止录音 —— 弹窗永远可关闭
  const closeLive = () => {
    if (live.recording) live.stop()
    setLiveOpen(false)
  }

  const columns = [
    { title: '文件名', dataIndex: 'filename',
      render: (t: string, r: Rec) => <a onClick={() => { setDetail(r); setEditText(r.text); setMinutesText(r.minutes || ''); setMinutesTouched(false); setSpkEdit({}) }}>
        <SoundOutlined style={{ marginRight: 6 }} />{t}</a> },
    { title: '时长', dataIndex: 'duration_s', width: 100,
      render: (s: number) => s ? fmtDur(s) : '-' },
    { title: '分段数', width: 90, render: (_: any, r: Rec) => r.segments.length || '-' },
    { title: '状态', dataIndex: 'status', width: 100,
      render: (s: string) => s === '完成' ? <Tag color="green">完成</Tag> :
        s === '转录中' ? <Tag color="blue" icon={<LoadingOutlined />}>转录中</Tag> :
        <Tag color="red">{s}</Tag> },
    { title: '转录人', dataIndex: 'created_by', width: 110 },
    { title: '时间', dataIndex: 'created_at', width: 140 },
    { title: '操作', width: 90, render: (_: any, r: Rec) => (
      <Popconfirm title="删除该转录记录？" onConfirm={async () => {
        await api.delete(`/transcripts/${r.id}`); load(); message.success('已删除')
      }}>
        <Button size="small" danger icon={<DeleteOutlined />} />
      </Popconfirm>
    ) },
  ]

  return (
    <>
      <PageHeader
        icon={<AudioOutlined />}
        title="语音转录"
        subtitle="谈话/会议录音 · 复用 Nexent 语音转写服务 · 分段时间戳"
        extra={
          <Space>
            <Button icon={<ThunderboltOutlined />} type="primary" ghost onClick={() => setLiveOpen(true)}>
              实时转录（边说边出字）
            </Button>
            {recording ? (
              <>
                <Tag color="red" icon={<LoadingOutlined />}>录音中 {Math.floor(recSec / 60)}:{String(recSec % 60).padStart(2, '0')}</Tag>
                <Tooltip title="停止并提交转录">
                  <Button danger icon={<StopOutlined />} onClick={() => mediaRef.current?.stop()}
                    style={{ animation: 'pulse-red 1.2s infinite' }}>停止</Button>
                </Tooltip>
              </>
            ) : (
              <Button icon={<AudioOutlined />} loading={uploading} onClick={startRec}>整段录音</Button>
            )}
            <Select value={lang} onChange={(v: string) => setLang(v)} style={{ width: 116 }}
              options={[
                { value: 'zh', label: '中文' }, { value: 'yue', label: '粤语' },
                { value: 'en', label: '英文' }, { value: 'auto', label: '自动检测' },
              ]} />
            <Tooltip title="粤语需专粤语模型保障精度；说话人分离为按停顿启发式标注（非声纹识别）">
              <InputNumber min={1} max={6} value={spkCount}
                onChange={(v: number | null) => setSpkCount(v || 2)}
                addonBefore="说话人" style={{ width: 116 }} />
            </Tooltip>
            <Upload accept="audio/*,.wav,.mp3,.m4a,.webm,.amr,.wma" showUploadList={false} beforeUpload={uploadFile}>
              <Button type="primary" icon={<UploadOutlined />} loading={uploading}>上传音频文件</Button>
            </Upload>
          </Space>
        } />
      <Card>
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="实时转录边说边出字；整段录音/上传音频适合长时谈话，转录在本机服务器完成、不外传。"
          description="合规提示：谈话录音转录仅作辅助整理，正式笔录仍须按规范制作并由当事人核对确认。" />
        <Table rowKey="id" size="middle" columns={columns} dataSource={rows}
          pagination={{ pageSize: 10 }}
          locale={{ emptyText: <Empty description="暂无转录记录，上传音频或开始实时转录" /> }} />
      </Card>

      {/* 实时转录弹窗：录音中也可随时关闭，关闭即自动停止 */}
      <Modal
        title={<Space><ThunderboltOutlined style={{ color: '#b45309' }} />实时语音转录（说话停顿即出字）</Space>}
        open={liveOpen}
        onCancel={closeLive}
        footer={[
          <Button key="clear" icon={<ClearOutlined />} onClick={live.clear} disabled={!live.segments.length}>清空</Button>,
          <Button key="copy" icon={<CopyOutlined />} disabled={!liveText}
            onClick={() => { navigator.clipboard?.writeText(liveText).then(() => message.success('已复制全文')) }}>复制全文</Button>,
          <Button key="exp" icon={<DownloadOutlined />} disabled={!live.segments.length} onClick={exportLive}>导出</Button>,
          live.recording
            ? <Button key="stop" danger icon={<StopOutlined />} onClick={live.stop}>停止录音</Button>
            : <Button key="start" type="primary" icon={<AudioOutlined />} onClick={live.start}>开始录音</Button>,
        ]}
        width={720}
        maskClosable={true}
        keyboard={true}
      >
        {live.micError && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={live.micError} />}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12,
          padding: '10px 14px', borderRadius: 8, background: live.recording ? '#fef2f2' : '#f8fafc',
          border: `1px solid ${live.recording ? '#f5c9c9' : '#eef1f5'}`,
        }}>
          <Badge status={live.recording ? 'error' : live.busy ? 'processing' : 'default'}
            text={live.recording ? '正在聆听…（检测到停顿自动出字）' : live.busy ? '识别中…' : '空闲'} />
          <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height: 24, marginLeft: 'auto' }}>
            {Array.from({ length: 18 }).map((_, i) => (
              <div key={i} className={`wave-bar${live.recording ? ' active' : ''}`}
                style={{ animationDelay: `${(i % 6) * 0.12}s` }} />
            ))}
          </div>
        </div>
        <div style={{
          minHeight: 260, maxHeight: 420, overflowY: 'auto', padding: '12px 16px',
          background: '#f8fafc', borderRadius: 8, border: '1px solid #eef1f5',
          fontFamily: 'inherit',
        }}>
          {live.segments.length === 0 && (
            <Typography.Text type="secondary">
              点击「开始录音」后正常说话；系统在您停顿时自动将刚才的话转成文字（首次识别需加载本地模型，约 30 秒）。
            </Typography.Text>
          )}
          {live.segments.map((s, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '6px 0', borderBottom: '1px dashed #e9edf3' }}>
              <Tag color="blue" style={{ height: 'fit-content', flexShrink: 0 }}>{s.ts}</Tag>
              <span>{s.text}</span>
            </div>
          ))}
          {live.busy && <div style={{ color: '#94a3b8', padding: 6 }}><LoadingOutlined /> 识别中…</div>}
        </div>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
          实时转录基于本地 faster-whisper 模型，全程离线，音频不出本机。识别结果仅供辅助，重要内容请人工校对。
        </Typography.Paragraph>
      </Modal>

      <Drawer title={detail ? `音文对照 · ${detail.filename}` : ''} open={!!detail} width={900}
        onClose={() => setDetail(null)}
        extra={detail && (
          <Space>
            <Button icon={<DownloadOutlined />} onClick={exportTxt}>导出 TXT</Button>
            <Button type="primary" onClick={save}>保存校对</Button>
          </Space>
        )}>
        {detail && (
          <>
            <Descriptions column={4} size="small" bordered style={{ marginBottom: 12 }}>
              <Descriptions.Item label="状态">
                {detail.status === '完成' ? <Tag color="green">完成</Tag> :
                  detail.status === '转录中' ? <Tag color="blue" icon={<LoadingOutlined />}>转录中…</Tag> :
                  <Tag color="red">{detail.status}</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="时长">{detail.duration_s ? fmtDur(detail.duration_s) : '-'}</Descriptions.Item>
              <Descriptions.Item label="语种">{langLabel(detail.lang)}</Descriptions.Item>
              <Descriptions.Item label="分段">{detail.segments.length} 段</Descriptions.Item>
              <Descriptions.Item label="说话人">
                {detail.speaker_count && detail.speaker_count >= 2
                  ? `${detail.speaker_count} 人（按停顿启发式分离）` : '未做分离'}
              </Descriptions.Item>
              {detail.minutes_status && (
                <Descriptions.Item label="纪要">
                  {detail.minutes_status === '完成' ? <Tag color="green">已生成</Tag>
                    : detail.minutes_status === '生成中' ? <Tag color="blue" icon={<LoadingOutlined />}>生成中</Tag>
                    : <Tag color="red">{detail.minutes_status}</Tag>}
                </Descriptions.Item>
              )}
            </Descriptions>
            {detail.error && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={detail.error} />}
            {detail.status === '转录中' && (
              <Alert type="info" showIcon style={{ marginBottom: 12 }} icon={<LoadingOutlined />}
                message="后台转录中，完成后自动刷新（长音频约为实时时长的 1/3 ~ 1 倍）" />
            )}

            {/* ---- 播放器：音文对照 ---- */}
            {detail.status === '完成' && (
              <Card size="small" style={{ marginBottom: 12 }}
                title={<Space><SoundOutlined style={{ color: '#1d4ed8' }} />音频回放（点击右侧文本可跳转到对应时间点）</Space>}>
                {audioUrl ? (
                  <audio ref={audioRef} src={audioUrl} controls style={{ width: '100%' }}
                    onTimeUpdate={(e) => {
                      const t = (e.target as HTMLAudioElement).currentTime
                      const i = detail.segments.findIndex((s) => t >= s.start && t < s.end)
                      if (i >= 0 && i !== curSeg) setCurSeg(i)
                    }} />
                ) : (
                  <Typography.Text type="secondary">
                    {detail.has_audio ? '音频加载中…' : '该记录未保存音频（历史记录或实时麦克风转录），仅显示文本'}
                  </Typography.Text>
                )}
              </Card>
            )}

            {/* ---- 关键词设置 ---- */}
            <Card size="small" style={{ marginBottom: 12 }} title="关键词高亮（点击关键词可跳到录音对应位置）">
              <Space wrap size={[4, 6]}>
                {kws.map((k) => (
                  <Tag key={k} closable color="volcano" onClose={() => setKws(kws.filter((x) => x !== k))}>{k}</Tag>
                ))}
              </Space>
              <Space.Compact style={{ marginTop: 8, width: 320 }}>
                <Input placeholder="添加关键词，如：工程项目" value={kwInput}
                  onChange={(e) => setKwInput(e.target.value)}
                  onPressEnter={() => {
                    const v = kwInput.trim()
                    if (v && !kws.includes(v)) setKws([...kws, v])
                    setKwInput('')
                  }} />
                <Button onClick={() => {
                  const v = kwInput.trim()
                  if (v && !kws.includes(v)) setKws([...kws, v])
                  setKwInput('')
                }}>添加</Button>
                <Button onClick={() => setKws(DEFAULT_KEYWORDS)}>恢复默认</Button>
              </Space.Compact>
              {hits.length > 0 && (
                <div style={{ marginTop: 8, maxHeight: 110, overflowY: 'auto' }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    命中 {hits.length} 处：
                  </Typography.Text>
                  <Space wrap size={[4, 4]} style={{ marginTop: 4 }}>
                    {hits.map((h, i) => (
                      <Tag key={i} color="volcano" style={{ cursor: 'pointer' }}
                        onClick={() => seek(h.start, true, h.idx)}>
                        {h.word} · {h.ts}
                      </Tag>
                    ))}
                  </Space>
                </div>
              )}
            </Card>

            {/* ---- 说话人重命名（启发式分离后人工校正角色）---- */}
            {detail.speaker_count && detail.speaker_count >= 2 && (
              <Card size="small" style={{ marginBottom: 12 }}
                title="说话人角色校正（按停顿分离为启发式标注，可在此改为真实身份）">
                <Space wrap size={[8, 8]}>
                  {Array.from({ length: detail.speaker_count }).map((_, i) => (
                    <Space.Compact key={i}>
                      <Tag color={spkColor(i)} style={{ marginRight: 0, height: 32, lineHeight: '30px' }}>
                        {spkLabel(i)}
                      </Tag>
                      <Input size="small" style={{ width: 130 }}
                        placeholder="改为真实身份，如：被谈话人"
                        defaultValue={(detail.speaker_names || {})[i] || ''}
                        onChange={(e) => setSpkEdit((m) => ({ ...m, [i]: e.target.value }))}
                        onPressEnter={renameSpeakers} />
                    </Space.Compact>
                  ))}
                </Space>
                <Button size="small" type="link" onClick={renameSpeakers}
                  disabled={Object.keys(spkEdit).length === 0}>保存角色命名</Button>
              </Card>
            )}

            {/* ---- 分段原文（音文对照主体）---- */}
            {detail.segments.length > 0 && (
              <Card size="small" title={<><FileTextOutlined /> 分段转录（音文对照 · 点击时间戳或文本跳转播放）</>}
                style={{ marginBottom: 12 }}>
                <div style={{ maxHeight: 340, overflowY: 'auto' }}>
                  {detail.segments.map((s, i) => (
                    <div key={i} ref={(el) => { segRefs.current[i] = el }}
                      onClick={() => seek(s.start, true, i)}
                      style={{
                        display: 'flex', gap: 8, padding: '6px 8px', cursor: 'pointer',
                        borderBottom: '1px dashed #eef1f5', borderRadius: 4,
                        background: i === curSeg ? '#eef3ff' : undefined,
                        boxShadow: i === curSeg ? 'inset 3px 0 0 #1d4ed8' : undefined,
                        transition: 'background .15s',
                      }}>
                      {detail.speaker_count && detail.speaker_count >= 2 && (
                        <Tag color={spkColor(s.speaker || 0)}
                          style={{ height: 'fit-content', flexShrink: 0 }}>
                          {spkLabel(s.speaker)}
                        </Tag>
                      )}
                      <Tag color={i === curSeg ? 'processing' : 'blue'}
                        style={{ height: 'fit-content', flexShrink: 0 }}>
                        {s.ts || fmtSec(s.start)}
                      </Tag>
                      <span style={{ lineHeight: 1.8 }}>{highlight(s.text, kws)}</span>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {/* ---- 会议纪要（LLM 生成）---- */}
            <Card size="small" style={{ marginBottom: 12 }}
              title={<><FileTextOutlined /> 会议纪要（AI 整理 · 可编辑留痕）</>}
              extra={detail.status === '完成' && (
                <Button size="small" type="primary" ghost loading={genMinutes}
                  onClick={genMeeting}>生成会议纪要</Button>
              )}>
              {detail.status !== '完成' ? (
                <Typography.Text type="secondary">转录完成后可一键生成会议纪要</Typography.Text>
              ) : detail.minutes_status === '生成中' ? (
                <Alert type="info" showIcon icon={<LoadingOutlined />}
                  message="纪要生成中（本机/在线模型推理较慢，请稍候，完成后自动刷新）" />
              ) : detail.minutes_status === '失败' ? (
                <Alert type="error" showIcon message={`纪要生成失败：${detail.minutes_error || '未知错误'}`} />
              ) : detail.minutes ? (
                <>
                  <Input.TextArea rows={14} value={minutesText}
                    onChange={(e) => { setMinutesText(e.target.value); setMinutesTouched(true) }}
                    placeholder="纪要内容（可编辑）" style={{ fontFamily: 'inherit', marginBottom: 8 }} />
                  <Button type="primary" loading={savingMinutes} onClick={saveMinutes}>保存纪要</Button>
                </>
              ) : (
                <Typography.Text type="secondary">点击右上角「生成会议纪要」由 AI 整理结构化纪要</Typography.Text>
              )}
            </Card>
            <Card size="small" title="校对后全文（可编辑 · 保存留痕）">
              <Input.TextArea rows={12} value={editText}
                onChange={(e) => setEditText(e.target.value)}
                placeholder={detail.status === '完成' ? '机器转录全文已载入，请逐段人工核对修正' : '等待转录完成'} />
            </Card>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
              提示：AI 转录可能存在错字漏字，正式使用前务必人工核对；每次保存均写入审计日志。
            </Typography.Paragraph>
          </>
        )}
      </Drawer>
    </>
  )
}
