// 五大菜单分组 → 主题强调色。
// 子模块页面据此统一配色：布局组件在 route-view 上注入 --mod-* CSS 变量，
// 全局 CSS（index.css）再用这些变量接管 PageHeader / 主按钮 / 表头 / Tabs 等公共原语。
// 设计原则：机构感、克制、不花哨；各组色相区分明显但饱和度偏低。

export interface GroupTheme {
  key: string
  label: string
  accent: string // 主强调色（hex）
  accent2: string // 渐变副色（更亮）
  soft: string // 浅色底（rgba，用于表头/选中行）
  ring: string // 发光 / 阴影色（rgba）
}

export const GROUP_THEME: Record<string, GroupTheme> = {
  g1: { key: 'g1', label: '智能辅助', accent: '#2f8fd6', accent2: '#5cb2f0', soft: 'rgba(47,143,214,0.10)', ring: 'rgba(47,143,214,0.35)' },
  g2: { key: 'g2', label: '业务办理', accent: '#1f9d6b', accent2: '#4fd0b0', soft: 'rgba(31,157,107,0.10)', ring: 'rgba(31,157,107,0.32)' },
  g3: { key: 'g3', label: '智能监督', accent: '#e0892b', accent2: '#f0b46a', soft: 'rgba(224,137,43,0.10)', ring: 'rgba(224,137,43,0.34)' },
  g4: { key: 'g4', label: '知识库', accent: '#7c6ce0', accent2: '#a99bf0', soft: 'rgba(124,108,224,0.10)', ring: 'rgba(124,108,224,0.34)' },
  g5: { key: 'g5', label: '系统管理', accent: '#5b6b87', accent2: '#8898b5', soft: 'rgba(91,107,135,0.10)', ring: 'rgba(91,107,135,0.32)' },
}

// 路由 → 所属分组（与 CtasLayout 的导航分组保持一致）
const ROUTE_GROUP: Record<string, string> = {
  '/dashboard': 'g1', '/qa': 'g1', '/verify': 'g1', '/ocr': 'g1', '/transcripts': 'g1',
  '/archives': 'g1', '/data-intake': 'g1', '/assistant': 'g1', '/education': 'g1',
  '/historical-reports': 'g1',
  '/ctas-dashboard': 'g1',
  '/petitions': 'g2', '/cases': 'g2', '/trials': 'g2', '/conduct': 'g2', '/inspections': 'g2',
  '/documents': 'g2', '/bank': 'g2', '/ctas-materials': 'g2',
  '/supervise': 'g3', '/alerts': 'g3', '/clue': 'g3', '/integrity': 'g3',
  '/regulations': 'g4',
  '/audit': 'g5', '/users': 'g5',
}

export function routeToTheme(pathname: string): GroupTheme {
  return GROUP_THEME[ROUTE_GROUP[pathname] || 'g1']
}

/** 给 route-view 容器用的内联 CSS 变量（注入后整页公共原语随分组变色） */
export function modAccentVars(pathname: string): React.CSSProperties {
  const t = routeToTheme(pathname)
  const vars: Record<string, string> = {
    '--mod-accent': t.accent,
    '--mod-accent2': t.accent2,
    '--mod-soft': t.soft,
    '--mod-ring': t.ring,
  }
  return vars as React.CSSProperties
}
