'use client'

import Link from 'next/link'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { useAuth } from '@/lib/auth/AuthContext'
import { homePathFor } from '@/lib/portal'

/**
 * Sticky header for the public landing page.
 *
 * The only auth-aware section: signed out the CTA reads "Login" and points at
 * `/login`; with a stored session it reads "Enter console" and points at that
 * role's portal home. A judge who already signed in is never bounced back to a
 * sign-in form.
 *
 * The CTA is gated on `hydrated` so it never flashes the wrong label on a hard
 * refresh — the session is read from localStorage after the first paint, and
 * rendering "Login" to an already-authenticated operator for one frame reads as
 * a bug. Until hydration resolves the CTA renders in its signed-out form but is
 * inert, which is the honest state: we do not yet know who this is.
 */

const NAV = [
  { href: '#modules', label: 'Modules' },
  { href: '#architecture', label: 'Architecture' },
  { href: '#trust', label: 'Trust' },
  { href: '#roadmap', label: 'Roadmap' },
]

export function LandingHeader() {
  const { session, hydrated } = useAuth()
  const signedIn = hydrated && session !== null

  return (
    <header className="sticky top-0 z-50 border-b border-border bg-background/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-8 px-6">
        <Link href="/" className="flex items-center gap-2.5">
          <AegisLockup size="md" />
        </Link>

        <nav className="hidden flex-1 items-center gap-7 md:flex">
          {NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3 md:ml-0">
          <Link
            href={signedIn ? homePathFor(session.role) : '/login'}
            className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            {signedIn ? 'Enter console' : 'Login'}
          </Link>
        </div>
      </div>
    </header>
  )
}
