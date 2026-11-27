import { useEffect, useState } from 'react'
import { Alert, Button, Select, Table, Tag, Typography, message } from 'antd'
import { SaveOutlined, ThunderboltOutlined, CheckCircleFilled, CloseCircleFilled, PartitionOutlined } from '@ant-design/icons'
import { api, errMsg } from '../../api/client'
import { NexentAgent } from '../../api/nexent'

interface Scene { key: string; name: string; desc: string; default: string }
type Result = { ok: boolean; agent?: string; latency_ms?: number; sample?: string; reason?: string }

/** 场景智能体：平台每类大模型调用交给哪个 Nexent 智能体（模型在 Nexent 里给智能体选，平台不做模型路由）。 */
export default function SceneAgentsCard({ agents }: { agents: NexentAgent[] }) {
  const [scenes, setScenes] = useState<Scene[]>([])
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState('')
  const [results, setResults] = useState<Record<string, Result>>({})

  useEffect(() => {
    api.get('/nexent/settings').then(({ data }) => { setScenes(data.scenes || []); setMapping(data.scene_agents || {}) })
      .catch((e) => message.error(errMsg(e)))
  }, [])

  const save = async () => {
    setSaving(true)
    try {
      const { data } = await api.put('/nexent/settings', { scene_agents: mapping })
      setMapping(data.scene_agents || {})
      message.success('场景智能体已保存，即时生效')
    } catch (e) { message.error(errMsg(e)) } finally { setSaving(false) }
  }

  const test = async (scene: string) => {
    setTesting(scene)
    try {
      const { data } = await api.post('/system/llm-mode/test', { scene }, { timeout: 180000 })
      setResults((r) => ({ ...r, [scene]: data }))
    } catch (e) { setResults((r) => ({ ...r, [scene]: { ok: false, reason: errMsg(e) } })) } finally { setTesting('') }
  }

  const names = new Set(agents.map((a) => a.name))

  return (
    <div className="cx-card" style={{ padding: 20, marginTop: 16 }}>
      <div className="cx-section-head" style={{ marginBottom: 10 }}>
        <div className="cx-section-title" style={{ marginBottom: 0 }}><PartitionOutlined /> 场景智能体</div>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={save}>保存</Button>
      </div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5 }}>
        平台里的大模型调用按场景交给指定的 Nexent 智能体处理；用哪个模型、挂哪些知识库和工具，在 Nexent 里给智能体配置即可。
        智能体不可用时，对应功能自动降级为规则 / 模板 / 纯检索，并在调用记录中注明原因。
      </Typography.Paragraph>
      {scenes.some((s) => mapping[s.key] && !names.has(mapping[s.key])) && agents.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 10 }}
          message="有场景指定的智能体在当前 Key 可见的已发布智能体中找不到，请在 Nexent 中发布，或改选其它智能体。" />
      )}
      <Table size="small" rowKey="key" pagination={false} dataSource={scenes}
        columns={[
          { title: '场景', dataIndex: 'name', width: 130, render: (v: string, s: Scene) => <div><b>{v}</b><div className="cx-muted">{s.desc}</div></div> },
          {
            title: '处理该场景的智能体', key: 'agent', width: 280,
            render: (_: any, s: Scene) => (
              <Select style={{ width: '100%' }} allowClear placeholder="未指定（该场景降级为规则 / 模板）"
                value={mapping[s.key] || undefined}
                onChange={(v) => setMapping((m) => ({ ...m, [s.key]: v || '' }))}
                status={mapping[s.key] && agents.length && !names.has(mapping[s.key]) ? 'warning' : undefined}
                options={[
                  ...agents.map((a) => ({ label: `${a.display_name}（${a.name}）`, value: a.name })),
                  ...(mapping[s.key] && !names.has(mapping[s.key]) ? [{ label: `${mapping[s.key]}（未找到）`, value: mapping[s.key] }] : []),
                ]} />
            ),
          },
          {
            title: '测试', key: 'test', render: (_: any, s: Scene) => {
              const r = results[s.key]
              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <Button size="small" icon={<ThunderboltOutlined />} loading={testing === s.key} onClick={() => test(s.key)}>实测</Button>
                  {r && (r.ok
                    ? <Tag color="green" icon={<CheckCircleFilled />} bordered={false}>{r.latency_ms} ms · {r.sample}</Tag>
                    : <Tag color="red" icon={<CloseCircleFilled />} bordered={false}>{r.reason}</Tag>)}
                </div>
              )
            },
          },
        ]} />
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
        默认智能体（ctas_extract / ctas_writer / ctas_law_qa / ctas_analyst）可用 <code>docker/nexent/install_scene_agents.py</code> 在 Nexent 中一键安装或更新。
      </Typography.Paragraph>
    </div>
  )
}
