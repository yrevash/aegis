'use client'

import Link from 'next/link'
import type { ReactElement } from 'react'

import { useAuth } from '@/lib/auth/AuthContext'
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
 * ## It sits on navy, and the contrast is measured
 *
 * The rail is `--rail` `#0b1f3f`, the product's single dark surface, so every
 * value here is stated against that ground rather than against white:
 *
 * | Element | Colours | Ratio |
 * |---|---|---|
 * | Idle row label | `--rail-text` `#a8c0e0` on `--rail` | **8.80:1** |
 * | Group heading | `--rail-text` at 70% (`#7890b0`) on `--rail` | **5.01:1** |
 * | Hover row label | `#ffffff` on `--rail-hover` `#12305e` | **13.04:1** |
 * | Active row label | `#ffffff` on `--rail-active` `#1570ef` | **4.57:1** |
 * | Active left marker | `#ffffff` on `--rail-active` | **4.57:1** |
 * | Focus ring | `#ffffff` on `--rail` / on `--rail-active` | **16.38:1 / 4.57:1** |
 *
 * **The focus ring is white, not `--blue-600`.** DESIGN.md §8 asks for a 2px
 * `--blue-600` ring, and on the navy ground that would be a defensible 3.59:1 —
 * but the active row is *filled* with that same `--blue-600`, so the ring would
 * land at 1:1 on the one row a keyboard user is most likely to be standing on.
 * A ring that disappears on the current page is not a focus indicator.
 * Accessibility wins the conflict order (DESIGN.md §10), so it is white on both
 * grounds and clears 3:1 either way.
 *
 * The active row is marked **four** ways on purpose: `aria-current="page"` for a
 * screen reader, and — for a sighted reader — a solid fill, a heavier weight in
 * white ink, and the left edge marker. Never the fill alone; the fill is also
 * the one cue that a colour-blind reader tells apart by *lightness* rather than
 * hue, which is why it is a saturated step rather than a tint (DESIGN.md §2).
 *
 * ## No `title=` on the rows
 *
 * Each row used to carry `title={item.tooltip}` — 34 native browser tooltips
 * averaging 28.5 words, one of them 129. A native tooltip clips, times out and
 * cannot be reached by keyboard at all, so that text had never once been read;
 * what it *did* do was pop under the pointer every time it crossed the rail.
 * Nothing was lost by deleting it: the same sentence is on the destination
 * screen's `PageHeader`, where a reader is actually looking. `Section.tooltip`
 * stays as the catalogue's plain-language gloss and is capped at 12 words —
 * asserted by `tests/design/navTooltipLength.test.mjs`.
 */
export function PortalNav({
  portal,
  active,
  onNavigate,
  density = 'rail',
  className,
}: PortalNavProps): ReactElement {
  const roomy = density === 'drawer'
  // The rail is drawn for a *principal*, not for a role. A tenant-pinned operator is
  // refused the process-wide sections outright (see `PLATFORM_ONLY_SECTIONS`), so
  // offering them here produced a nav item that could only ever 403 — with a Retry
  // button beside it. Same role un-pinned is platform staff and keeps them.
  const { session } = useAuth()
  return (
    <nav aria-label="Sections" className={cn('flex flex-col gap-6', className)}>
      {navGroupsFor(portal, session?.tenantId ?? null).map((group) => (
        <div key={group.heading}>
          {/* Not `.eyebrow`: that utility hard-sets `--muted-foreground`, which
              is 3.45:1 on navy and fails AA at 11px. Same anatomy, rail ink. */}
          <h2 className="mb-2 px-3 font-mono text-[0.68rem] font-medium uppercase tracking-[0.16em] text-rail-text/70">
            {group.heading}
          </h2>
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const Icon = item.icon
              const isActive = item.id === active
              return (
                <li key={item.id}>
                  <Link
                    href={`/app/${portal}/${item.id}`}
                    aria-current={isActive ? 'page' : undefined}
                    onClick={onNavigate}
                    className={cn(
                      'group relative flex w-full touch-manipulation items-center gap-3 rounded-lg px-3 text-sm outline-none transition-colors duration-[--dur-fast]',
                      'focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-rail',
                      roomy ? 'min-h-11 py-2.5' : 'py-2',
                      isActive
                        ? 'bg-rail-active font-semibold text-white'
                        : 'text-rail-text hover:bg-rail-hover hover:text-white',
                    )}
                  >
                    <span
                      aria-hidden
                      className={cn(
                        'absolute top-1/2 left-0 h-4 -translate-y-1/2 rounded-r-full bg-white transition-[width] duration-[--dur-fast] motion-reduce:transition-none',
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
