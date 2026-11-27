import { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  subtitle?: string
  icon?: ReactNode
  extra?: ReactNode
}

/** 统一的页面顶部横幅：标题 + 说明 + 图标 + 右侧操作区 */
export default function PageHeader({ title, subtitle, icon, extra }: PageHeaderProps) {
  return (
    <div className="ji-page-header">
      {icon && <div className="ji-page-header-icon">{icon}</div>}
      <div className="ji-page-header-text">
        <div className="ji-page-header-title">{title}</div>
        {subtitle && <div className="ji-page-header-sub">{subtitle}</div>}
      </div>
      {extra && <div className="ji-page-header-extra">{extra}</div>}
    </div>
  )
}
