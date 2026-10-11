import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Keep the API URL on the development server; requests stay same-origin.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'DISPATCHIQ_')
  const target = env.DISPATCHIQ_API_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': { target, changeOrigin: true },
        '/fixtures': { target, changeOrigin: true },
      },
    },
  }
})
