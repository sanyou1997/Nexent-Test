import { useEffect, useState } from 'react'
import { Card, Table, Tag, Button, message, Space, Typography } from 'antd'
import { SafetyCertificateOutlined, AuditOutlined } from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import PageHeader from '../components/PageHeader'

const ACTION_COLOR: Record<string, string> = {
  login: 'blue', logout: 'default', qa_ask: 'purple', petition_create: 'cyan',
  petition_extract: 'cyan', petition_suggest: 'cyan', petition_confirm: 'green',
  petition_dup_check: 'cyan', document_generate: 'geekblue', document_edit: 'geekblue',
  document_confirm: 'green', regulation_ingest: 'orange', user_create: 'red', user_toggle: 'red',
}

export default function Audit() {
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [verify, setVerify] = useState<{ valid: boolean; broken_at: number | null; total: number } | null>(null)

  const load = async (p = 1) => {
    setLoading(true)
    try {
      const { data } = await api.get('/audit', { params: { page: p, per_page: 15 } })
      setRows(data.items || [])
      setTotal(data.total || 0)
      setPage(p)
    } catch (e) { message.error(errMsg(e)) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const doVerify = async () => {
    try {
      const { data } = await api.get('/audit/verify')
      setVerify(data)
      data.valid ? message.success(`哈希链校验通过（${data.total} 条日志，未被篡改）`)
                 : message.error(`哈希链在第 ${data.broken_at} 条断裂，存在篡改！`)
    } catch (e) { message.error(errMsg(e)) }
  }

  return (
    <>
    <PageHeader
      icon={<AuditOutlined />}
      title="审计日志"
      subtitle="追加哈希链 · 防篡改"
      extra={<Button icon={<SafetyCertificateOutlined />} onClick={doVerify}>校验哈希链</Button>} />
    <Card>
      {verify && (
        <Typography.Paragraph>
          链状态：<Tag color={verify.valid ? 'green' : 'red'}>{verify.valid ? '完整' : '已破坏'}</Tag>
          共 {verify.total} 条
        </Typography.Paragraph>
      )}
      <Table rowKey="id" size="small" loading={loading}
        pagination={{ pageSize: 15, current: page, total, onChange: (p: number) => load(p) }}
        dataSource={rows}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 60 },
          { title: '时间', dataIndex: 'created_at', width: 160 },
          { title: '用户', dataIndex: 'username', width: 100 },
          { title: '操作', dataIndex: 'action', width: 140,
            render: (a: string) => <Tag color={ACTION_COLOR[a] || 'default'}>{a}</Tag> },
          { title: '对象', width: 120,
            render: (_: any, r: any) => r.resource_type ? `${r.resource_type}#${r.resource_id}` : '-' },
          { title: '详情', dataIndex: 'detail', ellipsis: true,
            render: (d: any) => <code style={{ fontSize: 12 }}>{JSON.stringify(d)}</code> },
          { title: '哈希(前16)', dataIndex: 'hash', width: 140,
            render: (h: string) => <code style={{ fontSize: 12 }}>{h}</code> },
        ]} />
    </Card>
    </>
  )
}
