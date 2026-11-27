import { useState } from 'react'
import { Button, Modal, Tag, Typography, Space, Alert, Collapse, Tooltip, Divider } from 'antd'
import { ApartmentOutlined, ThunderboltOutlined, RobotOutlined } from '@ant-design/icons'
import {
  SYSTEM_INTRO, MAIN_STAGES, INSPECTION_FLOW, SUPERVISE_LOOP, SUPPORT_MODULES, MODULE_GROUPS,
  type FlowStep, type MainStage,
} from '../workflow'
import { SYSTEM_DISCLAIMER } from '../disclaimer'

/** 步骤执行者标签：AI=本机模型/引擎，自动=系统规则，人工=工作人员 */
const BY_TAG: Record<FlowStep['by'], { text: string; color: string }> = {
  ai: { text: 'AI', color: 'purple' },
  auto: { text: '自动', color: 'blue' },
  human: { text: '人工', color: 'orange' },
}

function ByTag({ by }: { by: FlowStep['by'] }) {
  const t = BY_TAG[by]
  return <Tag color={t.color} style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: '16px' }}>{t.text}</Tag>
}

/** 一条步骤链：序号 + 标签 + 名称 + 备注 */
function StepChain({ steps }: { steps: FlowStep[] }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-start', gap: 4 }}>
      {steps.map((s, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'flex-start' }}>
          {i > 0 && <span style={{ color: '#b6c0cf', margin: '0 6px', fontSize: 13, lineHeight: '22px' }}>→</span>}
          <Tooltip title={s.note || ''}>
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 5,
              background: '#f6f7f9', border: '1px solid #e8eaed', borderRadius: 6,
              padding: '2px 8px', fontSize: 12.5, lineHeight: '20px', whiteSpace: 'nowrap',
            }}>
              <ByTag by={s.by} />
              {s.label}
            </span>
          </Tooltip>
        </div>
      ))}
    </div>
  )
}

/** 主线阶段卡片 */
function StageCard({ stage, last }: { stage: MainStage; last?: boolean }) {
  return (
    <div>
      <div style={{
        display: 'flex', gap: 12, background: '#fff', border: '1px solid #e5e7eb',
        borderRadius: 10, padding: '10px 14px', boxShadow: '0 1px 2px rgba(0,0,0,.03)',
      }}>
        <div style={{
          minWidth: 30, height: 30, borderRadius: 8, background: '#1d4ed8', color: '#fff',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontWeight: 700, fontSize: 14,
        }}>{stage.no}</div>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <Typography.Text strong style={{ fontSize: 14.5 }}>{stage.name}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>{stage.module}</Typography.Text>
          </div>
          <div style={{ marginTop: 5, display: 'flex', flexWrap: 'wrap', gap: '4px 14px' }}>
            {stage.actions.map((a, i) => (
              <span key={i} style={{ fontSize: 12, color: '#475569' }}>· {a}</span>
            ))}
          </div>
        </div>
      </div>
      {!last && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0 3px 15px' }}>
          <span style={{ color: '#1d4ed8', fontSize: 15 }}>↓</span>
          <span style={{ fontSize: 11.5, color: '#64748b' }}>{stage.handoff}</span>
        </div>
      )}
    </div>
  )
}

