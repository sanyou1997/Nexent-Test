import { useState } from 'react'
import { useNavigate, Navigate } from 'react-router-dom'
import { Form, Input, Button, message } from 'antd'
import { LockOutlined, UserOutlined, SafetyCertificateOutlined, CloudServerOutlined, DatabaseOutlined } from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { useAuth } from '../auth'
import { IS_CTAS, APP_TITLE, APP_SUBTITLE } from '../appMode'

const allAccounts = [
  ['admin', 'Admin@123', '管理员'],
  ['xinfang', 'Xf@123456', '信访室'],
  ['banan', 'Ba@123456', '办案人员'],
  ['shenli', 'Sl@123456', '审理室'],
]
// CTAS 审理辅助系统只列审理域相关账号
const demoAccounts = IS_CTAS ? allAccounts.filter(([u]) => ['admin', 'shenli'].includes(u)) : allAccounts

// 背景粒子（确定性分布，避免每次渲染抖动）
const PARTICLES = [
  { left: 8, top: 18, s: 4, d: 0 }, { left: 22, top: 70, s: 6, d: 2.1 },
  { left: 35, top: 32, s: 3, d: 1.2 }, { left: 47, top: 82, s: 5, d: 3.4 },
  { left: 58, top: 24, s: 4, d: 0.8 }, { left: 68, top: 64, s: 7, d: 2.7 },
  { left: 78, top: 40, s: 3, d: 1.9 }, { left: 88, top: 76, s: 5, d: 0.4 },
  { left: 14, top: 90, s: 4, d: 3.0 }, { left: 30, top: 12, s: 3, d: 1.5 },
  { left: 63, top: 90, s: 4, d: 2.3 }, { left: 82, top: 16, s: 6, d: 1.0 },
]

