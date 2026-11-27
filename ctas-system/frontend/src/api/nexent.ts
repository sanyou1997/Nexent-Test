import { useCallback, useEffect, useState } from 'react'
import { api } from './client'

/** Nexent 智能体接入（经 CTAS 后端代理，浏览器不接触 API Key）。 */

export interface NexentAgent {
  name: string
  display_name: string
  description: string
  greeting: string
  author: string
  version_name: string
  version_no?: number
  available: boolean
  unavailable_reasons: string[]
  tool_count: number
}

export interface NexentStatus {
  enabled: boolean
  configured: boolean
  reachable: boolean
  authorized: boolean
  agent_count: number
  default_agent: string
  attach_context_default: boolean
  error: string
  /** 场景 → 智能体（extract / writing / review / law_qa / minutes / analysis） */
  scene_agents?: Record<string, string>
}

export interface NexentConversation {
  id: number
  conversation_id: number
  agent_name: string
  agent_display_name: string
  trial_id: number | null
  subject_kind?: SubjectKind | ''
  subject_id?: number | null
  title: string
  with_context: boolean
  last_answer: string
  created_at: string
  updated_at: string
}

export interface ChatEvent {
  type: string
  content: any
  answer_phase?: 'delta' | 'complete'
  unit_index?: number
}

export interface ChatRequest {
  agent_name: string
  agent_display_name?: string
  query: string
  conversation_id?: number | null
  trial_id?: number | null
  subject_kind?: SubjectKind | ''
  subject_id?: number | null
  with_context?: boolean
  attachments?: any[]
}

/** 发起会话所在的业务对象：审理事项 / 案件线索 / 信访件（首轮可附带其上下文）。 */
export type SubjectKind = 'trial' | 'case' | 'petition'
export interface Subject { kind: SubjectKind; id: number; title?: string }
export const SUBJECT_LABEL: Record<SubjectKind, string> = { trial: '本案', case: '本线索 / 案件', petition: '本信访件' }

/* ---------- 状态 / 智能体列表：模块级缓存，多处组件共用一次请求 ---------- */

let statusCache: { at: number; value: NexentStatus | null; inflight: Promise<NexentStatus | null> | null } = {
  at: 0, value: null, inflight: null,
}
const listeners = new Set<(s: NexentStatus | null) => void>()

export function fetchNexentStatus(force = false): Promise<NexentStatus | null> {
  const now = Date.now()
  if (!force && statusCache.value && now - statusCache.at < 20000) return Promise.resolve(statusCache.value)
  if (!force && statusCache.inflight) return statusCache.inflight
  const p = api.get(`/nexent/status${force ? '?refresh=true' : ''}`)
    .then(({ data }) => data as NexentStatus)
    .catch(() => null)
    .then((v) => {
      statusCache = { at: Date.now(), value: v, inflight: null }
      listeners.forEach((fn) => fn(v))
      return v
    })
  statusCache.inflight = p
  return p
}

export function useNexentStatus() {
  const [status, setStatus] = useState<NexentStatus | null>(statusCache.value)
  const [loading, setLoading] = useState(!statusCache.value)
  useEffect(() => {
    listeners.add(setStatus)
    fetchNexentStatus().then((v) => { setStatus(v); setLoading(false) })
    return () => { listeners.delete(setStatus) }
  }, [])
  const refresh = useCallback(() => {
    setLoading(true)
    return fetchNexentStatus(true).finally(() => setLoading(false))
  }, [])
  return { status, loading, refresh, ready: !!status?.authorized }
}

let agentsCache: { at: number; items: NexentAgent[]; def: string } | null = null

export async function fetchAgents(force = false): Promise<{ items: NexentAgent[]; default_agent: string }> {
  if (!force && agentsCache && Date.now() - agentsCache.at < 60000) {
    return { items: agentsCache.items, default_agent: agentsCache.def }
  }
  const { data } = await api.get('/nexent/agents')
  agentsCache = { at: Date.now(), items: data.items || [], def: data.default_agent || '' }
  return { items: agentsCache.items, default_agent: agentsCache.def }
}