/** 受控弹窗本体：任意位置可用（顶栏按钮、工作台卡片均复用它） */
export function SystemIntroModal({ open, onCancel }: { open: boolean; onCancel: () => void }) {
  return (
    <Modal open={open} onCancel={onCancel} footer={null} width={860}
      title={<Space><ApartmentOutlined style={{ color: '#1d4ed8' }} />系统介绍 · 全系统工作流</Space>}>
        {/* 一、一段简易清晰的介绍 */}
        <Alert type="info" showIcon style={{ marginBottom: 10 }}
          message={<Typography.Text style={{ fontSize: 13 }}>{SYSTEM_INTRO.lead}</Typography.Text>} />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 4 }}>
          {SYSTEM_INTRO.facts.map((f, i) => (
            <Tag key={i} color="geekblue" style={{ fontSize: 11.5 }}>{f}</Tag>
          ))}
        </div>

        <Divider orientation="left" plain style={{ margin: '12px 0 10px' }}>
          <Space size={6}><ThunderboltOutlined style={{ color: '#b45309' }} />主业务线：从信访到整改的闭环</Space>
        </Divider>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {MAIN_STAGES.map((s, i) => (
            <StageCard key={s.no} stage={s} last={false} />
          ))}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0 3px 15px' }}>
            <span style={{ color: '#1d4ed8', fontSize: 15 }}>↓</span>
            <span style={{ fontSize: 11.5, color: '#64748b' }}>巡察发现的问题按类别移交，汇入对应环节闭环</span>
          </div>
          <StageCard stage={INSPECTION_FLOW} last />
        </div>

        {/* 二、横向层 */}
        <Divider orientation="left" plain style={{ margin: '14px 0 10px' }}>
          <Space size={6}><RobotOutlined style={{ color: '#4f46e5' }} />横向层一 · 大数据监督闭环（贯穿各业务线）</Space>
        </Divider>
        <div style={{
          background: '#faf5ff', border: '1px dashed #d3adf7', borderRadius: 10, padding: '10px 12px',
        }}>
          <StepChain steps={SUPERVISE_LOOP} />
          <div style={{ fontSize: 11.5, color: '#8c6bb1', marginTop: 8 }}>
            监督模型工厂负责「数据→预警」，督办预警中枢负责「预警→整改」；信访/案管/审理/党风四路 SLA 在此汇总调度。
          </div>
        </div>

        <Divider orientation="left" plain style={{ margin: '14px 0 10px' }}>
          <Space size={6}><ApartmentOutlined style={{ color: '#0f6e56' }} />横向层二 · 智能辅助支撑（各模块复用）</Space>
        </Divider>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {SUPPORT_MODULES.map((m) => (
            <Tooltip key={m.route} title={m.what}>
              <Tag color="green" style={{ fontSize: 12, padding: '2px 8px', marginInlineEnd: 0 }}>{m.name}</Tag>
            </Tooltip>
          ))}
        </div>
        <div style={{ fontSize: 11.5, color: '#64748b', margin: '6px 0 0' }}>
          知识底座：法规库（条款级建库、检索、问答溯源）　·　安全底座：用户分权 + 全程审计留痕
        </div>

        {/* 三、分模块流程明细 */}
        <Divider orientation="left" plain style={{ margin: '14px 0 8px' }}>分模块流程明细</Divider>
        <Collapse
          size="small"
          defaultActiveKey={['业务办理']}
          items={MODULE_GROUPS.map((g) => ({
            key: g.group,
            label: <Typography.Text strong style={{ fontSize: 13 }}>{g.group}</Typography.Text>,
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {g.flows.map((f) => (
                  <div key={f.route}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                      <Typography.Text strong style={{ fontSize: 13 }}>{f.name}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>{f.goal}</Typography.Text>
                    </div>
                    <div style={{ marginTop: 5 }}>
                      <StepChain steps={f.steps} />
                    </div>
                  </div>
                ))}
              </div>
            ),
          }))}
        />
        <div style={{
          marginTop: 12, fontSize: 11.5, color: '#94a3b8', borderTop: '1px dashed #e5e7eb', paddingTop: 8,
        }}>
          说明：所有流程节点仅作「提效 + 留痕 + 提醒」，系统不办理、不审批、不流转任何案件或信访件；
          标注 AI 的步骤由本机模型完成，输出须人工复核；系统可完全断网运行。
        </div>
        {/* 使用限制（原顶栏「声明」按钮内容并入此处，顶栏不再单独占位） */}
        <Collapse
          size="small" ghost
          style={{ marginTop: 6 }}
          items={[{
            key: 'limits',
            label: <Typography.Text style={{ fontSize: 12, color: '#64748b' }}>使用限制与免责声明</Typography.Text>,
            children: (
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: '#475569', lineHeight: 1.75 }}>
                {SYSTEM_DISCLAIMER.limits.map((x, i) => <li key={i}>{x}</li>)}
              </ul>
            ),
          }]}
        />
      </Modal>
  )
}

/** 顶栏「系统介绍」按钮：自带开合状态，内部复用受控弹窗 */
export function SystemIntroButton({ ghost = false, iconOnly = false }: { ghost?: boolean; iconOnly?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Tooltip title={iconOnly ? '系统介绍 · 全系统工作流 · 使用声明' : ''}>
        <Button ghost={ghost} size="small" icon={<ApartmentOutlined />}
          onClick={() => setOpen(true)} style={iconOnly ? { paddingInline: 7 } : undefined}>
          {iconOnly ? null : '系统介绍'}
        </Button>
      </Tooltip>
      <SystemIntroModal open={open} onCancel={() => setOpen(false)} />
    </>
  )
}
