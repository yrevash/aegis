'use client'

import { RotateCcw } from 'lucide-react'
import Link from 'next/link'
import { useEffect, type ReactElement } from 'react'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { Absence, Receipt } from '@/components/primitives/Receipt'

/**
 * The runtime error boundary for the app. Previously Next's own default, which is
 * an unstyled page a jury could reach from any screen that threw.
 *
 * **No illustration here, deliberately.** Nothing in `public/illustrations` depicts
 * a screen that stopped rendering. `messy bun` and `Charity` are already marked
 * unused in `CREDITS.md` for exactly this reason, and reaching for one of them
 * because the page looks bare is how a product acquires stock art. What this page
 * gets instead is the thing an error surface actually needs: the identifier the
 * failure was recorded under.
 *
 * **The digest is a receipt, and its absence is an absence.** React stamps a
 * `digest` on a server-side error so the same failure can be found in the logs;
 * a client-side throw has none. Printing an empty `Error:` line for the second
 * case would be the "figure that cannot be sourced rendered anyway" failure
 * DESIGN.md §1 exists to prevent, so the two states are told apart and the
 * missing one says what would have to happen for it to exist.
 *
 * The copy is direction, not mood. It names what happened, offers the retry that
 * is actually likely to work — this boundary re-renders the segment rather than
 * reloading the document — and a way out that does not depend on the retry.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}): ReactElement {
  // The console has no client error sink of its own, so the browser's log is the
  // only place a developer can read the stack. Rendering the message on screen
  // instead would put a stack trace in front of a jury.
  useEffect(() => {
    console.error('Aegis screen failed to render', error)
  }, [error])

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

      <main id="main" className="flex flex-1 items-center px-6 pb-16 sm:px-10">
        <div className="mx-auto w-full max-w-2xl">
          <p className="eyebrow mb-3">Screen error</p>
          <h1 className="font-display text-[1.75rem] leading-9 font-semibold tracking-[-0.02em] text-balance text-foreground sm:text-[2rem] sm:leading-10">
            This screen stopped before it could render.
          </h1>
          <p className="mt-4 max-w-prose text-pretty text-[0.9375rem] leading-relaxed text-muted-foreground">
            Nothing was written and no run was affected — this is the console failing to
            draw, not the platform failing to work. Retrying re-renders this screen
            alone; if it fails the same way twice, the identifier below is what to search
            the logs for.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={reset}
              className="inline-flex h-11 touch-manipulation items-center gap-2 rounded-lg bg-blue-600 px-6 text-sm font-medium text-white outline-none transition-colors duration-[--dur-fast] hover:bg-blue-700 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <RotateCcw className="size-4" aria-hidden />
              Try this screen again
            </button>
            <Link
              href="/"
              className="inline-flex h-11 touch-manipulation items-center rounded-lg border border-border bg-surface px-6 text-sm font-medium text-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              Back to the front page
            </Link>
          </div>

          <div className="mt-10">
            {error.digest == null || error.digest === '' ? (
              <Absence
                figure="No error identifier"
                why="the failure happened in the browser, so the server never stamped a digest"
                needed="the same screen failing on the server — that path records a digest you can search the logs for"
              />
            ) : (
              <Receipt label="Recorded as" origin={error.digest} detail="server error digest" />
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
