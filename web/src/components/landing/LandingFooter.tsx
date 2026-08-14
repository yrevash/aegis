import Link from 'next/link'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { API_BASE } from '@/lib/api/config'

/**
 * Closing band: the mark in ink-on-white, a last way into the console, and the
 * self-describing surfaces worth knowing about.
 */
export function LandingFooter() {
  return (
    <footer className="bg-background">
      <div className="mx-auto flex max-w-6xl flex-col gap-8 px-6 py-14 sm:flex-row sm:items-center sm:justify-between">
        <AegisLockup size="md" />

        {/* These are backend routes, not console pages — resolve them against the
            API origin so they work whether the API is same-origin or on :8000. */}
        <nav className="flex flex-wrap items-center gap-x-7 gap-y-2 font-mono text-[0.72rem] text-muted-foreground">
          <a href={`${API_BASE}/docs`} className="transition-colors hover:text-foreground">
            API docs
          </a>
          <a href={`${API_BASE}/about`} className="transition-colors hover:text-foreground">
            About
          </a>
          <a
            href={`${API_BASE}/platform/capabilities`}
            className="transition-colors hover:text-foreground"
          >
            Capabilities
          </a>
          <Link href="/login" className="transition-colors hover:text-foreground">
            Login
          </Link>
        </nav>
      </div>
    </footer>
  )
}
