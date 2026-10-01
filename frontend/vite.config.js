import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// During `npm run dev` API calls proxy to the FastAPI server.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000'
    }
  }
})
