/**
 * The standards band's selection and arithmetic, kept out of the component so it can be
 * tested.
 *
 * Everything here is a function of the payload `GET /v1/platform/standards` sent. **No
 * number is written down in this file, and none may be**: the whole reason the endpoint
 * exists is that the landing page is public while `GET /v1/compliance` is
 * platform-staff-only, and a count typed into a marketing section is a count nobody
 * re-derives when a control changes state. `web/tests/landing/standardsBand.test.mjs`
 * asserts that this module and the band component contain no control totals.
 *
 * **No control identifier is written down here either**, and that is the newer rule.
 * The band names individual controls now — "EU AI Act Art. 14 — Human oversight" rather
 * than a bare "EU AI Act" wordmark — and a list of ids pasted into this file would be a
 * list that keeps rendering after a control stops being enforced. So the shortlist
 * names *frameworks*, and the controls it prints are whichever ones the endpoint is
 * still serving as enforced at the moment of the read.
 *
 * Pure and framework-free — no React, no icons, no DOM — so the resolution runs under
 * `node --test` directly.
 */

import type { EnforcedControl, FrameworkSummary } from '@/lib/api/standards'

/**
 * ## The shortlist — the one place to edit
 *
 * ### Why the unit is a control and not a framework
 *
 * The band used to draw twelve framework wordmarks with a four-state strip under each.
 * That surface cannot answer the only question a reviewer actually asks — *which of
 * these are you compliant with?* — because **no framework here is fully enforced**.
 * Twelve marks, or three, invite a reader to hear "we do ISO 27001", and the honest
 * answer is that we enforce seven of its seventeen mapped controls.
 *
 * So the unit is the control. `enforced` in the compliance table means a real mechanism
 * *and* a test that fails when the mechanism is switched off; a control in any other
 * state is not served to this page at all. Every line the band prints is therefore a
 * claim held in full, named by the framework's own identifier so a reader can look it
 * up in the published standard and hold us to it.
 *
 * ### The three groups, and why each
 *
 * - **`eu-ai-act`** — Art. 12 record-keeping, Art. 13 transparency to deployers, Art. 14
 *   human oversight. The trustworthy-AI trio, and it is complete: the three enforced EU
 *   AI Act controls *are* those three. Aegis's append-only audit trail, its provenance
 *   on every answer and its approval gate are what satisfy them.
 * - **`owasp-llm`** — the five named LLM risks this platform is built against: prompt
 *   injection, sensitive information disclosure, improper output handling, excessive
 *   agency, system prompt leakage. Five of the ten, and the card says *five of ten* on
 *   its face, because the other five are partial and this page must not imply otherwise.
 * - **`dpdp` + `privacy`** — the data-principal rights, paired deliberately across
 *   jurisdictions: India's DPDP s.12 correction and erasure beside GDPR Art. 16 and Art.
 *   17. One mechanism — memory correction and hard delete, audited — answering the home
 *   regulator and the European one at once. DPDP s.12 is enforced in full.
 *
 * A strong alternative, if one of the above ever stops laying out: `mitre-atlas`, which
 * is five of five on the LLM techniques (direct and indirect injection, jailbreak,
 * meta-prompt extraction, data leakage) and reads as one complete story about the
 * attacks that apply to an LLM platform.
 *
 * ### What "enforced" does and does not mean
 *
 * It means: a mechanism in this repository, and a test that fails if it is removed. It
 * does **not** mean audited, certified, or attested by anybody — Aegis holds no
 * certificate of any kind, and the band says so three times. It also says nothing about
 * the rest of the framework: a group that shows three of ten controls is a claim about
 * three controls, which is why the denominator is printed beside every count.
 *
 * ### Changing it
 *
 * Reorder the groups, retitle one, or swap a framework id — that is the whole edit. The
 * ids are the endpoint's own, and a framework it stops serving simply drops out. Nothing
 * is hidden by trimming: all twelve frameworks and all 114 mapped controls stay in
 * `docs/compliance/README.md`, on the devops Compliance screen, and on the public
 * endpoint the band links to.
 */
export interface ControlGroup {
  /** The editorial line: what these controls have in common, in a reader's words. */
  title: string
  /**
   * The frameworks whose enforced controls this group prints, in printing order.
   *
   * Ids only. **Never control ids** — see the module docstring: the controls are
   * whatever the endpoint still calls enforced, so this list cannot outlive the table.
   */
  frameworks: readonly string[]
}

/** The three groups the public band draws. One line each to change. */
export const SHORTLIST: readonly ControlGroup[] = [
  // "human" is dropped from the title and not from the claim: Art. 14's own words are
  // printed two lines below it, and the third line of a card title is a wrapped heading
  // that knocks this card's rule out of line with its neighbours'.
  { title: 'Record-keeping, transparency, oversight', frameworks: ['eu-ai-act'] },
  { title: 'The LLM attack surface', frameworks: ['owasp-llm'] },
  { title: 'Data-principal rights, India and the EU', frameworks: ['dpdp', 'privacy'] },
]

/** One framework's contribution to a group, with both halves of its count. */
export interface ResolvedClaim {
  frameworkId: string
  /** The short display mark, as the endpoint serves it. */
  mark: string
  /** The controls this framework currently enforces, id and title, off the wire. */
  controls: EnforcedControl[]
  /** How many controls of this framework are mapped in total — the denominator. */
  mapped: number
}

/** One group, resolved against a live payload. */
export interface ResolvedGroup {
  title: string
  claims: ResolvedClaim[]
  /** How many controls this group prints. Derived by counting them. */
  count: number
}

/**
 * Resolve the shortlist against what the endpoint actually served.
 *
 * The numerator of every "N of M" is `controls.length` — a count of the rows about to
 * be drawn, not a figure carried alongside them, so the two cannot disagree. The
 * denominator is the framework's own mapped total, likewise off the wire.
 *
 * A framework the endpoint no longer serves, or one that currently enforces nothing,
 * contributes no claim; a group left with no claims is dropped entirely rather than
 * drawn as an empty card. That is the failure mode that matters here: a control losing
 * its enforced state must make the band *smaller*, never make it lie.
 */
export function resolveGroups(
  frameworks: readonly FrameworkSummary[],
  groups: readonly ControlGroup[] = SHORTLIST,
): ResolvedGroup[] {
  const served = new Map(frameworks.map((framework) => [framework.id, framework]))

  return groups.flatMap((group) => {
    const claims = group.frameworks.flatMap((id) => {
      const framework = served.get(id)
      if (framework === undefined || framework.enforced_controls.length === 0) return []
      return [
        {
          frameworkId: framework.id,
          mark: framework.mark,
          controls: [...framework.enforced_controls],
          mapped: framework.coverage.total,
        },
      ]
    })
    if (claims.length === 0) return []
    return [
      {
        title: group.title,
        claims,
        count: claims.reduce((sum, claim) => sum + claim.controls.length, 0),
      },
    ]
  })
}

/** How many controls the band is about to print, across every resolved group. */
export function shownCount(groups: readonly ResolvedGroup[]): number {
  return groups.reduce((sum, group) => sum + group.count, 0)
}
