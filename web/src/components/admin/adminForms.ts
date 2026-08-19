/**
 * The rules the three admin write forms obey, with no React and no DOM in sight.
 *
 * Three things live here and nothing else does:
 *
 * 1. **Who may set what.** A tenant admin cannot raise their own tenant's cap
 *    (§7.16 row 2, `writable_by: platform`) and cannot name a tenant other than
 *    their own on a new user (`admin_create_user` answers 403). Both are server
 *    rules; these functions exist so the form can render them as *not yours* with
 *    a reason, instead of offering a control the backend will refuse.
 * 2. **Whether a draft is well-formed.** Each check mirrors a Pydantic constraint
 *    that is already in `backend/src/app/api/schemas.py` — `usd_cap > 0` and
 *    `<= 100000`, `password` at least 8 characters, `name` at most 255. The
 *    browser check is a courtesy that saves a round trip; the server stays the
 *    authority, and every one of these refusals is also enforced there.
 * 3. **What a draft becomes on the wire.** Blank optional fields become `null`
 *    rather than `""` or `0`, because an empty string and "no cap" mean very
 *    different things to a budget row.
 *
 * Nothing here reads the network or the clock, so `web/tests/admin/adminForms.test.mjs`
 * exercises it directly under `node --test`.
 */

import type { Budget, BudgetScope, BudgetWindow, FineRole } from '@/lib/api/types'
import type { Role } from '@/lib/stream'

/** The tier operating a form — the only thing that decides which fields exist. */
export type AdminTier = 'platform' | 'tenant' | 'none'

/**
 * Which tier a session's fine role puts it in.
 *
 * Fails closed: anything that is not one of the two admin tiers gets `none`, so a
 * build that meets a fine role it does not know about offers no write control
 * rather than every write control.
 */
export function adminTier(fineRole: FineRole | null | undefined): AdminTier {
  if (fineRole === 'platform_admin') return 'platform'
  if (fineRole === 'tenant_admin') return 'tenant'
  return 'none'
}

/** Field name → the one sentence saying what is wrong with it. */
export type FieldProblems = Readonly<Record<string, string>>

/** Whether a draft has nothing wrong with it. */
export function isWellFormed(problems: FieldProblems): boolean {
  return Object.keys(problems).length === 0
}

// ── Create tenant ────────────────────────────────────────────────────────────

/** The two accounting windows a cap can run over. */
export const WINDOWS: readonly BudgetWindow[] = ['day', 'month'] as const

/** The largest USD cap `TenantCreateRequest` accepts. */
export const MAX_USD_CAP = 100000

/** What the operator has typed into the create-tenant form. */
export interface TenantDraft {
  name: string
  /** Kept as typed text so an empty box stays distinguishable from a typed zero. */
  usdCap: string
  window: BudgetWindow
}

/**
 * What is wrong with a tenant draft, field by field.
 *
 * The spend cap is checked as hard as the name, because it is required for the
 * same reason the tenant is: an absent `budgets` row means uncapped, and a tenant
 * onboarded without one spends without limit until the invoice says so.
 */
export function checkTenantDraft(draft: TenantDraft): FieldProblems {
  const problems: Record<string, string> = {}

  const name = draft.name.trim()
  if (name === '') problems.name = 'Name the tenant.'
  else if (name.length > 255) problems.name = 'A tenant name is at most 255 characters.'

  const cap = numberOrNull(draft.usdCap)
  if (cap === null) problems.usdCap = 'Set a spend cap. A tenant without one is uncapped.'
  else if (cap <= 0) problems.usdCap = 'A cap of zero is not a cap. Set an amount above $0.'
  else if (cap > MAX_USD_CAP) problems.usdCap = `The largest cap is $${MAX_USD_CAP}.`

  return problems
}

/** The `POST /admin/tenants` body for a draft that passed {@link checkTenantDraft}. */
export function tenantBody(draft: TenantDraft): {
  name: string
  usd_cap: number
  window: BudgetWindow
} {
  return {
    name: draft.name.trim(),
    usd_cap: numberOrNull(draft.usdCap) ?? 0,
    window: draft.window,
  }
}

// ── Create user ──────────────────────────────────────────────────────────────

/** The four coarse roles `POST /admin/users` accepts. */
export const ASSIGNABLE_ROLES: readonly Role[] = ['admin', 'ai_team', 'devops', 'client'] as const

/** The shortest password the backend will hash. */
export const MIN_PASSWORD = 8

