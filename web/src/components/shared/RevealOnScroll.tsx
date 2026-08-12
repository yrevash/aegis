'use client'

import { useEffect, useRef, useState, type ReactElement, type ReactNode } from 'react'

import { prefersReducedMotion } from '@/components/console/motion'
import { cn } from '@/lib/utils'

interface RevealOnScrollProps {
  children: ReactNode
  /** Stagger delay (ms) applied to the reveal, typically `index * 40`. */
  delayMs?: number
  className?: string
}

/**
 * Fade + rise a block into view the first time it enters the viewport
 * (§2.5). Reduced-motion / SSR / no-IntersectionObserver viewers render the
 * content visible immediately — the motion is purely additive and never hides
 * content that a viewer cannot animate back in.
 */
export function RevealOnScroll({
  children,
  delayMs = 0,
  className,
}: RevealOnScrollProps): ReactElement {
  const canObserve =
    typeof window !== 'undefined' && typeof IntersectionObserver === 'function'
  // Start shown when we cannot (or should not) animate, so content is never stuck hidden.
  const [shown, setShown] = useState(() => !canObserve || prefersReducedMotion())
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (shown) return
    const el = ref.current
    if (el == null) return
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setShown(true)
            observer.disconnect()
            break
          }
        }
      },
      { threshold: 0.1, rootMargin: '0px 0px -8% 0px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [shown])

  return (
    <div
      ref={ref}
      className={cn(shown ? 'animate-reveal' : 'opacity-0', className)}
      style={shown && delayMs ? { animationDelay: `${delayMs}ms` } : undefined}
    >
      {children}
    </div>
  )
}