import { useCallback, useEffect, useState } from 'react'
import { Drawer, Select, Space, Tag, Typography } from 'antd'
import { RobotOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import ChatPanel, { NexentEmpty } from './ChatPanel'
import {
  NexentConversation, Subject, listConversations, useNexentAgents, useNexentStatus,
} from '../../api/nexent'

/** 各业务对象默认交给哪个场景智能体（与「系统管理 → Nexent 接入 → 场景智能体」一致，取不到时用默认智能体）。 */
const SCENE_OF: Record<Subject['kind'], string> = { trial: 'analysis', case: 'analysis', petition: 'analysis' }

const PROMPTS: Record<Subject['kind'], string[]> = {
  trial: [
    '审查本案证据链，指出薄弱环节和需要补充的材料',
    '核对量纪说理引用的法条是否准确、有无遗漏',
  ],
  case: [
    '研判这条线索：可查性如何？建议采取哪种处置方式，依据是什么？',
    '列出初核需要调取的材料和需要谈话的对象',
    '查一下同一被反映人有无其他线索或类似问题，是否需要并案',
    '对照纪律处分条例，反映的问题可能涉及哪些条款？',
  ],
  petition: [
    '提炼这件信访的核心问题和可查要素，判断是否属于受理范围',
    '给出分办建议（承办部门与理由），并列出需补充核实的事项',
    '这件信访反映的问题可能违反哪些纪律或法规条款？',
    '起草一份实名举报受理告知书的要点',
  ],
}

export interface AiAssistDrawerProps {
  open: boolean
  onClose: () => void
  subject: Subject | null
  user?: { role?: string } | null
  width?: number
}

/**
 * 业务对象「AI 研判」抽屉：在信访件 / 线索 / 案件里直接与 Nexent 智能体对话，
 * 首轮可附带该对象的结构化上下文（可预览），会话按「用户 + 业务对象」归档，可续聊、可存为文书草稿。
 */
export default function AiAssistDrawer({ open, onClose, subject, user, width = 560 }: AiAssistDrawerProps) {
  const navigate = useNavigate()
  const { status, ready } = useNexentStatus()
  const { agents, defaultAgent } = useNexentAgents(ready && open)
  const [agent, setAgent] = useState('')
  const [cid, setCid] = useState<number | null>(null)
  const [convs, setConvs] = useState<NexentConversation[]>([])
  const sceneAgent: string = (status?.scene_agents || {})[SCENE_OF[subject?.kind || 'case']] || ''

  useEffect(() => {
    if (agent || !agents.length) return
    const pick = [sceneAgent, defaultAgent].find((n) => n && agents.some((a) => a.name === n))
    setAgent(pick || agents[0].name)
  }, [agents, sceneAgent, defaultAgent, agent])

  const loadConvs = useCallback(() => {
    if (!ready || !subject) { setConvs([]); return }
    listConversations(subject).then(setConvs).catch(() => setConvs([]))
  }, [ready, subject?.kind, subject?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setCid(null); if (open) loadConvs() }, [subject?.kind, subject?.id, open, loadConvs])

  return (
    <Drawer open={open} onClose={onClose} width={width} destroyOnClose={false}
      styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
      title={
        <Space size={8} style={{ width: '100%', justifyContent: 'space-between' }}>
          <span><RobotOutlined /> AI 研判 <Tag className="cx-tag-soft" bordered={false}>Nexent</Tag></span>
          {ready && convs.length > 0 && (
            <Select size="small" style={{ width: 200 }} value={cid ?? 0} popupMatchSelectWidth={false}
              onChange={(v) => {
                if (!v) { setCid(null); return }
                const c = convs.find((x) => x.conversation_id === v)
                if (c) setAgent(c.agent_name)
                setCid(v)
              }}
              options={[{ label: '＋ 新会话', value: 0 }, ...convs.map((c) => ({ label: `${c.title}（${c.updated_at.slice(5)}）`, value: c.conversation_id }))]} />
          )}
        </Space>
      }>
      {subject && (
        <Typography.Text type="secondary" style={{ padding: '8px 16px 0', fontSize: 12 }} ellipsis>
          {subject.title}
        </Typography.Text>
      )}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {ready && subject ? (
          <ChatPanel
            variant="side"
            agents={agents}
            agentName={agent}
            onAgentChange={setAgent}
            conversationId={cid}
            onConversationChange={setCid}
            onConversationsChanged={loadConvs}
            subject={subject}
            contextDefault={status?.attach_context_default ?? true}
            quickPrompts={PROMPTS[subject.kind]}
          />
        ) : (
          <div style={{ padding: 24 }}>
            <NexentEmpty status={status} isAdmin={user?.role === 'admin'} onGoSettings={() => navigate('/nexent-settings')} />
          </div>
        )}
      </div>
    </Drawer>
  )
}
