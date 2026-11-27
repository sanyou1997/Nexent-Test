import { useState } from 'react'
import { Button, Modal, Input, Space, Tag, Tooltip, Popconfirm, message } from 'antd'
import { api, errMsg } from '../api/client'

/**
 * 模型预警误报反馈：误报 / 属实 + 理由（误报必填，作为模型调优依据）。
 * 已反馈的行显示判定标签与撤销；撤销后预警退回「待处理」。
 */
export default function AlertFeedback({ row, onDone }: { row: any; onDone: () => void }) {
  const [open, setOpen] = useState(false)
  const [verdict, setVerdict] = useState<'误报' | '属实'>('误报')
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    if (verdict === '误报' && !reason.trim()) { message.warning('标记误报必须填写理由（作为模型调优依据）'); return }
    setSaving(true)
    try {
      await api.post(`/alerts/${row.id}/feedback`, { verdict, reason })
      message.success(verdict === '误报' ? '已标记误报，该模型再次运行将不再产出此对象' : '已标记属实')
      setOpen(false)
      onDone()
    } catch (e) { message.error(errMsg(e)) } finally { setSaving(false) }
  }

  const revoke = async () => {
    try {
      await api.delete(`/alerts/${row.id}/feedback`)
      message.success('已撤销反馈，预警退回待处理')
      onDone()
    } catch (e) { message.error(errMsg(e)) }
  }

  if (row.verdict) {
    return (
      <Space size={2}>
        <Tooltip title={row.feedback_reason || ''}>
          <Tag color={row.verdict === '误报' ? 'orange' : 'green'} style={{ margin: 0 }}>
            {row.verdict}
          </Tag>
        </Tooltip>
        <Popconfirm title="撤销反馈？预警将退回「待处理」，恢复再次产出资格" onConfirm={revoke}>
          <Button size="small" type="link">撤销</Button>
        </Popconfirm>
      </Space>
    )
  }
  return (
    <>
      <Space size={2}>
        <Button size="small" type="link" onClick={() => { setVerdict('误报'); setReason(''); setOpen(true) }}>误报</Button>
        <Button size="small" type="link" onClick={() => { setVerdict('属实'); setReason(''); setOpen(true) }}>属实</Button>
      </Space>
      <Modal title={verdict === '误报' ? '标记误报 · 填写理由' : '标记属实'} open={open}
        onCancel={() => setOpen(false)} onOk={submit} okText="提交" cancelText="取消"
        confirmLoading={saving} width={480}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <div>
            <Tag color="blue">{row.model_key || '未知模型'}</Tag>
            <span style={{ color: '#475569', fontSize: 13 }}>{row.title}</span>
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#475569', marginBottom: 4 }}>
              {verdict === '误报'
                ? '误报理由（必填）——将作为该模型的调优依据，之后该模型不再对此对象重复预警'
                : '属实说明（可选）——确认模型判断准确，留档备查'}
            </div>
            <Input.TextArea rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
              maxLength={300} showCount
              placeholder={verdict === '误报'
                ? '如：该人员为借调人员，数据源未及时更新，属正常情况'
                : '如：经核查情况属实，已转承办部门'} />
          </div>
        </Space>
      </Modal>
    </>
  )
}
