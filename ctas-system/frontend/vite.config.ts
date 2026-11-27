import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建期标题：VITE_APP_MODE=ctas 时替换为「审理辅助系统 CTAS」，
// 默认全业务标题。index.html 中的 %VITE_APP_TITLE% 由 Vite 在 build 时替换。
export default defineConfig(() => {
  if (!process.env.VITE_APP_TITLE) {
    process.env.VITE_APP_TITLE = '纪检监察业务辅助平台（演示环境·模拟数据）'
  }
  return {
    plugins: [react()],
    server: {
      host: true,
      port: 5173,
      proxy: {
        '/api': { target: 'http://127.0.0.1:8090', changeOrigin: true },
      },
    },
  }
})
