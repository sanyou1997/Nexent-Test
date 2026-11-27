import { useState, useRef, useEffect } from 'react'
import {
  Card, Input, Button, Segmented, Tag, Collapse, Spin, Empty, Typography, message as antdMessage,
} from 'antd'
import { CustomerServiceOutlined, SendOutlined, ClearOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'

interface SrcItem {
  article_id: number; regulation: string; chapter: string; article_no: string; content: string; score: number
}
interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  sources?: SrcItem[]
  mode?: string
  llm_used?: boolean
}

export default function Assistant() {
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [mode, setMode] = useState<'general' | 'draft'>('general')
  const [loading, setLoading] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, loading])

  const send = async () => {
    const text = input.trim()
    if (!text || loading) return
    const history = messages.map((m) => ({ role: m.role, content: m.content }))
    setMessages((prev) => [...prev, { role: 'user', content: text }])
    setInput('')
    setLoading(true)
    try {
      const { data } = await api.post('/assistant/chat', { message: text, history, mode })
      setMessages((prev) => [...prev, {
        role: 'assistant',
        content: data.answer,
        sources: data.sources || [],
        mode: data.mode,
        llm_used: data.llm_used,
      }])
    } catch {
      antdMessage.error('请求失败，请稍后再试')
      setMessages((prev) => [...prev, { role: 'assistant', content: '（服务异常，暂无法响应）' }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page-wrap ji-fill-page">
      <PageHeader
        icon={<CustomerServiceOutlined />}
        title="纪检办公通用助手"
        subtitle="本机大模型 · 自由对话 / 写材料 / 梳理思路（数据不出域）"
        extra={<Button icon={<ClearOutlined />} onClick={() => setMessages([])}>新对话</Button>}
      />
      <Card className="assistant-card" style={{ flex: 1, minHeight: 0 }}
        styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 } }}>
        <div ref={scrollRef} style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 16 }}>
          {messages.length === 0 ? (
            <Empty style={{ marginTop: 80 }}
              description="直接跟本机大模型聊——写材料、改稿、理思路、问流程都行" />
          ) : (
            messages.map((m, i) => (
              <div key={i} style={{
                display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 16,
              }}>
                <div style={{
                  maxWidth: '82%',
                  padding: '10px 14px',
                  borderRadius: 10,
                  background: m.role === 'user' ? 'linear-gradient(135deg,#1d4ed8,#69b1ff)' : '#fff',
                  color: m.role === 'user' ? '#fff' : '#0f172a',
                  boxShadow: '0 1px 4px rgba(0,0,0,.08)',
                  whiteSpace: 'pre-wrap',
                  fontSize: 14,
                  lineHeight: 1.7,
                }}>
                  <Typography.Text style={{ color: m.role === 'user' ? '#fff' : '#0f172a', whiteSpace: 'pre-wrap' }}>
                    {m.content}
                  </Typography.Text>
                  {m.role === 'assistant' && m.sources && m.sources.length > 0 && (
                    <Collapse size="small" ghost style={{ marginTop: 8, background: '#f8fafc', borderRadius: 8 }}
                      items={[{
                        key: 'src', label: <span style={{ fontSize: 12 }}>参考法规条款（{m.sources.length}）</span>,
                        children: m.sources.map((s) => (
                          <div key={s.article_id} style={{ fontSize: 12, marginBottom: 8 }}>
                            <Tag color="blue" style={{ marginInlineEnd: 4 }}>{s.article_no}</Tag>
                            <span style={{ color: '#475569' }}>《{s.regulation}》{s.chapter}</span>
                            <div style={{ color: '#1e293b', marginTop: 2 }}>{s.content}</div>
                          </div>
                        )),
                      }]} />
                  )}
                  {m.role === 'assistant' && (
                    <div style={{ marginTop: 6 }}>
                      <Tag color={m.llm_used ? 'green' : 'orange'} style={{ fontSize: 11 }}>
                        {m.llm_used ? '本机大模型' : '模型未就绪'}
                      </Tag>
                      <Tag style={{ fontSize: 11 }}>{m.mode === 'draft' ? '文书起草' : '通用对话'}</Tag>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
          {loading && (
            <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 16 }}>
              <div style={{ padding: '10px 14px', borderRadius: 10, background: '#fff', boxShadow: '0 1px 4px rgba(0,0,0,.08)' }}>
                <Spin size="small" /> <span style={{ marginLeft: 8, color: '#64748b', fontSize: 13 }}>助手思考中…</span>
              </div>
            </div>
          )}
        </div>
        <div style={{
          flexShrink: 0, borderTop: '1px solid #eef1f5', padding: '12px 16px',
          display: 'flex', gap: 8, alignItems: 'center',
        }}>
          <Segmented value={mode} onChange={(v) => setMode(v as 'general' | 'draft')}
            options={[{ label: '通用问答', value: 'general' }, { label: '文书起草', value: 'draft' }]} />
          <Input.TextArea value={input} onChange={(e) => setInput(e.target.value)}
            placeholder={mode === 'draft' ? '描述需起草的文书，如：起草一份初核工作方案' : '随便聊点什么，或让我帮你写/改材料'}
            autoSize={{ minRows: 1, maxRows: 3 }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
          <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={send}>发送</Button>
        </div>
      </Card>
    </div>
  )
}
