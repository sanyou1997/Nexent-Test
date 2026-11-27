import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Card, Table, Button, Modal, Form, Input, Select, Tag, message, Popconfirm, Space } from 'antd'
import { UserAddOutlined, TeamOutlined, ClusterOutlined } from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { ROLE_NAMES } from '../auth'
import PageHeader from '../components/PageHeader'

interface Row {
  id: number; username: string; real_name: string; role: string; role_name: string
  dept_id: number | null; dept_name: string; is_active: boolean; created_at: string
}

const roleOptions = Object.entries(ROLE_NAMES).map(([v, l]) => ({ value: v, label: l }))

export default function Users() {
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const [rows, setRows] = useState<Row[]>([])
  const [depts, setDepts] = useState<{ id: number; name: string; enabled: boolean }[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [edit, setEdit] = useState<Row | null>(null)
  const [pwdFor, setPwdFor] = useState<Row | null>(null)
  const [form] = Form.useForm()
  const [eform] = Form.useForm()
  const [pform] = Form.useForm()
  const deptFilter = params.get('dept') ? Number(params.get('dept')) : undefined

  const load = async () => {
    setLoading(true)
    try {
      const [{ data: us }, { data: ds }] = await Promise.all([api.get('/users'), api.get('/departments')])
      setRows(us)
      setDepts(ds.items)
    } catch (e) { message.error(errMsg(e)) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const deptOptions = useMemo(() => depts.filter((d) => d.enabled).map((d) => ({ value: d.id, label: d.name })), [depts])
  const shown = deptFilter ? rows.filter((r) => r.dept_id === deptFilter) : rows

  const create = async (v: any) => {
    try {
      await api.post('/users', v)
      message.success('账号已创建')
      setCreateOpen(false); form.resetFields(); load()
    } catch (e) { message.error(errMsg(e)) }
  }
  const saveEdit = async (v: any) => {
    if (!edit) return
    try {
      await api.put(`/users/${edit.id}`, v)
      message.success('已保存（该账号下次进入系统时按新部门 / 角色生效）')
      setEdit(null); load()
    } catch (e) { message.error(errMsg(e)) }
  }
  const resetPwd = async (v: any) => {
    if (!pwdFor) return
    try {
      await api.post(`/users/${pwdFor.id}/password`, { password: v.password })
      message.success('密码已重置')
      setPwdFor(null); pform.resetFields()
    } catch (e) { message.error(errMsg(e)) }
  }
  const toggle = async (uid: number) => {
    try { await api.put(`/users/${uid}/toggle`); load() } catch (e) { message.error(errMsg(e)) }
  }

  return (
    <>
      <PageHeader
        icon={<TeamOutlined />}
        title="用户管理"
        subtitle="账号归属部门：部门决定可见的功能模块与业务数据范围；角色（岗位）决定模块内可办理的操作。"
        extra={<Space>
          <Button icon={<ClusterOutlined />} onClick={() => nav('/departments')}>部门与权限</Button>
          <Button type="primary" icon={<UserAddOutlined />} onClick={() => {
            form.setFieldsValue({ role: 'banan', dept_id: deptFilter }); setCreateOpen(true)
          }}>新建账号</Button>
        </Space>} />
      <Card>
        <Space style={{ marginBottom: 12 }}>
          <span style={{ color: '#64748b' }}>部门</span>
          <Select allowClear placeholder="全部部门" style={{ width: 220 }} value={deptFilter}
            options={depts.map((d) => ({ value: d.id, label: d.name }))}
            onChange={(v) => { if (v) setParams({ dept: String(v) }); else setParams({}) }} />
          <span style={{ color: '#94a3b8' }}>共 {shown.length} 个账号</span>
        </Space>
        <Table rowKey="id" size="middle" dataSource={shown} loading={loading} pagination={{ pageSize: 20, hideOnSinglePage: true }}
          columns={[
            { title: '用户名', dataIndex: 'username' },
            { title: '姓名', dataIndex: 'real_name' },
            { title: '所属部门', dataIndex: 'dept_name', render: (n: string) => n ? <Tag>{n}</Tag> : <Tag color="red">未分配</Tag> },
            { title: '角色', dataIndex: 'role', render: (r: string) => <Tag color="blue">{ROLE_NAMES[r] || r}</Tag> },
            { title: '状态', dataIndex: 'is_active',
              render: (a: boolean) => <Tag color={a ? 'green' : 'red'}>{a ? '启用' : '停用'}</Tag> },
            { title: '创建日期', dataIndex: 'created_at', width: 110 },
            { title: '操作', width: 220, render: (_: any, r: Row) => (
              <Space size={4}>
                <Button size="small" type="link" onClick={() => { setEdit(r); eform.setFieldsValue({ real_name: r.real_name, dept_id: r.dept_id, role: r.role }) }}>编辑</Button>
                <Button size="small" type="link" onClick={() => setPwdFor(r)}>重置密码</Button>
                <Popconfirm title={r.is_active ? '停用该账号？' : '启用该账号？'} onConfirm={() => toggle(r.id)}>
                  <Button size="small" type="link" danger={r.is_active}>{r.is_active ? '停用' : '启用'}</Button>
                </Popconfirm>
              </Space>
            ) },
          ]} />
      </Card>

      <Modal title="新建账号" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={() => form.submit()} destroyOnClose>
        <Form form={form} layout="vertical" onFinish={create}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 8, message: '至少 8 位' }]}><Input.Password /></Form.Item>
          <Form.Item name="real_name" label="姓名"><Input /></Form.Item>
          <Form.Item name="dept_id" label="所属部门" rules={[{ required: true, message: '请选择部门' }]}>
            <Select options={deptOptions} placeholder="选择部门" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}><Select options={roleOptions} /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`编辑账号：${edit?.username || ''}`} open={!!edit} onCancel={() => setEdit(null)} onOk={() => eform.submit()} destroyOnClose>
        <Form form={eform} layout="vertical" onFinish={saveEdit}>
          <Form.Item name="real_name" label="姓名"><Input /></Form.Item>
          <Form.Item name="dept_id" label="所属部门" rules={[{ required: true, message: '请选择部门' }]}
            extra="调整部门后，该账号只能看到新部门的功能模块与业务数据。">
            <Select options={deptOptions} />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}><Select options={roleOptions} /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`重置密码：${pwdFor?.username || ''}`} open={!!pwdFor} onCancel={() => setPwdFor(null)} onOk={() => pform.submit()} destroyOnClose>
        <Form form={pform} layout="vertical" onFinish={resetPwd}>
          <Form.Item name="password" label="新密码" rules={[{ required: true, min: 8, message: '至少 8 位' }]}><Input.Password /></Form.Item>
        </Form>
      </Modal>
    </>
  )
}