/** What the operator has typed into the create-user form. */
export interface UserDraft {
  username: string
  role: Role
  /** Tenant id as text; `''` means the platform scope (platform admin only). */
  tenantId: string
  email: string
  password: string
}

/**
 * Whether this tier gets to choose the new user's tenant at all.
 *
 * A tenant admin does not: the route pins them to `auth.tenant_id` and answers 403
 * for anything else. Offering the picker anyway would be a control whose only
 * possible use is to produce a refusal.
 */
export function canChooseTenant(tier: AdminTier): boolean {
  return tier === 'platform'
}

/** What is wrong with a user draft, field by field. */
export function checkUserDraft(draft: UserDraft, tier: AdminTier): FieldProblems {
  const problems: Record<string, string> = {}

  const username = draft.username.trim()
  if (username === '') problems.username = 'Give the user a sign-in name.'
  else if (username.length > 255) problems.username = 'A username is at most 255 characters.'
  else if (/\s/.test(username)) problems.username = 'A username cannot contain spaces.'

  if (!ASSIGNABLE_ROLES.includes(draft.role)) problems.role = 'Choose the portal this user lands in.'

  const email = draft.email.trim()
  if (email !== '' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    problems.email = 'That is not an email address. Leave it blank if you do not have one.'
  } else if (email.length > 320) {
    problems.email = 'An email address is at most 320 characters.'
  }

  // The password is required here even though the schema allows null, because the
  // acceptance test for this form is signing in as the user it just created, and a
  // user with no password cannot do that.
  if (draft.password === '') problems.password = 'Set a password so this user can sign in.'
  else if (draft.password.length < MIN_PASSWORD) {
    problems.password = `A password is at least ${MIN_PASSWORD} characters.`
  }

  if (canChooseTenant(tier)) {
    const tenant = draft.tenantId.trim()
    if (tenant !== '' && positiveIntOrNull(tenant) === null) {
      problems.tenantId = 'Pick a tenant, or leave it on the platform scope.'
    }
  }

  return problems
}

/**
 * The `POST /admin/users` body for a draft that passed {@link checkUserDraft}.
 *
 * A tenant admin sends no `tenant_id` at all: the server fills in their own, and a
 * client that guessed it would be asserting an isolation key it does not own.
 */
export function userBody(
  draft: UserDraft,
  tier: AdminTier,
): { username: string; role: Role; tenant_id: number | null; email: string | null; password: string } {
  const email = draft.email.trim()
  return {
    username: draft.username.trim(),
    role: draft.role,
    tenant_id: canChooseTenant(tier) ? positiveIntOrNull(draft.tenantId) : null,
    email: email === '' ? null : email,
    password: draft.password,
  }
}

// ── Set a budget ─────────────────────────────────────────────────────────────

/** What the operator has typed into the budget form; every cap is optional text. */
export interface BudgetDraft {
  scopeType: BudgetScope
  /** Id of the tenant or user the cap governs, as text. */
  scopeId: string
  window: BudgetWindow
  usdCap: string
  tokenCap: string
  rpm: string
  tpm: string
}

/** Whether a scope is this tier's to write, and why not when it is not. */
export interface ScopeVerdict {
  writable: boolean
  /** The reason the control is not theirs, or null when it is. */
  reason: string | null
}

/**
 * Whether this tier may write a cap at this scope.
 *
 * §7.16 row 2: a tenant's **own** cap is `writable_by: platform`. A tenant admin
 * who could raise it could raise it to anything, which is the same as having no
 * cap. Their user sub-caps are theirs, and the route agrees — it refuses a
 * tenant-scoped write whose `scope_id` is not their own tenant.
 */
export function scopeVerdict(scopeType: BudgetScope, tier: AdminTier): ScopeVerdict {
  if (tier === 'platform') return { writable: true, reason: null }
  if (tier === 'none') {
    return { writable: false, reason: 'Only an admin sets budgets.' }
  }
  if (scopeType === 'tenant') {
    return {
      writable: false,
      reason:
        'Aegis sets your tenant’s own cap — raising it is not yours to do. You set the caps on your users.',
    }
  }
  return { writable: true, reason: null }
}

/** The scopes this tier can actually write, for the picker. */
export function writableScopes(tier: AdminTier): readonly BudgetScope[] {
  return (['tenant', 'user'] as const).filter((s) => scopeVerdict(s, tier).writable)
}

