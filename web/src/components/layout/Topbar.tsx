'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Presentation, UserRound } from 'lucide-react'
import { portalLabelFor, SECTIONS, type Role } from '@/lib/portal'

/**
 * Topbar — TailAdmin's header restyled to our tokens: a breadcrumb (portal ›
 * section) on the left, and a present toggle + user chip on the right. The
 * present button is a placeholder hook for projector mode (wired later).
 */
export function Topbar({ role }: { role: Role }) {
  const pathname = usePathname()
  const sectionId = pathname.split('/')[3] ?? ''
  const sectionLabel = SECTIONS[sectionId]?.label ?? portalLabelFor(role)
  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-border bg-surface/80 px-5 backdrop-blur md:px-8">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-sm">
        <Link href={`/app/${role}`} className="text-muted-foreground hover:text-foreground">
          {portalLabelFor(role)}
        </Link>
        <span aria-hidden className="text-muted-foreground/50">
          /
        </span>
        <span className="font-medium text-foreground">{sectionLabel}</span>
      </nav>

      {/* Right cluster */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          title="Present (projector mode) — wired later"
          className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
        >
          <Presentation className="size-4" />
          <span className="hidden sm:inline">Present</span>
        </button>
        <Link
          href="/login"
          title="Switch role"
          className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-foreground transition-colors hover:bg-surface-2"
        >
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-surface-2 text-muted-foreground">
            <UserRound className="size-4" />
          </span>
          <span className="hidden capitalize sm:inline">{role.replace('_', ' ')}</span>
        </Link>
      </div>
    </header>
  )
}
