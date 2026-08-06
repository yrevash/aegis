/**
 * Theme layer — the console is light-only.
 *
 * The product ships a single light identity, so dark is unreachable: this
 * module never reads a stored preference, never applies a `.dark` class or
 * `data-theme="dark"`, and never persists or toggles to dark. It exists only to
 * (a) guarantee the document root is in the light state before first paint and
 * (b) hand the fixed `'light'` value to the few consumers (e.g. the toast host)
 * that still ask for a theme. The `toggle` is a retained no-op so callers keep
 * compiling; there is nothing to switch to.
 */

import { useEffect } from 'react'

/** The only identity the console wears. */
export type Theme = 'light'

/**
 * Force the document root out of any dark state. Call once from the entry
 * module, before React renders, so the light palette is present on first paint.
 */
export function reflectLight(): void {
  document.documentElement.classList.remove('dark')
  if (document.documentElement.dataset.theme === 'dark') {
    document.documentElement.dataset.theme = 'light'
  }
}

/**
 * Apply the light theme immediately. Call once from the entry module, before
 * React renders, so the correct palette is present on the very first paint.
 */
export function applyInitialTheme(): void {
  reflectLight()
}

/**
 * Return the active theme. Always `'light'`; the `toggle` is a no-op because the
 * app has no dark identity to switch to.
 */
export function useTheme(): { theme: Theme; toggle: () => void } {
  // Defend against any stray dark state introduced after first paint.
  useEffect(() => {
    reflectLight()
  }, [])

  return { theme: 'light', toggle: () => {} }
}
