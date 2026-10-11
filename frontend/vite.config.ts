import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite is pinned to 6.x on purpose: 7 and 8 declare
// engines ^20.19.0 || >=22.12.0 and this machine runs Node 20.13.1.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Same-origin in dev, so CORS never enters the picture. 127.0.0.1
      // rather than localhost avoids Node's IPv6-first resolution on Windows.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      // Fixture audio lives outside this package, so the backend serves it.
      '/fixtures': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
