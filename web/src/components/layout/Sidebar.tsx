'use client'

import { usePathname } from 'next/navigation'
import type { ReactElement } from 'react'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { portalLabelFor, type Portal } from '@/lib/portal'

import { PortalNav } from './PortalNav'
import { activeSectionFrom } from './navGroups'

/**
 * The fixed navigation rail, from `lg` up.
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
    <aside className="sticky top-0 hidden h-dvh w-[264px] shrink-0 self-start border-r border-border bg-surface lg:flex lg:flex-col">
      <div className="px-5 pt-7 pb-6">
        <AegisLockup size="md" />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        <PortalNav portal={portal} active={activeSectionFrom(pathname)} />
      </div>

      <p className="eyebrow border-t border-border px-5 py-4">{portalLabelFor(portal)}</p>
    </aside>
  )
}