export default function Login() {
  const { login, user } = useAuth()
  const nav = useNavigate()
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()

  if (user) return <Navigate to="/" replace />

  const onFinish = async (v: { username: string; password: string }) => {
    setLoading(true)
    try {
      const { data } = await api.post('/auth/login', v)
      login(data.user, data.access_token)
      message.success(`欢迎，${data.user.real_name}`)
      nav('/', { replace: true })
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-screen">
      {/* 背景科技层 */}
      <div className="login-bg-grid" />
      <div className="login-bg-glow" />
      <div className="login-bg-particles">
        {PARTICLES.map((p, i) => (
          <span key={i} style={{
            left: `${p.left}%`, top: `${p.top}%`,
            width: p.s, height: p.s, animationDelay: `${p.d}s`,
          }} />
        ))}
      </div>

      <div className="login-inner">
        {/* ===== 左侧品牌区 ===== */}
        <section className="login-brand">
          <div className="login-scanline" />
          <div className="login-brand-top">
            <div className="login-emblem">
              <svg viewBox="0 0 120 140" width="92" height="108" aria-hidden>
                <defs>
                  <linearGradient id="emG" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor="#7fb2ff" />
                    <stop offset="55%" stopColor="#1d4ed8" />
                    <stop offset="100%" stopColor="#0b1f4d" />
                  </linearGradient>
                  <linearGradient id="emRing" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor="#c8a45c" />
                    <stop offset="100%" stopColor="#7a6a3a" />
                  </linearGradient>
                </defs>
                <path className="em-shield" d="M60 6 L110 26 V66 C110 102 88 122 60 134 C32 122 10 102 10 66 V26 Z"
                  fill="url(#emG)" stroke="url(#emRing)" strokeWidth="2.5" />
                <path d="M60 6 L110 26 V66 C110 102 88 122 60 134 C32 122 10 102 10 66 V26 Z"
                  fill="none" stroke="#7fb2ff" strokeWidth="1" opacity="0.5" transform="scale(1.12)" transform-origin="60 70" />
                <circle cx="60" cy="60" r="22" fill="none" stroke="#c8a45c" strokeWidth="1.5" opacity="0.8" />
                <path d="M48 60 L56 70 L74 50" fill="none" stroke="#fff" strokeWidth="4"
                  strokeLinecap="round" strokeLinejoin="round" className="em-check" />
              </svg>
            </div>
            <div className="login-brand-text">
              <div className="login-org">纪律检查委员会 · 监察委员会</div>
              <h1 className="login-title">{APP_TITLE}</h1>
              <div className="login-subtitle">JIDI · Discipline Inspection Business Platform</div>
            </div>
          </div>

          <div className="login-divider">
            <span /><i>智慧纪检 · 数据赋能</i><span />
          </div>

          <p className="login-motto">
            {IS_CTAS ? (
              <>
                以信息化手段汇聚送审材料、辅助阅卷取证、把准量纪档次，<br />
                减轻审理环节重复性事务负担，提升案件审理规范化、智能化水平。
              </>
            ) : (
              <>
                以信息化手段赋能监督检查与审查调查，<br />
                减轻重复性事务负担，提升执纪执法规范化、智能化水平。
              </>
            )}
          </p>

          <ul className="login-corevals">
            <li>忠诚</li>
            <li>干净</li>
            <li>担当</li>
          </ul>

          {/* 数据中枢示意 */}
          <div className="login-dataflow">
            <svg viewBox="0 0 320 70" width="100%" height="70" aria-hidden>
              <path className="df-line" d="M20 35 H300" stroke="#3b6fe0" strokeWidth="1.5" fill="none"
                strokeDasharray="6 8" />
              {[40, 105, 170, 235, 285].map((x, i) => (
                <g key={i}>
                  <circle cx={x} cy="35" r="5" fill="#1d4ed8" className="df-node" style={{ animationDelay: `${i * 0.5}s` }} />
                  <circle cx={x} cy="35" r="10" fill="none" stroke="#38bdf8" strokeWidth="1" opacity="0.5"
                    className="df-pulse" style={{ animationDelay: `${i * 0.5}s` }} />
                </g>
              ))}
            </svg>
          </div>

          <div className="login-badges">
            <span><CloudServerOutlined /> 本地离线部署</span>
            <span><DatabaseOutlined /> 数据不出域</span>
            <span><SafetyCertificateOutlined /> 审计全程留痕</span>
          </div>
        </section>

        {/* ===== 右侧登录区 ===== */}
        <section className="login-form-pane">
          <div className="login-glass">
            <div className="login-glass-head">
              <span className="login-env-tag">演示环境</span>
              <h2>欢迎登录</h2>
              <p>请使用工作账号登录系统</p>
            </div>

            <Form form={form} onFinish={onFinish} initialValues={{ username: 'admin', password: 'Admin@123' }} size="large" layout="vertical">
              <Form.Item
                name="username"
                label={<span style={{ color: '#cfe0ff', fontSize: 12.5, fontWeight: 600 }}>用户名 / 工号</span>}
                rules={[{ required: true, message: '请输入用户名' }]}
              >
                <Input prefix={<UserOutlined style={{ color: '#9db4e8' }} />} placeholder="请输入用户名或工号" />
              </Form.Item>
              <Form.Item
                name="password"
                label={<span style={{ color: '#cfe0ff', fontSize: 12.5, fontWeight: 600 }}>密码</span>}
                rules={[{ required: true, message: '请输入密码' }]}
              >
                <Input.Password prefix={<LockOutlined style={{ color: '#9db4e8' }} />} placeholder="请输入密码" />
              </Form.Item>
              <Button type="primary" htmlType="submit" block loading={loading} className="login-submit">
                登 录 系 统
              </Button>
            </Form>

            <div className="login-demo">
              <div className="login-demo-label">演示账号（点击自动填充）</div>
              <div className="login-demo-accounts">
                {demoAccounts.map(([u, p, r]) => (
                  <button key={u} type="button" className="login-demo-chip"
                    onClick={() => form.setFieldsValue({ username: u, password: p })}>
                    {r}
                  </button>
                ))}
              </div>
            </div>

            <div className="login-declare">
              <div className="login-declare-title">系统性质声明</div>
              <div className="login-declare-body">
                本系统为纪检监察工作<b>辅助应用系统</b>，非业务办理系统，<b>不具备业务流转效力</b>；
                用于减轻信访分类、材料 OCR、录音转译、数据清洗、审理报告与法规比对、公文格式校对等重复性工作。
                所有输出<b>须经工作人员核实并按程序审批</b>后使用。
              </div>
            </div>
          </div>
          <div className="login-copyright">© {APP_SUBTITLE.split(' · ')[0]} · 模拟数据 · 非涉密</div>
        </section>
      </div>
    </div>
  )
}
