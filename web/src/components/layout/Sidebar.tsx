'use client'

import { usePathname } from 'next/navigation'
import type { ReactElement } from 'react'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { portalLabelFor, type Portal } from '@/lib/portal'

import { PortalNav } from './PortalNav'
import { activeSectionFrom } from './navGroups'

/**
 * The fixed navigation rail, from `lg` up — and the product's one dark surface.
 *
 * It used to be `bg-surface`, i.e. `#ffffff`, and the canvas beside it is now
 * `#eef2f8`. The rail was therefore **lighter than the page it framed**, which
 * is why the console had no spatial anchor: nothing on screen said "this column
 * is chrome and that region is content". DESIGN.md §1 gives the rail its own
 * token set — `--rail` `#0b1f3f` and the four states around it — and this is the
 * only place in the product they are allowed to appear.
 *
 * Contrast is stated and measured in {@link PortalNav}; the two values this file
 * owns are the lockup (`#ffffff` on `--rail`, **16.38:1**) and the footer portal
 * label (`--rail-text` at 70%, `#7890b0` on `--rail`, **5.01:1**).
 *
 * It draws {@link PortalNav} and nothing else — the section list, the grouping
 * and the active-row rule are shared with the drawer that carries the same
 * navigation below `lg` ({@link MobileNav}), because the five portals genuinely
 * differ and two copies of that list would eventually differ too.
 *
 * `<aside>` rather than `<nav>`: the rail is the landmark, and the nav element
 * with its `aria-label` lives one level in, so a screen reader announces one
 * navigation here rather than two nested ones.
 */
export function Sidebar({ portal }: { portal: Portal }): ReactElement {
  const pathname = usePathname()

  return (
    <aside className="sticky top-0 hidden h-dvh w-[264px] shrink-0 self-start border-r border-rail-border bg-rail lg:flex lg:flex-col">
      <div className="px-5 pt-7 pb-6">
        <AegisLockup size="md" className="text-white" />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        <PortalNav portal={portal} active={activeSectionFrom(pathname)} />
      </div>

      <p className="border-t border-rail-border px-5 py-4 font-mono text-[0.68rem] font-medium uppercase tracking-[0.16em] text-rail-text/70">
        {portalLabelFor(portal)}
      </p>
    </aside>
  )
}
