import { useCallback, useEffect, useState } from 'react'
import { Button, Empty, Skeleton, Tag } from 'antd'
import {
  ReloadOutlined, RobotOutlined, RightOutlined, MessageOutlined, PlusOutlined, MailOutlined, SolutionOutlined,
  FileDoneOutlined, AlertOutlined, RadarChartOutlined, RedoOutlined, FileTextOutlined, BookOutlined,
  FileImageOutlined, AudioOutlined, HistoryOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth, ROLE_NAMES } from '../auth'
import { NexentConversation, listConversations, useNexentAgents, useNexentStatus } from '../api/nexent'

interface Item { id: number | string; title: string; sub: string; tag: string; link: string; due_at: string; overdue: boolean; soon: boolean }
interface Section { key: string; title: string; route: string; total: number; overdue: number; soon: number; items: Item[]; error?: string }
interface WB { role: string; sections: Section[]; totals: { pending: number; overdue: number; soon: number } }

const ICON: Record<string, React.ReactNode> = {
  petitions: <MailOutlined />, cases: <SolutionOutlined />, trials: <FileDoneOutlined />,
  alerts: <AlertOutlined />, rectify: <RadarChartOutlined />, appeals: <RedoOutlined />,
}
const QUICK: Record<string, { t: string; to: string; i: React.ReactNode }[]> = {
  xinfang: [{ t: '信访受理', to: '/petitions', i: <MailOutlined /> }, { t: '材料识别', to: '/ocr', i: <FileImageOutlined /> }],
  banan: [{ t: '案件管理', to: '/cases', i: <SolutionOutlined /> }, { t: '预警中枢', to: '/alerts', i: <AlertOutlined /> }, { t: '谈话录音', to: '/transcripts', i: <AudioOutlined /> }],
  shenli: [{ t: '审理台账', to: '/trials', i: <FileDoneOutlined /> }, { t: '送审材料', to: '/ctas-materials', i: <FileImageOutlined /> }, { t: '历史报告库', to: '/historical-reports', i: <HistoryOutlined /> }],
  admin: [{ t: '信访受理', to: '/petitions', i: <MailOutlined /> }, { t: '案件管理', to: '/cases', i: <SolutionOutlined /> }, { t: '审理台账', to: '/trials', i: <FileDoneOutlined /> }, { t: '预警中枢', to: '/alerts', i: <AlertOutlined /> }],
}
const COMMON = [{ t: '文书库', to: '/documents', i: <FileTextOutlined /> }, { t: '法规库', to: '/regulations', i: <BookOutlined /> }]

function greet() {
  const h = new Date().getHours()
  return h < 6 ? '夜深了' : h < 11 ? '上午好' : h < 13 ? '中午好' : h < 18 ? '下午好' : '晚上好'
}

