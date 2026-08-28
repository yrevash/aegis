'use client'

import { useEffect, useState } from 'react'

/** How often the live counters tick. Fast enough to read as live, slow enough to read. */
export const TICK_MS = 100

/**
 * The clock, while a run is in flight. Stops the moment it is not.
 *
 * Its own module because two surfaces need it — the stage rows inside the collapsed
 * panel and the path scaffold that is always on screen — and `RunStages` mounts
 * `RunPreview`, so the scaffold importing the hook from there would close a cycle.
 */
export function useTick(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const id = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(id)
  }, [active])
  return now
}
