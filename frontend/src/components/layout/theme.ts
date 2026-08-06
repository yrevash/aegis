/**
 * Theme layer — light default with an opt-in dark control-room mode.
 *
 * The token system in `index.css` keys off a `.dark` class on the document
 * root. This module is the single owner of that class: it reads the persisted
 * preference, applies it before first paint (see `applyInitialTheme`, called
 * from `main.tsx` to avoid a flash), and exposes a small `useTheme` hook for the
 * top-bar toggle. No external state library — the choice is trivial and local.
 */

import { useCallback, useEffect, useState } from 'react'

/** The two identities the console can wear. */
export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'aegis-theme'

/** Read the saved preference, defaulting to the light dashboard identity. */
function readStoredTheme(): Theme {
  if (typeof localStorage === 'undefined') return 'light'
  return localStorage.getItem(STORAGE_KEY) === 'dark' ? 'dark' : 'light'
}

/** Reflect a theme onto the document root so the CSS variable layer switches. */
function reflect(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

/**
 * Apply the stored theme immediately. Call once from the entry module, before
 * React renders, so the correct palette is present on the very first paint.
 */
export function applyInitialTheme(): void {
  reflect(readStoredTheme())
}

/**
 * Subscribe to and control the active theme. Returns the current value and a
 * toggle that persists the new choice and updates the document root.
 */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(readStoredTheme)

  useEffect(() => {
    reflect(theme)
    if (typeof localStorage !== 'undefined') localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  const toggle = useCallback(() => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))
  }, [])

  return { theme, toggle }
}
