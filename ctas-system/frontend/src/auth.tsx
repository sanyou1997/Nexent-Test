import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api } from './api/client'

export interface CurrentUser {
  id: number
  username: string
  real_name: string
  role: string
  dept_id?: number | null
  dept_name?: string
  /** 部门开通的功能模块；null = 全部（平台管理员） */
  modules?: string[] | null
  data_scope?: string
  data_scope_label?: string
}

export const ROLE_NAMES: Record<string, string> = {
  admin: '平台管理员', xinfang: '信访岗', banan: '办案岗', shenli: '审理岗',
}

/** 系统管理类页面：仅平台管理员 */
export const SYSTEM_MODULES = ['data-intake', 'audit', 'departments', 'users', 'nexent-settings', 'recognition-settings']

/** 当前账号能否使用某功能模块（模块键 = 路由首段，如 petitions / trials） */
export function canUseModule(user: CurrentUser | null, key: string): boolean {
  if (!user) return false
  if (user.role === 'admin') return true
  if (SYSTEM_MODULES.includes(key)) return false
  if (key === 'workbench') return true
  // 旧登录态（升级前缓存的 user 没有 modules 字段）：放行，待 /auth/me 刷新后按部门配置
  if (user.modules === undefined) return true
  return !!user.modules && user.modules.includes(key)
}

interface AuthCtx {
  user: CurrentUser | null
  login: (u: CurrentUser, token: string) => void
  logout: () => void
  refresh: () => Promise<void>
  can: (key: string) => boolean
}

const Ctx = createContext<AuthCtx>({
  user: null, login: () => {}, logout: () => {}, refresh: async () => {}, can: () => false,
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(() => {
    const raw = localStorage.getItem('user')
    try { return raw ? JSON.parse(raw) : null } catch { return null }
  })

  const save = (u: CurrentUser | null) => {
    if (u) localStorage.setItem('user', JSON.stringify(u))
    else localStorage.removeItem('user')
    setUser(u)
  }

  /** 部门的模块 / 数据范围可能被管理员调整：进入系统时以服务端为准刷新一次 */
  const refresh = useCallback(async () => {
    if (!localStorage.getItem('token')) return
    try {
      const { data } = await api.get('/auth/me')
      save(data)
    } catch { /* 401 由拦截器处理 */ }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  return (
    <Ctx.Provider
      value={{
        user,
        login: (u, token) => {
          localStorage.setItem('token', token)
          save(u)
        },
        logout: () => {
          localStorage.removeItem('token')
          save(null)
        },
        refresh,
        can: (key: string) => canUseModule(user, key),
      }}
    >
      {children}
    </Ctx.Provider>
  )
}

export const useAuth = () => useContext(Ctx)
