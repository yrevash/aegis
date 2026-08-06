import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Self-hosted fonts (no CDN — portable for the offline demo machine).
// Inter carries the UI + numbers; Space Grotesk is the display face for hero
// numbers and panel titles (§2.1); JetBrains Mono is kept for IDs and raw data.
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/space-grotesk/500.css'
import '@fontsource/space-grotesk/600.css'
import '@fontsource/space-grotesk/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/700.css'

import './index.css'
import App from './App.tsx'
import { applyInitialTheme } from '@/components/layout/theme'

const rootEl = document.getElementById('root')
if (rootEl === null) throw new Error('Root element #root not found')

// Light-only app: guarantee the root is never in a dark state before paint.
applyInitialTheme()

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
