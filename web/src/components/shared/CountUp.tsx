'use client'

import { useEffect, useRef, useState, type ReactElement } from 'react'

import { prefersReducedMotion } from '@/components/console/motion'
import { cn } from '@/lib/utils'

/**
 * Ease a displayed number toward `target` over `durationMs` so a headline figure
 * ticks rather than snapping when it mounts or a new sample arrives. Promoted out
 * of `RoiPanel` (§5) into the shared library so every KPI surface reuses the same
 * count-up. The *target* is always the real value; the animation only
 * interpolates the render, and reduced-motion viewers snap straight to it.
 */
export function useCountUp(target: number | null, durationMs = 900): number {
  const [display, setDisplay] = useState(target ?? 0)
  const fromRef = useRef(target ?? 0)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    if (target == null) return
    // Respect reduced-motion: land on the real value immediately.
    if (prefersReducedMotion()) {
      setDisplay(target)
      fromRef.current = target
      return
    }
    const from = fromRef.current
    const start = performance.now()
    const tick = (now: number): void => {
      const t = Math.min(1, (now - start) / durationMs)
      // easeOutCubic — quick then settle.
      const eased = 1 - Math.pow(1 - t, 3)
      setDisplay(from + (target - from) * eased)
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        fromRef.current = target
      }
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
      fromRef.current = target
    }
  }, [target, durationMs])

  return display
}

interface CountUpProps {
  value: number
  durationMs?: number
  /** Format the interpolated number for display (default: locale integer). */
  format?: (n: number) => string
  className?: string
}

/** A number that counts up to `value` on mount / change. Tabular by default. */
export function CountUp({
  value,
  durationMs = 900,
  format = (n) => Math.round(n).toLocaleString(),
  className,
}: CountUpProps): ReactElement {
  const display = useCountUp(value, durationMs)
  return <span className={cn('tabular', className)}>{format(display)}</span>
}