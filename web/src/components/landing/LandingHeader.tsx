'use client'

import { Menu, X } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useId, useState, type ReactElement } from 'react'

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
 *
 * **Below `md` the four section links used to simply disappear**, which left a
 * phone visitor with a logo and a login button on a page with five sections
 * under it. They now collapse into a disclosure beneath the bar rather than
 * vanishing: it is four in-page anchors, so a full modal drawer would be
 * heavier machinery than the content deserves, but no navigation at all was
 * never the lighter option.
 *
 * The backdrop blur is gone with it — glassmorphism, DESIGN.md §4.
 */

const NAV = [
  { href: '#modules', label: 'Modules' },
  { href: '#architecture', label: 'Architecture' },
  { href: '#trust', label: 'Trust' },
  { href: '#roadmap', label: 'Roadmap' },
]

export function LandingHeader(): ReactElement {
  const { session, hydrated } = useAuth()
  const signedIn = hydrated && session !== null
  const [open, setOpen] = useState(false)
  const panelId = useId()

  // Escape closes the disclosure, the same way it closes the console drawer —
  // one gesture for "put that away" across both shells.
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open])

  return (
    <header className="sticky top-0 z-50 border-b border-border bg-background">
      {/* First in the tab order, because four section anchors and a CTA sit
          between the top of the document and the page's own content. */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50 focus:rounded-lg focus:border focus:border-border focus:bg-card focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      >
        Skip to the main content
      </a>
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-6 px-6">
        {/* The lockup already renders the word "Aegis", so an sr-only "Aegis home"
            beside it made the link announce as "Aegis Aegis home". The label
            replaces the name rather than appending to it. */}
        <Link
          href="/"
          aria-label="Aegis home"
          className="shrink-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <AegisLockup size="md" />
        </Link>

        <nav aria-label="Sections" className="hidden flex-1 items-center gap-7 md:flex">
          {NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="rounded-sm text-sm text-muted-foreground outline-none transition-colors duration-[--dur-fast] hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2 md:ml-0">
          <Link
            href={signedIn ? homePathFor(session.fineRole) : '/login'}
            className="inline-flex h-10 touch-manipulation items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            {signedIn ? 'Enter console' : 'Login'}
          </Link>
          <button
            type="button"
            onClick={() => setOpen((was) => !was)}
            aria-expanded={open}
            aria-controls={panelId}
            className="inline-flex size-11 shrink-0 touch-manipulation items-center justify-center rounded-lg border border-border bg-card text-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background md:hidden"
          >
            {open ? <X className="size-5" aria-hidden /> : <Menu className="size-5" aria-hidden />}
            <span className="sr-only">{open ? 'Close the section list' : 'Open the section list'}</span>
          </button>
        </div>
      </div>

      <nav
        id={panelId}
        aria-label="Sections, expanded"
        hidden={!open}
        className="border-t border-border bg-surface md:hidden"
      >
        <ul className="mx-auto max-w-6xl px-4 py-2">
          {NAV.map((item) => (
            <li key={item.href}>
              <a
                href={item.href}
                onClick={() => setOpen(false)}
                className="flex min-h-11 touch-manipulation items-center rounded-lg px-3 text-sm text-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
              >
                {item.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </header>
  )
}
