import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Input, Modal, Radio, Select, Space, Spin, Typography } from 'antd'
import { RobotOutlined, StopOutlined, LoadingOutlined, CheckCircleFilled } from '@ant-design/icons'
import Markdown from './Markdown'
import { ChatEvent, reviewDocument, stopChat, useNexentAgents, useNexentStatus } from '../../api/nexent'

interface Props {
  open: boolean
  doc: { id: number; title: string } | null
  onClose: () => void
  /** 新版本已保存：父组件刷新文书并可直接对比 / 采用 */
  onSaved: (versionNo: number, opts: { compare?: boolean }) => void
}

const REVISED_BEGIN = '<<<修改稿>>>'
const REVISED_END = '<<<结束>>>'

/** 审理文书库 · 用 Nexent 智能体校对 / 润色：流式显示过程，完成后另存为新版本（待采用），不覆盖当前正文。 */
export default function DocReviewModal({ open, doc, onClose, onSaved }: Props) {
  const { status, ready } = useNexentStatus()
  const { agents, defaultAgent } = useNexentAgents(ready)
  const [agent, setAgent] = useState('')
  const [mode, setMode] = useState<'proofread' | 'polish'>('proofread')
  const [instruction, setInstruction] = useState('')
  const [running, setRunning] = useState(false)
  const [answer, setAnswer] = useState('')
  const [steps, setSteps] = useState(0)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  const cidRef = useRef<number | null>(null)

  // 默认优先选「公文规范与校对」类智能体，其次是系统默认智能体
  useEffect(() => {
    if (!open || agent || !agents.length) return
    const doc = agents.find((a) => /公文|校对|official/i.test(a.display_name + a.name))
    setAgent((doc || agents.find((a) => a.name === defaultAgent) || agents[0]).name)
  }, [open, agents, defaultAgent, agent])

  useEffect(() => {
    if (open) { setAnswer(''); setSteps(0); setResult(null); setError(''); cidRef.current = null }
  }, [open, doc?.id])

  const display = useMemo(() => {
    // 过程中把「修改稿」标记换成更易读的分隔
    return answer.split(REVISED_BEGIN).join('**【修改稿】**\n\n').split(REVISED_END).join('\n\n**【修改说明】**')
  }, [answer])

  const start = async () => {
    if (!doc || !agent) return
    setRunning(true); setAnswer(''); setSteps(0); setResult(null); setError('')
    const ac = new AbortController()
    abortRef.current = ac
    const a = agents.find((x) => x.name === agent)
    const subIds = new Set<string>()
    try {
      await reviewDocument(doc.id, { agent_name: agent, agent_display_name: a?.display_name, mode, instruction },
        (e: ChatEvent & Record<string, any>) => {
          if (e.type === 'ctas_conversation') cidRef.current = Number(e.content?.conversation_id) || null
          else if (e.type === 'subagent_start' && e.invocation_id) subIds.add(e.invocation_id)
          else if (e.type === 'final_answer' && !(e.invocation_id && subIds.has(e.invocation_id))) {
            const t = typeof e.content === 'string' ? e.content : ''
            if (e.answer_phase === 'complete') { if (t) setAnswer(t) } else setAnswer((p) => p + t)
          } else if (e.type === 'tool' || e.type === 'step_count') setSteps((n) => n + 1)
          else if (e.type === 'error') setError(String(e.content || '智能体运行出错'))
          else if (e.type === 'ctas_document_version') {
            setResult(e.content)
            if (e.content?.answer) setAnswer(e.content.answer)
            if (e.content?.saved) onSaved(e.content.version_no, {})
          }
        }, ac.signal)
    } catch (err: any) {
      if (err?.name !== 'AbortError') setError(err?.message || '请求失败')
    } finally {
      setRunning(false); abortRef.current = null
    }
  }

  const stop = async () => {
    abortRef.current?.abort()
    if (cidRef.current) { try { await stopChat(cidRef.current) } catch { /* ignore */ } }
  }

  return (
    <Modal
      title={<span><RobotOutlined /> 智能体校对 / 润色 · {doc?.title}</span>}
      open={open} width={820} onCancel={() => { if (!running) onClose() }} maskClosable={!running}
      footer={result?.saved ? [
        <Button key="close" onClick={onClose}>关闭</Button>,
        <Button key="cmp" type="primary" onClick={() => { onSaved(result.version_no, { compare: true }); onClose() }}>对比并决定是否采用</Button>,
      ] : [
        <Button key="cancel" onClick={onClose} disabled={running}>取消</Button>,
        running
          ? <Button key="stop" danger icon={<StopOutlined />} onClick={stop}>停止</Button>
          : <Button key="go" type="primary" icon={<RobotOutlined />} disabled={!ready || !agent} onClick={start}>开始{mode === 'proofread' ? '校对' : '润色'}</Button>,
      ]}
    >
      {!ready ? (
        <Alert type="warning" showIcon message="Nexent 智能体暂不可用" description={status?.error || '请管理员在「系统管理 → Nexent 接入」完成配置'} />
      ) : (
        <>
          <Space direction="vertical" style={{ width: '100%' }} size={10}>
            <Space wrap>
              <span>智能体</span>
              <Select style={{ minWidth: 260 }} value={agent || undefined} onChange={setAgent} disabled={running}
                options={agents.map((a) => ({ label: a.display_name, value: a.name, disabled: !a.available }))} />
              <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)} disabled={running}
                optionType="button" buttonStyle="solid"
                options={[{ label: '校对（只改错）', value: 'proofread' }, { label: '润色（规范表述）', value: 'polish' }]} />
            </Space>
            <Input placeholder="补充要求（可选），如：重点核对法规条款名称与条号" value={instruction}
              onChange={(e) => setInstruction(e.target.value)} disabled={running} maxLength={300} />
            <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
              文书全文将发送给所选 Nexent 智能体；结果另存为新版本（标记「待采用」），当前正文不会被覆盖，对比确认后再决定是否采用。
            </Typography.Text>
          </Space>

          {(running || answer || error) && (
            <div className="cx-review-box">
              <div className="cx-review-head">
                {running ? <><LoadingOutlined /> 智能体处理中{steps ? ` · ${steps} 个步骤` : ''}</>
                  : result?.saved ? <><CheckCircleFilled style={{ color: '#15803d' }} /> 已保存为 v{result.version_no}（待采用）{result.unchanged ? ' · 与原文相同' : ''}</>
                    : null}
              </div>
              {answer ? <Markdown text={display} /> : running ? <Spin size="small" /> : null}
            </div>
          )}
          {error && <Alert type="error" showIcon message={error} style={{ marginTop: 10 }} />}
          {result?.saved && !result.structured && (
            <Alert type="warning" showIcon style={{ marginTop: 10 }} message="智能体没有按「修改稿」格式返回，新版本保存的是它的完整回复，请对比后谨慎采用。" />
          )}
          {result && !result.saved && result.error && <Alert type="error" showIcon style={{ marginTop: 10 }} message={result.error} />}
        </>
      )}
    </Modal>
  )
}
