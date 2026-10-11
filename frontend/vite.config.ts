import fs from 'node:fs'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Serve over HTTPS when scripts/make_cert.py has been run.
//
// This is not security theatre for a local demo: getUserMedia only works in a
// secure context, and while `localhost` counts as one, a LAN address does
// not. Without TLS the microphone silently stops working the moment someone
// opens the dashboard on their phone. With the certificate present we serve
// HTTPS; without it we fall back to HTTP on localhost, which still works.
const certDir = path.resolve(__dirname, '../certs')
const key = path.join(certDir, 'dev.key')
const cert = path.join(certDir, 'dev.crt')
const secure = fs.existsSync(key) && fs.existsSync(cert)

// Vite is pinned to 6.x on purpose: 7 and 8 declare
// engines ^20.19.0 || >=22.12.0 and this machine runs Node 20.13.1.
export default defineConfig({
  plugins: [react()],
  server: {
    // Every interface, so the demo is reachable from a phone or a second
    // laptop on the same network.
    host: '0.0.0.0',
    port: 5173,
    https: secure ? { key: fs.readFileSync(key), cert: fs.readFileSync(cert) } : undefined,
    proxy: {
      // Same-origin in dev, so CORS never enters the picture. 127.0.0.1
      // rather than localhost avoids Node's IPv6-first resolution on Windows.
      // `secure: false` because the backend presents the same self-signed
      // certificate, which Node will not trust and does not need to here.
      '/api': {
        target: (secure ? 'https' : 'http') + '://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
      },
      // Fixture audio lives outside this package, so the backend serves it.
      '/fixtures': {
        target: (secure ? 'https' : 'http') + '://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
      },
    },
  },
})