/** 我的工作台：按角色聚合待办与时限（/api/workbench）+ Nexent 智能体入口。 */
export default function Workbench() {
  const nav = useNavigate()
  const { user, can } = useAuth()
  const [wb, setWb] = useState<WB | null>(null)
  const [loading, setLoading] = useState(true)
  const [convs, setConvs] = useState<NexentConversation[]>([])
  const { status, ready } = useNexentStatus()
  const { agents, defaultAgent } = useNexentAgents(ready)

  const load = useCallback(() => {
    setLoading(true)
    api.get('/workbench').then(({ data }) => setWb(data)).catch(() => setWb(null)).finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => { if (ready) listConversations().then((c) => setConvs(c.slice(0, 4))).catch(() => setConvs([])) }, [ready])

  const role = user?.role || 'admin'
  const t = wb?.totals

  return (
    <div className="cx-page">
      <div className="cx-hello">
        <div>
          <div className="cx-hello-title">{greet()}，{user?.real_name || user?.username}</div>
          <div className="cx-hello-sub">
            {user?.dept_name ? `${user.dept_name} · ` : ''}{ROLE_NAMES[role] || role} · {new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' })}
            {t && <span style={{ marginLeft: 12 }}>
              待办 <b>{t.pending}</b> 项
              {t.overdue ? <span style={{ color: '#b91c1c', marginLeft: 8 }}>已超期 {t.overdue} 项</span> : null}
              {t.soon ? <span style={{ color: '#c2410c', marginLeft: 8 }}>7 日内到期 {t.soon} 项</span> : null}
            </span>}
          </div>
        </div>
        <div className="cx-page-actions">
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
          {can('agents') && <Button icon={<RobotOutlined />} onClick={() => nav('/agents')}>智能体中心</Button>}
          {(role === 'shenli' || role === 'admin') && can('trials') && <Button type="primary" icon={<PlusOutlined />} onClick={() => nav('/trials?create=1')}>新建审理</Button>}
          {role === 'xinfang' && can('petitions') && <Button type="primary" icon={<PlusOutlined />} onClick={() => nav('/petitions')}>信访登记</Button>}
        </div>
      </div>

      <div className="cx-kpis" style={{ gridTemplateColumns: `repeat(${Math.min(Math.max(wb?.sections.length || 3, 2), 6)}, minmax(0, 1fr))` }}>
        {(wb?.sections || []).map((s) => (
          <div key={s.key} className={`cx-card cx-kpi ${s.overdue ? 'danger' : s.soon ? 'warn' : ''}`} onClick={() => nav(s.route)}>
            <div className="cx-kpi-label">{ICON[s.key]}{s.title}</div>
            <div className="cx-kpi-value">{s.total}<span className="cx-kpi-unit">项</span></div>
            <div className="cx-kpi-foot">{s.overdue ? `超期 ${s.overdue}` : '无超期'}{s.soon ? ` · 临期 ${s.soon}` : ''}</div>
          </div>
        ))}
        {loading && !wb && [1, 2, 3].map((i) => <div key={i} className="cx-card cx-kpi"><Skeleton active paragraph={false} /></div>)}
      </div>

      <div className="cx-dash-grid">
        <div className="cx-dash-col">
          {(wb?.sections || []).map((s) => (
            <div key={s.key} className="cx-card">
              <div className="cx-card-head">
                <span>{ICON[s.key]} {s.title}<Tag className="cx-tag-soft" style={{ marginLeft: 8 }}>{s.total}</Tag></span>
                <button className="cx-link" onClick={() => nav(s.route)}>查看全部 <RightOutlined /></button>
              </div>
              {s.error ? <div className="cx-muted" style={{ padding: '0 14px 14px' }}>加载失败：{s.error}</div>
                : !s.items.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待办" style={{ padding: '6px 0 14px' }} />
                  : s.items.map((it) => (
                    <div key={it.id} className="cx-todo-item" onClick={() => nav(it.link)}>
                      <div className="cx-todo-main">
                        <div className="cx-todo-title">{it.title}</div>
                        <div className="cx-todo-sub">{it.sub && <span>{it.sub}</span>}{it.due_at && <span>时限 {it.due_at}</span>}</div>
                      </div>
                      {it.tag && <Tag bordered={false}>{it.tag}</Tag>}
                      {it.overdue ? <Tag color="red" bordered={false}>已超期</Tag> : it.soon ? <Tag color="orange" bordered={false}>临期</Tag> : null}
                      <RightOutlined style={{ color: '#cbd5e1' }} />
                    </div>
                  ))}
            </div>
          ))}
        </div>

        <div className="cx-dash-col">
          <div className="cx-card">
            <div className="cx-ai-card-head">
              <div className="t"><RobotOutlined /> Nexent 智能体</div>
              <div className="s">{ready ? `已连接 · ${status?.agent_count} 个已发布智能体可用` : (status?.error || '正在检测连接…')}</div>
            </div>
            {ready ? (
              <>
                {agents.slice(0, 4).map((a) => (
                  <div key={a.name} className="cx-mini-agent" onClick={() => nav(`/agents?agent=${encodeURIComponent(a.name)}`)}>
                    <div className="cx-agent-avatar sm">{a.display_name.slice(0, 1)}</div>
                    <div className="cx-agent-meta">
                      <div className="cx-agent-name">{a.display_name}{a.name === defaultAgent && <Tag color="gold" bordered={false} style={{ marginLeft: 6 }}>默认</Tag>}</div>
                      <div className="cx-agent-desc">{a.description || '—'}</div>
                    </div>
                    <RightOutlined style={{ color: '#cbd5e1' }} />
                  </div>
                ))}
                {convs.length > 0 && (
                  <>
                    <div className="cx-card-head" style={{ borderTop: '1px solid var(--cx-border)', fontSize: 13 }}><span>最近会话</span></div>
                    {convs.map((c) => (
                      <div key={c.id} className="cx-conv-item" style={{ margin: '0 8px' }}
                        onClick={() => nav(`/agents?agent=${encodeURIComponent(c.agent_name)}&c=${c.conversation_id}`)}>
                        <MessageOutlined className="cx-conv-icon" />
                        <div className="cx-conv-meta">
                          <div className="cx-conv-title">{c.title}</div>
                          <div className="cx-conv-sub"><span>{c.agent_display_name}</span><span>{c.updated_at}</span></div>
                        </div>
                      </div>
                    ))}
                  </>
                )}
                <div style={{ padding: '10px 14px 14px' }}>
                  <Button block icon={<RobotOutlined />} onClick={() => nav('/agents')}>进入智能体中心</Button>
                </div>
              </>
            ) : (
              <div style={{ padding: 16, color: '#64748b', fontSize: 13 }}>
                {role === 'admin' ? <Button type="primary" block onClick={() => nav('/nexent-settings')}>配置 Nexent 接入</Button> : '请联系管理员配置 Nexent 接入。'}
              </div>
            )}
          </div>

          <div className="cx-card">
            <div className="cx-card-head"><span>常用入口</span></div>
            <div style={{ padding: '0 8px 10px' }}>
              {[...(QUICK[role] || QUICK.admin), ...COMMON].map((x) => (
                <div key={x.t} className="cx-mini-agent" onClick={() => nav(x.to)} style={{ padding: '7px 8px', borderRadius: 8 }}>
                  <span style={{ color: 'var(--cx-primary)', fontSize: 15 }}>{x.i}</span>
                  <span style={{ flex: 1, fontSize: 13 }}>{x.t}</span>
                  <RightOutlined style={{ color: '#cbd5e1', fontSize: 11 }} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
