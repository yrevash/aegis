'use client'

import { ShieldAlert } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { getStandards, type FrameworkSummary, type StandardsResponse } from '@/lib/api/standards'
import { cn } from '@/lib/utils'

import { gridTail } from './gridTail'
import { LandingSection } from './LandingSection'
import { byJurisdiction, segments, type Segment } from './standardsSummary'

/**
 * The standards band: which published frameworks Aegis is built to, and how far.
 *
 * **The framing is the product here, not the wordmarks.** ISO 27001, SOC 2 and GDPR
 * normally mean *audited and certified by a named body*. Aegis has none of that — no
 * certificate, no report, no conformity assessment, nobody independent has looked. A
 * bare row of framework marks would therefore be a claim a jury can puncture with one
 * question, and it would take the genuinely strong evidence beside it down with it.
 * So the correction is made three times, in three registers a reader cannot miss: the
 * section's own heading says *certified against none*, a persistent notice sits above
 * the marks rather than inside a tooltip, and the four-state strip draws the gaps at
 * the same weight as the wins.
 *
 * **No certification marks, no accreditation seals, no logos.** Every framework is set
 * in type. That is partly the legal line — reproducing a certification body's mark you
 * have not earned is the part that is an actual problem rather than merely an
 * embarrassing one — and partly that twelve wordmarks in one typeface read as a system
 * where twelve sourced-from-anywhere logos read as a clip-art shelf.
 *
 * **Every number is fetched.** `GET /platform/standards` counts the four states over
 * the real control table on each request; nothing in this file or in
 * {@link ./standardsSummary} writes a total down. The India frameworks are being
 * extended as this ships, so a hardcoded "27 enforced" would have been wrong within
 * the week — on the page whose entire argument is that this platform does not do that.
 *
 * The whole section is removable by one constant: see {@link ./bands.config}.
 */
export function StandardsBand(): ReactElement | null {
  const [state, setState] = useState<
    { kind: 'loading' } | { kind: 'up'; data: StandardsResponse } | { kind: 'down' }
  >({ kind: 'loading' })

  useEffect(() => {
    let live = true
    getStandards()
      .then((data) => live && setState({ kind: 'up', data }))
      .catch(() => live && setState({ kind: 'down' }))
    return () => {
      live = false
    }
  }, [])

  if (state.kind === 'loading') return null

  if (state.kind === 'down') {
    return (
      <LandingSection
        id="standards"
        eyebrow="Built to"
        title="Aligned with published frameworks. Certified against none."
      >
        <NotCertified />
        <Absence
          className="mt-6"
          figure="The framework coverage"
          why="the public standards endpoint did not answer"
          needed="a reachable backend — the counts are derived from the control table on every read, never stored on this page"
        />
      </LandingSection>
    )
  }

  const { frameworks, coverage } = state.data
  const groups = byJurisdiction(frameworks)

  return (
    <LandingSection
      id="standards"
      eyebrow="Built to"
      title={`Aligned with ${frameworks.length} published frameworks. Certified against none.`}
    >
      <NotCertified />

      <CoverageStrip coverage={coverage} className="mt-8" />

      <div className="mt-10 grid gap-x-12 gap-y-8">
        {groups.map((group) => (
          <section key={group.jurisdiction} className="min-w-0">
            <h3 className="eyebrow mb-4">{group.jurisdiction}</h3>
            <ul className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 lg:grid-cols-3">
              {group.frameworks.map((framework, index) => (
                <FrameworkCell
                  key={framework.id}
                  framework={framework}
                  className={
                    index === group.frameworks.length - 1
                      ? gridTail(group.frameworks.length)
                      : undefined
                  }
                />
              ))}
            </ul>
          </section>
        ))}
      </div>

      <Meaning className="mt-8" />

      <Receipt
        origin="GET /platform/standards"
        detail="public, unauthenticated · counts derived per request"
        className="mt-6"
      />
    </LandingSection>
  )
}

/**
 * The sentence a reader must not be able to leave without.
 *
 * Deliberately not a tooltip, not a footnote and not grey 11px type under the grid: it
 * sits *above* the marks, in the amber the console already reserves for "a human needs
 * to look at this", with the icon and the word the status rule requires (DESIGN.md
 * §2). It is two clauses, because the second one is the part that makes the first
 * credible — a page that only disclaims sounds evasive; a page that disclaims and then
 * says what it *does* have sounds like it knows the difference.
 */
function NotCertified(): ReactElement {
  return (
    <p className="flex min-w-0 items-start gap-2.5 rounded-lg border border-risk bg-risk/15 px-4 py-3">
      <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-risk-ink" strokeWidth={2} />
      <span className="min-w-0 text-pretty text-[0.875rem] leading-relaxed text-foreground">
        <strong className="font-semibold">
          Compliance-readiness evidence — not certification.
        </strong>{' '}
        <span className="text-muted-foreground">
          {/* Not "each control below": the notice is rendered in the unreachable-backend
              state too, where there is nothing below it. */}
          No certificate held, no independent audit, no attestation. Each control maps to
          a file, a route or a test in this repository.
        </span>
      </span>
    </p>
  )
}

