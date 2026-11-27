import { useEffect, useRef, useState } from 'react'
import { Card, Input, Button, Tag, Typography, Space, Collapse, Alert, message, Tooltip } from 'antd'
import { SendOutlined, RobotOutlined, AudioOutlined, StopOutlined, LoadingOutlined, ToolOutlined } from '@ant-design/icons'
import { errMsg } from '../api/client'
import { streamSSE } from '../api/nexent'
import { useRealtimeASR } from '../hooks/useRealtimeASR'
import PageHeader from '../components/PageHeader'
import Markdown from '../components/nexent/Markdown'

interface Citation {
  article_id: number; regulation: string; chapter: string
  article_no: string; content: string; score: number
}
interface QARecord {
  id: number
  question: string
  answer: string
  mode: string
  citations: Citation[]
  status: 'retrieving' | 'streaming' | 'done' | 'error'
  weak?: boolean
  step?: string
  reason?: string
}

const MODE_TAG: Record<string, { color: string; text: string }> = {
  llm: { color: 'green', text: '智能体作答 · 依据法规库条款' },
  retrieval: { color: 'orange', text: '检索合成（未调用智能体，全程溯源）' },
  refused: { color: 'red', text: '拒答（法规库中未找到足够依据）' },
}

const TOOL_LABEL: Record<string, string> = {
  knowledge_base_search: '检索法规知识库',
  get_regulation_article: '按条号核对原文',
}

const SAMPLES = [
  '党员违规收受礼金应如何处分？',
  '违规经商办企业违反哪项纪律？',
  '受到警告处分后多长时间内不能提拔？',
  '村干部克扣惠民资金如何处理？',
  '留党察看最长期限是多久？',
]

const CITE = /\[\[[a-zA-Z]?\d+\]\]/g
const toText = (c: any): string => (typeof c === 'string' ? c : c?.content || c?.text || '')

type RecState = 'idle' | 'recording' | 'transcribing'

