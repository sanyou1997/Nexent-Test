import { ReactNode, useEffect, useRef } from 'react'
import { hexA } from './EChart'

/** 驾驶舱共享套件 —— 深蓝/蓝 · 毛玻璃 · 细发光边（外观由 cockpit.css 承载，此处为薄壳 + 共享常量）。
 *  供全业务 Dashboard 与 CTAS 审理驾驶舱复用，保证两个门面视觉统一。 */

export const C = {
  cyan: '#5cb2f0', blue: '#3d8bea', teal: '#3aa0d6', amber: '#e8b968',
  green: '#4fd0b0', red: '#f08a9e', violet: '#6f8ce0', dim: '#8aa0bf',
}

/** 驾驶舱标签（半透明底 + 同色描边） */
export function GlassTag({ color, children }: { color: string; children: ReactNode }) {
  return (
    <span style={{
      fontSize: 11, lineHeight: '18px', height: 20, padding: '0 7px', borderRadius: 5,
      color, background: hexA(color, 0.14), border: `1px solid ${hexA(color, 0.35)}`,
      display: 'inline-flex', alignItems: 'center', whiteSpace: 'nowrap', flexShrink: 0,
    }}>{children}</span>
  )
}

/** 毛玻璃卡片外壳 + 顶部发光边 + 右下角辉光 */
export function GlassCard({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="ckp-card" style={style}>
      <div className="ckp-glow" />
      <div className="ckp-card-pad">{children}</div>
    </div>
  )
}

/** 卡片标题（左侧发光竖条） */
export function CardHead({ icon, color, children }: { icon?: ReactNode; color?: string; children: ReactNode }) {
  return (
    <div className="ckp-h">
      {icon && <span style={{ color: color || C.cyan, display: 'inline-flex' }}>{icon}</span>}
      {children}
    </div>
  )
}

/** 微弱粒子连线背景 */
export function Particles() {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const cv = ref.current; if (!cv) return
    const ctx = cv.getContext('2d'); if (!ctx) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    let w = 0, h = 0, raf = 0
    type P = { x: number; y: number; vx: number; vy: number; r: number }
    let ps: P[] = []
    const N = 46
    const resize = () => {
      const p = cv.parentElement!; w = p.clientWidth; h = p.clientHeight
      cv.width = w * dpr; cv.height = h * dpr; cv.style.width = w + 'px'; cv.style.height = h + 'px'
      ctx.setTransform(dpr, 0, 0, 0, 0, 0)
      ps = Array.from({ length: N }, () => ({
        x: Math.random() * w, y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.22, vy: (Math.random() - 0.5) * 0.22,
        r: Math.random() * 1.5 + 0.6,
      }))
    }
    const draw = () => {
      ctx.clearRect(0, 0, w, h)
      for (let i = 0; i < ps.length; i++) {
        const p = ps[i]
        p.x += p.vx; p.y += p.vy
        if (p.x < 0 || p.x > w) p.vx *= -1
        if (p.y < 0 || p.y > h) p.vy *= -1
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
        ctx.fillStyle = 'rgba(125,200,255,0.26)'; ctx.fill()
        for (let j = i + 1; j < ps.length; j++) {
          const q = ps[j]; const dx = p.x - q.x, dy = p.y - q.y; const d = Math.hypot(dx, dy)
          if (d < 120) {
            ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y)
            ctx.strokeStyle = `rgba(95,180,240,${0.05 * (1 - d / 120)})`; ctx.lineWidth = 1; ctx.stroke()
          }
        }
      }
    }
    const frame = () => { draw(); raf = requestAnimationFrame(frame) }
    resize(); window.addEventListener('resize', resize)
    if (reduce) draw(); else raf = requestAnimationFrame(frame)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize) }
  }, [])
  return <canvas ref={ref} className="cockpit-particles" />
}

export const fmtClock = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, '0')
  const wk = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()]
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} 周${wk} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