/**
 * The aggregate, drawn before it is described (DESIGN.md §4).
 *
 * One proportional strip answers *how much of this holds* in the first half-second;
 * the legend under it answers *which*, and carries the four counts as text so the
 * figures are readable by a screen reader and by anyone who cannot separate the hues.
 * The bar itself is `aria-hidden` for exactly that reason — it is a second rendering
 * of the legend, and announcing both would read the same four numbers twice.
 */
function CoverageStrip({
  coverage,
  className,
}: {
  coverage: StandardsResponse['coverage']
  className?: string
}): ReactElement {
  const bands: Segment[] = segments(coverage)

  return (
    <div className={cn('min-w-0', className)}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Figure size="stat" className="text-foreground">
          {coverage.total.toLocaleString()}
        </Figure>
        <span className="text-[0.9375rem] leading-relaxed text-muted-foreground">
          mapped controls, in four honest states
        </span>
      </div>

      <div aria-hidden className="mt-4 flex h-2.5 w-full overflow-hidden rounded-full bg-surface-2">
        {bands.map((band) => (
          <span key={band.state} className={cn('h-full', band.fill)} style={{ width: band.width }} />
        ))}
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-8 gap-y-3 sm:flex sm:flex-wrap sm:gap-x-10">
        {bands.map((band) => (
          <div key={band.state} className="flex min-w-0 items-center gap-2">
            <span aria-hidden className={cn('size-2.5 shrink-0 rounded-sm', band.fill)} />
            <dt className="truncate text-[0.8125rem] leading-5 text-muted-foreground">
              {band.label}
            </dt>
            <dd className={cn('tabular font-mono text-[0.8125rem] font-medium', band.ink)}>
              {band.count}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/**
 * One framework, set as a wordmark rather than shown as a logo.
 *
 * The cell carries three things and stops: the name, the edition it was read from, and
 * how many of its controls are mapped. The per-control detail — which one is missing
 * and what would close it — is behind the login on purpose; a public gap map is a
 * target list, which is why `GET /compliance` is guarded and this band summarises it.
 */
function FrameworkCell({
  framework,
  className,
}: {
  framework: FrameworkSummary
  className?: string
}): ReactElement {
  const bands = segments(framework.coverage)

  return (
    <li className={cn('min-w-0 bg-surface px-5 py-4', className)}>
      <p
        className="truncate text-[0.9375rem] font-semibold tracking-[-0.01em] text-foreground"
        title={framework.name}
      >
        {framework.mark}
      </p>
      {/* Truncated rather than wrapped: a version string that grows to two lines would
          push this cell's strip out of line with its neighbours', and the strip is the
          thing being compared across the row. The full string stays on hover. */}
      <p
        className="tabular truncate font-mono text-[0.7rem] text-muted-foreground"
        title={framework.version}
      >
        {framework.version}
      </p>
      <div aria-hidden className="mt-3 flex h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
        {bands.map((band) => (
          <span key={band.state} className={cn('h-full', band.fill)} style={{ width: band.width }} />
        ))}
      </div>
      <p className="tabular mt-2 font-mono text-[0.7rem] text-blue-700">
        {framework.coverage.enforced} enforced of {framework.coverage.total}
      </p>
    </li>
  )
}

/**
 * The detail, in a disclosure — the page's rule for prose (DESIGN.md §4).
 *
 * Three lines, closed by default. A reader who wants the qualification opens it; a
 * reader who does not has already been told the one thing that matters, twice, above.
 */
function Meaning({ className }: { className?: string }): ReactElement {
  return (
    <details className={cn('group min-w-0', className)}>
      <summary className="inline-flex cursor-pointer touch-manipulation items-center rounded-md text-[0.875rem] font-medium text-blue-700 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background">
        What &ldquo;built to&rdquo; means here
      </summary>
      <ul className="mt-3 max-w-[68ch] space-y-2 border-l-2 border-border pl-4 text-[0.875rem] leading-relaxed text-muted-foreground">
        <li>
          <span className="text-foreground">Alignment, not certification.</span> Aegis holds
          no ISO 27001 or ISO/IEC 42001 certificate, no SOC 2 report and no EU AI Act
          conformity assessment.
        </li>
        <li>
          <span className="text-foreground">Every claim resolves.</span> Each control names
          a file, a served route or a pytest node id, and the suite resolves every one of
          them against the real filesystem, route table and test files on each run.
        </li>
        <li>
          <span className="text-foreground">The full map is not public.</span> The
          control-by-control table lives in{' '}
          <code className="tabular font-mono text-[0.8125rem] text-blue-700">
            docs/compliance/README.md
          </code>{' '}
          and behind{' '}
          <code className="tabular font-mono text-[0.8125rem] text-blue-700">
            GET /v1/compliance
          </code>
          , which platform staff sign in to read.
        </li>
      </ul>
    </details>
  )
}
