import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Frontend on 5174; API proxied to the read-only adapter on 4317.
// (Old second-brain app used 3001/5173 — no clash.)
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    strictPort: false,
    proxy: {
      '/api': 'http://localhost:4317',
    },
  },
})
