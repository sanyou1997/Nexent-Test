import { Modal, Select, message } from 'antd'
import { api } from '../api/client'

/**
 * 跨部门移交时确定承办部门（信访转线索、移送审理、党风 / 巡察移交）。
 * - 只有一个可承办部门、或本部门即可承办时，不打扰用户，直接返回该部门；
 * - 多个部门可承办时弹窗选择。
 * 返回：部门 id；null = 交由后端自动确定；undefined = 用户取消。
 */
export async function pickReceiver(modules: string, action: string): Promise<number | null | undefined> {
  let data: { items: { id: number; name: string }[]; default: number | null }
  try {
    ({ data } = await api.get('/departments/receivers', { params: { modules } }))
  } catch {
    return null
  }
  const items = data.items || []
  if (data.default) return data.default
  if (items.length <= 1) return items[0]?.id ?? null
  return new Promise((resolve) => {
    let chosen: number | undefined
    Modal.confirm({
      title: `${action}：选择承办部门`,
      width: 460,
      content: (
        <div style={{ marginTop: 8 }}>
          <div style={{ color: '#64748b', fontSize: 13, marginBottom: 10 }}>
            有多个部门开通了该业务。新记录归承办部门所有；承办部门可查阅本条来源记录，本部门可继续跟踪办理结果。
          </div>
          <Select
            style={{ width: '100%' }} placeholder="请选择承办部门" autoFocus
            options={items.map((d) => ({ value: d.id, label: d.name }))}
            onChange={(v: number) => { chosen = v }}
          />
        </div>
      ),
      okText: '确定', cancelText: '取消',
      onOk: () => {
        if (!chosen) {
          message.warning('请选择承办部门')
          return Promise.reject(new Error('no dept'))
        }
        resolve(chosen)
        return undefined
      },
      onCancel: () => resolve(undefined),
    })
  })
}
