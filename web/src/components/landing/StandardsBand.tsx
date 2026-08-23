'use client'

import { useEffect, useState, type ReactElement } from 'react'

import { Absence, Receipt } from '@/components/primitives/Receipt'
import { API_BASE } from '@/lib/api/config'
import { getStandards, type StandardsResponse } from '@/lib/api/standards'
import { cn } from '@/lib/utils'

import { LandingSection } from './LandingSection'
import {
  completeFrameworks,
  resolveGroups,
  shownCount,
  type ResolvedClaim,
  type ResolvedGroup,
} from './standardsSummary'

/**
 * The standards band: the controls Aegis enforces in full, named one by one.
 *
 * **The unit is a control, not a framework, and that is the whole design.** This band
 * used to be a wall of framework wordmarks with a four-state strip under each. It could
 * not survive the only question a reviewer asks — *which of these are you compliant
 * with?* — because for most of these frameworks the answer is a fraction, and a wordmark
 * grid invites a reader to hear a whole. So the page stopped claiming frameworks except
 * where the endpoint says every mapped control is enforced. It names controls:
 * `Art. 14 — Human oversight`, `LLM06 — Excessive Agency`, `s.12(3) — Right to
 * erasure`. Each one is checkable against the published standard, and each one is a
 * claim held in full.
 *
 * **Only `enforced` reaches this page.** `enforced` in the compliance table means a
 * mechanism in this repository *and* a test that fails when the mechanism is removed.
 * The endpoint serves control ids for that state and no other, so a control slipping to
 * `partial` disappears from here on the next read rather than becoming a stale boast.
 * Nothing partial and nothing unimplemented is named on a public page — that map lives
 * behind `GET /v1/compliance`, which platform staff sign in to read.
 *
 * **The denominator is on the face of every card.** A group showing five OWASP-LLM
 * controls says *five of ten*, because the other five are partial and a card that hid
 * its denominator would be the whole-framework claim this redesign exists to delete.
 *
 * **Frameworks held in full lead, and that group is derived.** A framework appears in the
 * first row only when the endpoint says every control it maps is enforced —
 * `completeFrameworks` compares two numbers off the wire and nothing in this file names a
 * framework. One reaching completeness joins that row on the next read; one losing a
 * control falls back into the shortlist below, or off the page. The denominator is printed
 * there too, because *in full* means every control **this table maps**, and the mapping is
 * ours: four functions of NIST AI RMF is a coarser unit than seventeen ISO controls, and a
 * reader is owed that rather than left to infer it.
 *
 * **The framing is the product here, not the wordmarks.** ISO 27001, SOC 2 and GDPR
 * normally mean *audited and certified by a named body*. Aegis has none of that — no
 * certificate, no report, no conformity assessment, nobody independent has looked. The
 * amber banner that used to carry that correction is gone by the owner's decision — this
 * is a hackathon project and the audience knows it holds no certificate — but the
 * correction itself is not: the section heading still says *certified against none*, and
 * the disclosure repeats it beside the frameworks it applies to. The page must never
 * *assert* certification, and `certified: false` stays on the wire.
 *
 * **No blended total.** Nothing here averages one group against another. The only
 * aggregates on screen are counts of things — controls enforced, controls mapped,
 * frameworks assessed — each one printed beside the thing it counts.
 *
 * **No certification marks, no accreditation seals, no logos.** Every framework and
 * every control is set in type. That is partly the legal line — reproducing a
 * certification body's mark you have not earned is the part that is an actual problem
 * rather than merely an embarrassing one — and partly that a page whose argument is
 * "here are the specific clauses" has no use for a logo shelf.
 *
 * **Every number is fetched.** `GET /platform/standards` counts the states over the real
 * control table on each request, and the numerator of every "N of M" is a count of the
 * rows this component is about to draw. Nothing in this file or in
 * {@link ./standardsSummary} writes a count or a control id down.
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
        eyebrow="Enforced"
        title="Controls we enforce end to end. Certified against none."
      >
        <Absence
          figure="The enforced controls"
          why="the public standards endpoint did not answer"
          needed="a reachable backend — every control named here is read from the compliance table on each request, never stored on this page"
        />
      </LandingSection>
    )
  }

  const { frameworks, coverage } = state.data
  const inFull = completeFrameworks(frameworks)
  const groups = resolveGroups(
    frameworks,
    undefined,
    new Set(inFull.map((claim) => claim.frameworkId)),
  )

  return (
    <LandingSection
      id="standards"
      eyebrow="Enforced"
      title={headline(inFull, coverage.enforced)}
    >
      {inFull.length > 0 && (
        <>
          <h3 className="text-[0.8125rem] font-medium uppercase tracking-[0.08em] text-muted-foreground">
            Every mapped control enforced
          </h3>
          <ul className="mt-3 min-w-0 divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface">
            {inFull.map((claim) => (
              <InFullRow key={claim.frameworkId} claim={claim} />
            ))}
          </ul>
        </>
      )}

      {groups.length === 0 ? (
        inFull.length === 0 && (
          <Absence
            figure="The enforced controls"
            why="none of the shortlisted frameworks are currently enforcing a control"
            needed="a shortlist whose framework ids match the compliance authority — see SHORTLIST in standardsSummary.ts"
          />
        )
      ) : (
        <>
          <h3
            className={cn(
              'text-[0.8125rem] font-medium uppercase tracking-[0.08em] text-muted-foreground',
              inFull.length > 0 && 'mt-8',
            )}
          >
            Individual controls, elsewhere
          </h3>
          <ul className="mt-3 grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
            {groups.map((group) => (
              <GroupCard key={group.title} group={group} />
            ))}
          </ul>
        </>
      )}

      <FullPicture
        shown={shownCount(groups) + inFull.reduce((sum, claim) => sum + claim.controls.length, 0)}
        enforced={coverage.enforced}
        frameworks={frameworks.length}
        mapped={coverage.total}
      />

      <Meaning className="mt-8" />

      <Receipt
        origin="GET /platform/standards"
        detail="public, unauthenticated · enforced controls only, read per request"
        className="mt-6"
      />
    </LandingSection>
  )
}

/**
 * The section heading, which is now also where the certification correction lives.
 *
 * **Three branches, and every one of them ends in the same four words.** The amber notice
 * that used to carry *not certification* above the grid is gone by the owner's decision;
 * with it gone, the heading is one of two places a reader meets the correction at all, and
 * a branch that dropped it would be a page asserting a certificate by omission. The test
 * checks every headline string in this file for those words rather than the one that
 * happens to render.
 *
 * **The single-framework branch names the framework rather than counting to one.** The
 * mark comes off the wire like everything else here, so this is not a framework written
 * into the page — it is the page printing the one the endpoint says is complete. "NIST AI
 * RMF enforced in full" is the claim; "1 framework enforced in full" is a sentence about
 * arithmetic.
 */
