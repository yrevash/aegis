'use client'

import Link from 'next/link'
import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'
import type { Portal } from '@/lib/portal'

import { navGroupsFor } from './navGroups'

interface PortalNavProps {
  /** Whose section list to draw. The only input — see {@link navGroupsFor}. */
  portal: Portal
  /** The section slug currently open, from {@link activeSectionFrom}. */
  active: string
  /**
   * Called after a row is followed. The drawer closes on it; the fixed rail
   * passes nothing, because a rail that vanished when you used it would be a
   * bug rather than a feature.
   */
  onNavigate?: () => void
  /**
   * `rail` is the `lg`-and-up fixed column. `drawer` is the same list at a size
   * a thumb can hit: 44px rows rather than 36px, per the touch-target floor.
   */
  density?: 'rail' | 'drawer'
  className?: string
}

/**
 * A portal's section list, drawn once and rendered in both places it appears.
 *
 * The shell shows this as a fixed rail from `lg` up and inside a drawer below
 * it. Those are two different chromes around **one** list, and keeping the list
 * itself in one component is what stops the five portals — whose section sets
 * genuinely differ — from drifting between the two.
 *
 * The active row is marked three ways on purpose: `aria-current="page"` for a
 * screen reader, weight and ink for a sighted reader, and the blue edge marker
 * for the glance. Never the marker alone — DESIGN.md §2, colour is never the
 * only carrier.
 */
export function PortalNav({
  portal,
  active,
  onNavigate,
  density = 'rail',
  className,
}: PortalNavProps): ReactElement {
  const roomy = density === 'drawer'
  return (
    <nav aria-label="Sections" className={cn('flex flex-col gap-6', className)}>
      {navGroupsFor(portal).map((group) => (
        <div key={group.heading}>
          <h2 className="eyebrow mb-2 px-3">{group.heading}</h2>
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const Icon = item.icon
              const isActive = item.id === active
              return (
                <li key={item.id}>
                  <Link
                    href={`/app/${portal}/${item.id}`}
                    aria-current={isActive ? 'page' : undefined}
                    title={item.tooltip}
                    onClick={onNavigate}
                    className={cn(
                      'group relative flex w-full touch-manipulation items-center gap-3 rounded-lg px-3 text-sm outline-none transition-colors duration-[--dur-fast]',
                      'focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface',
                      roomy ? 'min-h-11 py-2.5' : 'py-2',
                      isActive
                        ? 'bg-blue-50 font-medium text-blue-700'
                        : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground',
                    )}
                  >
                    <span
                      aria-hidden
                      className={cn(
                        'absolute top-1/2 left-0 h-4 -translate-y-1/2 rounded-r-full bg-blue-600 transition-[width] duration-[--dur-fast] motion-reduce:transition-none',
                        isActive ? 'w-1' : 'w-0',
                      )}
                    />
                    <Icon className="size-[18px] shrink-0" aria-hidden />
                    <span className="min-w-0 truncate">{item.label}</span>
                  </Link>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </nav>
  )
}
