import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 只代理 API，根路径留给 dev 热更新
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
