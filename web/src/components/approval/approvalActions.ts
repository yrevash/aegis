/**
 * What one approval actually authorises — read once, here.
 *
 * `ApprovalRequired` carries two things that are easy to confuse and mean different
 * amounts of consent. `action`/`args`/`risk` are the **representative**: the single
 * highest-risk call, kept on the event so a client written before Phase 5 still shows
 * something true. `actions` is the **whole list**: every call approving will run.
 *
 * A fan-out can have three sub-agents each propose a consequential write in one turn.
 * The backend was fixed to enumerate all three (commit `7285bd6`); a dialog that renders
 * `action` alone then asks a person to authorise three writes while naming one, which is
 * the same defect one layer up. So the list is what the card renders, and this module is
 * the one place that decides what the list is.
 *
 * `actions` is `list[dict]` on the wire, so entries are checked rather than trusted:
 * a member with no tool name is dropped, and an approval whose list is empty or entirely
 * unreadable falls back to the representative rather than rendering a gate that shows
 * nothing at all.
 *
 * Pure and framework-free, so the consent rule is testable without a renderer.
 */

import type { ApprovalAction, ApprovalRequired, RiskLevel } from '@/lib/stream'

/** The risk words the wire uses. Anything else is not a risk level. */
const RISKS: readonly string[] = ['low', 'medium', 'high']

const asString = (value: unknown): string => (typeof value === 'string' ? value : '')

const asRisk = (value: unknown, fallback: RiskLevel): RiskLevel =>
  typeof value === 'string' && RISKS.includes(value) ? (value as RiskLevel) : fallback

const asArgs = (value: unknown): Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}

/** One call this approval authorises, checked. */
export interface AuthorisedAction {
  /** The call id, or `''` when the wire carried none. */
  id: string
  /** The tool this call runs. */
  name: string
  args: Record<string, unknown>
  risk: RiskLevel
}

/** Everything the approval gate renders. */
export interface ApprovalView {
  /** Every call approving will run, in the order the backend ranked them. */
  actions: AuthorisedAction[]
  /** True when approving runs more than one call — the case `action` alone hides. */
  many: boolean
  /**
   * True when the list was rebuilt from the representative because the wire carried
   * none. The gate still shows a call rather than nothing, and it is the one call the
   * event named.
   */
  representativeOnly: boolean
  /** One sentence naming exactly what the Approve button does. */
  summary: string
}

/** Check one wire member, or `null` when it names no call. */
function readAction(raw: ApprovalAction, fallbackRisk: RiskLevel): AuthorisedAction | null {
  const name = asString(raw.name)
  if (name === '') return null
  return { id: asString(raw.id), name, args: asArgs(raw.args), risk: asRisk(raw.risk, fallbackRisk) }
}

/** The sentence under the list: what approving does, counted. */
function summaryFor(count: number): string {
  if (count === 0) return 'Approving resumes the run.'
  if (count === 1) return 'Approving runs this one call.'
  return `Approving runs all ${count} of these calls.`
}

/**
 * Read an `approval_required` event into what the gate should show.
 *
 * @param approval - The live gate event.
 * @returns The calls approving authorises, and the sentence that counts them.
 */
export function readApproval(approval: ApprovalRequired): ApprovalView {
  const listed = (approval.actions ?? [])
    .map((raw) => readAction(raw, approval.risk))
    .filter((entry): entry is AuthorisedAction => entry !== null)

  const representativeOnly = listed.length === 0
  const representative: AuthorisedAction[] =
    approval.action === ''
      ? []
      : [
          {
            id: approval.approval_id,
            name: approval.action,
            args: approval.args,
            risk: approval.risk,
          },
        ]
  const actions = representativeOnly ? representative : listed

  return {
    actions,
    many: actions.length > 1,
    representativeOnly,
    summary: summaryFor(actions.length),
  }
}
