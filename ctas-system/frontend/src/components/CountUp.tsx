import { useEffect, useRef, useState } from 'react'

interface CountUpProps {
  end: number
  duration?: number
  decimals?: number
}

/** 数字滚动计数：从「上一次显示值」平滑补间到目标值（easeOutCubic）。
 *  首屏从 0 缓动入场；后续数据刷新时从旧值过渡到新值，不再每次归零。 */
export default function CountUp({ end, duration = 1100, decimals = 0 }: CountUpProps) {
  const [val, setVal] = useState(0)
  const raf = useRef<number>()
  const valRef = useRef(0)

  useEffect(() => {
    const from = valRef.current
    const start = performance.now()
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      const v = from + (end - from) * eased
      valRef.current = v
      setVal(v)
      if (p < 1) raf.current = requestAnimationFrame(tick)
      else { valRef.current = end; setVal(end) }
    }
    raf.current = requestAnimationFrame(tick)
    return () => { if (raf.current) cancelAnimationFrame(raf.current) }
  }, [end, duration])

  return (
    <>
      {val.toLocaleString('zh-CN', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}
    </>
  )
}
