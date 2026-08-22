import { Check, ShieldAlert, X } from 'lucide-react'
import type { ReactElement } from 'react'

import { LandingSection } from './LandingSection'

/**
 * The two refusals that are not the model's to make, drawn rather than described.
 *
 * **This section used to be twelve paragraphs.** A three-line standfirst, a
 * four-line paragraph under each of two headings, three stacked facts each
 * carrying a full explanatory sentence, a closing two-line summary, and a quote
 * block glossed by another paragraph — roughly 300 words to convey two ideas: *a
 * risky action stops and asks a person*, and *one tenant cannot read another's
 * rows*. Both are states, and DESIGN.md §4 is explicit that a state is drawn
 * before it is described.
 *
 * So the gate is a branch — one call, two tiers, and the two outcomes the second
 * tier can have — and the wall is a two-row truth table over one query. Each
 * keeps exactly one sentence, as a `figcaption`, and it is the sentence the
 * picture cannot say.
 *
 * **The consent quote went with the prose, and that is not a loss.** It is
 * rendered verbatim by {@link RunChain} at the gate link, from the same
 * `summaryFor()` string; a page should not quote its own product twice on one
 * scroll. The outcome pair below says the same thing in the generalised form,
 * without pasting a count no run on this page produced.
 *
 * The mono strings are the state `aegis/src/aegis/governance/rls.py` installs:
 * `FORCE ROW LEVEL SECURITY` per tenant-scoped table, a `NOSUPERUSER
 * NOBYPASSRLS` serving role, and the `app.tenant_id` GUC bound per request by
 * `set_config` and re-audited against the catalog on every boot.
 */

/** How the database is configured. Term in the system's voice, gloss in ours. */
const WALL = [
  { term: 'FORCE ROW LEVEL SECURITY', gloss: 'on every tenant-scoped table — the owner is not exempt' },
  { term: 'NOSUPERUSER NOBYPASSRLS', gloss: 'the serving role cannot turn it off' },
  { term: "set_config('app.tenant_id')", gloss: 'bound per request, re-audited against the catalog at boot' },
] as const

/** A framed exhibit: a heading, the drawing, and the one sentence it cannot draw. */
function Exhibit({
  title,
  caption,
  children,
}: {
  title: string
  caption: string
  children: ReactElement
}): ReactElement {
  return (
    <article className="min-w-0">
      <h3 className="font-display text-lg leading-6 font-semibold text-foreground">{title}</h3>
      <figure className="mt-4 rounded-lg border border-border bg-surface p-4 sm:p-5">
        {children}
        <figcaption className="mt-4 border-t border-border pt-3 text-pretty text-[0.8125rem] leading-relaxed text-muted-foreground">
          {caption}
        </figcaption>
      </figure>
    </article>
  )
}

export function GovernanceSection(): ReactElement {
  return (
    <LandingSection
      id="governance"
      eyebrow="What stops it"
      title="Two refusals the model does not get a vote on."
      lead="One asks a person. The other simply does not return the rows."
    >
      <div className="grid gap-x-12 gap-y-10 lg:grid-cols-2">
        {/* ── The gate: one call, two tiers, two outcomes ───────────────────── */}
        <Exhibit
          title="The gate"
          caption="Fires on the tool's declared risk tier, never on model confidence."
        >
          <div>
            <p className="eyebrow">One tool call</p>

            <div className="mt-3 flex min-w-0 items-center gap-3 rounded-md bg-surface-2 px-3 py-2.5">
              <span aria-hidden className="size-2 shrink-0 rounded-full bg-blue-600" />
              <code className="tabular shrink-0 font-mono text-[0.75rem] text-blue-700">
                risk: low
              </code>
              <span className="ml-auto min-w-0 text-right text-sm text-foreground">runs</span>
            </div>

            <div className="mt-2 min-w-0 rounded-md border border-risk bg-risk/15 px-3 py-2.5">
              <div className="flex min-w-0 items-center gap-3">
                <ShieldAlert aria-hidden className="size-4 shrink-0 text-risk-ink" strokeWidth={2} />
                <code className="tabular shrink-0 font-mono text-[0.75rem] text-risk-ink">
                  risk: high
                </code>
                <span className="ml-auto min-w-0 text-right text-sm font-medium text-risk-ink">
                  stops for a person
                </span>
              </div>

              <ul className="mt-3 grid gap-2 border-t border-risk pt-3">
                <li className="flex min-w-0 items-start gap-2 text-[0.8125rem] leading-5 text-ok-ink">
                  <Check aria-hidden className="mt-px size-4 shrink-0" strokeWidth={2.5} />
                  <span className="min-w-0">Approve — every call in the batch runs</span>
                </li>
                <li className="flex min-w-0 items-start gap-2 text-[0.8125rem] leading-5 text-block-ink">
                  <X aria-hidden className="mt-px size-4 shrink-0" strokeWidth={2.5} />
                  <span className="min-w-0">Reject — the run ends, none of them run</span>
                </li>
              </ul>
            </div>
          </div>
        </Exhibit>

        {/* ── The wall: one query over a shared table ───────────────────────── */}
        <Exhibit
          title="The wall"
          caption="Isolation is the database's job here, not the prompt's."
        >
          <div>
            <p className="eyebrow">One query, one shared table</p>

            <ul className="mt-3 grid gap-2">
              <li className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-surface-2 px-3 py-2.5">
                <Check aria-hidden className="size-4 shrink-0 text-ok-ink" strokeWidth={2.5} />
                <code className="tabular min-w-0 font-mono text-[0.75rem] break-words text-blue-700">
                  tenant_id = this session
                </code>
                <span className="ml-auto text-sm text-foreground">returned</span>
              </li>
              <li className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-block bg-block/15 px-3 py-2.5">
                <X aria-hidden className="size-4 shrink-0 text-block-ink" strokeWidth={2.5} />
                <code className="tabular min-w-0 font-mono text-[0.75rem] break-words text-block-ink">
                  tenant_id = anything else
                </code>
                <span className="ml-auto text-sm font-medium text-block-ink">
                  never returned
                </span>
              </li>
            </ul>

            <dl className="mt-4 divide-y divide-border border-t border-border">
              {WALL.map((fact) => (
                <div key={fact.term} className="min-w-0 py-2.5">
                  <dt className="tabular font-mono text-[0.72rem] break-words text-blue-700">
                    {fact.term}
                  </dt>
                  <dd className="mt-0.5 text-pretty text-[0.8125rem] leading-5 text-muted-foreground">
                    {fact.gloss}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </Exhibit>
      </div>
    </LandingSection>
  )
}
