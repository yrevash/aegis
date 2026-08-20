import type { ReactElement } from 'react'

import { LandingScene } from './LandingScene'
import { LandingSection } from './LandingSection'

/**
 * The two refusals that are not the model's to make.
 *
 * Both exhibits answer the same jury question — *what stops this thing?* — and
 * neither answer is "the prompt asked it nicely". The gate is a risk tier on the
 * tool, checked before the call runs; the wall is a Postgres policy, checked
 * before the row is returned. A model that decided it was feeling confident today
 * changes neither.
 *
 * **The two exhibits are deliberately different shapes.** A scene beside a quote
 * on the left, a plain three-line specimen on the right. Two illustrated bands
 * alternating left and right is the marketing zigzag, and the second picture
 * would have been decoration: there is no honest scene for row-level security,
 * and `401 Error Unauthorized` — the one that looks close — draws the string
 * "401" into the artwork for a refusal that is not a 401. So the wall gets no
 * picture, which is the right amount of picture.
 *
 * The consent sentence is quoted verbatim from `summaryFor()` in
 * `components/approval/approvalActions.ts`; the three lines under the wall are
 * the state `aegis/src/aegis/governance/rls.py` installs and
 * `app/data/session.py` connects with.
 */

/** What the database is actually configured to do. Three facts, no SQL fragment. */
const WALL = [
  { term: 'Force row level security', detail: 'on every tenant-scoped table, so the owner has no exemption either' },
  { term: 'A serving role that owns nothing', detail: 'nosuperuser, nobypassrls — bypass is a property of the connection, not a rule code is trusted to follow' },
  { term: 'Row visibility bound to the tenant', detail: 'set on the session before the query runs, and audited against the catalog on every boot' },
] as const

export function GovernanceSection(): ReactElement {
  return (
    <LandingSection
      id="governance"
      eyebrow="What stops it"
      title="Two refusals the model does not get a vote on."
      lead="An agent platform is only as trustworthy as the things it cannot talk its way past. In Aegis those are a risk tier on the tool and a policy on the table — one asks a person, the other simply does not return the rows."
    >
      <div className="grid gap-x-12 gap-y-12 lg:grid-cols-2">
        {/* ── The gate ─────────────────────────────────────────────────────── */}
        <article className="min-w-0">
          <h3 className="font-display text-lg leading-6 font-semibold text-foreground">
            The gate
          </h3>
          <p className="mt-3 max-w-prose text-pretty text-[0.9375rem] leading-relaxed text-muted-foreground">
            A consequential tool call stops and waits for a person. It fires on the
            tool&rsquo;s declared risk tier — closing a customer request is high risk
            whatever the model thinks of its own answer — and a fan-out that proposes
            three writes asks about all three, not the one it happened to name.
          </p>

          <figure className="mt-6 rounded-lg border border-border bg-surface p-5 sm:p-6">
            <LandingScene name="consent" width={260} className="mx-auto" />
            <figcaption className="mt-5 border-t border-border pt-4">
              <p className="eyebrow mb-2">On screen, every time</p>
              <blockquote className="text-pretty text-[0.9375rem] leading-relaxed font-medium text-risk-ink">
                &ldquo;Approving runs all 3 of these calls. Rejecting ends the run without
                running any of them.&rdquo;
              </blockquote>
              <p className="mt-3 text-[0.8125rem] leading-relaxed text-muted-foreground">
                The sentence counts the calls it is asking about, and it stays on screen
                enclosing the two buttons rather than appearing once and scrolling away.
              </p>
            </figcaption>
          </figure>
        </article>

        {/* ── The wall ─────────────────────────────────────────────────────── */}
        <article className="min-w-0">
          <h3 className="font-display text-lg leading-6 font-semibold text-foreground">
            The wall
          </h3>
          <p className="mt-3 max-w-prose text-pretty text-[0.9375rem] leading-relaxed text-muted-foreground">
            One tenant physically cannot read another&rsquo;s rows. Ask a tenant about a
            document it does not hold and its run does not decline politely — there is
            nothing in the result set to decline about, so the answer states that it
            cannot source one.
          </p>

          <dl className="mt-6 divide-y divide-border border-y border-border">
            {WALL.map((fact) => (
              <div key={fact.term} className="py-4">
                <dt className="text-[0.9375rem] leading-6 font-semibold text-foreground">
                  {fact.term}
                </dt>
                <dd className="mt-1 text-pretty text-[0.8125rem] leading-relaxed text-muted-foreground">
                  {fact.detail}
                </dd>
              </div>
            ))}
          </dl>

          <p className="mt-5 max-w-prose text-pretty text-[0.8125rem] leading-relaxed text-muted-foreground">
            Isolation is the database&rsquo;s job here, not the prompt&rsquo;s. That is
            the difference between a tenant boundary you can test and one you can only
            hope for.
          </p>
        </article>
      </div>
    </LandingSection>
  )
}