export function useNexentAgents(enabled = true) {
  const [agents, setAgents] = useState<NexentAgent[]>(agentsCache?.items || [])
  const [defaultAgent, setDefaultAgent] = useState(agentsCache?.def || '')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const load = useCallback((force = false) => {
    if (!enabled) return Promise.resolve()
    setLoading(true)
    return fetchAgents(force)
      .then((r) => { setAgents(r.items); setDefaultAgent(r.default_agent); setError('') })
      .catch((e) => setError(e?.response?.data?.detail || e?.message || '加载智能体失败'))
      .finally(() => setLoading(false))
  }, [enabled])
  useEffect(() => { load() }, [load])
  return { agents, defaultAgent, loading, error, reload: () => load(true) }
}

export function invalidateNexentCache() {
  agentsCache = null
  statusCache = { at: 0, value: null, inflight: null }
}

/* ---------- 会话 ---------- */

export async function listConversations(target?: number | Subject | null): Promise<NexentConversation[]> {
  const params = !target ? {} : typeof target === 'number'
    ? { trial_id: target } : { subject_kind: target.kind, subject_id: target.id }
  const { data } = await api.get('/nexent/conversations', { params })
  return data.items || []
}

export async function getConversation(cid: number) {
  const { data } = await api.get(`/nexent/conversations/${cid}`)
  return data as { conversation: NexentConversation; messages: { role: string; content: string; with_context?: boolean }[] }
}

export async function hideConversation(cid: number) {
  await api.delete(`/nexent/conversations/${cid}`)
}

export async function stopChat(cid: number) {
  await api.get(`/nexent/chat/stop/${cid}`)
}

export async function uploadAttachments(files: File[]) {
  const fd = new FormData()
  files.forEach((f) => fd.append('files', f))
  const { data } = await api.post('/nexent/attachments', fd, { timeout: 300000 })
  return (data.files || []) as { filename: string; s3_url: string; size: number }[]
}

export async function saveAsDocument(body: {
  title: string; content: string; trial_id?: number | null; agent_name?: string; conversation_id?: number | null
  subject_kind?: SubjectKind | ''; subject_id?: number | null
}) {
  const { data } = await api.post('/nexent/save-document', body)
  return data as { id: number; title: string }
}

export async function previewTrialContext(trialId: number) {
  const { data } = await api.get(`/nexent/trial-context/${trialId}`)
  return data as { text: string; chars: number; title: string }
}

export async function previewContext(s: Subject) {
  const { data } = await api.get(`/nexent/context/${s.kind}/${s.id}`)
  return data as { text: string; chars: number; title: string }
}

/** 流式会话：fetch + ReadableStream 解析 SSE（axios 不支持流式读取）。 */
export function streamChat(body: ChatRequest, onEvent: (e: ChatEvent) => void, signal?: AbortSignal) {
  return streamSSE('/api/nexent/chat', body, onEvent, signal)
}

/** 审理文书库：调用智能体校对 / 润色，结果另存为新版本（事件 ctas_document_version）。 */
export function reviewDocument(did: number, body: { agent_name: string; agent_display_name?: string; mode: 'proofread' | 'polish'; instruction?: string },
  onEvent: (e: ChatEvent) => void, signal?: AbortSignal) {
  return streamSSE(`/api/nexent/documents/${did}/review`, body, onEvent, signal)
}

export async function streamSSE(url: string, body: any, onEvent: (e: ChatEvent) => void, signal?: AbortSignal) {
  const token = localStorage.getItem('token')
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
    signal,
  })
  if (res.status === 401) {
    localStorage.removeItem('token'); localStorage.removeItem('user')
    window.location.href = '/login'
    return
  }
  if (!res.ok || !res.body) {
    let detail = `请求失败（HTTP ${res.status}）`
    try { const j = await res.json(); detail = j.detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    let i: number
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, i)
      buf = buf.slice(i + 2)
      const data = raw.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n')
      if (!data) continue
      try { onEvent(JSON.parse(data)) } catch { /* 非 JSON 行忽略 */ }
    }
  }
}
