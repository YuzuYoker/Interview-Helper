import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 只代理 API，根路径留给 dev 热更新
      // 固定 127.0.0.1 指向本地 uvicorn（Docker 容器同端口 0.0.0.0:8000 会优先于 localhost）
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
