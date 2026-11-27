import { useEffect, useRef } from 'react'
// 离网信创交付：ECharts 以 vendor 单文件随包分发，运行时零网络依赖、零 npm 依赖。
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore - UMD 单文件无类型声明，调用侧均为 any
import * as echarts from '../vendor/echarts.esm.js'

/**
 * 驾驶舱图表容器：深蓝主题、按需引入（echarts/core ~350KB，离网打包无运行时依赖）。
 * option 变化才重绘；ResizeObserver 自适应卡片尺寸；StrictMode 下 init/dispose 成对，安全。
 */
export default function EChart({
  option, height = 240, style, onClick,
}: {
  option: any
  height?: number
  style?: React.CSSProperties
  onClick?: (params: any) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const key = JSON.stringify(option)
  // 用 ref 持有最新 onClick，避免每次渲染重建图表实例
  const onClickRef = useRef(onClick)
  onClickRef.current = onClick
  useEffect(() => {
    if (!box.current) return
    const c = echarts.init(box.current)
    c.setOption(JSON.parse(key))
    if (onClickRef.current) c.on('click', (p: any) => onClickRef.current && onClickRef.current(p))
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(box.current)
    return () => { ro.disconnect(); c.dispose() }
  }, [key])
  return <div ref={box} style={{ width: '100%', height, ...style }} />
}

/* ---------- 驾驶舱图表主题常量（低饱和霓虹 · 深蓝底） ---------- */
export const AX = 'rgba(190,214,240,0.75)'
export const AX_FAINT = 'rgba(190,214,240,0.45)'
export const SPLIT = 'rgba(125,200,255,0.10)'
export const AXIS_LINE = 'rgba(125,200,255,0.22)'
export const TIP = {
  backgroundColor: 'rgba(8,22,42,0.94)',
  borderColor: 'rgba(125,200,255,0.28)',
  borderWidth: 1,
  textStyle: { color: '#e3ebfd', fontSize: 12 },
  extraCssText: 'border-radius: 8px; box-shadow: 0 6px 24px rgba(0,0,0,0.45);',
}
export const LEGEND = {
  bottom: 0, left: 'center', itemWidth: 10, itemHeight: 10, icon: 'circle',
  textStyle: { color: AX, fontSize: 11 }, itemGap: 14,
}

/** 竖直渐变（柱状图用）：顶部亮、底部沉 */
export function vGrad(hex: string, a0 = 0.95, a1 = 0.45) {
  return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: hexA(hex, a0) },
    { offset: 1, color: hexA(hex, a1) },
  ])
}
/** 水平渐变（横向条形用）：左沉右亮 */
export function hGrad(hex: string, a0 = 0.5, a1 = 0.95) {
  return new echarts.graphic.LinearGradient(0, 0, 1, 0, [
    { offset: 0, color: hexA(hex, a0) },
    { offset: 1, color: hexA(hex, a1) },
  ])
}

export function hexA(hex: string, a: number) {
  const m = hex.replace('#', '')
  const r = parseInt(m.slice(0, 2), 16), g = parseInt(m.slice(2, 4), 16), b = parseInt(m.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${a})`
}
