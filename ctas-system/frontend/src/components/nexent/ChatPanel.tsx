import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Checkbox, Collapse, Empty, Input, message, Modal, Select, Space, Spin, Tag, Tooltip, Typography, Upload,
} from 'antd'
import {
  SendOutlined, StopOutlined, CopyOutlined, FileAddOutlined, PaperClipOutlined, CloseOutlined,
  RobotOutlined, UserOutlined, LoadingOutlined, ToolOutlined, BulbOutlined, WarningOutlined, EyeOutlined,
  CheckCircleFilled, ExclamationCircleFilled,
} from '@ant-design/icons'
import Markdown, { stripCitations } from './Markdown'
import {
  ChatEvent, NexentAgent, SUBJECT_LABEL, Subject, getConversation, previewContext, saveAsDocument, stopChat, streamChat,
  uploadAttachments,
} from '../../api/nexent'

interface Step { kind: 'think' | 'tool' | 'log' | 'warn' | 'step'; text: string }
interface UIMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  withContext?: boolean
  files?: string[]
  steps?: Step[]
  status?: 'streaming' | 'done' | 'error' | 'stopped'
  error?: string
  questions?: { id: string; title: string; options?: { id: string; label: string }[] }[]
}

export interface ChatPanelProps {
  agents: NexentAgent[]
  agentName: string
  onAgentChange?: (name: string) => void
  conversationId: number | null
  onConversationChange: (cid: number | null) => void
  onConversationsChanged?: () => void
  trialId?: number | null
  trialTitle?: string
  /** 通用业务对象（审理事项 / 案件线索 / 信访件）；与 trialId 二选一，trialId 为兼容旧用法 */
  subject?: Subject | null
  contextDefault?: boolean
  quickPrompts?: string[]
  variant?: 'full' | 'side'
  headerExtra?: ReactNode
}

const toText = (c: any): string => {
  if (c == null) return ''
  if (typeof c === 'string') return c
  try { return JSON.stringify(c) } catch { return String(c) }
}
const uid = () => Math.random().toString(36).slice(2)

