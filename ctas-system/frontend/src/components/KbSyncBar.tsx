import { useState } from 'react'
import { Button, Popover, Tag, Typography, message } from 'antd'
import { ApiOutlined, CheckCircleFilled, ClockCircleFilled, CopyOutlined, DownOutlined, UpOutlined, ReloadOutlined } from '@ant-design/icons'

export interface KbSyncItem { regulation_id: number; title: string; file: string; articles: number; status: 'synced' | 'changed' | 'new' }
export interface KbSync {
  configured: boolean; kb: string; synced_at: string; total: number; synced: number; changed: number; new: number
  removed: string[]; in_sync: boolean; items: KbSyncItem[]; sync_command: string; still_processing: string[]
}

export const KB_STATUS: Record<string, { label: string; color: string }> = {
  synced: { label: '已同步', color: 'green' },
  changed: { label: '有更新待同步', color: 'orange' },
  new: { label: '未同步', color: 'default' },
}

/** 法规库 ↔ Nexent 知识库 联动状态条：同步情况、待同步清单、同步方式。 */
export default function KbSyncBar({ data, isAdmin, onRefresh }: { data: KbSync | null; isAdmin: boolean; onRefresh: () => void }) {
  const [open, setOpen] = useState(false)
  if (!data) return null
  const pending = data.items.filter((i) => i.status !== 'synced')
  const pendingCount = pending.length + data.removed.length
  const state = !data.configured ? 'none' : data.in_sync ? 'ok' : 'pending'

  const copy = () => {
    navigator.clipboard?.writeText(data.sync_command).then(() => message.success('已复制同步命令'))
  }
  const howTo = (
    <div style={{ maxWidth: 380, fontSize: 12.5, lineHeight: 1.7 }}>
      平台法规库是主本，Nexent 知识库是它的检索副本。知识库入库必须走 Nexent 自身流水线，
      由运维在部署机上执行（增量：只上传有变化的法规，下架的自动删除）：
      <div style={{ margin: '8px 0', padding: '6px 8px', background: '#f6f8fb', borderRadius: 6, fontFamily: 'Consolas, monospace', fontSize: 12 }}>
        {data.sync_command}
      </div>
      <Button size="small" icon={<CopyOutlined />} onClick={copy}>复制命令</Button>
    </div>
  )

  return (
    <div className="cx-card" style={{ padding: '12px 16px', marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ width: 32, height: 32, borderRadius: 8, background: '#eef3ff', color: '#1d4ed8',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}><ApiOutlined /></span>
        <div style={{ minWidth: 240, flex: 1 }}>
          <div style={{ fontWeight: 600, color: '#0f172a' }}>
            Nexent 知识库联动 <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 12.5 }}>「{data.kb}」</Typography.Text>
            {state === 'ok' && <Tag color="green" icon={<CheckCircleFilled />} bordered={false} style={{ marginLeft: 8 }}>已同步</Tag>}
            {state === 'pending' && <Tag color="orange" icon={<ClockCircleFilled />} bordered={false} style={{ marginLeft: 8 }}>{pendingCount} 项待同步</Tag>}
            {state === 'none' && <Tag bordered={false} style={{ marginLeft: 8 }}>尚未建立同步</Tag>}
          </div>
          <div style={{ fontSize: 12.5, color: '#475569', marginTop: 2 }}>
            {data.configured
              ? <>最近同步 {data.synced_at} · 已同步 {data.synced}/{data.total} 部{data.still_processing.length ? ` · ${data.still_processing.length} 部仍在入库处理` : ''}</>
              : <>平台法规库尚未同步到 Nexent 知识库，智能体检索不到平台法规</>}
            <span style={{ color: '#64748b' }}> ｜ 智能体按条号取条文原文直接读取本库，无需等待同步</span>
          </div>
        </div>
        {pendingCount > 0 && (
          <Button size="small" type="link" onClick={() => setOpen(!open)} icon={open ? <UpOutlined /> : <DownOutlined />}>
            待同步清单
          </Button>
        )}
        {isAdmin && <Popover content={howTo} title="如何同步到 Nexent 知识库" trigger="click" placement="bottomRight">
          <Button size="small">如何同步</Button>
        </Popover>}
        <Button size="small" icon={<ReloadOutlined />} onClick={onRefresh}>刷新状态</Button>
      </div>
      {open && pendingCount > 0 && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid #eef1f5', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {pending.map((i) => (
            <Tag key={i.file} color={KB_STATUS[i.status].color} bordered={false}>{KB_STATUS[i.status].label} · {i.title}</Tag>
          ))}
          {data.removed.map((f) => <Tag key={f} color="red" bordered={false}>已下架待从知识库移除 · {f.replace(/\.md$/, '')}</Tag>)}
        </div>
      )}
    </div>
  )
}
