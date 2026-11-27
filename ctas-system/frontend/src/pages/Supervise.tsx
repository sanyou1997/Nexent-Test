import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Tag, Space, message, Switch, Select, InputNumber, Input,
  Typography, Empty, Statistic, Row, Col, Spin, Tabs, Upload, Drawer, Descriptions, Modal, Popconfirm, Alert,
  Progress,
} from 'antd'
import {
  ExperimentOutlined, ThunderboltOutlined, ReloadOutlined, DatabaseOutlined,
  BuildOutlined, AlertOutlined, PlusOutlined, DeleteOutlined, RiseOutlined,
  FallOutlined, RollbackOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { useAuth } from '../auth'
import PipelineStudio from './PipelineStudio'
import AlertFeedback from '../components/AlertFeedback'
import PageHeader from '../components/PageHeader'

const LEVEL_COLOR: Record<string, string> = { 高: 'red', 中: 'orange', 低: 'green', 提示: 'blue' }
const CATE_COLOR: Record<string, string> = {
  时限类: 'volcano', 聚集类: 'purple', 合规类: 'geekblue', 资金类: 'gold', 碰撞类: 'cyan',
}
const RULE_OPTIONS = [
  { value: 'intersection', label: '交集命中（同一对象同时出现在两个数据集）' },
  { value: 'duplicate', label: '同库重复（同一对象在单数据集重复出现）' },
  { value: 'threshold', label: '数值阈值（按关键字段聚合超阈值）' },
  { value: 'inclusion', label: '包含命中（A 中对象出现在 B 的某字段中）' },
]

export default function Supervise() {
  const { user } = useAuth()
  const [tab, setTab] = useState('data')
  const [datasets, setDatasets] = useState<any[]>([])
  const [models, setModels] = useState<any[]>([])
  const [alerts, setAlerts] = useState<any[]>([])
  const [previewDs, setPreviewDs] = useState<any | null>(null)
  const [scanning, setScanning] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [building, setBuilding] = useState(false)
  const [runningKey, setRunningKey] = useState('')
  const [runResult, setRunResult] = useState<any>(null)

  // 误报反馈分析（模型迭代闭环）
  const [fbStats, setFbStats] = useState<any>(null)
  const [fbRows, setFbRows] = useState<any[]>([])
  const [fbLoading, setFbLoading] = useState(false)

  // 搭建模型表单
  const [f, setF] = useState<any>({ name: '', dataset_a: undefined, dataset_b: undefined,
    join_key_a: undefined, join_key_b: undefined, rule: 'intersection', severity: '中',
    agg: 'sum', agg_field: undefined, value: 0, field_b: undefined })

  const loadDatasets = () => api.get('/datasets').then(({ data }) => setDatasets(data)).catch(() => setDatasets([]))
  const loadModels = () => api.get('/supervise/models').then(({ data }) => setModels(data)).catch(() => setModels([]))
  const loadAlerts = () => api.get('/alerts?source=数据碰撞').then(({ data }) => setAlerts(data)).catch(() => setAlerts([]))
  const loadFeedback = () => {
    setFbLoading(true)
    Promise.all([
      api.get('/alerts/feedback/stats').catch(() => null),
      api.get('/alerts/feedback').catch(() => null),
    ]).then(([s, r]) => {
      setFbStats(s?.data ?? null)
      setFbRows(r?.data ?? [])
    }).finally(() => setFbLoading(false))
  }

  useEffect(() => {
    if (tab === 'data' || tab === 'studio') loadDatasets()
    if (tab === 'repo' || tab === 'build') loadModels()
    if (tab === 'alerts') loadAlerts()
    if (tab === 'feedback') loadFeedback()
  }, [tab])

  const dsCols = (id?: number) => (datasets.find((d) => d.id === id)?.columns || [])
  const dsName = (id?: number) => (datasets.find((d) => d.id === id)?.name || '')

  // 数据接入
  const uploadDs = async (file: File) => {
    setUploading(true)
    try {
      const fd = new FormData(); fd.append('file', file); fd.append('name', file.name)
      const { data } = await api.post('/datasets/upload', fd, { timeout: 120000 })
      message.success(`数据集「${data.name}」已接入（${data.row_count} 行 / ${data.columns.length} 字段）`)
      loadDatasets()
    } catch (e) { message.error(errMsg(e)) } finally { setUploading(false) }
    return false
  }
  const openPreview = async (d: any) => { setPreviewDs((await api.get(`/datasets/${d.id}`)).data) }
  const delDs = async (id: number) => { await api.delete(`/datasets/${id}`); loadDatasets(); message.success('已删除') }

  // 模型仓库
  const onToggle = (r: any, enabled: boolean) => {
    api.put(`/supervise/models/${r.key}`, { enabled }).then(() => message.success('已更新')).catch((e) => message.error(errMsg(e)))
  }
  const doScan = () => {
    setScanning(true)
    api.post('/alerts/scan').then(({ data }) => { message.success(`扫描完成，生成 ${data.generated} 条预警`); loadModels() })
      .catch((e) => message.error(errMsg(e))).finally(() => setScanning(false))
  }
  const runModel = async (m: any) => {
    setRunningKey(m.key)
    try {
      const { data } = await api.post(`/supervise/models/${m.key}/run`)
      setRunResult(data)
      const sup = data.suppressed ? `，按误报反馈抑制 ${data.suppressed} 条` : ''
      message.success(`${data.model}：命中 ${data.total_hits} 条，已写入预警中枢 ${data.upserted} 条${sup}`)
      loadAlerts()
    } catch (e) { message.error(errMsg(e)) } finally { setRunningKey('') }
  }
  const delModel = async (m: any) => {
    try {
      await api.delete(`/supervise/models/${m.key}`)
      message.success(`已删除模型「${m.name}」（已产出的预警保留在预警中枢）`)
      loadModels()
    } catch (e) { message.error(errMsg(e)) }
  }

  // 搭建模型
  const build = async () => {
    if (!f.name || f.dataset_a == null || f.join_key_a == null) { message.warning('请填写模型名称、数据集A与关联字段'); return }
    if (f.rule === 'intersection' && f.dataset_b == null) { message.warning('交集模型需选择数据集B'); return }
    setBuilding(true)
    const params: any = {}
    if (f.rule === 'threshold') { params.agg = f.agg; params.agg_field = f.agg_field; params.value = f.value }
    if (f.rule === 'inclusion') { params.field_b = f.field_b }
    try {
      const { data } = await api.post('/supervise/models/build', {
        name: f.name, dataset_a: f.dataset_a, dataset_b: f.dataset_b ?? f.dataset_a,
        join_key_a: f.join_key_a, join_key_b: f.join_key_b ?? f.join_key_a,
        rule: f.rule, params, severity: f.severity,
      })
      message.success(`模型「${data.name}」已建好，可在模型仓库运行`)
      setTab('repo'); loadModels()
      Modal.confirm({ title: '立即运行该模型？', content: `${data.name} 将执行碰撞并生成预警`, okText: '运行',
        onOk: () => runModel(data) })
    } catch (e) { message.error(errMsg(e)) } finally { setBuilding(false) }
  }

  const modelColumns = [
    { title: '模型', dataIndex: 'name', width: 200, render: (s: string, r: any) => (
      <div><div style={{ fontWeight: 500 }}>{s}</div>
        <Tag color={CATE_COLOR[r.category] || 'default'} style={{ marginTop: 2 }}>{r.category}</Tag>
        {r.kind === 'user' && <Tag color="cyan">自建</Tag>}</div>) },
    { title: '说明', dataIndex: 'description', ellipsis: true,
      render: (t: string, r: any) => r.kind === 'user'
        ? <Typography.Text type="secondary">{t}（{r.config?.ds_a_name} × {r.config?.ds_b_name}）</Typography.Text> : t },
    { title: '严重级别', dataIndex: 'severity', width: 90,
      render: (s: string) => <Tag color={LEVEL_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '启用', dataIndex: 'enabled', width: 70,
      render: (en: boolean, r: any) => r.builtin ? <Switch checked={en} onChange={(v) => onToggle(r, v)} />
        : <Tag color="cyan">自建</Tag> },
    { title: '操作', key: 'op', width: 160, render: (_: any, r: any) => (
      <Space>
        {r.kind === 'user'
          ? <Button size="small" type="primary" loading={runningKey === r.key} onClick={() => runModel(r)}>运行</Button>
          : <span style={{ color: '#b6c0cf' }}>—</span>}
        {r.kind === 'user' && <PopconfirmDel onOk={() => delModel(r)} />}
      </Space>) },
  ]

  return (
    <div>
      <PageHeader
        icon={<ExperimentOutlined />}
        title="大数据监督模型工厂"
        subtitle="内置规则模型持续运行 · 上传数据集 → 选关联字段与比对规则（交集 / 重复 / 阈值 / 包含）→ 碰撞生成预警 · 支持零代码拖拽建模" />

      <Tabs activeKey={tab} onChange={setTab} items={[
        {
          key: 'data', label: <span><DatabaseOutlined /> 数据接入</span>,
          children: (
            <Card size="small">
              <Space style={{ marginBottom: 12 }}>
                <Upload accept=".csv,.xlsx,.xls" showUploadList={false} beforeUpload={uploadDs}>
                  <Button type="primary" icon={<PlusOutlined />} loading={uploading}>上传数据集（CSV/Excel）</Button>
                </Upload>
                <Typography.Text type="secondary">如：公职人员名册、补贴发放表、中标人名册、工商登记等</Typography.Text>
              </Space>
              <Table rowKey="id" size="small" dataSource={datasets} pagination={{ pageSize: 10 }}
                locale={{ emptyText: <Empty description="暂无数据集，请上传" /> }}
                columns={[
                  { title: '名称', dataIndex: 'name', ellipsis: true },
                  { title: '字段', dataIndex: 'columns', ellipsis: true,
                    render: (cs: string[]) => cs?.map((c) => <Tag key={c}>{c}</Tag>) },
                  { title: '行数', dataIndex: 'row_count', width: 80 },
                  { title: '接入人', dataIndex: 'created_by', width: 90 },
                  { title: '接入时间', dataIndex: 'created_at', width: 140 },
                  { title: '操作', width: 120, render: (_: any, r: any) => (
                    <Space>
                      <Button size="small" onClick={() => openPreview(r)}>预览</Button>
                      <PopconfirmDel onOk={() => delDs(r.id)} />
                    </Space>) },
                ]} />
            </Card>
          ),
        },
        {
          key: 'build', label: <span><BuildOutlined /> 搭建模型</span>,
          children: (
            <Card size="small" title="拖拽式建模向导">
              <Row gutter={16}>
                <Col span={12}>
                  <Form item="模型名称" ><Input placeholder="如：公职人员领取灵活就业补贴碰撞" value={f.name}
                    onChange={(e) => setF({ ...f, name: e.target.value })} /></Form>
                  <Form item="数据集 A"><Select placeholder="选择数据集A" style={{ width: '100%' }}
                    value={f.dataset_a} onChange={(v) => setF({ ...f, dataset_a: v, join_key_a: undefined })}
                    options={datasets.map((d) => ({ label: `${d.name}（${d.row_count}行）`, value: d.id }))} /></Form>
                  <Form item="关联字段 A（Join Key）"><Select placeholder="选择关联字段" style={{ width: '100%' }}
                    value={f.join_key_a} onChange={(v) => setF({ ...f, join_key_a: v })}
                    options={dsCols(f.dataset_a).map((c: string) => ({ label: c, value: c }))} /></Form>
                  <Form item="比对规则"><Select style={{ width: '100%' }} value={f.rule}
                    onChange={(v) => setF({ ...f, rule: v, dataset_b: undefined, field_b: undefined })}
                    options={RULE_OPTIONS} /></Form>
                  {f.rule === 'intersection' || f.rule === 'inclusion' ? (
                    <Form item="数据集 B"><Select placeholder="选择数据集B" style={{ width: '100%' }}
                      value={f.dataset_b} onChange={(v) => setF({ ...f, dataset_b: v, join_key_b: undefined })}
                      options={datasets.filter((d) => d.id !== f.dataset_a).map((d) => ({ label: `${d.name}（${d.row_count}行）`, value: d.id }))} /></Form>
                  ) : null}
                  {f.rule === 'intersection' ? (
                    <Form item="关联字段 B"><Select placeholder="选 B 的关联字段" style={{ width: '100%' }}
                      value={f.join_key_b} onChange={(v) => setF({ ...f, join_key_b: v })}
                      options={dsCols(f.dataset_b).map((c: string) => ({ label: c, value: c }))} /></Form>
                  ) : null}
                </Col>
                <Col span={12}>
                  {f.rule === 'threshold' && (
                    <>
                      <Form item="聚合字段"><Select style={{ width: '100%' }} value={f.agg_field}
                        onChange={(v) => setF({ ...f, agg_field: v })}
                        options={dsCols(f.dataset_a).map((c: string) => ({ label: c, value: c }))} /></Form>
                      <Form item="聚合方式"><Select style={{ width: '100%' }} value={f.agg}
                        onChange={(v) => setF({ ...f, agg: v })}
                        options={[{ label: '求和 sum', value: 'sum' }, { label: '平均 avg', value: 'avg' }, { label: '计数 count', value: 'count' }]} /></Form>
                      <Form item="阈值"><InputNumber style={{ width: '100%' }} min={0} value={f.value}
                        onChange={(v) => setF({ ...f, value: v })} /></Form>
                    </>
                  )}
                  {f.rule === 'inclusion' && (
                    <Form item="B 中比对字段"><Select style={{ width: '100%' }} value={f.field_b}
                      onChange={(v) => setF({ ...f, field_b: v })}
                      options={dsCols(f.dataset_b).map((c: string) => ({ label: c, value: c }))} /></Form>
                  )}
                  <Form item="预警级别"><Select style={{ width: '100%' }} value={f.severity}
                    onChange={(v) => setF({ ...f, severity: v })}
                    options={['高', '中', '低', '提示'].map((x) => ({ label: x, value: x }))} /></Form>
                  <Alert type="info" showIcon style={{ marginTop: 12 }}
                    message={RULE_OPTIONS.find((r) => r.value === f.rule)?.label} />
                </Col>
              </Row>
              <div style={{ marginTop: 16, textAlign: 'right' }}>
                <Button type="primary" loading={building} onClick={build}>保存模型</Button>
              </div>
            </Card>
          ),
        },
        {
          key: 'studio', label: <span><ExperimentOutlined /> 拖拽建模</span>,
          children: <PipelineStudio datasets={datasets} />,
        },
        {
          key: 'repo', label: <span><ExperimentOutlined /> 模型仓库</span>,
          children: (
            <Card size="small" extra={
              <Space>
                <Button type="primary" icon={<ThunderboltOutlined />} loading={scanning} onClick={doScan}>一键扫描（内置模型）</Button>
                <Button icon={<ReloadOutlined />} onClick={loadModels}>刷新</Button>
              </Space>
            }>
              <Table rowKey="key" size="small" dataSource={models} pagination={false} columns={modelColumns} />
            </Card>
          ),
        },
        {
          key: 'alerts', label: <span><AlertOutlined /> 碰撞预警</span>,
          children: (
            <Card size="small" extra={<Button icon={<ReloadOutlined />} onClick={loadAlerts}>刷新</Button>}>
              <Table rowKey="id" size="small" dataSource={alerts} pagination={{ pageSize: 10 }}
                locale={{ emptyText: <Empty description="暂无碰撞预警，请运行自建模型" /> }}
                columns={[
                  { title: '级别', dataIndex: 'level', width: 70, render: (l: string) => <Tag color={LEVEL_COLOR[l] || 'default'}>{l}</Tag> },
                  { title: '标题', dataIndex: 'title', ellipsis: true },
                  { title: '详情', dataIndex: 'detail', ellipsis: true, render: (t: string) => <Typography.Text type="warning">{t}</Typography.Text> },
                  { title: '状态', dataIndex: 'status', width: 90 },
                  { title: '误报反馈', key: 'fb', width: 130, render: (_: any, r: any) => (
                    <AlertFeedback row={r} onDone={loadAlerts} />) },
                  { title: '最近发现', dataIndex: 'last_seen', width: 140 },
                ]} />
            </Card>
          ),
        },
        {
          key: 'feedback', label: <span><RiseOutlined /> 误报分析</span>,
          children: (
            <Spin spinning={fbLoading}>
              <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
                <Col span={6}><Card size="small"><Statistic title="模型预警总数" value={fbStats?.total_alerts ?? 0} /></Card></Col>
                <Col span={6}><Card size="small"><Statistic title="已人工反馈" value={fbStats?.feedback ?? 0} /></Card></Col>
                <Col span={6}><Card size="small"><Statistic title="误报" value={fbStats?.false ?? 0}
                  valueStyle={{ color: (fbStats?.false ?? 0) ? '#b45309' : undefined }} /></Card></Col>
                <Col span={6}><Card size="small"><Statistic title="属实" value={fbStats?.true ?? 0}
                  valueStyle={{ color: '#15803d' }} /></Card></Col>
              </Row>
              <Alert type="info" showIcon style={{ marginBottom: 12 }}
                message="误报反馈直接驱动模型行为：被判「误报」的对象，对应模型再次运行将被抑制、不再重复打扰；误报率持续偏高的模型，建议上调阈值或收紧条件后重新运行。" />
              <Card size="small" title={<span><RiseOutlined /> 分模型误报率（调优依据）</span>}
                style={{ marginBottom: 12 }}
                extra={<Button size="small" icon={<ReloadOutlined />} onClick={loadFeedback}>刷新</Button>}>
                <Table rowKey="model_key" size="small" pagination={false}
                  locale={{ emptyText: <Empty description="暂无模型产出记录" /> }}
                  dataSource={fbStats?.per_model ?? []}
                  columns={[
                    { title: '模型', dataIndex: 'name', ellipsis: true,
                      render: (n: string, r: any) => (
                        <span>{n}<Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 6 }}>{r.model_key}</Typography.Text></span>) },
                    { title: '预警数', dataIndex: 'alerts', width: 80 },
                    { title: '已反馈', dataIndex: 'feedback', width: 80 },
                    { title: '误报', dataIndex: 'false', width: 70,
                      render: (v: number) => <span style={{ color: v ? '#b45309' : undefined }}>{v}</span> },
                    { title: '属实', dataIndex: 'true', width: 70,
                      render: (v: number) => <span style={{ color: v ? '#15803d' : undefined }}>{v}</span> },
                    { title: '误报率', key: 'rate', width: 150,
                      render: (_: any, r: any) => r.false_rate == null
                        ? <Typography.Text type="secondary">—（暂无反馈）</Typography.Text>
                        : (
                          <Space>
                            <Progress percent={Math.round(r.false_rate * 100)} size="small"
                              strokeColor={r.false_rate >= 0.5 ? '#b91c1c' : r.false_rate >= 0.25 ? '#b45309' : '#15803d'}
                              style={{ width: 90, marginBottom: 0 }} />
                            {r.false_rate >= 0.5 && <Tag color="red">建议调优</Tag>}
                          </Space>) },
                  ]} />
              </Card>
              <Card size="small" title={<span><FallOutlined /> 反馈记录</span>}>
                <Table rowKey="id" size="small" pagination={{ pageSize: 10 }}
                  locale={{ emptyText: <Empty description="暂无反馈记录，可在「碰撞预警」或预警中枢对模型预警标记误报/属实" /> }}
                  dataSource={fbRows}
                  columns={[
                    { title: '模型', dataIndex: 'model_key', width: 150, ellipsis: true },
                    { title: '对象', dataIndex: 'obj_key', width: 130, ellipsis: true },
                    { title: '判定', dataIndex: 'verdict', width: 80,
                      render: (v: string) => <Tag color={v === '误报' ? 'orange' : 'green'}>{v}</Tag> },
                    { title: '理由/说明', dataIndex: 'reason', ellipsis: true,
                      render: (t: string) => t || <Typography.Text type="secondary">—</Typography.Text> },
                    { title: '反馈人', dataIndex: 'by', width: 90 },
                    { title: '时间', dataIndex: 'at', width: 140 },
                    { title: '操作', key: 'op', width: 90, render: (_: any, r: any) => (
                      <Popconfirm title="撤销该反馈？对应预警将退回待处理，恢复再次产出资格"
                        onConfirm={async () => {
                          try {
                            await api.delete(`/alerts/${r.alert_id}/feedback`)
                            message.success('已撤销'); loadFeedback()
                          } catch (e) { message.error(errMsg(e)) }
                        }}>
                        <Button size="small" danger icon={<RollbackOutlined />}>撤销</Button>
                      </Popconfirm>) },
                  ]} />
              </Card>
            </Spin>
          ),
        },
      ]} />

      {/* 数据集预览抽屉 */}
      <Drawer title={previewDs ? `数据集预览 · ${previewDs.name}` : ''} open={!!previewDs} width={760}
        onClose={() => setPreviewDs(null)}>
        {previewDs && (
          <>
            <Descriptions size="small" bordered column={2} style={{ marginBottom: 12 }}>
              <Descriptions.Item label="行数">{previewDs.row_count}</Descriptions.Item>
              <Descriptions.Item label="字段数">{previewDs.columns?.length}</Descriptions.Item>
            </Descriptions>
            <Table rowKey={(_: any, i?: number) => String(i ?? 0)} size="small" pagination={{ pageSize: 10 }}
              columns={previewDs.columns.map((c: string) => ({ title: c, dataIndex: c, ellipsis: true }))}
              dataSource={previewDs.preview} />
          </>
        )}
      </Drawer>

      {runResult && (
        <Drawer title={`碰撞结果 · ${runResult.model}`} open={!!runResult} width={620}
          onClose={() => setRunResult(null)}>
          <Statistic title="命中条数" value={runResult.total_hits} />
          <Statistic title="写入预警中枢" value={runResult.upserted} style={{ marginTop: 8 }} />
          <Typography.Title level={5} style={{ marginTop: 12 }}>命中样例</Typography.Title>
          <Table rowKey={(_: any, i?: number) => String(i ?? 0)} size="small" pagination={false}
            columns={[{ title: '姓名', dataIndex: 'name', width: 90, render: (v: string) => v || '—' }, { title: '关联键（脱敏）', dataIndex: 'key', width: 170 }, { title: '说明', dataIndex: 'reason', ellipsis: true }]}
            dataSource={runResult.sample} />
        </Drawer>
      )}
    </div>
  )
}

function Form({ item, children }: { item: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 12, color: '#475569', marginBottom: 4 }}>{item}</div>
      {children}
    </div>
  )
}
function PopconfirmDel({ onOk }: { onOk: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <Popconfirm title="确认删除？" open={open} onConfirm={() => { setOpen(false); onOk() }}
      onCancel={() => setOpen(false)}>
      <Button size="small" danger icon={<DeleteOutlined />} onClick={() => setOpen(true)} />
    </Popconfirm>
  )
}
