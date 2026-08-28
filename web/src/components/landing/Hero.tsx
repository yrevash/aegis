'use client'

import Link from 'next/link'
import type { ReactElement } from 'react'

import { Receipt } from '@/components/primitives/Receipt'

import { useCapabilities } from './useCapabilities'
import { RevealGroup } from '@/components/shared/RevealGroup'

/**
 * The opening claim, and — beside it — proof that this page is wired to something.
 *
 * **There is no product screenshot and no invented number.** The shot this
 * section used to carry was captured against the in-browser mock, so every figure
 * in it was fictional; a platform whose pitch is honest instrumentation cannot
 * open with a picture of numbers it never measured.
 *
 * What sits in that space instead is the smallest true thing available before
 * anyone signs in: whether the public capability endpoint answers, and what it
 * says. When it answers, the hero names the build and its module count and shows
 * where both came from. When it does not, the hero says *that* — which is the
 * more convincing of the two states, because a page willing to report its own
 * backend as unreachable is a page not running on fixtures.
 *
 * `.t-hero` is used here and nowhere else in the product: DESIGN.md §3 retires it
 * from every console screen precisely so this page can keep it.
 */
export function Hero(): ReactElement {
  return (
    <section className="bg-background">
      <div className="mx-auto grid max-w-6xl gap-x-16 gap-y-10 px-4 pt-14 pb-14 sm:px-6 sm:pt-20 sm:pb-16 lg:grid-cols-[minmax(0,8fr)_minmax(0,4fr)] lg:items-end">
        {/* M1 "Arrive" — the one GSAP motion in the system, and the only place on this
            page that gets it. Above the fold, first mount only, four siblings staggered
            40ms apart. Everything below the fold uses `RevealOnScroll`, which is an
            IntersectionObserver that fails safe; GSAP earns its place here and nowhere
            else on this page because a staggered entrance across siblings is the one
            thing CSS `animation-delay` genuinely does badly. */}
        <RevealGroup className="min-w-0">
          <p className="eyebrow mb-6">Enterprise agentic AI · domain-agnostic · multi-tenant</p>
          <h1 className="t-hero max-w-[15ch] text-balance text-foreground">
            Autonomy you can audit.
          </h1>
          <p className="mt-7 max-w-[52ch] text-pretty text-lg leading-relaxed text-muted-foreground">
            A team of agents answers the question, and the platform records who decided,
            what was read, which rail fired and who signed for the write. Every figure
            carries its origin — and where one cannot be sourced, Aegis says so instead
            of inventing it.
          </p>

          <div className="mt-9 flex flex-wrap items-center gap-3">
            <Link
              href="/login"
              className="inline-flex h-11 touch-manipulation items-center rounded-lg bg-blue-600 px-6 text-sm font-medium text-white outline-none transition-colors duration-[--dur-fast] hover:bg-blue-700 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              Enter the console
            </Link>
            <a
              href="#run"
              className="inline-flex h-11 touch-manipulation items-center rounded-lg border border-border bg-surface px-6 text-sm font-medium text-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              Follow one run
            </a>
          </div>
        </RevealGroup>

        <BackendState />
      </div>
    </section>
  )
}

/**
 * Whether the backend behind this page is answering, said plainly.
 *
 * Deliberately not a badge with a green dot and nothing else: the point is not
 * "we are up", it is that the page has no other source. So the state carries its
 * own receipt in both directions — the endpoint that answered, or the sentence
 * explaining what the rest of the page will therefore not show.
 */
function BackendState(): ReactElement {
  const manifest = useCapabilities()

  return (
    <div className="min-w-0 border-t border-border pt-6 lg:border-t-0 lg:border-l lg:pt-1 lg:pb-2 lg:pl-10">
      <p className="eyebrow mb-3">Where this page reads from</p>

      {manifest.state === 'loading' ? (
        <p className="text-[0.9375rem] leading-relaxed text-muted-foreground">
          Asking the platform&hellip;
        </p>
      ) : manifest.state === 'down' ? (
        <>
          <p className="flex items-center gap-2.5 text-[0.9375rem] leading-relaxed font-medium text-foreground">
            <span aria-hidden className="size-2 shrink-0 rounded-full bg-border ring-4 ring-surface-2" />
            No backend answering
          </p>
          <p className="mt-3 max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground">
            So the sections below that read live endpoints will render nothing at all.
            There is no fixture behind them and no demo mode to fall back to.
          </p>
        </>
      ) : (
        <>
          <p className="flex items-center gap-2.5 text-[0.9375rem] leading-relaxed font-medium text-foreground">
            <span aria-hidden className="size-2 shrink-0 rounded-full bg-blue-600 ring-4 ring-blue-100" />
            Connected to a running platform
          </p>
          <p className="tabular mt-3 font-mono text-[0.8125rem] leading-relaxed text-blue-700">
            {manifest.data.product} · {manifest.data.module_count} modules
          </p>
          <Receipt
            origin="GET /platform/capabilities"
            detail="public, unauthenticated"
            className="mt-3"
          />
        </>
      )}
    </div>
  )
}
