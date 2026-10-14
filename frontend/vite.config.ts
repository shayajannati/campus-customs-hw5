import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type Plugin } from 'vite'

/** Production builds get a Content-Security-Policy that only lets the page talk to the API origin. */
function csp(apiOrigin: string): Plugin {
  return {
    name: 'campus-customs-csp',
    apply: 'build',
    transformIndexHtml(html) {
      const policy = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        `connect-src ${apiOrigin}`,
        "base-uri 'none'",
        "form-action 'none'",
        "object-src 'none'",
      ].join('; ')
      return html.replace('<!--csp-->', `<meta http-equiv="Content-Security-Policy" content="${policy}" />`)
    },
  }
}

// `npm run dev` serves on http://localhost:5173, the one origin the backend's CORS allows.
// VITE_API_ORIGIN (default http://localhost:8000) is only for isolated tests on another backend port.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const apiOrigin = env.VITE_API_ORIGIN ?? 'http://localhost:8000'
  return {
    plugins: [react(), csp(apiOrigin)],
    server: { host: 'localhost', port: 5173, strictPort: true },
    test: { environment: 'jsdom', include: ['src/**/*.test.ts', 'src/**/*.test.tsx'] },
  }
})
