/**
 * Vitest configuration for the pure state/mock logic.
 *
 * Kept separate from `vite.config.ts` (owned by the design build) so the test
 * runner stays self-contained. Tests target the framework-free reducer and the
 * scripted mock transport, so a plain Node environment is all we need; the `@`
 * alias mirrors the app's so imports read identically.
 */

import path from 'node:path'

import { defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