export default function ChatPanel(props: ChatPanelProps) {
  const {
    agents, agentName, onAgentChange, conversationId, onConversationChange, onConversationsChanged,
    trialId, subject: subjectProp, contextDefault = true, quickPrompts = [], variant = 'full', headerExtra,
  } = props
  const subject: Subject | null = useMemo(
    () => subjectProp || (trialId ? { kind: 'trial', id: trialId } : null),
    [subjectProp?.kind, subjectProp?.id, trialId], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const agent = useMemo(() => agents.find((a) => a.name === agentName), [agents, agentName])
  const [msgs, setMsgs] = useState<UIMsg[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [input, setInput] = useState('')
  const [withContext, setWithContext] = useState(contextDefault)
  const [files, setFiles] = useState<{ filename: string; s3_url: string; size: number }[]>([])
  const [uploading, setUploading] = useState(false)
  const [ctxPreview, setCtxPreview] = useState<{ text: string; chars: number } | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const cidRef = useRef<number | null>(conversationId)
  const selfCreated = useRef<number | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const checkpoints = useRef<Record<string, { len: number; steps: number }>>({})
  // 子智能体调用（subagent_start 声明）；主智能体事件同样带 invocation_id
  const subIds = useRef<Set<string>>(new Set())
  const isSub = (e: any) => !!e.invocation_id && subIds.current.has(e.invocation_id)

  useEffect(() => { setWithContext(contextDefault) }, [contextDefault, subject?.kind, subject?.id])

  // 外部切换会话 → 加载历史；本组件刚创建的会话不重复加载，避免覆盖流式内容
  useEffect(() => {
    cidRef.current = conversationId
    if (!conversationId) {
      if (!streaming) setMsgs([])
      return
    }
    if (selfCreated.current === conversationId) return
    setHistoryLoading(true)
    getConversation(conversationId)
      .then((d) => setMsgs(d.messages.map((m) => ({
        id: uid(), role: m.role === 'user' ? 'user' : 'assistant', content: m.content,
        withContext: m.with_context, status: 'done',
      }))))
      .catch((e) => { message.error(e?.response?.data?.detail || '加载会话失败'); setMsgs([]) })
      .finally(() => setHistoryLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [msgs])

  const patchLast = useCallback((fn: (m: UIMsg) => UIMsg) => {
    setMsgs((list) => {
      if (!list.length) return list
      const copy = list.slice()
      copy[copy.length - 1] = fn(copy[copy.length - 1])
      return copy
    })
  }, [])

  const pushStep = (m: UIMsg, step: Step): UIMsg => {
    const steps = (m.steps || []).slice()
    const last = steps[steps.length - 1]
    if (step.kind === 'think' && last?.kind === 'think') steps[steps.length - 1] = { ...last, text: last.text + step.text }
    else steps.push(step)
    return { ...m, steps }
  }

  const applyEvent = useCallback((e: ChatEvent & Record<string, any>) => {
    const t = e.type
    if (t === 'ctas_conversation') {
      const cid = Number(e.content?.conversation_id)
      if (cid && cid !== cidRef.current) {
        cidRef.current = cid
        selfCreated.current = cid
        onConversationChange(cid)
      }
      onConversationsChanged?.()
      return
    }
    if (t === 'ctas_done') {
      patchLast((m) => (m.status === 'streaming' ? { ...m, status: 'done' } : m))
      onConversationsChanged?.()
      return
    }
    if (t === 'subagent_start') {
      let inv = e.invocation_id
      if (!inv && typeof e.content === 'string') { try { inv = JSON.parse(e.content).invocation_id } catch { /* ignore */ } }
      if (inv) subIds.current.add(inv)
    }
    if (t === 'model_attempt_control' && e.attempt_id && !isSub(e)) {
      if (e.phase === 'begin') {
        patchLast((m) => { checkpoints.current[e.attempt_id] = { len: m.content.length, steps: (m.steps || []).length }; return m })
      } else if (e.phase === 'rollback') {
        const cp = checkpoints.current[e.attempt_id]
        if (cp) patchLast((m) => ({ ...m, content: m.content.slice(0, cp.len), steps: (m.steps || []).slice(0, cp.steps) }))
        delete checkpoints.current[e.attempt_id]
      } else delete checkpoints.current[e.attempt_id]
      return
    }
    patchLast((m) => {
      switch (t) {
        case 'final_answer': {
          const text = toText(e.content)
          if (isSub(e)) return text ? pushStep(m, { kind: 'log', text: `子智能体输出：${text.slice(0, 300)}` }) : m
          if (e.answer_phase === 'complete') return text ? { ...m, content: text } : m
          return { ...m, content: m.content + text }
        }
        case 'model_output_thinking':
        case 'model_output_deep_thinking':
          return pushStep(m, { kind: 'think', text: toText(e.content) })
        case 'step_count': {
          const s = toText(e.content).trim()
          return pushStep(m, { kind: 'step', text: /^\d+$/.test(s) ? `第 ${s} 步` : s || '新步骤' })
        }
        case 'tool':
          return pushStep(m, { kind: 'tool', text: toText(e.content) })
        case 'execution_logs': case 'search_content': case 'parse': case 'memory_search':
        case 'verification': case 'plan': case 'plan_step_update': {
          const s = toText(e.content).trim()
          return s ? pushStep(m, { kind: 'log', text: s.length > 600 ? s.slice(0, 600) + '…' : s }) : m
        }
        case 'file_artifact': case 'skill_artifact': {
          const c = e.content
          const names = Array.isArray(c) ? c.map((x: any) => x?.filename || x?.name || '文件').join('、')
            : (c?.filename || c?.name || '文件')
          return pushStep(m, { kind: 'log', text: `智能体生成了文件：${names}（可在 Nexent 会话中下载）` })
        }
        case 'warning': case 'max_steps_reached':
          return pushStep(m, { kind: 'warn', text: toText(e.content) || '智能体达到最大步数' })
        case 'human_interaction': {
          const qs = e.content?.questions
          return Array.isArray(qs) ? { ...m, questions: qs } : m
        }
        case 'error':
          return { ...m, status: 'error', error: toText(e.content) || '智能体运行出错' }
        default:
          return m
      }
    })
  }, [onConversationChange, onConversationsChanged, patchLast])

  const send = async (text?: string) => {
    const query = (text ?? input).trim()
    if (!query || streaming) return
    if (!agentName) { message.warning('请先选择智能体'); return }
    const isNew = !cidRef.current
    const attachCtx = !!subject && withContext && isNew
    const sendFiles = files.slice()
    setMsgs((list) => [
      ...list,
      { id: uid(), role: 'user', content: query, withContext: attachCtx, files: sendFiles.map((f) => f.filename), status: 'done' },
      { id: uid(), role: 'assistant', content: '', steps: [], status: 'streaming' },
    ])
    setInput('')
    setFiles([])
    setStreaming(true)
    checkpoints.current = {}
    subIds.current = new Set()
    const ac = new AbortController()
    abortRef.current = ac
    try {
      await streamChat({
        agent_name: agentName,
        agent_display_name: agent?.display_name || agentName,
        query,
        conversation_id: cidRef.current,
        trial_id: subject?.kind === 'trial' ? subject.id : null,
        subject_kind: subject?.kind || '',
        subject_id: subject?.id ?? null,
        with_context: attachCtx,
        attachments: sendFiles.map((f) => f.s3_url),
      }, applyEvent, ac.signal)
      patchLast((m) => (m.status === 'streaming' ? { ...m, status: 'done' } : m))
    } catch (err: any) {
      if (err?.name === 'AbortError') patchLast((m) => ({ ...m, status: 'stopped' }))
      else patchLast((m) => ({ ...m, status: 'error', error: err?.message || '请求失败' }))
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }

  const stop = async () => {
    const cid = cidRef.current
    abortRef.current?.abort()
    if (cid) { try { await stopChat(cid) } catch { /* 已结束 */ } }
  }

  const newConversation = () => {
    if (streaming) return
    selfCreated.current = null
    cidRef.current = null
    setMsgs([])
    onConversationChange(null)
  }

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).then(() => message.success('已复制'), () => message.error('复制失败'))
  }

  const saveDoc = (m: UIMsg) => {
    const baseTitle = subjectProp?.title || props.trialTitle
    let title = baseTitle ? `${baseTitle}·智能体意见` : `${agent?.display_name || agentName}意见`
    Modal.confirm({
      title: '存为审理文书草稿',
      content: (
        <div>
          <div style={{ marginBottom: 8, color: '#64748b' }}>保存到「审理文书」，状态为 AI 初稿，须人工核对后才能使用。</div>
          <Input defaultValue={title} onChange={(e) => { title = e.target.value }} maxLength={120} />
        </div>
      ),
      okText: '保存',
      onOk: async () => {
        try {
          const r = await saveAsDocument({
            title, content: stripCitations(m.content), agent_name: agentName, conversation_id: cidRef.current,
            trial_id: subject?.kind === 'trial' ? subject.id : null, subject_kind: subject?.kind || '', subject_id: subject?.id ?? null,
          })
          message.success(`已保存：${r.title}`)
        } catch (e: any) { message.error(e?.response?.data?.detail || '保存失败') }
      },
    })
  }

  const showContext = async () => {
    if (!subject) return
    try { setCtxPreview(await previewContext(subject)) } catch (e: any) { message.error(e?.response?.data?.detail || '加载失败') }
  }

  const beforeUpload = async (file: File) => {
    if (file.size > 20 * 1024 * 1024) { message.error('单个附件不超过 20MB'); return false }
    setUploading(true)
    try {
      const r = await uploadAttachments([file])
      setFiles((f) => [...f, ...r])
    } catch (e: any) { message.error(e?.response?.data?.detail || '上传失败') } finally { setUploading(false) }
    return false
  }

  const isNew = !conversationId
  const greeting = agent?.greeting || (agent?.description ? `我是「${agent.display_name}」：${agent.description}` : '')

  return (
    <div className={`cx-chat cx-chat-${variant}`}>
      <div className="cx-chat-head">
        <div className="cx-chat-agent">
          <div className="cx-agent-avatar sm">{(agent?.display_name || agentName || '智').slice(0, 1)}</div>
          {onAgentChange ? (
            <Select
              size="small" value={agentName || undefined} placeholder="选择智能体" style={{ minWidth: 180, maxWidth: 240 }}
              onChange={(v) => { onAgentChange(v); newConversation() }} disabled={streaming}
              options={agents.map((a) => ({ label: a.display_name, value: a.name, disabled: !a.available }))}
              popupMatchSelectWidth={false}
            />
          ) : (
            <div className="cx-chat-agent-name">{agent?.display_name || agentName || '未选择智能体'}</div>
          )}
          {conversationId ? <Tag className="cx-tag-soft">会话 #{conversationId}</Tag> : <Tag className="cx-tag-soft">新会话</Tag>}
        </div>
        <Space size={6}>
          {headerExtra}
          <Button size="small" onClick={newConversation} disabled={streaming || (!conversationId && !msgs.length)}>新会话</Button>
        </Space>
      </div>

      <div className="cx-chat-body" ref={scrollRef}>
        <Spin spinning={historyLoading}>
          {!msgs.length && !historyLoading && (
            <div className="cx-chat-empty">
              <div className="cx-chat-empty-icon"><RobotOutlined /></div>
              <div className="cx-chat-empty-title">{agent?.display_name || '选择一个智能体开始对话'}</div>
              {greeting && <div className="cx-chat-empty-sub">{greeting}</div>}
              {quickPrompts.length > 0 && (
                <div className="cx-quick">
                  {quickPrompts.map((q) => (
                    <button key={q} className="cx-quick-item" onClick={() => send(q)} disabled={!agentName || streaming}>{q}</button>
                  ))}
                </div>
              )}
            </div>
          )}
          {msgs.map((m) => (
            <div key={m.id} className={`cx-msg cx-msg-${m.role}`}>
              <div className="cx-msg-avatar">{m.role === 'user' ? <UserOutlined /> : <RobotOutlined />}</div>
              <div className="cx-msg-main">
                {m.role === 'user' ? (
                  <div className="cx-bubble-user">
                    <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
                    {(m.withContext || (m.files && m.files.length > 0)) && (
                      <div className="cx-msg-meta">
                        {m.withContext && <Tag color="blue" bordered={false}>已附案件上下文</Tag>}
                        {m.files?.map((f) => <Tag key={f} icon={<PaperClipOutlined />} bordered={false}>{f}</Tag>)}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="cx-bubble-ai">
                    {m.steps && m.steps.length > 0 && (
                      <Collapse
                        size="small" ghost className="cx-steps"
                        items={[{
                          key: 's',
                          label: (
                            <span className="cx-steps-label">
                              {m.status === 'streaming' ? <LoadingOutlined /> : <CheckCircleFilled style={{ color: '#15803d' }} />}
                              执行过程 · {m.steps.filter((s) => s.kind === 'tool').length} 次工具调用
                            </span>
                          ),
                          children: (
                            <div className="cx-steps-list">
                              {m.steps.map((s, i) => (
                                <div key={i} className={`cx-step cx-step-${s.kind}`}>
                                  {s.kind === 'tool' ? <ToolOutlined /> : s.kind === 'think' ? <BulbOutlined /> : s.kind === 'warn' ? <WarningOutlined /> : <span className="cx-dot" />}
                                  <span>{s.kind === 'tool' ? `调用工具：${s.text}` : s.text}</span>
                                </div>
                              ))}
                            </div>
                          ),
                        }]}
                      />
                    )}
                    {m.content ? <Markdown text={m.content} /> : m.status === 'streaming' ? (
                      <div className="cx-typing"><span /><span /><span /></div>
                    ) : null}
                    {m.questions && m.questions.length > 0 && (
                      <div className="cx-questions">
                        {m.questions.map((q) => (
                          <div key={q.id} className="cx-question">
                            <div className="cx-question-title">{q.title}</div>
                            {q.options && q.options.length > 0 && (
                              <Space wrap size={6}>
                                {q.options.map((o) => (
                                  <Button key={o.id} size="small" disabled={streaming}
                                    onClick={() => send(`补充上一轮问题：${q.title} —— ${o.label}`)}>{o.label}</Button>
                                ))}
                              </Space>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    {m.status === 'error' && <Alert type="error" showIcon icon={<ExclamationCircleFilled />} message={m.error} style={{ marginTop: 8 }} />}
                    {m.status === 'stopped' && <div className="cx-msg-note">已停止生成</div>}
                    {m.status === 'done' && m.content && (
                      <div className="cx-msg-actions">
                        <Tooltip title="复制"><Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copy(m.content)} /></Tooltip>
                        <Button type="text" size="small" icon={<FileAddOutlined />} onClick={() => saveDoc(m)}>存为文书草稿</Button>
                        <span className="cx-msg-disclaimer">AI 生成，须核对原件与法规原文</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}
        </Spin>
      </div>

      <div className="cx-chat-input">
        {subject ? (
          <div className="cx-ctx-row">
            {isNew ? (
              <Checkbox checked={withContext} onChange={(e) => setWithContext(e.target.checked)} disabled={streaming}>
                首轮附带{SUBJECT_LABEL[subject.kind]}上下文发送给 Nexent
              </Checkbox>
            ) : (
              <span className="cx-ctx-hint">续聊不重复发送上下文</span>
            )}
            <Button type="link" size="small" icon={<EyeOutlined />} onClick={showContext}>查看将发送的内容</Button>
          </div>
        ) : null}
        {files.length > 0 && (
          <div className="cx-files">
            {files.map((f) => (
              <Tag key={f.s3_url} icon={<PaperClipOutlined />} closable closeIcon={<CloseOutlined />}
                onClose={() => setFiles((list) => list.filter((x) => x.s3_url !== f.s3_url))}>{f.filename}</Tag>
            ))}
          </div>
        )}
        <div className="cx-input-box">
          <Input.TextArea
            value={input} onChange={(e) => setInput(e.target.value)}
            placeholder={agentName ? '输入问题，Enter 发送，Shift+Enter 换行' : '请先选择智能体'}
            autoSize={{ minRows: 2, maxRows: 8 }} variant="borderless" disabled={!agentName}
            onPressEnter={(e) => { if (!e.shiftKey && !(e.nativeEvent as any).isComposing) { e.preventDefault(); send() } }}
          />
          <div className="cx-input-tools">
            <Upload beforeUpload={beforeUpload} showUploadList={false} multiple disabled={streaming || uploading || !agentName}>
              <Tooltip title="上传附件到 Nexent（PDF / Word / 图片 / 录音，单个 ≤20MB）">
                <Button type="text" size="small" icon={uploading ? <LoadingOutlined /> : <PaperClipOutlined />} disabled={streaming || !agentName} />
              </Tooltip>
            </Upload>
            {streaming ? (
              <Button danger size="small" icon={<StopOutlined />} onClick={stop}>停止</Button>
            ) : (
              <Button type="primary" size="small" icon={<SendOutlined />} onClick={() => send()} disabled={!input.trim() || !agentName}>发送</Button>
            )}
          </div>
        </div>
      </div>

      <Modal title="将随首轮提问发送给 Nexent 的案件上下文" open={!!ctxPreview} onCancel={() => setCtxPreview(null)}
        footer={<Button onClick={() => setCtxPreview(null)}>关闭</Button>} width={720}>
        <Alert type="info" showIcon style={{ marginBottom: 10 }}
          message={`共 ${ctxPreview?.chars || 0} 字，取自本系统已登记的案件信息与离线引擎结论；不勾选则只发送你输入的问题。`} />
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', maxHeight: 440, overflow: 'auto', background: '#f8fafc', padding: 12, borderRadius: 8, fontSize: 13 }}>
          {ctxPreview?.text}
        </Typography.Paragraph>
      </Modal>
    </div>
  )
}

export function NexentEmpty({ status, isAdmin, onGoSettings }: { status: any; isAdmin: boolean; onGoSettings?: () => void }) {
  return (
    <Empty
      image={<RobotOutlined style={{ fontSize: 48, color: '#94a3b8' }} />}
      description={(
        <div>
          <div style={{ fontWeight: 600, color: '#334155', marginBottom: 4 }}>Nexent 智能体暂不可用</div>
          <div style={{ color: '#64748b' }}>{status?.error || '无法获取 Nexent 连接状态'}</div>
        </div>
      )}
    >
      {isAdmin ? <Button type="primary" onClick={onGoSettings}>去配置 Nexent 接入</Button>
        : <span style={{ color: '#94a3b8' }}>请联系管理员在「系统管理 → Nexent 接入」中完成配置</span>}
    </Empty>
  )
}
