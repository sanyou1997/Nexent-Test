import { useEffect, useMemo, useState } from 'react'
import { Button, Dropdown, Menu, Tooltip } from 'antd'
import {
  DashboardOutlined, FileDoneOutlined, ApartmentOutlined, DatabaseOutlined, FileTextOutlined,
  QuestionCircleOutlined, FileSearchOutlined, FileImageOutlined, AudioOutlined, BookOutlined,
  SafetyCertificateOutlined, AuditOutlined, TeamOutlined, ApiOutlined, RobotOutlined, LogoutOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, UserOutlined, DownOutlined, ScanOutlined,
  MailOutlined, AimOutlined, SolutionOutlined, FundOutlined, RadarChartOutlined, ExperimentOutlined,
  AlertOutlined, BankOutlined, HistoryOutlined, CloudUploadOutlined, DesktopOutlined, ClusterOutlined,
} from '@ant-design/icons'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth, ROLE_NAMES, canUseModule } from '../auth'
import { SystemIntroButton } from './SystemIntroModal'
import { useNexentStatus } from '../api/nexent'
import './CtasLayout.css'

/** 导航项按所属部门开通的功能模块显示（模块键 = 路由首段，见后端 tenancy.MODULES）；系统管理仅平台管理员。 */
interface NavItem { key: string; label: string; icon: React.ReactNode; badge?: string }
interface NavGroup { key: string; label: string; items: NavItem[] }

/** 平台导航：13 个工作面，按「办理 → 监督 → 材料文书 → 知识智能 → 管理」组织。 */
const NAV: NavGroup[] = [
  {
    key: 'home', label: '工作台', items: [
      { key: '/workbench', label: '我的工作台', icon: <DashboardOutlined /> },
    ],
  },
  {
    key: 'biz', label: '业务办理', items: [
      { key: '/petitions', label: '信访受理', icon: <MailOutlined /> },
      { key: '/clue', label: '线索研判', icon: <AimOutlined /> },
      { key: '/cases', label: '案件管理', icon: <SolutionOutlined /> },
      { key: '/trials', label: '审理台账', icon: <FileDoneOutlined /> },
      { key: '/ctas-materials', label: '送审材料', icon: <ApartmentOutlined /> },
      { key: '/conduct', label: '党风政风', icon: <FundOutlined /> },
      { key: '/inspections', label: '巡察整改', icon: <RadarChartOutlined /> },
    ],
  },
  {
    key: 'sup', label: '监督预警', items: [
      { key: '/supervise', label: '监督模型', icon: <ExperimentOutlined /> },
      { key: '/alerts', label: '预警中枢', icon: <AlertOutlined /> },
      { key: '/bank', label: '资金分析', icon: <BankOutlined /> },
      { key: '/integrity', label: '廉政档案', icon: <SafetyCertificateOutlined /> },
    ],
  },
  {
    key: 'mat', label: '材料与文书', items: [
      { key: '/ocr', label: '材料识别', icon: <FileImageOutlined /> },
      { key: '/transcripts', label: '谈话录音', icon: <AudioOutlined /> },
      { key: '/archives', label: '档案数字化', icon: <DatabaseOutlined /> },
      { key: '/documents', label: '文书库', icon: <FileTextOutlined /> },
      { key: '/verify', label: '文书校验', icon: <FileSearchOutlined /> },
    ],
  },
  {
    key: 'kb', label: '知识与智能', items: [
      { key: '/agents', label: '智能体中心', icon: <RobotOutlined />, badge: 'Nexent' },
      { key: '/qa', label: '法规问答', icon: <QuestionCircleOutlined /> },
      { key: '/regulations', label: '法规库', icon: <BookOutlined /> },
      { key: '/historical-reports', label: '历史报告库', icon: <HistoryOutlined /> },
    ],
  },
  {
    key: 'sys', label: '系统管理', items: [
      { key: '/departments', label: '部门与权限', icon: <ClusterOutlined /> },
      { key: '/data-intake', label: '数据接入', icon: <CloudUploadOutlined /> },
      { key: '/audit', label: '审计日志', icon: <AuditOutlined /> },
      { key: '/users', label: '用户管理', icon: <TeamOutlined /> },
      { key: '/nexent-settings', label: 'Nexent 接入', icon: <ApiOutlined /> },
      { key: '/recognition-settings', label: '识别服务', icon: <ScanOutlined /> },
    ],
  },
]

const COLLAPSE_KEY = 'ctas.nav.collapsed'

