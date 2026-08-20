'use client'

import type { ReactElement, ReactNode } from 'react'

import { Card } from '@/components/ui/Card'
import { cn } from '@/lib/utils'

import { RevealOnScroll } from './RevealOnScroll'

interface BentoGridProps {
  children: ReactNode
  className?: string
}

/** The 12-column bento container (§2.2). Tiles set their own span. */
export function BentoGrid({ children, className }: BentoGridProps): ReactElement {
  return <div className={cn('grid grid-cols-12 gap-4 lg:gap-5', className)}>{children}</div>
}

export type BentoSpan = 3 | 4 | 5 | 6 | 7 | 8 | 12

interface BentoTileProps {
  /** lg column span; always full-width (12) below lg. Default 6. */
  span?: BentoSpan
  /** Row span (hero tiles span 2). Default 1. */
  rows?: 1 | 2
  /** Hero styling: roomier padding and display type context. */
  hero?: boolean
  /** Adds hover-lift + focus ring (for tiles that navigate / expand). */
  interactive?: boolean
  /** Wrap in RevealOnScroll, staggered by `index`. */
  reveal?: boolean
  /** Stagger order for the reveal (index * 40ms). */
  index?: number
  className?: string
  children: ReactNode
}

// Full class strings so Tailwind's scanner keeps them (no dynamic interpolation).
// Spans collapse in two steps so mid-desktop widths never cramp or overflow:
// small tiles (3/4/5) go 2-up at `sm`, larger tiles (6/7/8) at `md`, then reach
// their full `lg` span at the design width.
const SPAN_CLASS: Record<BentoSpan, string> = {
  3: 'col-span-12 sm:col-span-6 lg:col-span-3',
  4: 'col-span-12 sm:col-span-6 lg:col-span-4',
  5: 'col-span-12 sm:col-span-6 lg:col-span-5',
  6: 'col-span-12 md:col-span-6 lg:col-span-6',
  7: 'col-span-12 md:col-span-6 lg:col-span-7',
  8: 'col-span-12 md:col-span-6 lg:col-span-8',
  12: 'col-span-12',
}

/**
 * A single bento tile — composes `Card` (not a new visual box) with a column
 * span, optional hero elevation, hover-lift, and reveal-on-scroll. Everything is
 * driven by the design tokens so light + dark stay consistent.
 */
export function BentoTile({
  span = 6,
  rows = 1,
  hero = false,
  interactive = false,
  reveal = false,
  index = 0,
  className,
  children,
}: BentoTileProps): ReactElement {
  const tile = (
    <Card
      className={cn(
        'h-full',
        hero ? 'p-6' : 'p-5',
        interactive &&
          'cursor-pointer transition-transform duration-200 ease-out hover:-translate-y-0.5 focus-visible:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
      tabIndex={interactive ? 0 : undefined}
    >
      {children}
    </Card>
  )

  // `min-w-0` lets the grid child shrink below its content's intrinsic width
  // instead of overflowing the row (the horizontal-scroll bug at sub-1440 widths).
  const spanClass = cn(SPAN_CLASS[span], 'min-w-0', rows === 2 && 'lg:row-span-2')

  if (reveal) {
    return (
      <RevealOnScroll className={spanClass} delayMs={index * 40}>
        {tile}
      </RevealOnScroll>
    )
  }
  return <div className={spanClass}>{tile}</div>
}