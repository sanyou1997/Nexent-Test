import { ReactNode } from 'react'

/** 轻量统计图表组件集（纯 SVG，零第三方依赖）。
 *  信创离线环境不便再装图表库，且本组件只需柱状/折线/环形三种形态。 */

export interface BarDatum { key: string; count: number; amount?: number }

/** 横向条形图：适合维度名称较长、类别数不多的场景（类型/地区/问题类别） */
export function BarChart({ data, max, onClick, emptyText = '暂无数据' }: {
  data: BarDatum[]; max?: number; onClick?: (d: BarDatum) => void; emptyText?: string
}) {
  if (!data.length) return <div style={{ padding: '18px 0', textAlign: 'center', color: '#94a3b8' }}>{emptyText}</div>
  const top = max ?? Math.max(...data.map((d) => d.count), 1)
  return (
    <div>
      {data.map((d) => (
        <div
          key={d.key}
          onClick={onClick ? () => onClick(d) : undefined}
          style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0',
            cursor: onClick ? 'pointer' : 'default',
          }}
        >
          <div style={{ width: 96, flex: '0 0 96px', fontSize: 12, color: '#475569',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            title={d.key}>
            {d.key || '未标注'}
          </div>
          <div style={{ flex: 1, background: '#f3f5f9', borderRadius: 3, height: 16, overflow: 'hidden' }}>
            <div style={{
              width: `${Math.max(2, (d.count / top) * 100)}%`, height: '100%',
              background: 'linear-gradient(90deg,#1d4ed8,#4096ff)', borderRadius: 3,
              transition: 'width .3s',
            }} />
          </div>
          <div style={{ width: 56, flex: '0 0 56px', textAlign: 'right', fontSize: 12, color: '#1e293b' }}>
            {d.count}
            {d.amount !== undefined && d.amount > 0 && (
              <span style={{ color: '#94a3b8', fontSize: 11 }}> · {d.amount}万</span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

/** 柱状+折线组合：年度趋势（柱=报告数，折线+右轴=涉及金额） */
export function TrendChart({ data, height = 150 }: {
  data: { key: string; count: number; amount?: number }[]; height?: number
}) {
  if (!data.length) return <div style={{ padding: '18px 0', textAlign: 'center', color: '#94a3b8' }}>暂无数据</div>
  const W = 640, H = height, padL = 34, padR = 44, padT = 12, padB = 26
  const iw = W - padL - padR, ih = H - padT - padB
  const maxC = Math.max(...data.map((d) => d.count), 1)
  const maxA = Math.max(...data.map((d) => d.amount || 0), 1)
  const n = data.length
  const slot = iw / n
  const bw = Math.min(38, slot * 0.5)
  const x = (i: number) => padL + slot * i + slot / 2
  const yC = (v: number) => padT + ih - (v / maxC) * ih
  const yA = (v: number) => padT + ih - (v / maxA) * ih
  const pts = data.map((d, i) => `${x(i)},${yA(d.amount || 0)}`).join(' ')

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: 'block' }}>
      {/* 网格 */}
      {[0, 0.5, 1].map((r) => (
        <line key={r} x1={padL} x2={W - padR} y1={padT + ih * (1 - r)} y2={padT + ih * (1 - r)}
          stroke="#eef0f3" strokeWidth={1} />
      ))}
      {/* 柱 */}
      {data.map((d, i) => (
        <g key={d.key}>
          <rect x={x(i) - bw / 2} y={yC(d.count)} width={bw} height={Math.max(1, padT + ih - yC(d.count))}
            fill="#1d4ed8" fillOpacity={0.85} rx={2}>
            <title>{`${d.key}：${d.count} 份`}</title>
          </rect>
          <text x={x(i)} y={yC(d.count) - 4} textAnchor="middle" fontSize={10} fill="#475569">{d.count}</text>
          <text x={x(i)} y={H - 8} textAnchor="middle" fontSize={10} fill="#64748b">{d.key}</text>
        </g>
      ))}
      {/* 金额折线 */}
      {maxA > 0 && (
        <>
          <polyline points={pts} fill="none" stroke="#b45309" strokeWidth={2} />
          {data.map((d, i) => (
            <circle key={d.key} cx={x(i)} cy={yA(d.amount || 0)} r={3} fill="#b45309">
              <title>{`${d.key}：${d.amount || 0} 万元`}</title>
            </circle>
          ))}
          <text x={W - padR + 6} y={padT + 10} fontSize={10} fill="#b45309">万元</text>
        </>
      )}
      <text x={4} y={padT + 8} fontSize={10} fill="#94a3b8">份</text>
    </svg>
  )
}

/** 环形占比图（用于处理结果/严重程度等小类目分布） */
export function DonutChart({ data, size = 132 }: { data: BarDatum[]; size?: number }) {
  if (!data.length) return <div style={{ padding: '18px 0', textAlign: 'center', color: '#94a3b8' }}>暂无数据</div>
  const total = data.reduce((s, d) => s + d.count, 0) || 1
  const colors = ['#1d4ed8', '#15803d', '#b45309', '#4f46e5', '#b91c1c', '#0e7490', '#b91c1c', '#64748b']
  const R = size / 2, r = R * 0.62
  let acc = 0
  const arcs = data.map((d, i) => {
    const frac = d.count / total
    const start = acc * 2 * Math.PI - Math.PI / 2
    acc += frac
    const end = acc * 2 * Math.PI - Math.PI / 2
    const large = frac > 0.5 ? 1 : 0
    const p = (ang: number, rad: number) => `${R + rad * Math.cos(ang)},${R + rad * Math.sin(ang)}`
    const path = frac >= 0.999
      ? `M ${R} ${R - R} A ${R} ${R} 0 1 1 ${R - 0.01} ${R - R} M ${R} ${R - r} A ${r} ${r} 0 1 0 ${R - 0.01} ${R - r} Z`
      : `M ${p(start, R)} A ${R} ${R} 0 ${large} 1 ${p(end, R)} L ${p(end, r)} A ${r} ${r} 0 ${large} 0 ${p(start, r)} Z`
    return { d, path, color: colors[i % colors.length], pct: Math.round(frac * 100) }
  })
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flex: '0 0 auto' }}>
        {arcs.map((a, i) => (
          <path key={i} d={a.path} fill={a.color} fillOpacity={0.88}>
            <title>{`${a.d.key}：${a.d.count}（${a.pct}%）`}</title>
          </path>
        ))}
        <text x={R} y={R - 2} textAnchor="middle" fontSize={17} fontWeight={600} fill="#1e293b">{total}</text>
        <text x={R} y={R + 14} textAnchor="middle" fontSize={10} fill="#94a3b8">总计</text>
      </svg>
      <div style={{ flex: 1, minWidth: 130 }}>
        {arcs.map((a, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, padding: '2px 0' }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: a.color, flex: '0 0 9px' }} />
            <span style={{ flex: 1, color: '#475569', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {a.d.key || '未标注'}
            </span>
            <span style={{ color: '#1e293b' }}>{a.d.count}</span>
            <span style={{ color: '#94a3b8', width: 34, textAlign: 'right' }}>{a.pct}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** 字段覆盖条：质检报告用 */
export function CoverageBar({ label, coverage, missing }: {
  label: string; coverage: number; missing: number
}) {
  const pct = Math.round(coverage * 100)
  const color = pct >= 90 ? '#15803d' : pct >= 70 ? '#b45309' : '#b91c1c'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
      <div style={{ width: 84, flex: '0 0 84px', fontSize: 12, color: '#475569' }}>{label}</div>
      <div style={{ flex: 1, background: '#f3f5f9', borderRadius: 3, height: 14, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3, transition: 'width .3s' }} />
      </div>
      <div style={{ width: 92, flex: '0 0 92px', textAlign: 'right', fontSize: 11, color: '#475569' }}>
        {pct}%{missing > 0 && <span style={{ color: '#b91c1c' }}> 缺{missing}</span>}
      </div>
    </div>
  )
}

export function StatTile({ label, value, suffix, color, icon }: {
  label: string; value: ReactNode; suffix?: string; color?: string; icon?: ReactNode
}) {
  return (
    <div style={{
      border: '1px solid #eef1f5', borderRadius: 6, padding: '8px 12px',
      background: '#fff', height: '100%',
    }}>
      <div style={{ fontSize: 12, color: '#64748b', display: 'flex', alignItems: 'center', gap: 4 }}>
        {icon}{label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 600, color: color || '#0f172a', lineHeight: 1.4 }}>
        {value}{suffix && <span style={{ fontSize: 12, fontWeight: 400, color: '#94a3b8', marginLeft: 3 }}>{suffix}</span>}
      </div>
    </div>
  )
}
