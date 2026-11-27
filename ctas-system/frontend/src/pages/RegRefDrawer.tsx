import { useEffect, useState } from 'react'
import {
  Drawer, Input, Empty, Spin, Tag, Typography, Space, Button, Switch,
} from 'antd'
import { BookOutlined } from '@ant-design/icons'
import { api } from '../api/client'

const { Paragraph, Text } = Typography

/**
 * 办案「相关依据检索」抽屉：复用法规知识库条款级检索（/api/regulations/articles?q=）。
 * 可在审理 / 审查调查 / 党风等办案页面内嵌调用，随问随查权威依据。
 * 命中结果仅作办案参考，不作为正式处分依据，须结合官方原件核对。
 * 支持「仅看必须项」：只列出标记为「案件审理必须查阅」的权威依据。
 */
export default function RegRefDrawer({
  open, onClose, seed = '', title = '相关依据检索',
}: {
  open: boolean
  onClose: () => void
  seed?: string
  title?: string
}) {
  const [q, setQ] = useState(seed)
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const [reqOnly, setReqOnly] = useState(false)

  const run = (query: string, p = 1, onlyReq = reqOnly) => {
    const kw = (query || '').trim()
    if (!kw) { setItems([]); setTotal(0); return }
    setLoading(true)
    const qs = `/regulations/articles?q=${encodeURIComponent(kw)}&page=${p}&per_page=20${onlyReq ? '&required_only=1' : ''}`
    api.get(qs)
      .then(({ data }: any) => {
        setItems(data.items || [])
        setTotal(data.total || 0)
        setPage(p)
      })
      .catch(() => { setItems([]); setTotal(0) })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (open) { setQ(seed); setExpanded({}); setReqOnly(false); run(seed, 1, false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  return (
    <Drawer
      width={680}
      open={open}
      onClose={onClose}
      title={<span><BookOutlined /> {title}（法规知识库）</span>}
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Input.Search
          placeholder="输入违纪类型 / 行为关键词，如：受贿、公款旅游、政务处分种类、证据指引"
          allowClear
          enterButton="检索"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onSearch={(v) => run(v || seed, 1)}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>
          检索范围：已入库的党纪法规、监察法律法规、纪检监察业务规范与中纪委审理室取证要点等。
          命中的权威条款随查随看，仅作办案参考，须结合官方原件核对。
        </Text>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            标注「必须项」的，为案件审理（审理检查）环节必须查阅的权威依据。
          </Text>
          <Space size={6}>
            <Text type="secondary" style={{ fontSize: 12 }}>仅看必须项</Text>
            <Switch size="small" checked={reqOnly} onChange={(v) => { setReqOnly(v); run(q, 1, v) }} />
          </Space>
        </Space>

        {loading ? (
          <div style={{ textAlign: 'center', padding: 24 }}><Spin tip="检索中…" /></div>
        ) : items.length ? (
          <>
            <Text type="secondary">共命中 {total} 条，展示前 {items.length} 条</Text>
            {items.map((it: any) => (
              <div key={it.id} style={{ border: '1px solid #eef1f5', borderRadius: 8, padding: 10, marginBottom: 8, background: '#f8fafc' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <Space size={4} wrap>
                    <Tag color="blue">{it.regulation}</Tag>
                    {it.chapter ? <Tag>{it.chapter}</Tag> : null}
                    <Tag color="geekblue">{it.article_no}</Tag>
                    {it.is_official ? <Tag color="gold">正式原文</Tag> : null}
                    {it.required ? <Tag color="red">必须项</Tag> : null}
                  </Space>
                  <Button type="link" size="small" onClick={() => setExpanded((s) => ({ ...s, [it.id]: !s[it.id] }))}>
                    {expanded[it.id] ? '收起' : '展开全文'}
                  </Button>
                </div>
                <Paragraph
                  style={{ marginTop: 6, fontSize: 13, whiteSpace: 'pre-wrap', marginBottom: 0 }}
                  ellipsis={expanded[it.id] ? false : { rows: 3 }}
                >
                  {it.content}
                </Paragraph>
              </div>
            ))}
          </>
        ) : (
          <Empty description={q.trim() ? '未命中相关依据，可换关键词试试' : '输入关键词开始检索'} />
        )}
      </Space>
    </Drawer>
  )
}