function headline(inFull: readonly ResolvedClaim[], enforced: number): string {
  if (inFull.length === 1) return `${inFull[0].mark} enforced in full. Certified against none.`
  if (inFull.length > 1) {
    return `${inFull.length} frameworks enforced in full. Certified against none.`
  }
  return `${enforced} controls we enforce end to end. Certified against none.`
}

/**
 * A framework whose every mapped control is enforced — one full-width row.
 *
 * **A row and not a card in the three-column grid below**, because this group has as many
 * members as the compliance table currently gives it: one today, three if the OWASP and
 * MITRE work lands. A three-column grid holding one card draws two empty cells, and an
 * empty cell on a compliance surface reads as something withheld. Rows grow and shrink
 * without a layout that has an opinion about the count.
 *
 * **The denominator is printed here too**, and `N of N` rather than a bare "complete" is
 * the honesty of the row: it says what the claim is measured against, and lets a reader
 * see that four functions of one framework and seventeen controls of another are not the
 * same size of claim.
 *
 * The controls are bare identifiers rather than id-and-title rows. A framework in full
 * contributes every control it has, and ten titled rows per framework is a wall of prose
 * where a checkable list is wanted — the titles are one request away at the endpoint this
 * section links to, and the id is what a reader looks up in the published standard.
 */
function InFullRow({ claim }: { claim: ResolvedClaim }): ReactElement {
  return (
    <li className="min-w-0 px-5 py-4">
      <p className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <span className="min-w-0 text-[0.9375rem] font-semibold tracking-[-0.01em] text-foreground">
          {claim.mark}
        </span>
        <span className="text-[0.8125rem] leading-6 text-muted-foreground">
          <span className="tabular font-mono font-semibold text-ok-ink">
            {claim.controls.length}
          </span>{' '}
          of <span className="tabular font-mono">{claim.mapped}</span> enforced
        </span>
      </p>
      <p className="mt-1 min-w-0 text-pretty font-mono text-[0.75rem] leading-6 text-muted-foreground">
        {claim.controls.map((control) => control.id).join(' · ')}
      </p>
    </li>
  )
}

/**
 * One group: what these controls have in common, how many of the framework they are,
 * and then the controls themselves.
 *
 * **The count sits above the list and carries its denominator**, because that pair is
 * the honesty of the card: `3 of 10 enforced` under `EU AI Act` says both that we hold
 * three clauses in full and that the framework has seven more. A group spanning two
 * frameworks prints one such row per framework rather than adding them up — `2 of 12`
 * and `2 of 9` do not sum to anything a reader could use.
 *
 * **There is no coverage strip here, and its absence is deliberate.** DESIGN.md §4 asks
 * a status surface to draw its state before describing it, and the old band did exactly
 * that. But every row on this card is in the *same* state, so a chart of them would be
 * one bar at 100% — the visual spelling of a claim this page refuses to make. What
 * varies across the card is which clauses, and a list is the honest drawing of that.
 */
