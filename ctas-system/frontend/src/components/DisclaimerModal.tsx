import { useState } from 'react'
import { Button, Modal, Tag, Typography, Space, Alert, List, Divider } from 'antd'
import { InfoCircleOutlined } from '@ant-design/icons'
import { SYSTEM_DISCLAIMER as D } from '../disclaimer'

/** 顶栏「系统声明」按钮：点击弹出完整声明 */
export function DisclaimerButton({ ghost = false }: { ghost?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button ghost={ghost} size="small" icon={<InfoCircleOutlined />} onClick={() => setOpen(true)}>
        系统声明
      </Button>
      <Modal open={open} onCancel={() => setOpen(false)} footer={null} width={780}
        title={<Space><InfoCircleOutlined style={{ color: '#1d4ed8' }} />{D.title}</Space>}>
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message={<Typography.Text strong>{D.core}</Typography.Text>} />
        <Typography.Paragraph style={{ fontSize: 13 }}>{D.purpose}</Typography.Paragraph>

        <Divider orientation="left" plain style={{ margin: '8px 0' }}>辅助能力范围</Divider>
        <List size="small" dataSource={D.capabilities} rowKey="role"
          renderItem={(c) => (
            <List.Item>
              <div style={{ width: '100%' }}>
                <Tag color="blue" style={{ marginBottom: 4 }}>{c.role}</Tag>
                <div style={{ fontSize: 12, color: '#475569', lineHeight: 1.9 }}>
                  {c.items.map((x, i) => (
                    <span key={i}>· {x}{i < c.items.length - 1 ? '　' : ''}</span>
                  ))}
                </div>
              </div>
            </List.Item>
          )} />

        <Divider orientation="left" plain style={{ margin: '8px 0' }}>使用限制</Divider>
        <List size="small" dataSource={D.limits} rowKey={(x) => x}
          renderItem={(x) => (
            <List.Item style={{ padding: '4px 0' }}>
              <Typography.Text style={{ fontSize: 12 }}>· {x}</Typography.Text>
            </List.Item>
          )} />
      </Modal>
    </>
  )
}
