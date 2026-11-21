import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Local dev mirrors the production topology: the dashboard calls the
// same-origin /api/* path and a proxy forwards it to the backend (the
// bundled nginx does the same in Docker; see nginx.conf.template and
// docs/AUTH.md). Set WAMSS_DEV_EMPLOYEE_TOKEN in the environment to have
// the dev proxy attach an X-Employee-Token header for employee-scoped
// reads, and WAMSS_DEV_API_KEY to attach the deployment key. Both are
// optional and read from the process environment only; nothing is
// hardcoded here.
const backendTarget = process.env.WAMSS_DEV_BACKEND_URL || 'http://localhost:8000'
const devHeaders: Record<string, string> = {}
if (process.env.WAMSS_DEV_EMPLOYEE_TOKEN) {
  devHeaders['X-Employee-Token'] = process.env.WAMSS_DEV_EMPLOYEE_TOKEN
}
if (process.env.WAMSS_DEV_API_KEY) {
  devHeaders['X-API-Key'] = process.env.WAMSS_DEV_API_KEY
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        headers: devHeaders,
      },
      '/video': {
        target: 'http://localhost:8002',
        changeOrigin: true,
      },
      '/snapshot.jpg': {
        target: 'http://localhost:8002',
        changeOrigin: true,
      },
    },
  },
})
