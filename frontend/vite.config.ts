import dotenv from 'dotenv'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

dotenv.config({ path: '../server/.env' })

const backendPort = process.env.DRUIDS_PORT || '8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': `http://localhost:${backendPort}`,
    },
  },
  build: {
    outDir: 'dist',
  },
})
