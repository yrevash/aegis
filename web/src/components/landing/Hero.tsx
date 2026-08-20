import Link from 'next/link'
import type { ReactElement } from 'react'

/**
 * The opening statement: one claim, and the rule the whole product follows.
 *
 * There is deliberately no product screenshot here. The shot this section used to
 * carry was captured against the in-browser mock, so every figure in it — spend,
 * approvals, the case it was resolving — was invented, and the console banner
 * saying so was part of the image. A platform whose pitch is honest
 * instrumentation cannot lead with a picture of numbers it never measured, so the
 * section makes its claim in words and sends the visitor to the live console for
 * the proof. A shot captured against a real backend can take this place.
 *
 * **The centring is gone.** A giant centred headline over a centred subline over
 * centred buttons is the single most templated arrangement on the web, and
 * DESIGN.md §8 lists it by name. The claim now sits left at a readable measure,
 * with the product's actual operating rule beside it — which is the honest thing
 * to put in the space a screenshot would have occupied, because it is the thing
 * the screenshot could not have shown.
 */
export function Hero(): ReactElement {
  return (
    <section className="border-b border-border bg-surface">
      <div className="mx-auto grid max-w-6xl gap-x-12 gap-y-10 px-6 pt-20 pb-16 sm:pt-24 sm:pb-20 lg:grid-cols-[minmax(0,7fr)_minmax(0,4fr)] lg:items-end">
        <div className="min-w-0">
          <p className="eyebrow mb-5">Bounded-autonomy AI, made watchable</p>
          <h1 className="max-w-[14ch] text-balance text-[2.75rem] leading-[1.05] font-semibold tracking-[-0.03em] text-foreground sm:text-6xl">
            Autonomy you can audit.
          </h1>
          <p className="mt-6 max-w-prose text-pretty text-lg leading-relaxed text-muted-foreground">
            Agents that take real actions, and prove every one of them.
          </p>

          <div className="mt-9 flex flex-wrap items-center gap-3">
            <Link
              href="/login"
              className="inline-flex h-11 touch-manipulation items-center rounded-lg bg-primary px-6 text-sm font-medium text-primary-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
            >
              Enter the console
            </Link>
            <a
              href="#architecture"
              className="inline-flex h-11 touch-manipulation items-center rounded-lg border border-border bg-card px-6 text-sm font-medium text-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
            >
              How it works
            </a>
          </div>
        </div>

        {/* The rule the console is built on, stated where a screenshot would be.
            Every claim here is enforced somewhere in the tree — the sections
            below that read live endpoints render nothing at all when the backend
            is unreachable, rather than falling back to a figure. */}
        <div className="border-t border-border pt-6 lg:border-t-0 lg:border-l lg:pt-0 lg:pl-12">
          <p className="eyebrow mb-3">The rule</p>
          <p className="max-w-prose text-pretty text-[0.9375rem] leading-relaxed text-foreground">
            No fixtures, and no demo mode. Every figure on every screen is read from a
            running backend, or it is stated as absent.
          </p>
          <p className="mt-3 max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground">
            Two sections of this page read live endpoints. With nothing to read, they
            render nothing rather than something plausible.
          </p>
        </div>
      </div>
    </section>
  )
}