function GroupCard({ group }: { group: ResolvedGroup }): ReactElement {
  return (
    <li className="min-w-0 bg-surface px-5 py-5">
      <h3 className="text-pretty text-[0.9375rem] font-semibold tracking-[-0.01em] text-foreground">
        {group.title}
      </h3>

      {/* Each framework owns its count *and* the rows that count describes. The first
          draft prefixed every row with its framework's mark instead, which printed
          "GDPR GDPR Art. 16" — the framework already names itself inside its own control
          ids, and no amount of string surgery on an identifier is worth doing. Nesting
          removes the repetition without editing anybody's clause number. */}
      {group.claims.map((claim, index) => (
        <section
          key={claim.frameworkId}
          className={cn('mt-4 min-w-0 border-t border-border pt-4', index > 0 && 'mt-5')}
        >
          <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-[0.8125rem] leading-6">
            <span className="min-w-0 truncate font-medium text-foreground">{claim.mark}</span>
            <span className="text-muted-foreground">
              <span className="tabular font-mono font-semibold text-ok-ink">
                {claim.controls.length}
              </span>{' '}
              of <span className="tabular font-mono">{claim.mapped}</span> enforced
            </span>
          </p>

          <ul className="mt-2 space-y-2">
            {claim.controls.map((control) => (
              <li
                key={control.id}
                className="min-w-0 text-[0.8125rem] leading-snug text-muted-foreground"
              >
                <span className="tabular font-mono text-[0.75rem] font-medium text-foreground">
                  {control.id}
                </span>{' '}
                <span className="text-pretty">{control.title}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </li>
  )
}

/**
 * The one line that keeps trimming the display from becoming a redaction.
 *
 * Twelve controls on screen out of twenty-nine enforced, out of a hundred and fourteen
 * mapped across twelve frameworks — an editorial choice on a compliance surface has to
 * name itself or it is a selection bias nobody declared. Every figure is off the wire,
 * and the link goes to the public endpoint that serves all of them: the same
 * unauthenticated JSON this band reads, so a reader can check what was left out against
 * what was not.
 */
function FullPicture({
  shown,
  enforced,
  frameworks,
  mapped,
}: {
  shown: number
  enforced: number
  frameworks: number
  mapped: number
}): ReactElement {
  return (
    <p className="mt-4 min-w-0 text-pretty text-[0.875rem] leading-relaxed text-muted-foreground">
      {shown} of {enforced} enforced controls shown.{' '}
      <a
        href={`${API_BASE}/platform/standards`}
        className="rounded-sm font-medium text-blue-700 underline decoration-blue-700/40 underline-offset-4 outline-none transition-colors duration-[--dur-fast] hover:decoration-blue-700 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        All {frameworks} frameworks and {mapped} mapped controls assessed in full
      </a>
      .
    </p>
  )
}

/**
 * The detail, in a disclosure — the page's rule for prose (DESIGN.md §4).
 *
 * Five lines, closed by default. A reader who wants the qualification opens it; a reader
 * who does not has already been told the one thing that matters, in the heading.
 */
function Meaning({ className }: { className?: string }): ReactElement {
  return (
    <details className={cn('group min-w-0', className)}>
      <summary className="inline-flex cursor-pointer touch-manipulation items-center rounded-md text-[0.875rem] font-medium text-blue-700 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background">
        What &ldquo;enforced&rdquo; means here
      </summary>
      <ul className="mt-3 max-w-[68ch] space-y-2 border-l-2 border-border pl-4 text-[0.875rem] leading-relaxed text-muted-foreground">
        <li>
          <span className="text-foreground">A mechanism and a test.</span> A control is
          enforced only when something in this repository implements it and a pytest node
          fails if that something is switched off. The suite resolves every reference
          against the real filesystem, route table and test files on each run.
        </li>
        <li>
          <span className="text-foreground">Alignment, not certification.</span> Aegis holds
          no ISO 27001 or ISO/IEC 42001 certificate, no SOC 2 report and no EU AI Act
          conformity assessment. Nobody independent has audited any of it.
        </li>
        <li>
          <span className="text-foreground">&ldquo;In full&rdquo; means every control this
          table maps.</span> The mapping is ours and the frameworks are not all mapped at the
          same grain, so the denominator is printed beside every claim — including the ones
          held in full.
        </li>
        <li>
          <span className="text-foreground">Only enforced controls are named.</span> Nothing
          partial and nothing unimplemented appears on this page, in any group or in the
          endpoint it reads.
        </li>
        <li>
          <span className="text-foreground">The full map is not public.</span> The
          control-by-control table, gaps included, lives in{' '}
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
