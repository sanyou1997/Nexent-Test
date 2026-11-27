import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { Row, Col, Empty } from 'antd'
import {
  MailOutlined, FileTextOutlined, BookOutlined, AuditOutlined, AlertOutlined,
  DatabaseOutlined, RadarChartOutlined, ApartmentOutlined, BankOutlined,
  RobotOutlined, ExperimentOutlined, AimOutlined, SafetyCertificateOutlined,
  FileImageOutlined, QuestionCircleOutlined, DashboardOutlined, NodeIndexOutlined,
  HistoryOutlined, BarChartOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import CountUp from '../components/CountUp'
import EChart, { vGrad, hGrad, TIP, LEGEND, AX, AX_FAINT, SPLIT, AXIS_LINE, hexA } from '../components/EChart'
import { SystemIntroModal } from '../components/SystemIntroModal'
import { modStatus } from '../modules'
import './cockpit.css'

/* ---------- 低饱和霓虹色板 ---------- */
const C = {
  cyan: '#5cb2f0', blue: '#3d8bea', teal: '#3aa0d6', amber: '#e8b968',
  green: '#4fd0b0', red: '#f08a9e', violet: '#6f8ce0', dim: '#8aa0bf',
}
const ALERT_STATUS: Record<string, string> = {
  待处理: C.amber, 已确认: C.green, 已分办: C.cyan, 已销号: C.dim, 已忽略: '#7a8aa3',
}
const ALERT_LEVEL: Record<string, string> = {
  高: C.red, 中: C.amber, 低: C.blue, 提示: C.cyan,
}
const PET_STATUS: Record<string, string> = {
  待登记: C.dim, 已抽取: C.blue, 已建议: C.amber, 已分办: C.green,
}
const DISP_COLOR: Record<string, string> = { 已下发: C.amber, 已反馈: C.blue, 已闭环: C.green }
const LEVEL_ORDER = ['高', '中', '低', '提示'] as const
const STATUS_ORDER = ['待处理', '已确认', '已分办', '已销号', '已忽略']

function GlassTag({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span style={{
      fontSize: 11, lineHeight: '18px', height: 20, padding: '0 7px', borderRadius: 5,
      color, background: hexA(color, 0.14), border: `1px solid ${hexA(color, 0.35)}`,
      display: 'inline-flex', alignItems: 'center', whiteSpace: 'nowrap', flexShrink: 0,
    }}>{children}</span>
  )
}

/* ---------- 微弱粒子背景 ---------- */
function Particles() {
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
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
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

const fmtClock = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, '0')
  const wk = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()]
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} 周${wk} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const CAPS = [
  { icon: <MailOutlined />, color: C.blue, title: '信访件辅助', desc: '识别·分类·摘要', to: '/petitions' },
  { icon: <FileTextOutlined />, color: C.cyan, title: '文书辅助生成', desc: '模板·格式·留痕', to: '/documents' },
  { icon: <BankOutlined />, color: C.violet, title: '流水清洗分析', desc: '大额·回流·不平', to: '/bank' },
  { icon: <QuestionCircleOutlined />, color: C.green, title: '法规智能问答', desc: '检索·溯源', to: '/qa' },
  { icon: <ExperimentOutlined />, color: C.teal, title: '监督模型工厂', desc: '规则·阈值', to: '/supervise' },
  { icon: <AlertOutlined />, color: C.amber, title: '督办预警中枢', desc: '分办·销号闭环', to: '/alerts' },
  { icon: <AimOutlined />, color: C.red, title: '线索研判画像', desc: '评分·穿透', to: '/clue' },
  { icon: <SafetyCertificateOutlined />, color: C.blue, title: '廉政档案画像', desc: '人员·关系', to: '/integrity' },
  { icon: <FileImageOutlined />, color: C.violet, title: '档案数字化OCR', desc: '扫描·编目', to: '/archives' },
]

/** 卡片外壳（毛玻璃 + 顶部发光边） */
function GlassCard({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="ckp-card" style={style}>
      <div className="ckp-glow" />
      <div className="ckp-card-pad">{children}</div>
    </div>
  )
}

/** 预警行高亮：高级/超期红闪，中级/临期橙闪，正常蓝 */
function alertRowClass(a: any) {
  if (a.level === '高' || a.sla_state === '超期') return 'ckp-alert-danger'
  if (a.level === '中' || a.sla_state === '临期') return 'ckp-alert-warn'
  return ''
}

