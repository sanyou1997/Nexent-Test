import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Tag, Space, message, Drawer, Typography, Descriptions, Empty,
  List, Modal, Input, Select, Alert, Divider, Checkbox,
} from 'antd'
import {
  SafetyCertificateOutlined, ReloadOutlined, EyeOutlined, FileAddOutlined,
  NodeIndexOutlined, PlusOutlined, DeleteOutlined, BellOutlined, EditOutlined, SaveOutlined,
  RadarChartOutlined,
} from '@ant-design/icons'
import { api } from '../api/client'
import { useAuth } from '../auth'
import PageHeader from '../components/PageHeader'

const RATING_COLOR: Record<string, string> = { 良好: 'green', 关注: 'orange', 重点监督: 'red' }
const REL_TYPES = ['家庭成员', '亲属', '同事', '特定关系人', '利益关联', '其他']

// 多维画像雷达图（径向 SVG，零依赖）：六维原始值标注在轴端，≥60 分标红
function RadarChart({ dims, scores, raw }: { dims: string[]; scores: Record<string, number>; raw: Record<string, number> }) {
  const W = 360, H = 320, cx = W / 2, cy = H / 2 + 6, R = 108
  const n = dims.length
  const pt = (i: number, r: number) => {
    const a = (2 * Math.PI * i) / n - Math.PI / 2
    return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) }
  }
  const poly = (frac: number) => dims.map((_, i) => { const p = pt(i, R * frac); return `${p.x},${p.y}` }).join(' ')
  const dataPoly = dims.map((d, i) => { const p = pt(i, R * Math.max((scores[d] || 0) / 100, 0.05)); return `${p.x},${p.y}` }).join(' ')
  return (
    <svg width={W} height={H}>
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <polygon key={f} points={poly(f)} fill="none" stroke="#ececec" />
      ))}
      {dims.map((d) => { const p = pt(dims.indexOf(d), R); return <line key={d} x1={cx} y1={cy} x2={p.x} y2={p.y} stroke="#f1f4f8" /> })}
      <polygon points={dataPoly} fill="rgba(22,119,255,0.16)" stroke="#1d4ed8" strokeWidth={2} />
      {dims.map((d, i) => {
        const v = scores[d] || 0
        const p = pt(i, R * Math.max(v / 100, 0.05))
        const lp = pt(i, R + 24)
        const hot = v >= 60
        return (
          <g key={d}>
            <circle cx={p.x} cy={p.y} r={hot ? 4.5 : 3.5} fill={hot ? '#b91c1c' : '#1d4ed8'} />
            <text x={lp.x} y={lp.y} fontSize={11} textAnchor="middle" fill={hot ? '#b91c1c' : '#475569'}>
              {d}{raw[d] ? ` ${raw[d]}` : ''}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// 年度趋势（分组柱状 SVG）：线索 / 四风 / 预警 按年对比
function TrendChart({ trend }: { trend: any[] }) {
  if (!trend.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无趋势数据" />
  const W = 360, H = 170, padL = 20, padB = 28, padT = 10
  const iw = W - padL - 10, ih = H - padB - padT
  const max = Math.max(1, ...trend.flatMap((t) => [t['线索'], t['四风'], t['预警']]))
  const series = [{ k: '线索', c: '#1d4ed8' }, { k: '四风', c: '#b45309' }, { k: '预警', c: '#b91c1c' }]
  const gw = iw / trend.length
  const bw = Math.max(6, Math.min(12, (gw - 12) / 3))
  return (
    <svg width={W} height={H}>
      {[0, 0.5, 1].map((f) => (
        <line key={f} x1={padL} x2={W - 10} y1={padT + ih * (1 - f)} y2={padT + ih * (1 - f)} stroke="#f1f4f8" />
      ))}
      {trend.map((t, gi) => {
        const gx = padL + gi * gw
        return (
          <g key={t.year}>
            {series.map((s, si) => {
              const v = t[s.k] || 0
              const h = (v / max) * ih
              return (
                <rect key={s.k} x={gx + 6 + si * (bw + 2)} y={padT + ih - h}
                  width={bw} height={v ? Math.max(h, 2) : 0} fill={s.c} rx={1} />
              )
            })}
            <text x={gx + gw / 2} y={H - 9} fontSize={10} textAnchor="middle" fill="#64748b">{t.year}</text>
          </g>
        )
      })}
    </svg>
  )
}

// 关系图谱（径向 SVG，离线渲染，无第三方依赖）：中心=被监督对象，外圈=关联人，红=危险主体
// 节点可点击下钻到该人档案；风险节点带角标（超期/未结督办数）
function RadialGraph({ graph, onPick }: { graph: any; onPick?: (name: string) => void }) {
  const W = 540, H = 460, cx = W / 2, cy = H / 2
  const nodes = graph?.nodes || [], edges = graph?.edges || []
  if (!nodes.length) return <Empty description="暂无关系数据" />
  const focal = graph.focal
  const adj: Record<string, string[]> = {}
  edges.forEach((e: any) => {
    if (!adj[e.source]) adj[e.source] = []
    if (!adj[e.target]) adj[e.target] = []
    adj[e.source].push(e.target)
    adj[e.target].push(e.source)
  })
  const level: Record<string, number> = { [focal]: 0 }
  const q = [focal]
  while (q.length) {
    const n = q.shift()!
    for (const m of (adj[n] || [])) if (level[m] == null) { level[m] = level[n] + 1; q.push(m) }
  }
  const l1 = nodes.filter((n: any) => level[n.id] === 1)
  const l2 = nodes.filter((n: any) => level[n.id] === 2)
  const R1 = 150, R2 = 205
  const pos: Record<string, { x: number; y: number }> = { [focal]: { x: cx, y: cy } }
  const place = (arr: any[], R: number) => arr.forEach((n: any, i: number) => {
    const a = (2 * Math.PI * i) / Math.max(1, arr.length) - Math.PI / 2
    pos[n.id] = { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a) }
  })
  place(l1, R1); place(l2, R2)
  const trunc = (s: string) => (s && s.length > 6 ? s.slice(0, 6) + '…' : s)
  return (
    <svg width={W} height={H}>
      {edges.map((e: any, i: number) => {
        const p1 = pos[e.source], p2 = pos[e.target]
        if (!p1 || !p2) return null
        return <line key={'l' + i} x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y} stroke="#dde3ec" strokeWidth={1.5} />
      })}
      {edges.map((e: any, i: number) => {
        const p1 = pos[e.source], p2 = pos[e.target]
        if (!p1 || !p2) return null
        return <text key={'t' + i} x={(p1.x + p2.x) / 2} y={(p1.y + p2.y) / 2} fontSize={10} fill="#94a3b8" textAnchor="middle">{e.label}</text>
      })}
      {nodes.map((n: any, i: number) => {
        const p = pos[n.id] || { x: cx, y: cy }
        const stroke = n.risk ? '#b91c1c' : n.type === 'focal' ? '#1d4ed8' : '#15803d'
        const fill = n.risk ? '#fef2f2' : n.type === 'focal' ? '#eef3ff' : '#effaf3'
        const clickable = n.type !== 'focal' && onPick
        const r = n.type === 'focal' ? 24 : 17
        return (
          <g key={i} style={{ cursor: clickable ? 'pointer' : 'default' }}
             onClick={() => clickable && onPick && onPick(n.label)}>
            <circle cx={p.x} cy={p.y} r={r} fill={fill} stroke={stroke} strokeWidth={2} />
            <text x={p.x} y={p.y + 4} fontSize={11} textAnchor="middle" fill={n.risk ? '#b91c1c' : '#1e293b'}>{trunc(n.label)}</text>
            {/* 风险角标：超期 + 未结督办数 */}
            {n.risk && n.badge > 0 && (
              <g>
                <circle cx={p.x + r - 2} cy={p.y - r + 2} r={8} fill="#b91c1c" />
                <text x={p.x + r - 2} y={p.y - r + 5} fontSize={9} textAnchor="middle" fill="#fff">{n.badge}</text>
              </g>
            )}
          </g>
        )
      })}
    </svg>
  )
}

export default function Integrity() {
  const { user } = useAuth()
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<any>(null)
  const [open, setOpen] = useState(false)
  const [replyOpen, setReplyOpen] = useState(false)
  const [reply, setReply] = useState<any>(null)
  const [purpose, setPurpose] = useState('干部选拔任用')
  const [genLoading, setGenLoading] = useState(false)
  const [graph, setGraph] = useState<any>(null)
  const [supervision, setSupervision] = useState<any>(null)
  const [relations, setRelations] = useState<any[]>([])
  const [relModal, setRelModal] = useState(false)
  const [relForm, setRelForm] = useState<any>({ related_name: '', relation_type: '特定关系人', source: '手工录入', note: '', bidirectional: false })
  // 深化：人工档案资料 / 多维画像 / 出具记录 / 复用文书模板
  const [archive, setArchive] = useState<any>(null)
  const [archForm, setArchForm] = useState<any>({ unit: '', post: '', rank: '', org_notes: '', personal_report: '', remark: '' })
  const [archEdit, setArchEdit] = useState(false)
  const [archSaving, setArchSaving] = useState(false)
  const [portrait, setPortrait] = useState<any>(null)
  const [replies, setReplies] = useState<any[]>([])
  const [docTpls, setDocTpls] = useState<any[]>([])
  const [replyTplId, setReplyTplId] = useState<number | undefined>(undefined)

  const load = () => {
    setLoading(true)
    api.get('/integrity/list').then(({ data }) => setRows(data)).catch(() => setRows([])).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])
  // 文书模板库（廉政意见复用时选择）
  useEffect(() => {
    api.get('/documents/templates').then(({ data }) => setDocTpls(data)).catch(() => setDocTpls([]))
  }, [])

  const loadPerson = (name: string) => {
    const enc = encodeURIComponent(name)
    setOpen(true)
    api.get(`/integrity/${enc}`).then(({ data }) => setDetail(data)).catch(() => {})
    api.get(`/integrity/${enc}/graph`).then(({ data }) => setGraph(data)).catch(() => setGraph(null))
    api.get(`/integrity/${enc}/relations`).then(({ data }) => setRelations(data)).catch(() => setRelations([]))
    api.get(`/integrity/${enc}/supervision`).then(({ data }) => setSupervision(data)).catch(() => setSupervision(null))
    api.get(`/integrity/${enc}/archive`).then(({ data }) => {
      setArchive(data)
      setArchForm({ unit: data.unit || '', post: data.post || '', rank: data.rank || '',
                    org_notes: data.org_notes || '', personal_report: data.personal_report || '',
                    remark: data.remark || '' })
      setArchEdit(false)
    }).catch(() => setArchive(null))
    api.get(`/integrity/${enc}/portrait`).then(({ data }) => setPortrait(data)).catch(() => setPortrait(null))
    api.get(`/integrity/${enc}/replies`).then(({ data }) => setReplies(data)).catch(() => setReplies([]))
  }
  const openDetail = (r: any) => loadPerson(r.name)

  const saveArchive = async () => {
    if (!detail) return
    setArchSaving(true)
    try {
      const { data } = await api.put(`/integrity/${encodeURIComponent(detail.name)}/archive`, archForm)
      setArchive(data); setArchEdit(false)
      message.success('档案资料已保存（更新留痕）')
      load()
    } catch (e) { message.error((e as any)?.response?.data?.detail || '保存失败') }
    finally { setArchSaving(false) }
  }

  const genReply = (r: any) => {
    setGenLoading(true)
    api.post(`/integrity/${encodeURIComponent(r.name)}/reply`, { purpose, template_id: replyTplId || null })
      .then(({ data }) => {
        setReply({ ...data, name: r.name }); setReplyOpen(true)
        api.get(`/integrity/${encodeURIComponent(r.name)}/replies`)
          .then(({ data: rs }) => setReplies(rs)).catch(() => {})
      })
      .catch((e) => message.error(e?.response?.data?.detail || '生成失败'))
      .finally(() => setGenLoading(false))
  }

  const addRelation = async () => {
    if (!relForm.related_name || !detail) { message.warning('请填写关联人姓名'); return }
    try {
      await api.post('/integrity/relations', { ...relForm, person: detail.name })
      message.success(relForm.bidirectional ? '已添加双向关系（双方档案均可见）' : '已添加关系')
      setRelModal(false); setRelForm({ related_name: '', relation_type: '特定关系人', source: '手工录入', note: '', bidirectional: false })
      const name = encodeURIComponent(detail.name)
      setRelations((await api.get(`/integrity/${name}/relations`)).data)
      setGraph((await api.get(`/integrity/${name}/graph`)).data)
      api.get(`/integrity/${name}/portrait`).then(({ data }) => setPortrait(data)).catch(() => {})
    } catch (e) { message.error((e as any)?.response?.data?.detail || '添加失败') }
  }
  const delRelation = async (id: number) => {
    await api.delete(`/integrity/relations/${id}`)
    if (detail) {
      const name = encodeURIComponent(detail.name)
      setRelations((await api.get(`/integrity/${name}/relations`)).data)
      setGraph((await api.get(`/integrity/${name}/graph`)).data)
    }
    message.success('已删除')
  }

  const columns = [
    { title: '姓名', dataIndex: 'name', width: 120, render: (s: string, r: any) => <a onClick={() => openDetail(r)}>{s}</a> },
    { title: '单位/职务', dataIndex: 'units', width: 180, ellipsis: true,
      render: (u: string[], r: any) => (u || []).join('、') || '—' },
    { title: '职务', dataIndex: 'post', width: 90, ellipsis: true, render: (t: string) => t || '—' },
    { title: '涉及域', dataIndex: 'domains', width: 140, render: (d: string[]) => (d || []).map((x) => <Tag key={x} color="blue">{x}</Tag>) },
    { title: '线索', dataIndex: 'case_count', width: 60 },
    { title: '四风', dataIndex: 'conduct_count', width: 60 },
    { title: '预警', dataIndex: 'alert_count', width: 60 },
    { title: '廉政评级', dataIndex: 'rating', width: 100, render: (s: string) => <Tag color={RATING_COLOR[s]}>{s}</Tag> },
    { title: '操作', key: 'op', width: 160, render: (_: any, r: any) => (
      <Space size={2}>
        <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(r)}>档案</Button>
        <Button size="small" type="link" icon={<FileAddOutlined />} onClick={() => genReply(r)}>廉政意见</Button>
      </Space>) },
  ]

  return (
    <div>
      <PageHeader
        icon={<SafetyCertificateOutlined />}
        title="廉政档案画像与廉政意见回复"
        subtitle="跨案管 / 党风 / 预警 / 审理自动聚合 · 社会关系图谱 · 多维风险画像 · 人工维护区 · 廉政意见留痕入档"
        extra={<Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>} />
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="画像由系统按同名人员自动聚合，仅供组织参考，不作为定性结论；同名不同人时请在档案资料中人工区分。" />
      <Card size="small">
        <Table rowKey="name" size="small" loading={loading} columns={columns} dataSource={rows} pagination={{ pageSize: 10 }} />
      </Card>
      <Drawer width={720} open={open} onClose={() => setOpen(false)} title={`廉政档案 · ${detail?.name || ''}`}>
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="姓名">{detail.name}</Descriptions.Item>
              <Descriptions.Item label="单位/职务">{(detail.units || []).join('、') || '—'}</Descriptions.Item>
              <Descriptions.Item label="涉及业务域">{(detail.domains || []).map((d: string) => <Tag key={d} color="blue">{d}</Tag>)}</Descriptions.Item>
              <Descriptions.Item label="廉政评级"><Tag color={RATING_COLOR[detail.rating?.rating]}>{detail.rating?.rating}</Tag>（评分 {detail.rating?.score}）</Descriptions.Item>
            </Descriptions>

            {/* 多维画像：六维雷达 + 年度趋势 */}
            <Card size="small" title={<><RadarChartOutlined /> 多维风险画像</>} style={{ margin: '12px 0' }}
              extra={portrait ? <Tag color={portrait.risk_level === '高' ? 'red' : portrait.risk_level === '中' ? 'orange' : 'green'}>
                综合风险 {portrait.risk_level}（{portrait.weighted_score} 分）</Tag> : null}>
              {portrait ? (
                <>
                  <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                    <RadarChart dims={portrait.dims} scores={portrait.scores} raw={portrait.raw} />
                    <div style={{ flex: 1, minWidth: 300 }}>
                      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>年度趋势（线索 / 四风 / 预警）</div>
                      <TrendChart trend={portrait.trend} />
                      <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>
                        <Tag color="blue" style={{ fontSize: 10 }}>线索</Tag>
                        <Tag color="orange" style={{ fontSize: 10 }}>四风</Tag>
                        <Tag color="red" style={{ fontSize: 10 }}>预警</Tag>
                        轴端数字为该维度事项数
                      </div>
                    </div>
                  </div>
                  {portrait.danger_relations?.length ? (
                    <Alert type="warning" showIcon style={{ marginTop: 8 }}
                      message={`关系风险：${portrait.danger_relations.length} 名关联人本身涉及监督事项`}
                      description={<Space wrap>{portrait.danger_relations.map((d: string) => (
                        <a key={d} onClick={() => loadPerson(d)}>{d}</a>))}</Space>} />
                  ) : null}
                  <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6 }}>{portrait.disclaimer}</div>
                </>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}
            </Card>

            {/* 人工维护档案资料（与系统聚合数据分区） */}
            <Card size="small" style={{ margin: '12px 0' }}
              title={<Space><EditOutlined /> 档案资料（人工维护）</Space>}
              extra={<Space>
                {archive?.updated_at ? <Typography.Text type="secondary" style={{ fontSize: 11 }}>更新于 {archive.updated_at}</Typography.Text> : null}
                {!archEdit && <Button size="small" icon={<EditOutlined />} onClick={() => setArchEdit(true)}>编辑</Button>}
              </Space>}>
              <Alert type="info" showIcon style={{ marginBottom: 8 }}
                message="上半部分为系统自动聚合（只读）；本区为组织人工掌握的情况，可编辑、更新留痕。"
                banner />
              {archEdit ? (
                <>
                  <Descriptions column={2} size="small" bordered>
                    <Descriptions.Item label="单位"><Input value={archForm.unit} onChange={(e) => setArchForm({ ...archForm, unit: e.target.value })} /></Descriptions.Item>
                    <Descriptions.Item label="职务"><Input value={archForm.post} onChange={(e) => setArchForm({ ...archForm, post: e.target.value })} /></Descriptions.Item>
                    <Descriptions.Item label="职级"><Input value={archForm.rank} onChange={(e) => setArchForm({ ...archForm, rank: e.target.value })} /></Descriptions.Item>
                    <Descriptions.Item label="个人事项报告要点" span={2}>
                      <Input.TextArea rows={2} value={archForm.personal_report}
                        onChange={(e) => setArchForm({ ...archForm, personal_report: e.target.value })} />
                    </Descriptions.Item>
                    <Descriptions.Item label="组织掌握的其他情况" span={2}>
                      <Input.TextArea rows={3} value={archForm.org_notes}
                        onChange={(e) => setArchForm({ ...archForm, org_notes: e.target.value })} />
                    </Descriptions.Item>
                    <Descriptions.Item label="备注" span={2}>
                      <Input.TextArea rows={2} value={archForm.remark}
                        onChange={(e) => setArchForm({ ...archForm, remark: e.target.value })} />
                    </Descriptions.Item>
                  </Descriptions>
                  <Space style={{ marginTop: 8 }}>
                    <Button type="primary" icon={<SaveOutlined />} loading={archSaving} onClick={saveArchive}>保存</Button>
                    <Button onClick={() => setArchEdit(false)}>取消</Button>
                  </Space>
                </>
              ) : (
                <Descriptions column={2} size="small" bordered>
                  <Descriptions.Item label="单位">{archive?.unit || '—'}</Descriptions.Item>
                  <Descriptions.Item label="职务">{archive?.post || '—'}</Descriptions.Item>
                  <Descriptions.Item label="职级">{archive?.rank || '—'}</Descriptions.Item>
                  <Descriptions.Item label="个人事项报告要点">{archive?.personal_report || '—'}</Descriptions.Item>
                  <Descriptions.Item label="组织掌握的其他情况">{archive?.org_notes || '—'}</Descriptions.Item>
                  <Descriptions.Item label="备注">{archive?.remark || '—'}</Descriptions.Item>
                </Descriptions>
              )}
            </Card>

            {/* 廉政意见出具记录 */}
            <Card size="small" title={<><FileAddOutlined /> 廉政意见出具记录（{replies.length}）</>} style={{ margin: '12px 0' }}>
              {replies.length ? (
                <List size="small" dataSource={replies} renderItem={(r: any) => (
                  <List.Item>
                    <Space wrap>
                      <Tag color="blue">{r.purpose}</Tag>
                      <Tag color={RATING_COLOR[r.rating] || 'default'}>{r.rating}</Tag>
                      <span>{r.document_title || '（文书已删除）'}</span>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {r.created_at}{r.created_by ? ' · ' + r.created_by : ''}{r.note ? ' · ' + r.note : ''}
                      </Typography.Text>
                    </Space>
                  </List.Item>
                )} />
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未出具过廉政意见" />}
            </Card>

            {/* 关系图谱 */}
            <Card size="small" title={<><NodeIndexOutlined /> 社会关系图谱</>} style={{ margin: '12px 0' }}
              extra={<Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setRelModal(true)}>维护关系</Button>}>
              <div style={{ textAlign: 'center', overflowX: 'auto' }}>
                <RadialGraph graph={graph} onPick={loadPerson} />
              </div>
              <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
                图例：<Tag color="blue">蓝</Tag>中心对象　<Tag color="green">绿</Tag>关联人　<Tag color="red">红</Tag>危险主体（有预警/跨域/超期/被督办）　点击关联人可下钻其档案
                {graph?.disclaimer ? `；${graph.disclaimer}` : ''}
              </div>
              {graph?.danger_related?.length ? (
                <Alert type="warning" showIcon style={{ marginTop: 8 }}
                  message={`危险关联人（${graph.danger_related.length}）：其社交圈内存在未结监督事项`}
                  description={<Space wrap>{graph.danger_related.map((d: any) => (
                    <a key={d.name} onClick={() => loadPerson(d.name)}>
                      {d.name}（{d.relation_type}·超期{d.sla_overdue}/督办{d.open_dispatch}）
                    </a>))}</Space>}
                />
              ) : null}
              <List size="small" style={{ marginTop: 8 }} dataSource={relations}
                renderItem={(r: any) => {
                  const danger = (graph?.danger_related || []).some((d: any) => d.name === r.related_name)
                  return (
                    <List.Item actions={[<Button key="d" size="small" danger icon={<DeleteOutlined />} onClick={() => delRelation(r.id)} />]}>
                      <Tag color="geekblue">{r.relation_type}</Tag>
                      {r.direction === '反向' ? <Tag color="orange">反向</Tag> : null}
                      {r.bidirectional ? <Tag color="green">双向</Tag> : null}
                      {' '}{r.direction === '反向' ? r.person : r.related_name}
                      {danger ? <Tag color="red" style={{ marginLeft: 4 }}>危险关联</Tag> : null}
                      <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>{r.source}{r.note ? ' · ' + r.note : ''}</Typography.Text>
                    </List.Item>
                  )
                }} />
            </Card>

            {/* 时限预警与督办（与智能督办预警中枢打通） */}
            <Card size="small" title={<><BellOutlined /> 时限预警与督办（与中枢联动）</>} style={{ margin: '12px 0' }}>
              {(() => {
                const s = supervision
                if (!s) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />
                const sum = s.summary || {}
                const hot = (s.cases || []).concat(s.conduct || []).filter((c: any) => c.sla?.overdue || c.sla?.warn)
                return (
                  <>
                    <Space wrap style={{ marginBottom: 8 }}>
                      <Tag color={sum.overdue ? 'red' : 'default'}>超期 {sum.overdue || 0}</Tag>
                      <Tag color={sum.due_soon ? 'orange' : 'default'}>临期 {sum.due_soon || 0}</Tag>
                      <Tag color={sum.open_dispatch ? 'volcano' : 'default'}>未结督办 {sum.open_dispatch || 0}</Tag>
                      <Tag color={sum.alert_count ? 'gold' : 'default'}>模型预警 {sum.alert_count || 0}</Tag>
                    </Space>
                    {s.dispatches?.length ? (
                      <List size="small" style={{ marginBottom: 6 }}
                        header={<div style={{ fontWeight: 600 }}>督办闭环（{s.dispatches.length}）</div>}
                        dataSource={s.dispatches}
                        renderItem={(d: any) => (
                          <List.Item>
                            <Tag color={d.level === '超期' ? 'red' : 'orange'}>{d.level}</Tag>
                            <Tag>{d.source}</Tag> {d.ref_code} {d.ref_title}
                            <Tag color={d.status === '已闭环' ? 'green' : 'blue'} style={{ marginLeft: 6 }}>{d.status}</Tag>
                            {d.feedback ? <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>反馈：{d.feedback}</Typography.Text> : null}
                          </List.Item>
                        )} />
                    ) : null}
                    {hot.length ? (
                      <List size="small" header={<div style={{ fontWeight: 600 }}>超期/临期事项（{hot.length}）</div>}
                        dataSource={hot}
                        renderItem={(c: any) => (
                          <List.Item>
                            <Tag color={c.sla?.overdue ? 'red' : 'orange'}>{c.sla?.overdue ? '超期' : '临期'}</Tag>
                            <Tag>{c.code}</Tag> {c.title}
                            <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                              {c.sla?.stage} 剩{c.sla?.days_left}天（{c.sla?.deadline}）
                            </Typography.Text>
                          </List.Item>
                        )} />
                    ) : <Typography.Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>该人名下暂无超期/临期时限事项。</Typography.Paragraph>}
                  </>
                )
              })()}
            </Card>

            <Card size="small" title="廉政时间线" style={{ margin: '12px 0' }}>
              <List size="small" dataSource={detail.timeline || []} renderItem={(t: any) => (
                <List.Item><Tag color="blue">{t.type}</Tag> {t.text}</List.Item>
              )} />
            </Card>
            <Card size="small" title={`涉及线索 / 审理（${detail.cases?.length || 0}）`} style={{ marginBottom: 12 }}>
              {detail.cases?.length ? detail.cases.map((c: any, i: number) => (
                <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid #f6f8fb' }}>{c.code} {c.title} <Tag>{c.status}</Tag></div>
              )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}
            </Card>
            <Card size="small" title={`涉及四风（${detail.conduct?.length || 0}）`} style={{ marginBottom: 12 }}>
              {detail.conduct?.length ? detail.conduct.map((c: any, i: number) => (
                <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid #f6f8fb' }}>{c.title} <Tag>{c.category}</Tag> <Tag>{c.status}</Tag></div>
              )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}
            </Card>
            <Divider style={{ margin: '12px 0' }} />
            <Space wrap align="end" style={{ marginBottom: 12 }}>
              <div>
                <div style={{ fontSize: 12, color: '#64748b', marginBottom: 2 }}>出具用途</div>
                <Select style={{ width: 160 }} value={purpose} onChange={setPurpose}
                  options={['干部选拔任用', '评优评先', '职级晋升', '其他'].map((x) => ({ label: x, value: x }))} />
              </div>
              <div>
                <div style={{ fontSize: 12, color: '#64748b', marginBottom: 2 }}>复用文书模板（可选）</div>
                <Select style={{ width: 220 }} allowClear placeholder="默认公文格式"
                  value={replyTplId} onChange={setReplyTplId} showSearch optionFilterProp="label"
                  options={docTpls.map((t: any) => ({ value: t.id, label: `${t.name}（${t.fields?.length || 0}字段）` }))} />
              </div>
              <Button type="primary" icon={<FileAddOutlined />} loading={genLoading}
                onClick={() => genReply({ name: detail.name })}>一键生成廉政意见回复</Button>
            </Space>
          </>
        )}
      </Drawer>

      <Modal title="维护社会关系" open={relModal} onOk={addRelation} onCancel={() => setRelModal(false)} okText="添加">
        <div style={{ marginBottom: 10 }}>主体：<b>{detail?.name}</b></div>
        <div style={{ marginBottom: 10 }}>关联人：<Input placeholder="如：张某（配偶）" value={relForm.related_name}
          onChange={(e) => setRelForm({ ...relForm, related_name: e.target.value })} /></div>
        <div style={{ marginBottom: 10 }}>关系类型：<Select style={{ width: '100%' }} value={relForm.relation_type}
          onChange={(v) => setRelForm({ ...relForm, relation_type: v })} options={REL_TYPES.map((x) => ({ label: x, value: x }))} /></div>
        <div style={{ marginBottom: 10 }}>来源：<Select style={{ width: '100%' }} value={relForm.source}
          onChange={(v) => setRelForm({ ...relForm, source: v })}
          options={['手工录入', '案管', '党风', '信访', '工商'].map((x) => ({ label: x, value: x }))} /></div>
        <div style={{ marginBottom: 10 }}>
          <Checkbox checked={!!relForm.bidirectional}
            onChange={(e) => setRelForm({ ...relForm, bidirectional: e.target.checked })}>
            双向关系（如配偶、父母）：系统自动生成反向边，双方档案均可见
          </Checkbox>
        </div>
        <div>备注：<Input.TextArea rows={2} value={relForm.note} onChange={(e) => setRelForm({ ...relForm, note: e.target.value })} /></div>
      </Modal>

      <Modal title="廉政意见回复（AI 初稿）" open={replyOpen} onCancel={() => setReplyOpen(false)} width={680}
        footer={[<Button key="ok" type="primary" onClick={() => { setReplyOpen(false); window.open('/documents', '_blank') }}>前往文书确认</Button>]}>
        {reply && (
          <>
            <Descriptions size="small" column={2} style={{ marginBottom: 8 }}>
              <Descriptions.Item label="对象">{reply.name || ''}</Descriptions.Item>
              <Descriptions.Item label="廉政评级"><Tag color={RATING_COLOR[reply.rating]}>{reply.rating}</Tag></Descriptions.Item>
              <Descriptions.Item label="文书模板" span={2}>
                {reply.template_used ? <Tag color="green">已复用文书模板</Tag> : <Tag>默认公文格式</Tag>}
              </Descriptions.Item>
            </Descriptions>
            <Input.TextArea value={reply.content} autoSize={{ minRows: 12, maxRows: 20 }} readOnly />
          </>
        )}
      </Modal>
    </div>
  )
}
