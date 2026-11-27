import { Popover } from 'antd'
import { QuestionCircleOutlined } from '@ant-design/icons'
import { MODULE_HELP } from '../help/modules'

/** 一个说明小节：小标题 + 正文 */
function Sec({ no, label, text, color }: { no: string; label: string; text: string; color?: string }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontWeight: 600, fontSize: 12.5, color: color || 'var(--ji-text, #1e293b)', marginBottom: 2 }}>
        {no}、{label}
      </div>
      <div style={{ color: '#475569', textAlign: 'justify' }}>{text}</div>
    </div>
  )
}

/**
 * 模块标题旁的「?」帮助气泡。
 * 面向两类读者：使用者（操作方式、常见问题）与汇报场景（设计理念、依据、实际调用的模型）。
 */
export default function HelpTooltip({ moduleKey, size = 14 }: { moduleKey: string; size?: number }) {
  const h = MODULE_HELP[moduleKey]
  if (!h) return null

  const content = (
    <div style={{ maxWidth: 420, fontSize: 13, lineHeight: 1.75 }}>
      <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10, color: 'var(--ji-text, #222)' }}>
        {h.title} · 功能说明
      </div>

      <Sec no="1" label="功能定位与设计理念" text={h.logic} />
      <Sec no="2" label="设计依据（法规 / 标准）" text={h.basis} color="#1d4ed8" />
      <Sec no="3" label="具体操作方式" text={h.howto} color="#1d4ed8" />
      <Sec no="4" label="实际调用的模型 / 算法" text={h.models} color="#0f6e56" />

      <div style={{ marginBottom: 10 }}>
        <div style={{ fontWeight: 600, fontSize: 12.5, marginBottom: 4, color: '#b45309' }}>
          5、常见问题与已知限制
        </div>
        <ol style={{ margin: 0, paddingLeft: 18, color: '#475569' }}>
          {h.faq.map((q, i) => <li key={i} style={{ marginBottom: 3 }}>{q}</li>)}
        </ol>
      </div>

      <div style={{
        borderTop: '1px dashed #e5e7eb', paddingTop: 8, marginTop: 2,
        fontSize: 12, color: '#64748b',
      }}>
        <b>运行依赖：</b>{h.deps}
      </div>
    </div>
  )

  return (
    <Popover
      title={null}
      content={content}
      trigger="click"
      placement="bottomRight"
      overlayStyle={{ maxWidth: 460 }}
      autoAdjustOverflow
    >
      <QuestionCircleOutlined
        style={{ fontSize: size, color: '#1d4ed8', cursor: 'pointer', marginLeft: 6, verticalAlign: 'middle' }}
        title="查看本模块的设计理念、依据、操作方式与常见问题"
      />
    </Popover>
  )
}
