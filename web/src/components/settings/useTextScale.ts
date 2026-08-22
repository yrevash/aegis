'use client'

import { useCallback, useEffect, useState } from 'react'

import {
  DEFAULT_TEXT_SCALE,
  applyScale,
  normaliseScale,
  persistScale,
  readStoredScale,
} from './textScale'

/**
 * The one text-size value the whole app shares.
 *
 * A module-level holder rather than a React context, for one reason: the control appears
 * twice — in Settings and in the top bar, so a person who cannot read the page does not
 * have to navigate to a settings screen to fix that — and two copies of a radio group
 * that disagree about which step is selected is worse than one copy in the wrong place.
 * A context would work and would mean a provider wrapping the whole tree for a single
 * integer.
 *
 * It starts at the default and is read from storage in an effect, never during render:
 * the server has no `localStorage`, and a component that renders 125% on the client
 * against 100% from the server is a hydration mismatch. The *visual* size does not wait
 * for this — the inline `TEXT_SCALE_BOOT` script has already applied it before the first
 * paint. Only which button looks selected settles a moment later.
 */
const store: { value: number; hydrated: boolean; listeners: Set<() => void> } = {
  value: DEFAULT_TEXT_SCALE,
  hydrated: false,
  listeners: new Set(),
}

/** The current step, and the way to change it. */
export function useTextScale(): readonly [number, (percent: number) => void] {
  const [value, setValue] = useState(store.value)

  useEffect(() => {
    if (!store.hydrated) {
      store.hydrated = true
      store.value = readStoredScale(window.localStorage)
    }
    setValue(store.value)
    const listener = (): void => setValue(store.value)
    store.listeners.add(listener)
    return () => {
      store.listeners.delete(listener)
    }
  }, [])

  const set = useCallback((percent: number): void => {
    const step = normaliseScale(percent)
    store.value = step
    store.hydrated = true
    persistScale(window.localStorage, step)
    applyScale(document.documentElement, step)
    for (const listener of store.listeners) listener()
  }, [])

  return [value, set] as const
}
