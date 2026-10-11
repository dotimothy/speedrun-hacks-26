import fs from 'node:fs'
import http from 'node:http'
import https from 'node:https'
import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Serve over HTTPS when scripts/make_cert.py has been run.
//
// This is not security theatre for a local demo: getUserMedia only works in a
// secure context, and while `localhost` counts as one, a LAN address does
// not. Without TLS the microphone silently stops working the moment someone
// opens the dashboard on their phone. With the certificate present we serve
// HTTPS; without it we fall back to HTTP, which still works on localhost.
const certDir = path.resolve(__dirname, '../certs')
const key = path.join(certDir, 'dev.key')
const cert = path.join(certDir, 'dev.crt')
const secure = fs.existsSync(key) && fs.existsSync(cert)

const BACKEND_PORT = 8000

/**
 * Ask the backend which protocol it speaks, rather than assuming.
 *
 * An earlier version chose the proxy scheme the same way this server chooses
 * its own: by whether certs/ exists on disk. That is wrong, and it broke in
 * practice. The two are separate processes and the backend's TLS is decided
 * by whether uvicorn was given --ssl-certfile, not by what is on disk. Start
 * it with the plain command from the README while certs/ happens to exist --
 * which is exactly what the docs tell you to do -- and this server proxies
 * HTTPS at an HTTP backend. Every /api call fails, and the dashboard looks
 * broken for a reason nothing on screen explains.
 *
 * DISPATCHIQ_API_TARGET still wins when set, for pointing at a backend that
 * is not on this machine at all.
 */
function probe(scheme: 'http' | 'https'): Promise<boolean> {
  return new Promise((resolve) => {
    const client = scheme === 'https' ? https : http
    const request = client.request(
      {
        host: '127.0.0.1',
        port: BACKEND_PORT,
        path: '/api/health',
        method: 'HEAD',
        timeout: 1500,
        // The backend presents the same self-signed certificate this server
        // does; Node will not trust it and does not need to here.
        rejectUnauthorized: false,
      },
      (response) => {
        response.resume()
        resolve(true)
      },
    )
    request.on('error', () => resolve(false))
    request.on('timeout', () => {
      request.destroy()
      resolve(false)
    })
    request.end()
  })
}

async function backendTarget(configured: string | undefined): Promise<string> {
  if (configured) return configured

  const order: Array<'http' | 'https'> = secure ? ['https', 'http'] : ['http', 'https']
  for (const scheme of order) {
    if (await probe(scheme)) {
      if (scheme !== order[0]) {
        console.log(
          `[dispatchiq] backend speaks ${scheme}, not ${order[0]} — proxying ${scheme}. ` +
            `(Start it with scripts/serve.py to keep both sides on the same protocol.)`,
        )
      }
      return `${scheme}://127.0.0.1:${BACKEND_PORT}`
    }
  }
  console.warn(
    `[dispatchiq] no backend answering on port ${BACKEND_PORT}. Assuming ` +
      `${order[0]}; start it with: python scripts/serve.py`,
  )
  return `${order[0]}://127.0.0.1:${BACKEND_PORT}`
}

// Vite is pinned to 6.x on purpose: 7 and 8 declare
// engines ^20.19.0 || >=22.12.0 and this machine runs Node 20.13.1.
export default defineConfig(async ({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'DISPATCHIQ_')
  const target = await backendTarget(env.DISPATCHIQ_API_TARGET)
  // `secure: false` because the backend presents a self-signed certificate.
  const proxy = { target, changeOrigin: true, secure: false }

  return {
    plugins: [react()],
    server: {
      // Every interface, so the demo is reachable from a phone or a second
      // laptop on the same network.
      host: '0.0.0.0',
      port: 5173,
      https: secure ? { key: fs.readFileSync(key), cert: fs.readFileSync(cert) } : undefined,
      proxy: {
        // Same-origin in dev, so CORS never enters the picture.
        '/api': proxy,
        '/fixtures': proxy,
      },
    },
  }
})
