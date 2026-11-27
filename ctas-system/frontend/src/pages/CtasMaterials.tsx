import { useEffect, useState } from 'react'
import {
  Card, Row, Col, Table, Button, Space, Tag, Modal, Form, Input, Select,
  Upload, message, Descriptions, Statistic, Alert, Empty,
} from 'antd'
import {
  PlusOutlined, ApartmentOutlined, UploadOutlined, FileTextOutlined,
  CheckCircleOutlined, CloseCircleOutlined, SearchOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import PageHeader from '../components/PageHeader'
import type { UploadFile } from 'antd'

const { TextArea } = Input

const STATUS_TAG: Record<string, string> = {
  已识别: 'green', 识别失败: 'red', 待识别: 'default',
}

/** CTAS 送审材料汇聚中心：按移送审理标准清单登记 → 上传/OCR 落库 → 完整度检查。
 *  现实锚点：案主（审查调查组）直接提供全过程材料，材料齐不齐必须可见、可评估。 */
export default function CtasMaterials() {
  const [trials, setTrials] = useState<any[]>([])
  const [trialId, setTrialId] = useState<number | undefined>()
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [checklist, setChecklist] = useState<{ categories: string[]; required: any[] }>({ categories: [], required: [] })
  const [completeness, setCompleteness] = useState<any>(null)
  const [checking, setChecking] = useState(false)
  const [fCategory, setFCategory] = useState('')

  // 登记/编辑弹窗
  const [editOpen, setEditOpen] = useState(false)
  const [editing, setEditing] = useState<any>(null)
  const [form] = Form.useForm()

  // OCR 结果预览
  const [ocrPreview, setOcrPreview] = useState<any>(null)

  useEffect(() => {
    api.get('/trials?per_page=200').then(({ data }: any) => setTrials(data.items || [])).catch(() => setTrials([]))
    api.get('/trial-materials/checklist').then(({ data }: any) => setChecklist(data)).catch(() => {})
  }, [])

  const load = () => {
    if (!trialId) { setRows([]); setTotal(0); setCompleteness(null); return }
    setLoading(true)
    api.get(`/trial-materials?trial_id=${trialId}`)
      .then(({ data }: any) => { setRows(data.items || []); setTotal(data.total || 0) })
      .catch(() => { setRows([]); setTotal(0) })
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [trialId])

  const runCheck = () => {
    if (!trialId) { message.warning('请先选择审理事项'); return }
    setChecking(true)
    api.post('/trial-materials/completeness', { trial_id: trialId })
      .then(({ data }: any) => setCompleteness(data))
      .catch((e: any) => message.error(errMsg(e)))
      .finally(() => setChecking(false))
  }

  const openCreate = () => {
    if (!trialId) { message.warning('请先选择审理事项'); return }
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ source: '审查调查组', is_original: '原件' })
    setEditOpen(true)
  }
  const openEdit = (r: any) => {
    setEditing(r)
    form.setFieldsValue({ ...r })
    setEditOpen(true)
  }
  const submitEdit = async () => {
    const v = await form.validateFields()
    const body = { ...v, trial_id: trialId }
    const done = () => { setEditOpen(false); load() }
    if (editing) {
      api.put(`/trial-materials/${editing.id}`, body).then(done).catch((e: any) => message.error(errMsg(e)))
    } else {
      api.post('/trial-materials', body).then(() => { message.success('已登记'); done() }).catch((e: any) => message.error(errMsg(e)))
    }
  }
  const del = (r: any) => {
    Modal.confirm({
      title: `删除材料「${r.name}」？`, okText: '删除', okButtonProps: { danger: true },
      onOk: () => api.delete(`/trial-materials/${r.id}`).then(() => { message.success('已删除'); load() })
        .catch((e: any) => message.error(errMsg(e))),
    })
  }

  // OCR 上传（antd Upload 自定义请求，直连后端 multipart）
  // 注意：材料 id 必须由调用处显式传入——beforeUpload 里先 setState 再读取会拿到旧值
  const doUpload = (file: File, mid: number) => {
    const fd = new FormData()
    fd.append('file', file)
    message.loading({ content: `OCR 识别中：${file.name}`, key: 'ocr', duration: 0 })
    api.post(`/trial-materials/${mid}/ocr`, fd, { timeout: 300000 })
      .then(({ data }: any) => {
        message.success({ content: `识别完成（${data.ocr_chars} 字）`, key: 'ocr' })
        setOcrPreview(data)
        load()
      })
      .catch((e: any) => message.error({ content: errMsg(e), key: 'ocr' }))
    return false // 阻止 antd 自动上传
  }

  const columns = [
    { title: '名称', dataIndex: 'name', ellipsis: true, render: (v: string, r: any) => v || r.category },
    { title: '类别', dataIndex: 'category', width: 120, render: (v: string, r: any) => r.required ? <Tag color="blue">{v}</Tag> : <Tag>{v}</Tag> },
    { title: '序号/档号', dataIndex: 'seq', width: 100, ellipsis: true },
    { title: '页数', dataIndex: 'pages', width: 64 },
    { title: '形成时间', dataIndex: 'formed_at', width: 110 },
    { title: '来源', dataIndex: 'source', width: 110, ellipsis: true },
    { title: '原件', dataIndex: 'is_original', width: 70 },
    {
      title: 'OCR', key: 'ocr', width: 110,
      render: (_: any, r: any) => (
        <Tag color={STATUS_TAG[r.ocr_status] || 'default'}>
          {r.ocr_status || '待识别'}{r.ocr_chars ? ` · ${r.ocr_chars}字` : ''}
        </Tag>
      ),
    },
    {
      title: '操作', key: 'op', width: 210, fixed: 'right' as const,
      render: (_: any, r: any) => (
        <Space size={4}>
          <Upload accept=".jpg,.jpeg,.png,.pdf" showUploadList={false} beforeUpload={(f) => doUpload(f, r.id)}>
            <Button size="small" icon={<UploadOutlined />}>上传识别</Button>
          </Upload>
          <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
          <Button size="small" danger onClick={() => del(r)}>删除</Button>
        </Space>
      ),
    },
  ]

  const levelColor = completeness?.level === '完整' ? 'green' : completeness?.level === '部分缺失' ? 'orange' : 'red'

  return (
    <div>
      <PageHeader
        icon={<ApartmentOutlined />}
        title="送审材料汇聚中心"
        subtitle="按移送审理标准清单登记案主提供的全过程材料 · 上传原件 OCR 落库 · 完整度一眼可查"
      />

      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap>
          <span>审理事项：</span>
          <Select
            showSearch optionFilterProp="label" placeholder="选择审理事项" style={{ width: 380 }}
            value={trialId}
            onChange={(v) => { setTrialId(v); setCompleteness(null) }}
            options={trials.map((t: any) => ({
              label: `${t.case_code || ''} ${t.case_subject || ''} ${t.title || ''}`.trim(),
              value: t.id,
            }))}
          />
          <Button type="primary" icon={<SearchOutlined />} loading={checking} onClick={runCheck} disabled={!trialId}>
            完整度检查
          </Button>
          <Button icon={<PlusOutlined />} onClick={openCreate} disabled={!trialId}>登记材料</Button>
          <Button icon={<ReloadOutlined />} onClick={load} disabled={!trialId}>刷新</Button>
        </Space>
      </Card>

      {trialId && (
        <Row gutter={[12, 12]}>
          <Col xs={24} lg={16}>
            <Card size="small" title={<span><FileTextOutlined /> 已登记材料（{total}）</span>}
              extra={
                <Select placeholder="按类别筛选" allowClear style={{ width: 150 }} value={fCategory || undefined}
                  onChange={(v) => setFCategory(v || '')}
                  options={checklist.categories.map((c) => ({ label: c, value: c }))} />
              }>
              <Table
                rowKey="id" size="small" loading={loading}
                dataSource={fCategory ? rows.filter((r) => r.category === fCategory) : rows}
                columns={columns} scroll={{ x: 1100 }} pagination={false}
              />
              {!rows.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未登记材料" style={{ marginTop: 12 }} />}
            </Card>
          </Col>
          <Col xs={24} lg={8}>
            <Card size="small" title="移送审理标准必备清单（7 项）">
              {(completeness ? completeness.items : checklist.required.map((c: any) => ({ ...c, status: '未检查' }))).map((it: any, i: number) => (
                <div key={i} style={{ display: 'flex', gap: 8, padding: '6px 0', borderBottom: '1px solid #f6f8fb', alignItems: 'flex-start' }}>
                  {it.status === '已登记'
                    ? <CheckCircleOutlined style={{ color: '#15803d', marginTop: 3 }} />
                    : it.status === '缺失'
                      ? <CloseCircleOutlined style={{ color: '#b91c1c', marginTop: 3 }} />
                      : <SearchOutlined style={{ color: '#b6c0cf', marginTop: 3 }} />}
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 13 }}>{it.label}</div>
                    {it.status === '已登记' && (
                      <div style={{ fontSize: 12, color: '#64748b' }}>
                        已登记 {it.count} 份 · OCR 已识别 {it.ocr_done ?? 0} 份
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </Card>

            {completeness && (
              <Card size="small" title="完整度结论" style={{ marginTop: 12 }}>
                <Statistic
                  title="必备材料齐备率"
                  value={Math.round(completeness.completeness * 100)}
                  suffix="%"
                  valueStyle={{ color: levelColor }}
                />
                <div style={{ margin: '8px 0' }}>
                  整体结论：<Tag color={levelColor}>{completeness.level}</Tag>
                  <span style={{ marginLeft: 8, fontSize: 12, color: '#64748b' }}>
                    必备 {completeness.required_done}/{completeness.required_total} · 材料共 {completeness.material_total} 份 · OCR 已识别 {completeness.ocr_done} 份
                  </span>
                </div>
                {completeness.missing?.length ? (
                  <Alert type="warning" showIcon message="缺失必备材料" description={
                    <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                      {completeness.missing.map((m: string, i: number) => <li key={i}>{m}</li>)}
                    </ul>
                  } />
                ) : (
                  <Alert type="success" showIcon message="必备材料已齐备，可进入实质审理（仍以承办人按卷核对为准）" />
                )}
                <div style={{ fontSize: 11.5, color: '#94a3b8', marginTop: 8 }}>{completeness.note}</div>
              </Card>
            )}
          </Col>
        </Row>
      )}

      {!trialId && (
        <Card><Empty description="请先选择审理事项，开始汇聚送审材料" /></Card>
      )}

      {/* 登记 / 编辑材料 */}
      <Modal
        title={editing ? `编辑材料 · ${editing.name}` : '登记送审材料'}
        open={editOpen} onOk={submitEdit} onCancel={() => setEditOpen(false)}
        destroyOnClose width={640} okText="保存"
      >
        <Form form={form} layout="vertical">
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item label="材料类别" name="category" rules={[{ required: true, message: '请选择类别' }]}>
                <Select placeholder="选择（必备类别自动标记）"
                  options={checklist.categories.map((c) => ({ label: c, value: c }))} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="材料名称" name="name"><Input placeholder="如：审查调查报告（张某案）" /></Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="序号 / 档号" name="seq"><Input placeholder="如：卷1-1" /></Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="页数" name="pages"><Input type="number" placeholder="0" /></Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="形成时间" name="formed_at"><Input placeholder="如：2026-08-15" /></Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="来源" name="source"><Input placeholder="如：审查调查组" /></Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="原件 / 复制件" name="is_original">
                <Select options={[{ label: '原件', value: '原件' }, { label: '复制件', value: '复制件' }, { label: '复印件', value: '复印件' }]} />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item label="签注 / 核对情况" name="sign_note">
                <Input placeholder="如：已与被审查调查人见面核对并签注“属实”" />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item label="备注" name="note"><TextArea rows={2} /></Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      {/* OCR 结果预览 */}
      <Modal
        title={`OCR 识别结果 · ${ocrPreview?.file_name || ''}`}
        open={!!ocrPreview} onCancel={() => setOcrPreview(null)} width={720}
        footer={[<Button key="close" onClick={() => setOcrPreview(null)}>关闭</Button>]}
      >
        {ocrPreview && (
          <Descriptions column={1} size="small" bordered style={{ marginBottom: 12 }}>
            <Descriptions.Item label="所属材料">{ocrPreview.name}（{ocrPreview.category}）</Descriptions.Item>
            <Descriptions.Item label="识别状态">
              <Tag color={STATUS_TAG[ocrPreview.ocr_status]}>{ocrPreview.ocr_status}</Tag> · {ocrPreview.ocr_chars} 字
            </Descriptions.Item>
          </Descriptions>
        )}
        <div style={{ whiteSpace: 'pre-wrap', maxHeight: 380, overflow: 'auto', background: '#f8fafc', padding: 12, fontSize: 13 }}>
          {ocrPreview?.text_preview || '（无预览文本）'}
        </div>
      </Modal>
    </div>
  )
}
