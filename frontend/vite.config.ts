import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The browser only ever talks to /api on its own origin; the dev server forwards it.
const backend = process.env.VITE_BACKEND_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: { '/api': { target: backend, changeOrigin: true } },
  },
})
