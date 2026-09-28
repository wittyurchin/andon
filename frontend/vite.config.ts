import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev the Vite server proxies /api to the FastAPI backend, so the frontend
// never needs to know a backend URL. In production FastAPI serves this bundle
// itself from the same origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.ANDON_API_URL ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