export default function QA() {
  const [q, setQ] = useState('')
  const [history, setHistory] = useState<QARecord[]>([])
  const [rec, setRec] = useState<RecState>('idle')
  const live = useRealtimeASR((m) => message.error(m, 6))
  const lastLenRef = useRef(0)
  const [waitingVoice, setWaitingVoice] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const busy = history[0]?.status === 'retrieving' || history[0]?.status === 'streaming'

  useEffect(() => () => abortRef.current?.abort(), [])

  // 停止录音后，等剩余分段识别完成再回填输入框
  useEffect(() => {
    if (!waitingVoice || live.recording) return
    if (live.busy) return
    if (live.segments.length > lastLenRef.current) {
      const text = live.segments.slice(lastLenRef.current).map((s) => s.text).join('')
      setQ((prev) => (prev ? prev + text : text))
      message.success('语音识别完成，可直接提问或继续编辑')
    }
    setWaitingVoice(false)
    setRec('idle')
  }, [waitingVoice, live.recording, live.busy, live.segments])

  const ask = async (question: string) => {
    if (!question.trim() || busy) return
    const id = Date.now()
    const patch = (fn: (r: QARecord) => QARecord) => setHistory((h) => h.map((r) => (r.id === id ? fn(r) : r)))
    setHistory((h) => [{ id, question, answer: '', mode: '', citations: [], status: 'retrieving' }, ...h])
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      await streamSSE('/api/qa/ask-stream', { question }, (e) => {
        switch (e.type) {
          case 'ctas_citations':
            patch((r) => ({ ...r, citations: e.content?.hits || [], weak: !!e.content?.weak,
              status: e.content?.refused ? r.status : 'streaming',
              step: e.content?.refused ? undefined : '智能体分析中…' }))
            break
          case 'tool': {
            const name = String(e.content?.name || e.content?.tool_name || toText(e.content)).trim()
            const label = Object.entries(TOOL_LABEL).find(([k]) => name.includes(k))?.[1]
            patch((r) => ({ ...r, step: label ? `${label}…` : '调用工具…' }))
            break
          }
          case 'final_answer': {
            const text = toText(e.content)
            patch((r) => ({ ...r, step: undefined, answer: e.answer_phase === 'complete' ? text : r.answer + text }))
            break
          }
          case 'ctas_qa_done':
            patch((r) => ({ ...r, answer: e.content?.answer || r.answer, mode: e.content?.mode || 'retrieval',
              reason: e.content?.reason, status: 'done', step: undefined }))
            break
          case 'error':
            patch((r) => ({ ...r, step: undefined, reason: toText(e.content) }))
            break
        }
      }, ctrl.signal)
      patch((r) => (r.status === 'done' ? r : { ...r, status: 'error', reason: r.reason || '连接已结束但未收到完整回答' }))
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return
      patch((r) => ({ ...r, status: 'error', reason: errMsg(err) }))
    }
  }

  const startRec = async () => {
    lastLenRef.current = live.segments.length
    await live.start()
    setRec('recording')
  }

  const stopRec = () => {
    live.stop()
    setWaitingVoice(true)
    setRec('transcribing')
  }

  const micButton = () => {
    if (rec === 'recording')
      return (
        <Tooltip title="停止并识别（说话停顿即出字，结束前可再说）">
          <Button danger icon={<StopOutlined />} onClick={stopRec}
            style={{ height: 40, animation: 'pulse-red 1.2s infinite' }} />
        </Tooltip>
      )
    if (rec === 'transcribing')
      return <Tooltip title="语音识别中…"><Button icon={<LoadingOutlined />} style={{ height: 40 }} disabled /></Tooltip>
    return (
      <Tooltip title="语音输入：点击开始录音">
        <Button icon={<AudioOutlined />} onClick={startRec} style={{ height: 40 }} />
      </Tooltip>
    )
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <PageHeader
        icon={<RobotOutlined />}
        title="党纪法规智能问答"
        subtitle="法规库检索即时溯源 · 智能体逐条核对原文作答 · 支持语音提问"
      />
      <Card>
        <Space.Compact style={{ width: '100%' }}>
          {micButton()}
          <Input.Search
            size="large" value={q} placeholder="输入纪法问题，或点击麦克风语音提问"
            onChange={(e) => setQ(e.target.value)}
            enterButton={<span><SendOutlined /> 提问</span>}
            loading={busy}
            onSearch={(v) => { if (!busy) { ask(v); setQ('') } }}
            style={{ height: 40 }}
          />
        </Space.Compact>
        {rec === 'recording' && (
          <Alert style={{ marginTop: 8 }} type="info" showIcon
            message={<span>正在聆听… 实时进度：</span>}
            description={live.segments.length
              ? <span style={{ color: '#1e293b' }}>{live.segments.slice(-3).map((s) => s.text).join(' ')}</span>
              : '开始说话后，停顿时会自动识别显示'} />
        )}
        <div style={{ marginTop: 8 }}>
          {SAMPLES.map((s) => (
            <Tag key={s} style={{ cursor: busy ? 'not-allowed' : 'pointer', marginTop: 4 }} onClick={() => ask(s)}>{s}</Tag>
          ))}
        </div>
      </Card>

      {history.map((h) => (
        <Card key={h.id} style={{ marginTop: 12 }} title={h.question}
          extra={h.status === 'done' && MODE_TAG[h.mode]
            ? <Tag color={MODE_TAG[h.mode].color} bordered={false}>{MODE_TAG[h.mode].text}</Tag>
            : h.status === 'error' ? <Tag color="red" bordered={false}>未完成</Tag>
              : <Tag icon={<LoadingOutlined />} color="processing" bordered={false}>{h.status === 'retrieving' ? '检索法规库' : '作答中'}</Tag>}>
          {h.mode === 'refused' && <Alert type="warning" showIcon style={{ marginBottom: 8 }} message="拒答机制生效：检索相关性低于阈值，为避免幻觉不生成回答" />}
          {h.status === 'done' && h.mode === 'retrieval' && h.reason && (
            <Alert type="info" showIcon style={{ marginBottom: 8 }} message={`未使用智能体：${h.reason}`} />
          )}
          {h.status === 'error' && <Alert type="error" showIcon style={{ marginBottom: 8 }} message={h.reason || '问答失败'} />}
          {(h.answer || h.step) && (
            <div className="answer-block" style={{ marginBottom: h.citations.length ? 12 : 0, padding: 12, background: '#fffbeb', border: '1px solid #f6d9b5', borderRadius: 6 }}>
              {h.answer && <Markdown text={h.answer.replace(CITE, '')} />}
              {h.step && <Typography.Text type="secondary"><ToolOutlined /> {h.step}</Typography.Text>}
            </div>
          )}
          {h.citations?.length > 0 && (
            <Collapse
              items={[{
                key: 'c',
                label: h.weak
                  ? `平台法规库本地检索候选（相关度较低，仅供参考 · ${h.citations.length} 条）`
                  : `平台法规库检索条款（${h.citations.length} 条 · 点击展开原文）`,
                children: h.citations.map((c) => (
                  <div key={c.article_id} className="citation-card" style={{ padding: '8px 12px', marginBottom: 8, borderRadius: 4 }}>
                    <Typography.Text strong>《{c.regulation}》{c.chapter} {c.article_no}</Typography.Text>
                    <Tag style={{ marginLeft: 8 }} color="blue">相关度 {c.score}</Tag>
                    <div style={{ marginTop: 4, color: '#1e293b' }}>{c.content}</div>
                  </div>
                )),
              }]}
            />
          )}
        </Card>
      ))}
    </div>
  )
}
