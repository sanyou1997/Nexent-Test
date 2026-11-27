import { Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { Button, Result } from 'antd'
import { AuthProvider, useAuth } from './auth'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import QA from './pages/QA'
import Petitions from './pages/Petitions'
import Cases from './pages/Cases'
import Trials from './pages/Trials'
import Conduct from './pages/Conduct'
import Inspections from './pages/Inspections'
import Documents from './pages/Documents'
import Regulations from './pages/Regulations'
import Audit from './pages/Audit'
import Users from './pages/Users'
import Transcripts from './pages/Transcripts'
import Bank from './pages/Bank'
import Verify from './pages/Verify'
import Ocr from './pages/Ocr'
import Supervise from './pages/Supervise'
import Alerts from './pages/Alerts'
import Clue from './pages/Clue'
import Integrity from './pages/Integrity'
import Archives from './pages/Archives'
import DataIntake from './pages/DataIntake'
import CtasMaterials from './pages/CtasMaterials'
import HistoricalReports from './pages/HistoricalReports'
import AgentCenter from './pages/AgentCenter'
import NexentSettings from './pages/NexentSettings'
import RecognitionSettings from './pages/RecognitionSettings'
import Workbench from './pages/Workbench'
import Departments from './pages/Departments'
import CtasLayout from './components/CtasLayout'

function Guard({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  return user ? <>{children}</> : <Navigate to="/login" replace />
}

/** 页面级模块把关：部门未开通的模块（含直接输入地址）显示无权访问；接口侧另有 403 兜底。 */
function RequireModule({ mod, children }: { mod: string; children: React.ReactNode }) {
  const { user, can } = useAuth()
  const nav = useNavigate()
  if (can(mod)) return <>{children}</>
  return (
    <Result
      status="403"
      title="无权访问"
      subTitle={`${user?.dept_name || '本部门'}未开通该功能模块。如需使用，请联系平台管理员在「部门与权限」中开通。`}
      extra={<Button type="primary" onClick={() => nav('/workbench')}>返回工作台</Button>}
    />
  )
}

/** 单应用路由：全业务模块 + 审理工作台；导航按角色显示（见 CtasLayout）。 */
const ROUTES: [string, React.ReactNode][] = [
  ['workbench', <Workbench />],
  // 业务办理
  ['petitions', <Petitions />],
  ['clue', <Clue />],
  ['cases', <Cases />],
  ['trials', <Trials />],
  ['ctas-materials', <CtasMaterials />],
  ['conduct', <Conduct />],
  ['inspections', <Inspections />],
  // 监督预警
  ['supervise', <Supervise />],
  ['alerts', <Alerts />],
  ['bank', <Bank />],
  ['integrity', <Integrity />],
  // 材料与文书
  ['ocr', <Ocr />],
  ['transcripts', <Transcripts />],
  ['archives', <Archives />],
  ['documents', <Documents />],
  ['verify', <Verify />],
  // 知识与智能
  ['agents', <AgentCenter />],
  ['qa', <QA />],
  ['regulations', <Regulations />],
  ['historical-reports', <HistoricalReports />],
  // 系统管理
  ['departments', <Departments />],
  ['data-intake', <DataIntake />],
  ['audit', <Audit />],
  ['users', <Users />],
  ['nexent-settings', <NexentSettings />],
  ['recognition-settings', <RecognitionSettings />],
]

/** 已并入其它工作面的旧地址 */
const REDIRECTS: [string, string][] = [
  ['dashboard', '/workbench'],
  ['ctas-dashboard', '/workbench'],
  ['assistant', '/agents'],     // 办公通用助手 → Nexent 智能体中心
  ['education', '/agents'],     // 廉洁教育智能体 → Nexent 智能体中心
]

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        {/* 大屏展示模式：原深色驾驶舱，独立全屏，不进日常导航 */}
        <Route path="/screen" element={<Guard><div className="app-content" style={{ height: '100vh', overflow: 'auto' }}><Dashboard /></div></Guard>} />
        <Route path="/" element={<Guard><CtasLayout /></Guard>}>
          <Route index element={<Navigate to="/workbench" replace />} />
          {ROUTES.map(([path, el]) => <Route key={path} path={path} element={<RequireModule mod={path}>{el}</RequireModule>} />)}
          {REDIRECTS.map(([from, to]) => <Route key={from} path={from} element={<Navigate to={to} replace />} />)}
          <Route path="*" element={<Navigate to="/workbench" replace />} />
        </Route>
      </Routes>
    </AuthProvider>
  )
}
