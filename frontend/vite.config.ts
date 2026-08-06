import path from 'node:path'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Split the heavy third-party libraries into their own long-lived,
        // cache-friendly vendor chunks so the app entry stays well under the
        // 500 kB warning limit. `recharts` (dashboard + admin-usage charts,
        // reached only from route-split views) is the big one; the React runtime
        // gets its own stable chunk. Matching by resolved module path (not bare
        // specifier) also catches submodule imports like `react-dom/client`.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/recharts/')) return 'recharts'
          if (
            id.includes('/react-dom/') ||
            id.includes('/react/') ||
            id.includes('/scheduler/')
          ) {
            return 'react'
          }
          return undefined
        },
      },
    },
  },
  server: {
    port: 5173,
  },
})
