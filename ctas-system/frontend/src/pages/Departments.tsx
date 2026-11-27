import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Button, Card, Checkbox, Drawer, Empty, Form, Input, InputNumber, Popconfirm, Radio, Select, Space,
  Switch, Table, Tabs, Tag, Tooltip, Typography, message,
} from 'antd'
import {
  ApiOutlined, ClusterOutlined, KeyOutlined, LockOutlined, PlusOutlined, ReloadOutlined, SettingOutlined, TeamOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { ROLE_NAMES } from '../auth'
import PageHeader from '../components/PageHeader'

interface ModuleDef { key: string; label: string; group: string; kind: 'base' | 'biz' | 'shared' | 'system' }
interface Catalog {
  modules: ModuleDef[]
  assignable: string[]
  data_scopes: Record<string, string>
  summary: { key: string; label: string }[]
}
interface Dept {
  id: number; code: string; name: string; description: string
  modules: string[]; data_scope: 'self' | 'custom' | 'all'; data_scope_label: string
  scope_dept_ids: number[]; scope_dept_names: string[]
  is_system: boolean; enabled: boolean; sort: number
  members: number; data: Record<string, number>; mcp_token: boolean; updated_at: string
}

const SCOPE_HELP: Record<string, string> = {
  self: '只能查看和办理本部门的业务数据（推荐默认）。',
  custom: '还可查看和办理所选部门的业务数据，例如审理室查阅审查调查室的全部案件。',
  all: '可查看和办理全部部门的业务数据，适用于委领导、案管室等统筹部门。',
}
const SCOPE_COLOR: Record<string, string> = { self: 'default', custom: 'blue', all: 'orange' }

function deptBody(d: Dept, patch: Partial<Dept> = {}) {
  const x = { ...d, ...patch }
  return {
    code: x.code, name: x.name, description: x.description, modules: x.modules, data_scope: x.data_scope,
    scope_dept_ids: x.data_scope === 'custom' ? x.scope_dept_ids : [], enabled: x.enabled, sort: x.sort,
  }
}

export default function Departments() {
  const nav = useNavigate()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [depts, setDepts] = useState<Dept[]>([])
  const [grantTotal, setGrantTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState('list')
  const [editing, setEditing] = useState<Dept | 'new' | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [{ data: c }, { data: d }] = await Promise.all([api.get('/departments/catalog'), api.get('/departments')])
      setCatalog(c)
      setDepts(d.items)
      setGrantTotal(d.grants)
    } catch (e) { message.error(errMsg(e)) } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  const modLabel = useMemo(() => Object.fromEntries((catalog?.modules || []).map((m) => [m.key, m.label])), [catalog])
  const assignable = useMemo(() => (catalog?.modules || []).filter((m) => catalog?.assignable.includes(m.key)), [catalog])

  const save = async (d: Dept, patch: Partial<Dept>, ok = '已保存') => {
    try {
      await api.put(`/departments/${d.id}`, deptBody(d, patch))
      message.success(ok)
      await load()
    } catch (e) { message.error(errMsg(e)) }
  }

  const remove = async (d: Dept) => {
    try { await api.delete(`/departments/${d.id}`); message.success('已删除'); load() } catch (e) { message.error(errMsg(e)) }
  }

  const columns = [
    {
      title: '部门', dataIndex: 'name', width: 230,
      render: (_: any, d: Dept) => (
        <div>
          <Space size={6} wrap>
            <b>{d.name}</b>
            <Tag style={{ marginInlineEnd: 0 }}>{d.code}</Tag>
            {d.is_system && <Tag color="purple" style={{ marginInlineEnd: 0 }}>平台管理</Tag>}
            {!d.enabled && <Tag color="red" style={{ marginInlineEnd: 0 }}>已停用</Tag>}
          </Space>
          {d.description && <div style={{ color: '#64748b', fontSize: 12, marginTop: 4 }}>{d.description}</div>}
        </div>
      ),
    },
    {
      title: '账号', dataIndex: 'members', width: 70, align: 'center' as const,
      render: (n: number, d: Dept) => <Button type="link" size="small" onClick={() => nav(`/users?dept=${d.id}`)}>{n}</Button>,
    },
    {
      title: '功能模块', dataIndex: 'modules',
      render: (mods: string[], d: Dept) => d.is_system ? <span style={{ color: '#64748b' }}>全部（平台管理员不受限）</span> : (
        <Tooltip title={mods.map((k) => modLabel[k] || k).join('、')}>
          <span>
            <b>{mods.length}</b><span style={{ color: '#64748b' }}> 个 · </span>
            <span style={{ color: '#334155' }}>{mods.filter((k) => assignable.find((m) => m.key === k)?.kind === 'biz').slice(0, 5).map((k) => modLabel[k]).join('、')}</span>
            {mods.filter((k) => assignable.find((m) => m.key === k)?.kind === 'biz').length > 5 && <span style={{ color: '#64748b' }}> 等</span>}
          </span>
        </Tooltip>
      ),
    },
    {
      title: '数据范围', dataIndex: 'data_scope', width: 190,
      render: (s: string, d: Dept) => (
        <div>
          <Tag color={SCOPE_COLOR[s]}>{d.data_scope_label}</Tag>
          {s === 'custom' && <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>+ {d.scope_dept_names.join('、')}</div>}
        </div>
      ),
    },
    {
      title: '名下业务数据', dataIndex: 'data', width: 220,
      render: (data: Record<string, number>) => {
        const parts = (catalog?.summary || []).filter((s) => data[s.key]).map((s) => `${s.label} ${data[s.key]}`)
        return parts.length ? <span style={{ fontSize: 12, color: '#334155' }}>{parts.join(' · ')}</span> : <span style={{ color: '#94a3b8' }}>暂无</span>
      },
    },
    {
      title: 'MCP 令牌', dataIndex: 'mcp_token', width: 90,
      render: (v: boolean) => v ? <Tag color="green">已配置</Tag> : <span style={{ color: '#94a3b8' }}>未配置</span>,
    },
    {
      title: '操作', width: 170,
      render: (_: any, d: Dept) => (
        <Space size={4}>
          <Button size="small" type="link" icon={<SettingOutlined />} onClick={() => setEditing(d)}>配置</Button>
          {!d.is_system && (
            <Popconfirm title={d.enabled ? '停用后该部门账号将无法登录，确定停用？' : '确定启用该部门？'}
              onConfirm={() => save(d, { enabled: !d.enabled }, d.enabled ? '已停用' : '已启用')}>
              <Button size="small" type="link" danger={d.enabled}>{d.enabled ? '停用' : '启用'}</Button>
            </Popconfirm>
          )}
          {!d.is_system && (
            <Popconfirm title="仅能删除没有账号和业务数据的部门，确定删除？" onConfirm={() => remove(d)}>
              <Button size="small" type="link" danger>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        icon={<ClusterOutlined />}
        title="部门与权限"
        subtitle="按部门划分租户：部门决定可见的功能模块与业务数据范围，部门之间业务数据相互隔离；法规库、历史报告库、智能体等通用能力全平台共享。"
        extra={<Space>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>新建部门</Button>
        </Space>}
      />
      <Card styles={{ body: { paddingTop: 4 } }}>
        <Tabs activeKey={tab} onChange={setTab} items={[
          {
            key: 'list', label: `部门（${depts.length}）`,
            children: <Table rowKey="id" size="middle" loading={loading} dataSource={depts} columns={columns} pagination={false}
              scroll={{ x: 1100 }} />,
          },
          {
            key: 'matrix', label: '模块权限矩阵',
            children: <ModuleMatrix depts={depts.filter((d) => !d.is_system)} modules={assignable} onSave={save} />,
          },
          { key: 'grants', label: `移交授权（${grantTotal}）`, children: <GrantList active={tab === 'grants'} /> },
        ]} />
      </Card>
      {catalog && (
        <DeptDrawer
          open={!!editing} dept={editing === 'new' || !editing ? null : (depts.find((d) => d.id === editing.id) || editing)}
          depts={depts} catalog={catalog}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} onReload={load}
        />
      )}
    </>
  )
}

/** 模块 × 部门矩阵：直接勾选开通 / 关闭 */
function ModuleMatrix({ depts, modules, onSave }: {
  depts: Dept[]; modules: ModuleDef[]; onSave: (d: Dept, patch: Partial<Dept>, ok?: string) => Promise<void>
}) {
  const [busy, setBusy] = useState('')
  if (!depts.length) return <Empty description="暂无业务部门" />
  const toggle = async (d: Dept, key: string, on: boolean) => {
    setBusy(`${d.id}:${key}`)
    const mods = on ? [...d.modules, key] : d.modules.filter((k) => k !== key)
    await onSave(d, { modules: mods }, `${d.name}：已${on ? '开通' : '关闭'}「${modules.find((m) => m.key === key)?.label}」`)
    setBusy('')
  }
  let lastGroup = ''
  const rows = modules.map((m) => {
    const first = m.group !== lastGroup
    lastGroup = m.group
    return { ...m, first, span: first ? modules.filter((x) => x.group === m.group).length : 0 }
  })
  return (
    <>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="勾选即开通，立即生效（该部门账号刷新页面后导航随之变化，未开通模块的接口直接拒绝访问）。工作台对所有部门开放；系统管理仅平台管理员可用。" />
      <Table rowKey="key" size="small" pagination={false} dataSource={rows} scroll={{ x: 300 + depts.length * 130 }}
        columns={[
          {
            title: '分组', dataIndex: 'group', width: 100, fixed: 'left' as const,
            onCell: (r: any) => ({ rowSpan: r.span }),
            render: (g: string) => <b style={{ color: '#334155' }}>{g}</b>,
          },
          {
            title: '功能模块', dataIndex: 'label', width: 170, fixed: 'left' as const,
            render: (l: string, m: ModuleDef) => <Space size={6}>{l}{m.kind === 'shared' && <Tag color="cyan" style={{ marginInlineEnd: 0 }}>通用 · 数据共享</Tag>}</Space>,
          },
          ...depts.map((d) => ({
            title: (
              <div style={{ lineHeight: 1.3 }}>
                <div style={{ fontWeight: 600 }}>{d.name}</div>
                <Tag color={SCOPE_COLOR[d.data_scope]} style={{ marginTop: 4, marginInlineEnd: 0, fontWeight: 400 }}>{d.data_scope_label}</Tag>
              </div>
            ),
            key: `d${d.id}`, width: 130, align: 'center' as const,
            render: (_: any, m: ModuleDef) => (
              <Checkbox checked={d.modules.includes(m.key)} disabled={!d.enabled || busy === `${d.id}:${m.key}`}
                onChange={(e) => toggle(d, m.key, e.target.checked)} />
            ),
          })),
        ]} />
    </>
  )
}

function GrantList({ active }: { active: boolean }) {
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!active) return
    setLoading(true)
    api.get('/departments/grants', { params: { limit: 300 } }).then(({ data }) => setRows(data.items))
      .catch((e) => message.error(errMsg(e))).finally(() => setLoading(false))
  }, [active])
  return (
    <>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="跨部门移交（信访转线索、移送审理、党风 / 巡察移交）时自动授权：接收部门可查阅来源记录，来源部门可跟踪新记录。授权只对这一条记录生效，不扩大部门数据范围。" />
      <Table rowKey="id" size="small" loading={loading} dataSource={rows} pagination={{ pageSize: 20, hideOnSinglePage: true }}
        columns={[
          { title: '记录', render: (_: any, g: any) => `${g.resource} #${g.resource_id}` },
          { title: '授权给', dataIndex: 'dept_name', render: (n: string) => <Tag color="blue">{n || '—'}</Tag> },
          { title: '原因', dataIndex: 'reason' },
          { title: '时间', dataIndex: 'created_at', width: 160 },
        ]} />
    </>
  )
}

/** 部门配置抽屉：基本信息 / 功能模块 / 数据范围（含可见数据预览）/ MCP 令牌 / 部门账号 */
function DeptDrawer({ open, dept, depts, catalog, onClose, onSaved, onReload }: {
  open: boolean; dept: Dept | null; depts: Dept[]; catalog: Catalog
  onClose: () => void; onSaved: () => void; onReload: () => void
}) {
  const nav = useNavigate()
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [preview, setPreview] = useState<Record<string, number> | null>(null)
  const [members, setMembers] = useState<any[]>([])
  const [token, setToken] = useState('')
  const scope = Form.useWatch('data_scope', form)
  const scopeIds = Form.useWatch('scope_dept_ids', form)
  const mods: string[] = Form.useWatch('modules', form) || []
  const timer = useRef<number>()

  useEffect(() => {
    if (!open) return
    setPreview(null)
    setToken('')
    form.setFieldsValue(dept ? {
      name: dept.name, code: dept.code, description: dept.description, sort: dept.sort, enabled: dept.enabled,
      modules: dept.modules, data_scope: dept.data_scope, scope_dept_ids: dept.scope_dept_ids,
    } : {
      name: '', code: '', description: '', sort: (depts.length + 1) * 10, enabled: true,
      modules: ['ocr', 'transcripts', 'documents', 'verify', 'agents', 'qa', 'regulations', 'historical-reports'],
      data_scope: 'self', scope_dept_ids: [],
    })
    if (dept) api.get('/users', { params: { dept_id: dept.id } }).then(({ data }) => setMembers(data)).catch(() => setMembers([]))
    else setMembers([])
    // 只在打开 / 切换部门时重置表单（刷新列表后不覆盖未保存的修改）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, dept?.id])

  // 数据范围可见量预览（未保存的配置也能先看效果）
  useEffect(() => {
    if (!open || !scope) return
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      api.post('/departments/preview-scope', {
        dept_id: dept?.id ?? null, data_scope: scope, scope_dept_ids: scope === 'custom' ? (scopeIds || []) : [],
      }).then(({ data }) => setPreview(data.counts)).catch(() => setPreview(null))
    }, 250)
    return () => window.clearTimeout(timer.current)
  }, [open, scope, scopeIds, dept?.id])

  const groups = useMemo(() => {
    const out: { group: string; items: ModuleDef[] }[] = []
    for (const m of catalog.modules.filter((x) => catalog.assignable.includes(x.key))) {
      const g = out.find((x) => x.group === m.group)
      if (g) g.items.push(m)
      else out.push({ group: m.group, items: [m] })
    }
    return out
  }, [catalog])

  const setGroup = (items: ModuleDef[], on: boolean) => {
    const keys = items.map((i) => i.key)
    const next = on ? Array.from(new Set([...mods, ...keys])) : mods.filter((k) => !keys.includes(k))
    form.setFieldValue('modules', next)
  }

  const submit = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      const body = { ...v, scope_dept_ids: v.data_scope === 'custom' ? v.scope_dept_ids : [] }
      if (dept) await api.put(`/departments/${dept.id}`, body)
      else await api.post('/departments', body)
      message.success(dept ? '部门配置已保存' : '部门已创建')
      onSaved()
    } catch (e) { message.error(errMsg(e)) } finally { setSaving(false) }
  }

  const issueToken = async () => {
    if (!dept) return
    try {
      const { data } = await api.post(`/departments/${dept.id}/mcp-token`)
      setToken(data.token)
      onReload()
    } catch (e) { message.error(errMsg(e)) }
  }
  const revokeToken = async () => {
    if (!dept) return
    try { await api.delete(`/departments/${dept.id}/mcp-token`); message.success('令牌已撤销'); setToken(''); onReload() }
    catch (e) { message.error(errMsg(e)) }
  }

  const others = depts.filter((d) => d.id !== dept?.id)
  const isSystem = !!dept?.is_system

  return (
    <Drawer
      open={open} onClose={onClose} width={760} destroyOnClose
      title={dept ? `配置部门：${dept.name}` : '新建部门'}
      footer={<div style={{ textAlign: 'right' }}><Space>
        <Button onClick={onClose}>取消</Button>
        <Button type="primary" loading={saving} onClick={submit}>保存</Button>
      </Space></div>}
    >
      <Form form={form} layout="vertical" requiredMark="optional">
        <SectionTitle>基本信息</SectionTitle>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', columnGap: 16 }}>
          <Form.Item name="name" label="部门名称" rules={[{ required: true, message: '请填写部门名称' }]}><Input maxLength={80} /></Form.Item>
          <Form.Item name="code" label="部门编码" rules={[{ required: true, message: '请填写编码' }, { pattern: /^[A-Za-z0-9_-]+$/, message: '字母、数字、下划线或短横线' }]}
            tooltip="用于对接与审计，保存时转为大写"><Input maxLength={30} disabled={isSystem} /></Form.Item>
        </div>
        <Form.Item name="description" label="职责说明"><Input.TextArea rows={2} maxLength={300} /></Form.Item>
        <Space size={32}>
          <Form.Item name="sort" label="排序"><InputNumber min={0} max={9999} /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch disabled={isSystem} /></Form.Item>
        </Space>

        <SectionTitle extra={<span style={{ color: '#64748b', fontWeight: 400 }}>已开通 {mods.length} 个</span>}>功能模块</SectionTitle>
        {isSystem ? (
          <Alert type="info" showIcon message="平台管理员不受部门模块限制，可使用全部模块（含系统管理）。此处配置仅作用于本部门的非管理员账号。" style={{ marginBottom: 12 }} />
        ) : (
          <div style={{ color: '#64748b', fontSize: 12, marginBottom: 8 }}>
            工作台对所有部门开放；系统管理仅平台管理员可用。标注「通用」的模块数据全平台共享，其余模块数据按部门隔离。
          </div>
        )}
        <Form.Item name="modules" noStyle>
          <Checkbox.Group style={{ width: '100%' }}>
            <div style={{ display: 'grid', gap: 10, width: '100%' }}>
              {groups.map((g) => {
                const keys = g.items.map((i) => i.key)
                const n = keys.filter((k) => mods.includes(k)).length
                return (
                  <div key={g.group} style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: '8px 12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6 }}>
                      <b style={{ color: '#334155' }}>{g.group}</b>
                      <span style={{ color: '#94a3b8', fontSize: 12, marginLeft: 8 }}>{n}/{keys.length}</span>
                      <span style={{ flex: 1 }} />
                      <Button size="small" type="link" onClick={() => setGroup(g.items, n < keys.length)}>{n < keys.length ? '全选' : '清空'}</Button>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 18px' }}>
                      {g.items.map((m) => (
                        <Checkbox key={m.key} value={m.key}>
                          {m.label}{m.kind === 'shared' && <Tag color="cyan" style={{ marginLeft: 6, marginInlineEnd: 0, fontSize: 11, lineHeight: '16px' }}>通用</Tag>}
                        </Checkbox>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          </Checkbox.Group>
        </Form.Item>

        <SectionTitle>数据范围</SectionTitle>
        <Form.Item name="data_scope" style={{ marginBottom: 8 }}>
          <Radio.Group style={{ width: '100%' }}>
            <div style={{ display: 'grid', gap: 8 }}>
              {Object.entries(catalog.data_scopes).map(([k, label]) => (
                <Radio key={k} value={k}>
                  <b>{label}</b><span style={{ color: '#64748b', marginLeft: 8 }}>{SCOPE_HELP[k]}</span>
                </Radio>
              ))}
            </div>
          </Radio.Group>
        </Form.Item>
        {scope === 'custom' && (
          <Form.Item name="scope_dept_ids" label="可访问的其他部门" rules={[{ required: true, message: '请至少选择一个部门' }]}>
            <Select mode="multiple" placeholder="选择部门" options={others.map((d) => ({ value: d.id, label: d.name }))} />
          </Form.Item>
        )}
        <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 8, padding: '10px 12px', marginBottom: 8 }}>
          <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>按此配置，本部门账号可见的业务数据（含移交授权的记录）</div>
          {preview ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px' }}>
              {catalog.summary.map((s) => (
                <span key={s.key} style={{ fontSize: 13 }}>{s.label} <b>{preview[s.key] ?? 0}</b></span>
              ))}
            </div>
          ) : <span style={{ color: '#94a3b8', fontSize: 13 }}>{dept ? '计算中…' : '新部门暂无数据'}</span>}
        </div>
        <div style={{ color: '#64748b', fontSize: 12 }}>
          跨部门移交（转线索、移送审理等）的记录不受此限制：接收部门可查阅来源记录，来源部门可跟踪办理结果。
        </div>
      </Form>

      {dept && (
        <>
          <SectionTitle><ApiOutlined /> MCP 服务令牌</SectionTitle>
          <div style={{ color: '#64748b', fontSize: 12, marginBottom: 8 }}>
            在本部门对应的 Nexent 租户注册业务平台 MCP（ctas-tools）时填写此令牌，智能体经 MCP 只能查到本部门数据范围内的数据。
            令牌只显示一次，重新生成即作废旧令牌。
          </div>
          <Space>
            <Tag color={dept.mcp_token ? 'green' : 'default'}>{dept.mcp_token ? '已配置' : '未配置'}</Tag>
            <Popconfirm title={dept.mcp_token ? '重新生成将使旧令牌立即失效，确定？' : '生成部门 MCP 令牌？'} onConfirm={issueToken}>
              <Button size="small" icon={<KeyOutlined />}>{dept.mcp_token ? '重新生成' : '生成令牌'}</Button>
            </Popconfirm>
            {dept.mcp_token && <Popconfirm title="撤销后使用该令牌的 MCP 连接立即失效，确定？" onConfirm={revokeToken}>
              <Button size="small" danger>撤销</Button>
            </Popconfirm>}
          </Space>
          {token && (
            <Alert style={{ marginTop: 10 }} type="warning" showIcon icon={<LockOutlined />}
              message="请立即复制保存，关闭后无法再次查看"
              description={<Typography.Paragraph copyable={{ text: token }} style={{ marginBottom: 0, fontFamily: 'monospace', wordBreak: 'break-all' }}>{token}</Typography.Paragraph>} />
          )}

          <SectionTitle extra={<Button size="small" type="link" icon={<TeamOutlined />} onClick={() => { onClose(); nav(`/users?dept=${dept.id}`) }}>管理账号</Button>}>
            部门账号（{members.length}）
          </SectionTitle>
          {members.length ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {members.map((u) => (
                <Tag key={u.id} color={u.is_active ? 'blue' : 'default'} style={{ padding: '2px 8px' }}>
                  {u.real_name || u.username}<span style={{ opacity: 0.7 }}> · {ROLE_NAMES[u.role] || u.role}{u.is_active ? '' : ' · 停用'}</span>
                </Tag>
              ))}
            </div>
          ) : <span style={{ color: '#94a3b8' }}>暂无账号，可在「用户管理」中新建或调整账号所属部门。</span>}
        </>
      )}
    </Drawer>
  )
}

function SectionTitle({ children, extra }: { children: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', margin: '18px 0 10px', paddingBottom: 6, borderBottom: '1px solid #f1f5f9' }}>
      <span style={{ fontWeight: 600, color: '#0f172a', fontSize: 14 }}>{children}</span>
      <span style={{ flex: 1 }} />
      {extra}
    </div>
  )
}