/** What is wrong with a budget draft, field by field. */
export function checkBudgetDraft(draft: BudgetDraft, tier: AdminTier): FieldProblems {
  const problems: Record<string, string> = {}

  const verdict = scopeVerdict(draft.scopeType, tier)
  if (!verdict.writable && verdict.reason !== null) problems.scopeType = verdict.reason

  if (positiveIntOrNull(draft.scopeId) === null) {
    problems.scopeId =
      draft.scopeType === 'tenant' ? 'Choose the tenant this caps.' : 'Choose the user this caps.'
  }

  for (const [field, label] of [
    ['usdCap', 'spend cap'],
    ['tokenCap', 'token cap'],
    ['rpm', 'requests a minute'],
    ['tpm', 'tokens a minute'],
  ] as const) {
    const raw = draft[field]
    if (raw.trim() === '') continue
    const value = numberOrNull(raw)
    if (value === null) problems[field] = `That ${label} is not a number.`
    else if (value <= 0) problems[field] = `A ${label} of zero is not a cap. Leave it blank instead.`
  }

  // A row with no cap on it governs nothing, and the enforcer would read it as
  // uncapped on every dimension — the opposite of what somebody filling in this
  // form meant.
  if (!hasAnyCap(draft) && !('usdCap' in problems)) {
    problems.usdCap = 'Set at least one cap. A budget with none does not limit anything.'
  }

  return problems
}

/** Whether the draft carries at least one cap. */
export function hasAnyCap(draft: BudgetDraft): boolean {
  return [draft.usdCap, draft.tokenCap, draft.rpm, draft.tpm].some((v) => v.trim() !== '')
}

/** The `POST /admin/budgets` body for a draft that passed {@link checkBudgetDraft}. */
export function budgetBody(draft: BudgetDraft): Budget {
  return {
    scope_type: draft.scopeType,
    scope_id: positiveIntOrNull(draft.scopeId) ?? 0,
    window: draft.window,
    usd_cap: numberOrNull(draft.usdCap),
    token_cap: positiveIntOrNull(draft.tokenCap),
    rpm: positiveIntOrNull(draft.rpm),
    tpm: positiveIntOrNull(draft.tpm),
  }
}

/**
 * The warning for a user cap that sits above the tenant cap governing it.
 *
 * This used to say the backend would accept the figure and clamp it inward, which was
 * true and was the defect: the row saved, the screen read back $500, and $50 bound.
 * `upsert_budget` now refuses it outright with a 422 naming both figures, so the
 * warning says what will actually happen rather than describing a clamp.
 *
 * Deliberately a warning and not a block. The tenant cap this compares against is
 * whatever the last load returned, and it can be stale — another admin may have raised
 * it a second ago. The server is the authority on the refusal, and its sentence is
 * more precise than anything computable here; this only saves a round trip when the
 * browser already has enough to know.
 *
 * @param userUsd - The USD cap being set on the user, or null.
 * @param tenantUsd - The USD cap already governing that user's tenant, or null.
 * @returns One sentence of warning, or null when there is nothing to warn about.
 */
export function capRefusalWarning(userUsd: number | null, tenantUsd: number | null): string | null {
  if (userUsd === null || tenantUsd === null) return null
  if (userUsd <= tenantUsd) return null
  return (
    `This is above the tenant cap of $${tenantUsd}, so the server will refuse it. A user ` +
    `sub-cap can never exceed the cap on its own tenant. Lower it to $${tenantUsd}, or ` +
    `raise the tenant cap first.`
  )
}

// ── What the operator reads after a write ────────────────────────────────────

/**
 * The sentence to render when a write fails.
 *
 * `ApiError.message` is already the server's own `detail` when it sent one, which
 * for these three routes is the interesting half of the product: *A tenant-admin may
 * only create users in its own tenant.* Anything that is not an `Error` at all — a
 * thrown string, a rejected non-error — still has to say something, and "something
 * went wrong" is the one answer this phase exists to delete.
 */
export function refusalSentence(error: unknown): string {
  if (error instanceof Error && error.message.trim() !== '') return error.message
  return 'That write did not go through, and the backend gave no reason. Check it is up.'
}

// ── Parsing ──────────────────────────────────────────────────────────────────

/** A finite number from typed text, or null for blank / not-a-number. */
export function numberOrNull(raw: string): number | null {
  const text = raw.trim()
  if (text === '') return null
  const value = Number(text)
  return Number.isFinite(value) ? value : null
}

/** A whole number above zero from typed text, or null for anything else. */
export function positiveIntOrNull(raw: string): number | null {
  const value = numberOrNull(raw)
  if (value === null || !Number.isInteger(value) || value <= 0) return null
  return value
}
