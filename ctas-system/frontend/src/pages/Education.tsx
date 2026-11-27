import { useState } from 'react'
import { Card, Input, Button, Select, InputNumber, Tag, Collapse, Typography, Space, message as antdMessage, Divider } from 'antd'
import { ReadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'

export default function Education() {
  const [topic, setTopic] = useState('')
  const [audience, setAudience] = useState('党员干部')
  const [count, setCount] = useState(5)
  const [loading, setLoading] = useState(false)
  const [material, setMaterial] = useState('')
  const [quizzes, setQuizzes] = useState<{
    question: string; options: string[]; answer: string; explain: string
  }[]>([])
  const [llmUsed, setLlmUsed] = useState<boolean | null>(null)
  const [sources, setSources] = useState<{ article_no: string; regulation: string; content: string }[]>([])

  const gen = async () => {
    if (!topic.trim() || loading) return
    setLoading(true)
    try {
      const { data } = await api.post('/education/generate', { topic, audience, count })
      setMaterial(data.material)
      setQuizzes(data.quizzes || [])
      setLlmUsed(data.llm_used)
      setSources(data.sources || [])
    } catch {
      antdMessage.error('生成失败，请稍后再试')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page-wrap">
      <PageHeader
        icon={<ReadOutlined />}
        title="廉洁教育智能体"
        subtitle="依据党纪法规生成学习材料与随堂测验（联网增强 / 离线检索双模式）"
      />
      <Card style={{ marginBottom: 16 }}>
        <Space wrap size={12} align="center">
          <Input
            value={topic} onChange={(e) => setTopic(e.target.value)}
            placeholder="学习主题，如：违规收受礼金、公款旅游、违规吃喝"
            style={{ width: 320 }}
            onPressEnter={gen}
          />
          <Select value={audience} onChange={setAudience} style={{ width: 160 }}
            options={[
              { label: '党员干部', value: '党员干部' },
              { label: '公职人员', value: '公职人员' },
              { label: '新提任干部', value: '新提任干部' },
              { label: '年轻干部', value: '年轻干部' },
              { label: '关键岗位人员', value: '关键岗位人员' },
            ]} />
          <Space size={4}>
            <span style={{ color: '#64748b' }}>题数</span>
            <InputNumber min={1} max={10} value={count} onChange={(v) => setCount(v || 5)} />
          </Space>
          <Button type="primary" icon={<ThunderboltOutlined />} loading={loading} onClick={gen}>生成</Button>
        </Space>
      </Card>

      {material && (
        <Card title="学习材料" style={{ marginBottom: 16 }}
          extra={llmUsed !== null && <Tag color={llmUsed ? 'green' : 'orange'}>{llmUsed ? '大模型生成' : '离线检索'}</Tag>}>
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>{material}</Typography.Paragraph>
          {sources.length > 0 && (
            <Collapse size="small" ghost items={[{
              key: 'src', label: <span style={{ fontSize: 12 }}>参考法规条款（{sources.length}）</span>,
              children: sources.map((s, i) => (
                <div key={i} style={{ fontSize: 12, marginBottom: 6 }}>
                  <Tag color="blue">{s.article_no}</Tag>
                  <span style={{ color: '#475569' }}>《{s.regulation}》</span>
                  <div style={{ color: '#1e293b' }}>{s.content}</div>
                </div>
              )),
            }]} />
          )}
        </Card>
      )}

      {quizzes.length > 0 && (
        <Card title={`随堂测验（${quizzes.length} 题）`}>
          {quizzes.map((q, i) => (
            <div key={i} style={{ marginBottom: 18 }}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>{i + 1}. {q.question}</div>
              {q.options && q.options.length > 0 ? (
                <div style={{ paddingLeft: 4 }}>
                  {q.options.map((o, j) => (
                    <div key={j} style={{
                      padding: '4px 8px', borderRadius: 6, marginBottom: 4,
                      background: o === q.answer ? '#effaf3' : '#f8fafc',
                      border: o === q.answer ? '1px solid #c4e6cf' : '1px solid #eef1f5',
                    }}>
                      {String.fromCharCode(65 + j)}. {o}
                      {o === q.answer && <Tag color="success" style={{ marginLeft: 8 }}>正确答案</Tag>}
                    </div>
                  ))}
                  {q.explain && <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>解析：{q.explain}</div>}
                </div>
              ) : (
                <div style={{ fontSize: 13, color: '#475569' }}>
                  <Tag color="blue">思考题</Tag>
                  {q.explain && <span>参考：{q.explain}</span>}
                </div>
              )}
              {i < quizzes.length - 1 && <Divider style={{ margin: '12px 0' }} />}
            </div>
          ))}
        </Card>
      )}
    </div>
  )
}
