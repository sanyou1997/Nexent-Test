import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import './index.css'

/**
 * 统一配色（机构感、克制）：
 * - 主色只有一个「政务蓝」，用于主操作、选中、链接；
 * - 绿 / 琥珀 / 红只表达状态与风险（正常、临期 / 待办、超期 / 违规），不做装饰；
 * - 其余一律用石板灰阶。页面里不要再写裸色值，用这些 token 或 CSS 变量（index.css :root）。
 */
const PALETTE = {
  primary: '#1d4ed8',
  primaryHover: '#1e40af',
  success: '#15803d',
  warning: '#b45309',
  error: '#b91c1c',
  text: '#0f172a',
  text2: '#475569',
  text3: '#94a3b8',
  border: '#dde3ec',
  borderSoft: '#e9edf3',
  bg: '#f3f5f9',
  headerBg: '#f6f8fb',
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: PALETTE.primary,
          colorInfo: PALETTE.primary,
          colorLink: PALETTE.primary,
          colorLinkHover: PALETTE.primaryHover,
          colorSuccess: PALETTE.success,
          colorWarning: PALETTE.warning,
          colorError: PALETTE.error,
          colorTextBase: PALETTE.text,
          colorText: PALETTE.text,
          colorTextSecondary: PALETTE.text2,
          colorTextTertiary: PALETTE.text3,
          colorTextQuaternary: '#b6c0cf',
          colorBorder: PALETTE.border,
          colorBorderSecondary: PALETTE.borderSoft,
          colorBgLayout: PALETTE.bg,
          colorFillAlter: PALETTE.headerBg,
          borderRadius: 8,
          borderRadiusSM: 6,
          controlHeight: 34,
          fontSize: 14,
          fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Hiragino Sans GB', system-ui, sans-serif",
        },
        components: {
          Button: { primaryShadow: '0 1px 2px rgba(29, 78, 216, 0.18)', defaultShadow: 'none', dangerShadow: 'none', fontWeight: 500 },
          Table: { headerBg: PALETTE.headerBg, headerColor: '#334155', headerSplitColor: 'transparent', rowHoverBg: '#f5f8ff', borderColor: PALETTE.borderSoft, cellPaddingBlock: 11 },
          Card: { headerFontSize: 15, colorBorderSecondary: PALETTE.borderSoft },
          Tabs: { itemSelectedColor: PALETTE.primary, inkBarColor: PALETTE.primary, itemHoverColor: PALETTE.primaryHover },
          Segmented: { itemSelectedColor: PALETTE.primary, trackBg: '#eef1f6' },
          Alert: { colorInfoBg: '#f2f6ff', colorInfoBorder: '#d6e2fb' },
          Statistic: { contentFontSize: 26 },
          Menu: { darkItemBg: 'transparent', darkSubMenuItemBg: 'transparent' },
        },
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
)
