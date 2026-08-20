import Link from 'next/link'
import type { ReactElement } from 'react'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { LandingScene } from '@/components/landing/LandingScene'

/**
 * The 404. Previously Next's own unstyled default, which a jury could reach by
 * mistyping one path.
 *
 * **The scene and the sentence have to agree.** `No data-rafiki` draws the words
 * "NO DATA" into a browser window, and a picture whose baked text argues with the
 * heading beside it is worse than no picture — so the heading is *"Nothing is
 * served at this address"* rather than the usual "page not found". Both then say
 * the same thing, which is also the true one: there is no route here, so there is
 * nothing to render.
 *
 * The copy is direction, not mood. DESIGN.md's rule for a failure surface is that
 * it says what happened and how to get out of it, in the interface's voice — so
 * there is no apology, no "oops", and two real ways forward rather than a single
 * "go back" that a person who arrived by link cannot use.
 */
export default function NotFound(): ReactElement {
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <header className="px-6 py-6 sm:px-10">
        <Link
          href="/"
          aria-label="Aegis home"
          className="inline-block rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <AegisLockup size="md" />
        </Link>
      </header>

      <main
        id="main"
        className="flex flex-1 items-center justify-center px-6 pb-16 sm:px-10"
      >
        <div className="grid max-w-4xl gap-x-14 gap-y-8 sm:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] sm:items-center">
          <LandingScene name="nothing" width={320} className="mx-auto sm:mx-0" />

          <div className="min-w-0">
            <p className="eyebrow mb-3">404</p>
            <h1 className="font-display text-[1.75rem] leading-9 font-semibold tracking-[-0.02em] text-balance text-foreground sm:text-[2rem] sm:leading-10">
              Nothing is served at this address.
            </h1>
            <p className="mt-4 max-w-prose text-pretty text-[0.9375rem] leading-relaxed text-muted-foreground">
              The link may be old, or the route may have moved. Every screen in Aegis is
              reachable from a portal, and which portal you get is decided by your role
              when you sign in.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link
                href="/login"
                className="inline-flex h-11 touch-manipulation items-center rounded-lg bg-blue-600 px-6 text-sm font-medium text-white outline-none transition-colors duration-[--dur-fast] hover:bg-blue-700 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                Sign in
              </Link>
              <Link
                href="/"
                className="inline-flex h-11 touch-manipulation items-center rounded-lg border border-border bg-surface px-6 text-sm font-medium text-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                Back to the front page
              </Link>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
