import { useEffect, useState } from 'react'
import {
  Alert, Button, Col, Descriptions, Form, Input, InputNumber, Row, Select, Space, Switch, Tag, message,
} from 'antd'
import { ApiOutlined, CheckCircleFilled, CloseCircleFilled, SaveOutlined, ThunderboltOutlined, KeyOutlined } from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { NexentAgent, invalidateNexentCache, fetchNexentStatus } from '../api/nexent'
import SceneAgentsCard from '../components/nexent/SceneAgentsCard'

interface Settings {
  enabled: boolean
  base_url: string
  api_root: string
  has_api_key: boolean
  api_key_masked: string
  default_agent: string
  attach_context_default: boolean
  timeout: number
}

/** 系统管理 → Nexent 接入（仅管理员）。 */
export default function NexentSettings() {
  const [form] = Form.useForm()
  const [cfg, setCfg] = useState<Settings | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [test, setTest] = useState<any>(null)
  const [agents, setAgents] = useState<NexentAgent[]>([])

  const load = () => api.get('/nexent/settings').then(({ data }) => {
    setCfg(data)
    form.setFieldsValue({ ...data, api_key: '' })
  }).catch((e) => message.error(errMsg(e)))

  useEffect(() => {
    load().then(() => {
      api.get('/nexent/agents').then(({ data }) => setAgents(data.items || [])).catch(() => {})
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const runTest = async () => {
    const v = form.getFieldsValue()
    setTesting(true)
    try {
      const { data } = await api.post('/nexent/settings/test', { base_url: v.base_url, api_key: v.api_key || undefined })
      setTest(data)
      if (data.agents?.length) setAgents(data.agents)
    } catch (e) { message.error(errMsg(e)) } finally { setTesting(false) }
  }

  const save = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      const body: any = {
        enabled: v.enabled, base_url: v.base_url, default_agent: v.default_agent || '',
        attach_context_default: v.attach_context_default, timeout: v.timeout,
      }
      if (v.api_key) body.api_key = v.api_key
      const { data } = await api.put('/nexent/settings', body)
      setCfg(data)
      form.setFieldsValue({ ...data, api_key: '' })
      invalidateNexentCache()
      fetchNexentStatus(true)
      message.success('已保存')
    } catch (e) { message.error(errMsg(e)) } finally { setSaving(false) }
  }

  const clearKey = async () => {
    try {
      const { data } = await api.put('/nexent/settings', { clear_api_key: true })
      setCfg(data); invalidateNexentCache(); fetchNexentStatus(true)
      message.success('已清除 API Key')
    } catch (e) { message.error(errMsg(e)) }
  }

  const ok = (b: boolean) => b ? <CheckCircleFilled style={{ color: '#15803d' }} /> : <CloseCircleFilled style={{ color: '#dc2626' }} />

  return (
    <div className="cx-page">
      <div className="cx-page-head">
        <div>
          <div className="cx-page-title"><ApiOutlined /> Nexent 接入</div>
          <div className="cx-page-sub">配置本系统调用 Nexent 北向接口所用的服务地址与 API Key。Key 只保存在服务端，任何页面都只显示尾号。</div>
        </div>
      </div>

      <Row gutter={16}>
        <Col xs={24} xl={14}>
          <div className="cx-card" style={{ padding: 20 }}>
            <Form form={form} layout="vertical" requiredMark={false}
              initialValues={{ enabled: true, attach_context_default: true, timeout: 600 }}>
              <Form.Item name="enabled" label="启用 Nexent 接入" valuePropName="checked">
                <Switch checkedChildren="启用" unCheckedChildren="停用" />
              </Form.Item>
              <Form.Item name="base_url" label="北向接口地址" rules={[{ required: true, message: '请填写服务地址' }]}
                extra="Docker 部署一般为 http://<Nexent 主机>:5013；也可直接填完整的 …/nb/v1。">
                <Input placeholder="http://127.0.0.1:5013" />
              </Form.Item>
              <Form.Item name="api_key" label={<span><KeyOutlined /> API Key</span>}
                extra={cfg?.has_api_key ? `已配置（${cfg.api_key_masked}）。留空表示不修改。` : '在 Nexent「个人信息」→「生成 API 密钥」中获取。'}>
                <Input.Password placeholder={cfg?.has_api_key ? '留空则保持现有 Key' : '粘贴 Nexent API Key'} autoComplete="new-password" />
              </Form.Item>
              <Form.Item name="default_agent" label="默认智能体" extra="案件工作台的 AI 协审默认使用该智能体；保存或测试连接后可从列表选择。">
                <Select allowClear showSearch placeholder="不指定则取列表第一个"
                  options={agents.map((a) => ({ label: `${a.display_name}（${a.name}）`, value: a.name }))} />
              </Form.Item>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item name="attach_context_default" label="首轮默认附带案件上下文" valuePropName="checked"
                    extra="用户每次发送前仍可取消勾选。">
                    <Switch />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="timeout" label="单次会话超时（秒）">
                    <InputNumber min={30} max={3600} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>
              <Space wrap>
                <Button icon={<ThunderboltOutlined />} loading={testing} onClick={runTest}>测试连接</Button>
                <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={save}>保存</Button>
                {cfg?.has_api_key && <Button danger type="text" onClick={clearKey}>清除已保存的 Key</Button>}
              </Space>
            </Form>
          </div>
        </Col>
        <Col xs={24} xl={10}>
          {test && (
            <div className="cx-card" style={{ padding: 20, marginBottom: 16 }}>
              <div className="cx-section-title">连接测试结果</div>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="接口地址">{test.api_root}</Descriptions.Item>
                <Descriptions.Item label="服务可达">{ok(test.reachable)}</Descriptions.Item>
                <Descriptions.Item label="Key 鉴权">{ok(test.authorized)}</Descriptions.Item>
                <Descriptions.Item label="可用智能体">{test.agents?.length || 0} 个</Descriptions.Item>
              </Descriptions>
              {test.error && <Alert type={test.authorized ? 'info' : 'warning'} showIcon message={test.error} style={{ marginTop: 8 }} />}
              {test.agents?.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  {test.agents.map((a: NexentAgent) => <Tag key={a.name} style={{ marginBottom: 6 }}>{a.display_name}</Tag>)}
                </div>
              )}
            </div>
          )}
          <div className="cx-card" style={{ padding: 20 }}>
            <div className="cx-section-title">接入说明</div>
            <ul className="cx-help-list">
              <li>全系统共用这一个 Key：Nexent 侧的会话都记在该 Key 所属账号下，能用的智能体也以该账号可见范围为准。</li>
              <li>会话归属由本系统登记：每个用户只能看到、续聊、停止自己发起的会话；每次调用写入审计日志。</li>
              <li>在案件工作台发起的首轮提问，可附带案件上下文（基本情况、阅卷摘要、证据与程序校验、量纪说理），发送前可预览、可取消。</li>
              <li>涉密材料只能发往内网部署、使用本地模型的 Nexent；若 Nexent 的对话模型走外部接口，请勿发送真实案件材料。</li>
            </ul>
          </div>
        </Col>
      </Row>
      <SceneAgentsCard agents={agents} />
    </div>
  )
}