function NexentPill() {
  const nav = useNavigate()
  const { status, loading } = useNexentStatus()
  const state = loading && !status ? 'pending' : status?.authorized ? 'ok' : status?.configured ? 'err' : 'off'
  const text = state === 'ok' ? `Nexent 已连接 · ${status?.agent_count} 个智能体`
    : state === 'pending' ? 'Nexent 检测中' : state === 'err' ? 'Nexent 连接异常' : 'Nexent 未配置'
  return (
    <Tooltip title={status?.error || (state === 'ok' ? '点击进入智能体中心' : '')}>
      <button className={`cx-pill cx-pill-${state}`} onClick={() => nav('/agents')}>
        <span className="cx-pill-dot" />{text}
      </button>
    </Tooltip>
  )
}

export default function CtasLayout() {
  const { user, logout } = useAuth()
  const nav = useNavigate()
  const loc = useLocation()
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(COLLAPSE_KEY) === '1' } catch { return false }
  })
  useEffect(() => { try { localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0') } catch { /* ignore */ } }, [collapsed])

  const groups = useMemo(() => NAV.map((g) => ({
    ...g, items: g.items.filter((i) => canUseModule(user, i.key.slice(1))),
  })).filter((g) => g.items.length), [user])

  const current = useMemo(() => {
    for (const g of groups) for (const i of g.items) if (loc.pathname === i.key || loc.pathname.startsWith(i.key + '/')) return { g, i }
    return null
  }, [groups, loc.pathname])

  const menuItems = groups.map((g) => ({
    type: 'group' as const,
    key: g.key,
    label: collapsed ? null : g.label,
    children: g.items.map((i) => ({
      key: i.key,
      icon: i.icon,
      label: (
        <span className="cx-nav-label">
          {i.label}
          {i.badge && <span className="cx-nav-badge">{i.badge}</span>}
        </span>
      ),
    })),
  }))

  const userMenu = {
    items: [
      {
        key: 'me', disabled: true,
        label: (
          <div style={{ padding: '2px 0' }}>
            <div style={{ fontWeight: 600, color: '#0f172a' }}>{user?.real_name || user?.username}</div>
            <div style={{ fontSize: 12, color: '#64748b' }}>{ROLE_NAMES[user?.role || ''] || user?.role} · {user?.username}</div>
            {user?.dept_name && (
              <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                {user.dept_name} · 数据范围：{user.data_scope_label || '本部门'}
              </div>
            )}
          </div>
        ),
      },
      { type: 'divider' as const },
      { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: () => { logout(); nav('/login') } },
    ],
  }

  return (
    <div className={`cx-shell${collapsed ? ' collapsed' : ''}`}>
      <div className="demo-watermark cx-watermark">
        {Array.from({ length: 48 }).map((_, i) => <span key={i}>演示环境 · 模拟数据 · 非涉密</span>)}
      </div>

      <aside className="cx-side">
        <div className="cx-brand" onClick={() => nav('/workbench')}>
          <div className="cx-brand-logo"><SafetyCertificateOutlined /></div>
          {!collapsed && (
            <div className="cx-brand-text">
              <div className="cx-brand-title">纪检监察业务辅助平台</div>
              <div className="cx-brand-sub">信访 · 案管 · 审理 · 监督</div>
            </div>
          )}
        </div>
        <div className="cx-side-scroll">
          <Menu
            mode="inline" theme="dark" className="cx-nav" inlineCollapsed={collapsed}
            selectedKeys={current ? [current.i.key] : []}
            items={menuItems}
            onClick={(e) => nav(e.key)}
          />
        </div>
        <div className="cx-side-foot">
          <Button type="text" className="cx-collapse-btn" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}>{collapsed ? null : '收起导航'}</Button>
        </div>
      </aside>

      <div className="cx-main">
        <header className="cx-top">
          <div className="cx-crumb">
            {current ? (
              <>
                <span className="cx-crumb-group">{current.g.label}</span>
                <span className="cx-crumb-sep">/</span>
                <span className="cx-crumb-page">{current.i.label}</span>
              </>
            ) : <span className="cx-crumb-page">纪检监察业务辅助平台</span>}
          </div>
          <div className="cx-top-actions">
            <NexentPill />
            <Tooltip title="大屏展示模式"><Button type="text" icon={<DesktopOutlined />} onClick={() => window.open('/screen', '_blank')} /></Tooltip>
            <SystemIntroButton iconOnly />
            <Dropdown menu={userMenu} trigger={['click']} placement="bottomRight">
              <button className="cx-user">
                <span className="cx-user-avatar"><UserOutlined /></span>
                <span className="cx-user-name">{user?.real_name || user?.username}</span>
                <DownOutlined style={{ fontSize: 10, color: '#94a3b8' }} />
              </button>
            </Dropdown>
          </div>
        </header>
        <main className="cx-content">
          <div className="route-view cx-route" key={loc.pathname}>
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
