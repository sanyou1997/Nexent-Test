import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Empty, Input, Popconfirm, Skeleton, Tag, Tooltip, message } from 'antd'
import {
  RobotOutlined, ReloadOutlined, DeleteOutlined, MessageOutlined, SearchOutlined, FileDoneOutlined, ToolOutlined,
} from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import ChatPanel, { NexentEmpty } from '../components/nexent/ChatPanel'
import {
  NexentConversation, hideConversation, listConversations, useNexentAgents, useNexentStatus,
} from '../api/nexent'
import { useAuth } from '../auth'

const QUICK: Record<string, string[]> = {
  default: ['这个智能体能帮我做什么？', '请列出你可以调用的工具和知识库'],
}

/** 智能体中心：调用 Nexent 平台已发布的智能体（会话能力经 CTAS 后端代理）。 */
export default function AgentCenter() {
  const nav = useNavigate()
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const { status, loading: stLoading, refresh, ready } = useNexentStatus()
  const { agents, defaultAgent, loading, reload } = useNexentAgents(ready)
  const [agentName, setAgentName] = useState(params.get('agent') || '')
  const [cid, setCid] = useState<number | null>(params.get('c') ? Number(params.get('c')) : null)
  const [convs, setConvs] = useState<NexentConversation[]>([])
  const [kw, setKw] = useState('')

  useEffect(() => {
    if (!agentName && agents.length) setAgentName(defaultAgent && agents.some((a) => a.name === defaultAgent) ? defaultAgent : agents[0].name)
  }, [agents, defaultAgent, agentName])

  const loadConvs = useCallback(() => {
    if (!ready) return
    listConversations().then(setConvs).catch(() => setConvs([]))
  }, [ready])
  useEffect(() => { loadConvs() }, [loadConvs])

  useEffect(() => {
    const next = new URLSearchParams()
    if (agentName) next.set('agent', agentName)
    if (cid) next.set('c', String(cid))
    setParams(next, { replace: true })
  }, [agentName, cid, setParams])

  const filtered = useMemo(() => {
    const k = kw.trim()
    return k ? agents.filter((a) => (a.display_name + a.description + a.name).includes(k)) : agents
  }, [agents, kw])

  const openConv = (c: NexentConversation) => {
    setAgentName(c.agent_name)
    setCid(c.conversation_id)
  }

  const removeConv = async (c: NexentConversation) => {
    try {
      await hideConversation(c.conversation_id)
      if (cid === c.conversation_id) setCid(null)
      loadConvs()
    } catch (e: any) { message.error(e?.response?.data?.detail || '移除失败') }
  }

  const agentDesc = agents.find((a) => a.name === agentName)

  return (
    <div className="cx-page cx-agent-center">
      <div className="cx-page-head">
        <div>
          <div className="cx-page-title"><RobotOutlined /> 智能体中心</div>
          <div className="cx-page-sub">调用 Nexent 平台已发布的智能体。会话经本系统代理，按人归属、全程留痕。</div>
        </div>
        <div className="cx-page-actions">
          <Button icon={<ReloadOutlined />} loading={stLoading || loading} onClick={() => { refresh().then(() => reload()) }}>刷新</Button>
        </div>
      </div>

      {!stLoading && !ready ? (
        <div className="cx-card" style={{ padding: 48 }}>
          <NexentEmpty status={status} isAdmin={user?.role === 'admin'} onGoSettings={() => nav('/nexent-settings')} />
        </div>
      ) : (
        <div className="cx-ac-grid">
          <aside className="cx-ac-side">
            <div className="cx-card cx-ac-agents">
              <div className="cx-card-head">
                <span>已发布智能体</span>
                <Tag className="cx-tag-soft">{agents.length}</Tag>
              </div>
              <div style={{ padding: '0 12px 8px' }}>
                <Input size="small" allowClear prefix={<SearchOutlined />} placeholder="搜索智能体" value={kw} onChange={(e) => setKw(e.target.value)} />
              </div>
              <div className="cx-ac-list">
                {loading && !agents.length ? <Skeleton active paragraph={{ rows: 4 }} style={{ padding: 12 }} /> : null}
                {!loading && !filtered.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有可用的智能体" /> : null}
                {filtered.map((a) => (
                  <button key={a.name} className={`cx-agent-item${a.name === agentName ? ' active' : ''}`}
                    onClick={() => { if (a.name !== agentName) { setAgentName(a.name); setCid(null) } }}
                    disabled={!a.available}>
                    <div className="cx-agent-avatar">{a.display_name.slice(0, 1)}</div>
                    <div className="cx-agent-meta">
                      <div className="cx-agent-name">
                        {a.display_name}
                        {a.name === defaultAgent && <Tag color="gold" bordered={false} style={{ marginLeft: 6 }}>默认</Tag>}
                        {!a.available && <Tag bordered={false} style={{ marginLeft: 6 }}>不可用</Tag>}
                      </div>
                      <div className="cx-agent-desc">{a.description || '（无描述）'}</div>
                      <div className="cx-agent-foot">
                        {a.version_name && <span>{a.version_name}</span>}
                        {a.tool_count > 0 && <span><ToolOutlined /> {a.tool_count} 个工具</span>}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            <div className="cx-card cx-ac-convs">
              <div className="cx-card-head">
                <span>我的会话</span>
                <Tag className="cx-tag-soft">{convs.length}</Tag>
              </div>
              <div className="cx-conv-list">
                {!convs.length && <div className="cx-muted" style={{ padding: '8px 14px 14px' }}>还没有会话</div>}
                {convs.map((c) => (
                  <div key={c.id} className={`cx-conv-item${c.conversation_id === cid ? ' active' : ''}`} onClick={() => openConv(c)}>
                    <MessageOutlined className="cx-conv-icon" />
                    <div className="cx-conv-meta">
                      <div className="cx-conv-title">{c.title || '未命名会话'}</div>
                      <div className="cx-conv-sub">
                        <span>{c.agent_display_name}</span>
                        {c.trial_id ? <Tag color="blue" bordered={false} icon={<FileDoneOutlined />}>案件 #{c.trial_id}</Tag> : null}
                        <span>{c.updated_at}</span>
                      </div>
                    </div>
                    <Popconfirm title="从列表中移除该会话？" description="仅在本系统隐藏，不删除 Nexent 侧记录。" onConfirm={(e) => { e?.stopPropagation(); removeConv(c) }} onCancel={(e) => e?.stopPropagation()}>
                      <Tooltip title="移除"><Button type="text" size="small" className="cx-conv-del" icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} /></Tooltip>
                    </Popconfirm>
                  </div>
                ))}
              </div>
            </div>
          </aside>

          <section className="cx-card cx-ac-chat">
            {agentName ? (
              <ChatPanel
                agents={agents}
                agentName={agentName}
                conversationId={cid}
                onConversationChange={setCid}
                onConversationsChanged={loadConvs}
                quickPrompts={QUICK[agentName] || QUICK.default}
                headerExtra={agentDesc?.author ? <span className="cx-muted">发布者：{agentDesc.author}</span> : null}
              />
            ) : (
              <Empty style={{ marginTop: 120 }} description="请选择左侧的智能体" />
            )}
          </section>
        </div>
      )}
    </div>
  )
}