export default function Dashboard() {
  const [stats, setStats] = useState<any>(null)
  const [alertStats, setAlertStats] = useState<any>(null)
  const [intake, setIntake] = useState<any>(null)
  const [allAlerts, setAllAlerts] = useState<any[]>([])
  const [dispRows, setDispRows] = useState<any[]>([])
  const [histStats, setHistStats] = useState<any>(null)
  const [histQuality, setHistQuality] = useState<any>(null)
  const [integrity, setIntegrity] = useState<any>(null)
  const [introOpen, setIntroOpen] = useState(false)
  const [clock, setClock] = useState('')
  const nav = useNavigate()

  const [refreshing, setRefreshing] = useState(false)
  const [lastSync, setLastSync] = useState('')
  const [flashed, setFlashed] = useState<Record<string, boolean>>({})
  const prevVals = useRef<Record<string, number>>({})

  // KPI 数值变化时高亮脉冲（仅当数值上升，避免无谓抖动）
  const flashIf = (key: string, nv: number) => {
    if (prevVals.current[key] !== undefined && nv !== prevVals.current[key]) {
      setFlashed((f) => ({ ...f, [key]: true }))
      window.setTimeout(() => setFlashed((f) => ({ ...f, [key]: false })), 1100)
    }
    prevVals.current[key] = nv
  }

  // 全量数据加载：进入即拉取，之后每 30s 自动轮询（真实动态渲染）
  const loadAll = useCallback(() => {
    setRefreshing(true)
    let done = 0
    const finish = () => { if (++done >= 8) { setRefreshing(false); setLastSync(fmtClock(new Date())) } }
    api.get('/stats')
      .then(({ data }) => {
        setStats(data)
        flashIf('petition_total', data?.petition_total ?? 0)
        flashIf('doc_total', data?.doc_total ?? 0)
        flashIf('article_total', data?.article_total ?? 0)
        flashIf('audit_total', data?.audit_total ?? 0)
      }).catch(() => {}).finally(finish)
    api.get('/alerts/stats')
      .then(({ data }) => { setAlertStats(data); flashIf('pending', data?.pending ?? 0) })
      .catch(() => {}).finally(finish)
    api.get('/data-intake/overview')
      .then(({ data }) => { setIntake(data); flashIf('lowBatches', data?.summary?.low_completeness_batches ?? 0) })
      .catch(() => {}).finally(finish)
    api.get('/alerts')
      .then(({ data }) => setAllAlerts(Array.isArray(data) ? data : [])).catch(() => {}).finally(finish)
    api.get('/alerts/dispatches')
      .then(({ data }) => setDispRows(Array.isArray(data) ? data : [])).catch(() => {}).finally(finish)
    api.get('/historical-reports/stats/dimensions')
      .then(({ data }) => setHistStats(data)).catch(() => {}).finally(finish)
    api.get('/historical-reports/quality-report')
      .then(({ data }) => setHistQuality(data)).catch(() => {}).finally(finish)
    api.get('/stats/integrity-overview')
      .then(({ data }) => setIntegrity(data)).catch(() => {}).finally(finish)
  }, [])

  useEffect(() => { loadAll() }, [loadAll])
  useEffect(() => {
    const id = setInterval(loadAll, 30000)
    return () => clearInterval(id)
  }, [loadAll])
  useEffect(() => {
    const t = setInterval(() => setClock(fmtClock(new Date())), 1000)
    setClock(fmtClock(new Date()))
    return () => clearInterval(t)
  }, [])

  const petitionTotal = stats?.petition_total ?? 0
  const pending = alertStats?.pending ?? 0
  const overdue = alertStats?.overdue ?? 0
  const dueSoon = alertStats?.due_soon ?? 0
  const docTotal = stats?.doc_total ?? 0
  const docFinal = stats?.doc_final ?? 0
  const articleTotal = stats?.article_total ?? 0
  const auditTotal = stats?.audit_total ?? 0
  const lowBatches = intake?.summary?.low_completeness_batches ?? 0
  const persistedTotal = intake?.summary?.persisted_total ?? 0
  const completenessPct = persistedTotal > 0 ? Math.round(((persistedTotal - lowBatches) / persistedTotal) * 100) : 0
  const overallColor = integrity?.overall == null ? C.dim
    : (integrity.overall >= 80 ? C.green : integrity.overall >= 55 ? C.amber : C.red)
  const alerts = allAlerts.slice(0, 9)

  const KPI = [
    { icon: <MailOutlined />, color: C.blue, label: '信访举报', value: petitionTotal, suffix: '件', fk: 'petition_total', to: '/petitions' },
    { icon: <AlertOutlined />, color: overdue > 0 ? C.red : C.amber, label: '待处置预警', value: pending, suffix: '件', meta: overdue > 0 ? `超期 ${overdue}` : (dueSoon > 0 ? `临期 ${dueSoon}` : ''), fk: 'pending', to: '/alerts' },
    { icon: <FileTextOutlined />, color: C.cyan, label: '生成文书', value: docTotal, suffix: '件', meta: `${docFinal} 定稿`, fk: 'doc_total', to: '/documents' },
    { icon: <BookOutlined />, color: C.teal, label: '法规条款', value: articleTotal, suffix: '条', fk: 'article_total', to: '/regulations' },
    { icon: <AuditOutlined />, color: C.violet, label: '审计留痕', value: auditTotal, suffix: '条', fk: 'audit_total', to: '/audit' },
    { icon: <DatabaseOutlined />, color: lowBatches > 0 ? C.amber : C.green, label: '数据缺口批次', value: lowBatches, suffix: '批', fk: 'lowBatches', to: '/data-intake' },
  ]

  /* ---------- 近 14 日新增预警（按级别堆叠柱状图） ---------- */
  const trend = useMemo(() => {
    const days: string[] = []
    const today = new Date()
    for (let i = 13; i >= 0; i--) {
      const d = new Date(today); d.setDate(d.getDate() - i)
      days.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`)
    }
    const map: Record<string, Record<string, number>> = {}
    days.forEach((d) => { map[d] = { 高: 0, 中: 0, 低: 0, 提示: 0 } })
    for (const a of allAlerts) {
      const day = (a.created_at || '').slice(0, 10)
      if (map[day] && map[day][a.level] !== undefined) map[day][a.level] += 1
    }
    return days.map((d) => ({ day: d, 高: map[d].高, 中: map[d].中, 低: map[d].低, 提示: map[d].提示 }))
  }, [allAlerts])

  const trendOption = useMemo(() => ({
    animationDuration: 1000, animationEasing: 'cubicOut', animationDurationUpdate: 700,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(125,200,255,0.07)' } }, ...TIP },
    legend: {
      top: 0, right: 0, itemWidth: 10, itemHeight: 10, icon: 'circle',
      textStyle: { color: AX, fontSize: 11 }, itemGap: 12,
    },
    grid: { left: 2, right: 6, top: 30, bottom: 0, containLabel: true },
    xAxis: {
      type: 'category', data: trend.map((t) => t.day.slice(5)), boundaryGap: true,
      axisLine: { lineStyle: { color: AXIS_LINE } }, axisTick: { show: false },
      axisLabel: { color: AX_FAINT, fontSize: 10.5 },
    },
    yAxis: {
      type: 'value', minInterval: 1,
      splitLine: { lineStyle: { color: SPLIT } }, axisLine: { show: false },
      axisLabel: { color: AX_FAINT, fontSize: 10.5 },
    },
    series: LEVEL_ORDER.map((lvl, i) => ({
      name: `${lvl}级`, type: 'bar', stack: 'alerts', barMaxWidth: 18,
      itemStyle: {
        color: vGrad(ALERT_LEVEL[lvl]),
        borderRadius: i === LEVEL_ORDER.length - 1 ? [4, 4, 0, 0] : 0,
        shadowBlur: 9, shadowColor: hexA(ALERT_LEVEL[lvl], 0.5),
      },
      emphasis: { focus: 'series' },
      data: trend.map((t) => t[lvl] || 0),
    })),
  }), [trend])

  /* ---------- 预警级别分布（环形饼图） ---------- */
  const levelData = LEVEL_ORDER
    .filter((l) => (alertStats?.by_level?.[l] ?? 0) > 0)
    .map((l) => ({ name: `${l}级`, value: alertStats!.by_level[l] }))
  const levelOption = useMemo(() => ({
    color: LEVEL_ORDER.map((l) => ALERT_LEVEL[l]),
    tooltip: { trigger: 'item', ...TIP, formatter: '{b}：{c} 件（{d}%）' },
    legend: LEGEND,
    title: {
      text: String(alertStats?.total ?? 0), subtext: '预警总数',
      left: 'center', top: '28%',
      textStyle: { color: '#eaf6ff', fontSize: 22, fontWeight: 700 },
      subtextStyle: { color: AX_FAINT, fontSize: 11 },
    },
    series: [{
      type: 'pie', radius: ['54%', '76%'], center: ['50%', '39%'],
      avoidLabelOverlap: true, minAngle: 3,
      itemStyle: { borderColor: 'rgba(8,20,38,0.85)', borderWidth: 2, borderRadius: 5 },
      label: { show: false }, labelLine: { show: false },
      emphasis: { scaleSize: 5, itemStyle: { shadowBlur: 16, shadowColor: 'rgba(92,178,240,0.5)' } },
      data: levelData,
    }],
  }), [alertStats, levelData.length])

  /* ---------- 预警来源分布（横向条形图） ---------- */
  const sourceEntries = useMemo(() => (
    Object.entries(alertStats?.by_source || {}).sort((a, b) => (b[1] as number) - (a[1] as number))
  ), [alertStats])
  const sourceOption = useMemo(() => ({
    animationDuration: 1000, animationEasing: 'cubicOut', animationDurationUpdate: 700,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...TIP },
    grid: { left: 2, right: 36, top: 4, bottom: 0, containLabel: true },
    xAxis: { type: 'value', splitLine: { show: false }, axisLabel: { show: false }, axisLine: { show: false } },
    yAxis: {
      type: 'category', inverse: true, data: sourceEntries.map(([k]) => k),
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: AX, fontSize: 11 },
    },
    series: [{
      type: 'bar', barMaxWidth: 13,
      itemStyle: { color: hGrad(C.blue), borderRadius: [0, 7, 7, 0], shadowBlur: 9, shadowColor: 'rgba(61,139,234,0.5)' },
      label: { show: true, position: 'right', color: '#dbe9fb', fontSize: 11, fontWeight: 600 },
      data: sourceEntries.map(([, v]) => v),
    }],
  }), [sourceEntries.length])

  /* ---------- 督办闭环情况（环形饼图） ---------- */
  const dispStat = useMemo(() => {
    const m: Record<string, number> = { 已下发: 0, 已反馈: 0, 已闭环: 0 }
    for (const d of dispRows) if (m[d.status] !== undefined) m[d.status] += 1
    return m
  }, [dispRows])
  const dispTotal = dispStat['已下发'] + dispStat['已反馈'] + dispStat['已闭环']
  const dispOption = useMemo(() => ({
    color: ['已下发', '已反馈', '已闭环'].map((k) => DISP_COLOR[k]),
    tooltip: { trigger: 'item', ...TIP, formatter: '{b}：{c} 单（{d}%）' },
    legend: LEGEND,
    title: {
      text: String(dispTotal), subtext: '督办单',
      left: 'center', top: '28%',
      textStyle: { color: '#eaf6ff', fontSize: 22, fontWeight: 700 },
      subtextStyle: { color: AX_FAINT, fontSize: 11 },
    },
    series: [{
      type: 'pie', radius: ['54%', '76%'], center: ['50%', '39%'],
      avoidLabelOverlap: true, minAngle: 3,
      itemStyle: { borderColor: 'rgba(8,20,38,0.85)', borderWidth: 2, borderRadius: 5 },
      label: { show: false }, labelLine: { show: false },
      emphasis: { scaleSize: 5, itemStyle: { shadowBlur: 16, shadowColor: 'rgba(92,178,240,0.5)' } },
      data: [
        { name: '已下发', value: dispStat['已下发'] },
        { name: '已反馈', value: dispStat['已反馈'] },
        { name: '已闭环', value: dispStat['已闭环'] },
      ],
    }],
  }), [dispStat, dispTotal])

  /* ---------- 数据接入完整度（仪表盘） ---------- */
  const gaugeColor = completenessPct >= 80 ? C.green : completenessPct >= 50 ? C.amber : C.red
  const gaugeOption = useMemo(() => ({
    series: [{
      type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: 100,
      radius: '96%', center: ['50%', '56%'],
      progress: {
        show: true, width: 11, roundCap: true,
        itemStyle: { color: gaugeColor, shadowColor: hexA(gaugeColor, 0.7), shadowBlur: 14 },
      },
      axisLine: { lineStyle: { width: 11, color: [[1, 'rgba(125,200,255,0.12)']] } },
      axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
      pointer: { show: false }, anchor: { show: false },
      detail: {
        valueAnimation: true, offsetCenter: [0, '-2%'], fontSize: 27, fontWeight: 700,
        color: '#eaf6ff', formatter: '{value}%',
      },
      title: { show: true, offsetCenter: [0, '30%'], fontSize: 11, color: AX_FAINT },
      data: [{ value: completenessPct, name: '数据接入完整度' }],
    }],
  }), [completenessPct])

  /* ---------- 信访办理状态（南丁格尔饼图） ---------- */
  const petEntries = useMemo(() => (
    Object.entries(stats?.petition_by_status || {})
      .filter(([, v]) => (v as number) > 0)
      .map(([k, v]) => ({ name: k, value: v as number }))
  ), [stats])
  const petOption = useMemo(() => ({
    color: Object.keys(PET_STATUS).map((k) => PET_STATUS[k]),
    tooltip: { trigger: 'item', ...TIP, formatter: '{b}：{c} 件（{d}%）' },
    legend: LEGEND,
    title: {
      text: String(petitionTotal), subtext: '信访举报（件）',
      left: 'center', top: '28%',
      textStyle: { color: '#eaf6ff', fontSize: 22, fontWeight: 700 },
      subtextStyle: { color: AX_FAINT, fontSize: 11 },
    },
    series: [{
      type: 'pie', radius: ['28%', '72%'], center: ['50%', '39%'],
      roseType: 'radius',
      itemStyle: { borderColor: 'rgba(8,20,38,0.85)', borderWidth: 2, borderRadius: 5 },
      label: { show: false }, labelLine: { show: false },
      emphasis: { scaleSize: 6, itemStyle: { shadowBlur: 16, shadowColor: 'rgba(92,178,240,0.5)' } },
      data: petEntries,
    }],
  }), [petEntries.length, petitionTotal])

  const channels = (intake?.channels || []).slice(0, 6)

  /* ---------- 历史报告库：年度趋势（柱=份数，线=涉及金额）+ 问题类别 ---------- */
  const histYearOption = useMemo(() => {
    const rows = histStats?.by_year || []
    return {
      animationDuration: 900, animationEasing: 'cubicOut',
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...TIP },
      legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, icon: 'circle', textStyle: { color: AX, fontSize: 11 } },
      grid: { left: 2, right: 4, top: 28, bottom: 0, containLabel: true },
      xAxis: {
        type: 'category', data: rows.map((r: any) => String(r.key)), boundaryGap: true,
        axisLine: { lineStyle: { color: AXIS_LINE } }, axisTick: { show: false },
        axisLabel: { color: AX_FAINT, fontSize: 10.5 },
      },
      yAxis: [
        { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: SPLIT } }, axisLine: { show: false }, axisLabel: { color: AX_FAINT, fontSize: 10.5 } },
        { type: 'value', splitLine: { show: false }, axisLine: { show: false }, axisLabel: { color: AX_FAINT, fontSize: 10.5, formatter: (v: number) => `${v}万` } },
      ],
      series: [
        {
          name: '报告份数', type: 'bar', barMaxWidth: 20,
          itemStyle: { color: vGrad(C.blue), borderRadius: [4, 4, 0, 0], shadowBlur: 9, shadowColor: hexA(C.blue, 0.45) },
          data: rows.map((r: any) => r.count),
        },
        {
          name: '涉及金额', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6,
          itemStyle: { color: C.amber }, lineStyle: { color: C.amber, width: 2 },
          data: rows.map((r: any) => r.amount || 0),
        },
      ],
    }
  }, [histStats])

  const histProblemOption = useMemo(() => {
    const rows = (histStats?.by_problem || []).slice(0, 8)
    return {
      animationDuration: 900,
      tooltip: { trigger: 'item', ...TIP, formatter: '{b}：{c} 份（{d}%）' },
      legend: LEGEND,
      title: {
        text: String(histStats?.total ?? 0), subtext: '历史报告（份）',
        left: 'center', top: '28%',
        textStyle: { color: '#eaf6ff', fontSize: 22, fontWeight: 700 },
        subtextStyle: { color: AX_FAINT, fontSize: 11 },
      },
      series: [{
        type: 'pie', radius: ['28%', '72%'], center: ['50%', '39%'],
        roseType: 'radius',
        itemStyle: { borderColor: 'rgba(8,20,38,0.85)', borderWidth: 2, borderRadius: 5 },
        label: { show: false }, labelLine: { show: false },
        emphasis: { scaleSize: 6, itemStyle: { shadowBlur: 16, shadowColor: 'rgba(92,178,240,0.5)' } },
        data: rows.map((r: any) => ({ name: r.key, value: r.count })),
      }],
    }
  }, [histStats])

  return (
    <div className="cockpit">
      <Particles />
      <div className="cockpit-scan" />
      <div className="cockpit-inner">
        {/* 极简工具行：只保留时钟与工作流入口，标题/副标题已由顶栏承担，不再重复 */}
        <div className="ckp-toolbar">
          <span className="ckp-live">
            <i className={refreshing ? 'ckp-spin' : ''} />
            {refreshing ? '数据同步中' : '实时数据接入中'}
            {lastSync && <span className="ckp-sync-time">· 同步于 {lastSync.slice(11)}</span>}
          </span>
          <div className="ckp-clock"><b>{clock || '—'}</b></div>
          <span className="ckp-flow" onClick={() => setIntroOpen(true)}>
            <ApartmentOutlined />
            全系统工作流
          </span>
        </div>

        {/* KPI 概览（紧凑） */}
        <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
          {KPI.map((c, i) => (
            <Col key={c.label} xs={12} sm={8} lg={4}>
              <div className={`ckp-card${flashed[c.fk] ? ' ckp-card--flash' : ''}${c.fk === 'pending' && overdue > 0 ? ' ckp-card--alert' : ''}`} style={{ animation: `ckp-rise .5s ${i * 60}ms both` }} onClick={() => nav(c.to)}>
                <div className="ckp-glow" />
                <div className="ckp-card-pad ckp-kpi-pad">
                  <div className="ckp-kpi-ico" style={{ background: `linear-gradient(135deg, ${hexA(c.color, 0.9)}, ${hexA(c.color, 0.5)})` }}>
                    {c.icon}
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div className="ckp-kpi-label">{c.label}</div>
                    <div className="ckp-kpi-val">
                      <CountUp end={c.value} />
                      <span className="ckp-kpi-unit">{c.suffix}</span>
                      {c.meta && <span className="ckp-kpi-meta">{c.meta}</span>}
                    </div>
                  </div>
                </div>
              </div>
            </Col>
          ))}
        </Row>

        {/* 图表区一：14 日趋势 + 级别分布 */}
        <Row gutter={[14, 14]} style={{ marginBottom: 14 }}>
          <Col xs={24} lg={14}>
            <GlassCard>
              <div className="ckp-h"><NodeIndexOutlined style={{ color: C.cyan }} />近 14 日新增预警趋势（按级别堆叠）</div>
              <EChart option={trendOption} height={252} />
            </GlassCard>
          </Col>
          <Col xs={24} lg={10}>
            <GlassCard>
              <div className="ckp-h"><RadarChartOutlined style={{ color: C.cyan }} />预警级别分布</div>
              {levelData.length === 0
                ? <Empty description="暂无预警" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ color: 'rgba(190,214,240,0.6)', padding: '40px 0' }} />
                : <EChart option={levelOption} height={252} />}
            </GlassCard>
          </Col>
        </Row>

        {/* 图表区二：完整度仪表 + 来源分布 + 督办闭环 */}
        <Row gutter={[14, 14]} style={{ marginBottom: 14 }}>
          <Col xs={24} lg={8}>
            <GlassCard>
              <div className="ckp-h"><DatabaseOutlined style={{ color: C.teal }} />数据接入完整度</div>
              <EChart option={gaugeOption} height={168} />
              {channels.map((ch: any) => {
                const comp = ch.completeness || {}
                let pct: number | null = null
                if (typeof comp.avg_score === 'number') pct = Math.round(comp.avg_score)
                else if (ch.key === 'archive') pct = comp.total_pages ? Math.round((1 - (comp.ocr_missing || 0) / comp.total_pages) * 100) : 100
                else if (ch.key === 'transcript') {
                  const done = comp.by_status?.完成 || 0, fail = comp.by_status?.失败 || 0
                  pct = (done + fail) > 0 ? Math.round((done / (done + fail)) * 100) : 100
                } else if (ch.key === 'dataset') pct = (comp.total_rows || 0) > 0 ? 100 : 0
                const col = pct == null ? C.cyan : (pct >= 80 ? C.green : pct >= 55 ? C.amber : C.red)
                return (
                  <div key={ch.key} style={{ marginBottom: 8 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, fontSize: 12 }}>
                      <span style={{ color: 'rgba(200,222,245,0.82)' }}>{ch.name}</span>
                      {pct == null
                        ? <GlassTag color={C.cyan}>实时·今日{ch.count}次</GlassTag>
                        : <span style={{ color: 'rgba(190,214,240,0.55)' }}>{pct}%</span>}
                    </div>
                    {pct == null ? null : (
                      <div className="ckp-bar-track">
                        <div className="ckp-bar-fill" style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${hexA(col, 0.6)}, ${col})` }} />
                      </div>
                    )}
                  </div>
                )
              })}
            </GlassCard>
          </Col>
          <Col xs={24} lg={8}>
            <GlassCard>
              <div className="ckp-h"><AlertOutlined style={{ color: C.amber }} />预警来源分布</div>
              {sourceEntries.length === 0
                ? <Empty description="暂无预警" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ color: 'rgba(190,214,240,0.6)', padding: '40px 0' }} />
                : <EChart option={sourceOption} height={268} />}
            </GlassCard>
          </Col>
          <Col xs={24} lg={8}>
            <GlassCard>
              <div className="ckp-h"><SafetyCertificateOutlined style={{ color: C.green }} />督办闭环情况</div>
              {dispTotal === 0
                ? <Empty description="暂无督办单" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ color: 'rgba(190,214,240,0.6)', padding: '40px 0' }} />
                : <EChart option={dispOption} height={252} />}
            </GlassCard>
          </Col>
        </Row>

        {/* 图表区三：实时预警 + 信访态势 */}
        <Row gutter={[14, 14]} style={{ marginBottom: 14 }}>
          <Col xs={24} lg={14}>
            <GlassCard>
              <div className="ckp-h"><AlertOutlined style={{ color: C.amber }} />实时预警 · 督办中枢</div>
              {alerts.length === 0 && <Empty description="暂无预警" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ color: 'rgba(190,214,240,0.6)' }} />}
              {alerts.map((a) => (
                <div key={a.id} className={`ckp-alert-row ${alertRowClass(a)}`} onClick={() => nav('/alerts')}>
                  <GlassTag color={ALERT_LEVEL[a.level] || C.dim}>{a.level}</GlassTag>
                  <GlassTag color={C.blue}>{a.source}</GlassTag>
                  {a.sla_state === '超期' && <GlassTag color={C.red}>超期</GlassTag>}
                  {a.sla_state === '临期' && <GlassTag color={C.amber}>临期</GlassTag>}
                  <span className="ckp-alert-title">{a.title}</span>
                  <GlassTag color={ALERT_STATUS[a.status] || C.dim}>{a.status}</GlassTag>
                  <span className="ckp-alert-time">{a.created_at ? new Date(a.created_at).toLocaleString('zh-CN', { hour12: false }).slice(5) : ''}</span>
                </div>
              ))}
            </GlassCard>
          </Col>
          <Col xs={24} lg={10}>
            <GlassCard>
              <div className="ckp-h"><MailOutlined style={{ color: C.blue }} />信访办理态势</div>
              {petEntries.length === 0
                ? <Empty description="暂无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ color: 'rgba(190,214,240,0.6)', padding: '30px 0' }} />
                : <EChart option={petOption} height={252} />}
            </GlassCard>
          </Col>
        </Row>

        {/* 图表区四：历史报告库统计（类案参考与多维统计的数据底座） */}
        {histStats && histStats.total > 0 && (
          <Row gutter={[14, 14]} style={{ marginBottom: 14 }}>
            <Col xs={24} lg={14}>
              <GlassCard>
                <div className="ckp-h"><HistoryOutlined style={{ color: C.violet }} />历史报告库 · 年度趋势与金额
                  <span style={{ marginLeft: 'auto', fontSize: 11, color: C.dim, cursor: 'pointer' }} onClick={() => nav('/historical-reports')}>进入报告库 →</span>
                </div>
                <EChart option={histYearOption} height={252} />
              </GlassCard>
            </Col>
            <Col xs={24} lg={10}>
              <GlassCard>
                <div className="ckp-h"><BarChartOutlined style={{ color: C.teal }} />问题类别分布</div>
                {histProblemOption.series[0].data.length === 0
                  ? <Empty description="暂无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ color: 'rgba(190,214,240,0.6)', padding: '30px 0' }} />
                  : <EChart option={histProblemOption} height={252} />}
              </GlassCard>
            </Col>
          </Row>
        )}

        {/* 数据完整性总览：跨模块「数据是否全了」一张图（数据不全，分析与预警都是空谈） */}
        {integrity && (
          <Row gutter={[14, 14]} style={{ marginBottom: 14 }}>
            <Col xs={24}>
              <GlassCard>
                <div className="ckp-h"><DatabaseOutlined style={{ color: C.teal }} />数据完整性总览
                  <span style={{ marginLeft: 'auto', fontSize: 11, color: C.dim }}>
                    {integrity.scored_modules} 个模块有数据 · 更新于 {integrity.generated_at}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 14, flexWrap: 'wrap' }}>
                  <div style={{ fontSize: 34, fontWeight: 700, color: overallColor }}>
                    {integrity.overall == null ? '—' : `${integrity.overall}%`}
                  </div>
                  <div style={{ fontSize: 12, color: 'rgba(190,214,240,0.65)', flex: 1, minWidth: 220 }}>
                    各模块数据完整度的均值（仅统计有数据的模块，空模块不计入）。低于 80% 的模块建议优先补数据——数据不全，分析与预警都是空谈。点击模块直达补数据页面。
                  </div>
                </div>
                <Row gutter={[10, 10]}>
                  {integrity.modules.map((m: any) => {
                    const col = m.score == null ? C.dim : (m.score >= 80 ? C.green : m.score >= 55 ? C.amber : C.red)
                    return (
                      <Col xs={12} sm={8} lg={6} key={m.key}>
                        <div className="ckp-int-cell" onClick={() => nav(m.link)}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                            <span style={{ color: 'rgba(200,222,245,0.85)' }}>{m.name}</span>
                            <span style={{ color: col, fontWeight: 600 }}>
                              {m.score == null ? '暂无数据' : `${m.score}%`}
                            </span>
                          </div>
                          {m.score == null ? (
                            <div style={{ fontSize: 11, color: C.dim }}>共 {m.total} 条</div>
                          ) : (
                            <>
                              <div className="ckp-bar-track">
                                <div className="ckp-bar-fill" style={{ width: `${m.score}%`, background: `linear-gradient(90deg, ${hexA(col, 0.6)}, ${col})` }} />
                              </div>
                              <div style={{ fontSize: 11, marginTop: 4, color: m.issues > 0 ? C.amber : 'rgba(190,214,240,0.45)' }}>
                                {m.issues > 0 ? `${m.issues} 条待处理 · ${m.issue_label}` : '数据完整'}（共 {m.total} 条）
                              </div>
                            </>
                          )}
                        </div>
                      </Col>
                    )
                  })}
                </Row>
              </GlassCard>
            </Col>
          </Row>
        )}

        {/* 辅助能力导航 */}
        <GlassCard>
          <div className="ckp-h"><DashboardOutlined style={{ color: C.violet }} />辅助能力导航</div>
          <Row gutter={[10, 10]}>
            {CAPS.map((c) => (
              <Col xs={12} key={c.to}>
                <div className="ckp-cap" onClick={() => nav(c.to)}>
                  <div className="ckp-cap-ico" style={{ background: `linear-gradient(135deg, ${hexA(c.color, 0.9)}, ${hexA(c.color, 0.5)})` }}>{c.icon}</div>
                  <div style={{ minWidth: 0 }}>
                    <div className="ckp-cap-t" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      {c.title}
                      {modStatus(c.to) && (
                        <span style={{ fontSize: 9, color: C.dim, border: `1px solid ${hexA(C.dim, 0.4)}`, borderRadius: 3, padding: '0 4px', lineHeight: '14px' }}>{modStatus(c.to)!.label}</span>
                      )}
                    </div>
                    <div className="ckp-cap-d">{c.desc}</div>
                  </div>
                </div>
              </Col>
            ))}
          </Row>
        </GlassCard>
      </div>
      <SystemIntroModal open={introOpen} onCancel={() => setIntroOpen(false)} />
    </div>
  )
}
