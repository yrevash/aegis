import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

import { gridTail } from './gridTail'
import { LandingSection } from './LandingSection'
import { STACK_CLAIMS } from './stackClaims'

/**
 * The technology band: the specific hard things, each with the file it lives in.
 *
 * **This is not a second module grid.** `LivePlatform` already renders the branded
 * Aegis modules and the tech under each name, fetched live from
 * `GET /platform/capabilities` — that answers *what the product is made of*. This band
 * answers the different question a technical jury actually asks: *which of these is
 * wired up, and which one is load-bearing?* Every row is a mechanism rather than a
 * dependency — a run resuming on a fresh worker, a guest token carrying the tenant's
 * row-level security — and the mechanism is the claim, not the logo.
 *
 * **Set in type, and every path resolves.** No logos, for the same reason the
 * standards band has none, and because thirteen wordmarks in one face read as a system
 * where thirteen sourced-from-anywhere images read as a clip-art shelf. The paths are
 * in a disclosure rather than on the face, because they are the *evidence* and the
 * mechanism is the point — but they are checked against the real repository by
 * `web/tests/landing/stackClaims.test.mjs`, so a renamed module fails a test rather
 * than leaving a marketing page claiming something that moved.
 *
 * Static: it reads no endpoint, so it is a server component and ships no JavaScript.
 * The whole section is removable by one constant — see {@link ./bands.config}.
 */
export function StackBand(): ReactElement {
  return (
    <LandingSection
      id="stack"
      tone="surface"
      eyebrow="Under it"
      title="Each claim, and the file it lives in."
      lead="Nothing is listed here that is not wired up. The paths are checked against the repository by a test."
    >
      <ul className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 lg:grid-cols-3">
        {STACK_CLAIMS.map((claim, index) => (
          <li
            key={`${claim.mark}-${claim.path}`}
            className={cn(
              'min-w-0 bg-surface px-5 py-4',
              index === STACK_CLAIMS.length - 1 && gridTail(STACK_CLAIMS.length),
            )}
          >
            <p className="text-[0.9375rem] font-semibold tracking-[-0.01em] text-balance text-foreground">
              {claim.mark}
            </p>
            <p className="mt-1.5 text-pretty text-[0.8125rem] leading-5 text-muted-foreground">
              {claim.mechanism}
            </p>
          </li>
        ))}
      </ul>

      <details className="mt-6 min-w-0">
        <summary className="inline-flex cursor-pointer touch-manipulation items-center rounded-md text-[0.875rem] font-medium text-blue-700 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background">
          Where each one lives
        </summary>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[34rem] border-collapse text-left">
            <thead>
              <tr className="border-b border-border">
                <th scope="col" className="eyebrow py-2 pr-6 font-normal">
                  Mechanism
                </th>
                <th scope="col" className="eyebrow py-2 font-normal">
                  In the repository
                </th>
              </tr>
            </thead>
            <tbody>
              {STACK_CLAIMS.map((claim) => (
                <tr key={`${claim.mark}-${claim.path}-row`} className="border-b border-border">
                  <th
                    scope="row"
                    className="min-w-0 py-2.5 pr-6 align-top text-[0.8125rem] leading-5 font-medium text-foreground"
                  >
                    {claim.mark}
                  </th>
                  <td className="tabular min-w-0 py-2.5 align-top font-mono text-[0.75rem] leading-5 break-all text-blue-700">
                    {claim.path}
                    {claim.proof == null ? null : (
                      <span className="block break-all text-muted-foreground">{claim.proof}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </LandingSection>
  )
}
