/**
 * Pure, recharts-free logic for the Roles & Access surface — the role catalog,
 * per-role roll-ups, and the self-demote safety guard. Side-effect free and free
 * of any React / chart import so every derivation stays unit-testable.
 *
 * `AdminUser.role` is a free-form string on the wire. The live backend stores the
 * four portal roles verbatim, but the in-repo mock still carries legacy RBAC
 * labels (`platform_admin` / `tenant_admin` / `member`). {@link normalizeRole}
 * folds both worlds onto the canonical portal {@link Role} so counts, chips and
 * the guard all agree — the one lossy edge (`member` → `client`) is called out
 * honestly at the mapping.
 */

import type { AdminUser } from '@/lib/api/types'
import type { Role } from '@/lib/stream'

/** The four assignable portal roles, in privilege order (admin first). */
export const PORTAL_ROLES: readonly Role[] = ['admin', 'ai_team', 'devops', 'client'] as const

/** One row of the role legend — the label, its badge tone, and what it can see. */
export interface RoleMeta {
  role: Role
  /** Human label for the chip / dropdown. */
  label: string
  /** Badge variant token reused from the shared trust taxonomy. */
  chip: 'ml' | 'agent' | 'graph' | 'risk' | 'secondary'
  /** One-line "what this role can see" — doubles as the legend and the "why". */
  sees: string
}

/**
 * The canonical catalog. `sees` is the delegation contract each role grants:
 * admin = everything + delegation; ai_team = build/tune the agent; devops =
 * stack/patches/ops/audit; client = outcomes/savings/risk-map (read-only).
 */
export const ROLE_CATALOG: Record<Role, RoleMeta> = {
  admin: {
    role: 'admin',
    label: 'Admin',
    chip: 'risk',
    sees: 'Everything — plus the power to delegate: grant, revoke and scope every other role.',
  },
  ai_team: {
    role: 'ai_team',
    label: 'AI team',
    chip: 'ml',
    sees: 'Builds and tunes the agent — console, orchestration, memory and the loop.',
  },
  devops: {
    role: 'devops',
    label: 'DevOps',
    chip: 'agent',
    sees: 'Runs the stack — versions, patches, ops and the audit trail.',
  },
  client: {
    role: 'client',
    label: 'Client',
    chip: 'graph',
    sees: 'Outcomes only — value, savings and the risk map, read-only.',
  },
}

/**
 * Privilege ranking; higher = more access. Admin is strictly the top so the guard
 * can reason about "demotion out of admin". The three focused roles sit below it
 * and are treated as peers — moving between ai_team / devops / client is a lateral
 * scope change, never a lockout risk, so it is never guarded.
 */
export const ROLE_RANK: Record<Role, number> = {
  admin: 3,
  ai_team: 2,
  devops: 2,
  client: 1,
}

/**
 * Fold any wire role string onto the canonical portal {@link Role}. Known legacy
 * mock labels map to their portal equivalent; the exact portal strings pass
 * through unchanged.
 *
 * Honest limitation: the mock collapses `ai_team` / `devops` / `client` all to
 * `member` on write, so `member` can only be recovered as `client` — the least
 * privileged bucket. The live backend round-trips the exact portal role, so this
 * lossiness is a mock artifact, not a product one.
 */
export function normalizeRole(raw: string): Role {
  switch (raw) {
    case 'admin':
    case 'platform_admin':
    case 'tenant_admin':
      return 'admin'
    case 'ai_team':
      return 'ai_team'
    case 'devops':
      return 'devops'
    case 'client':
    case 'member':
    default:
      return 'client'
  }
}

/** Whether a wire role resolves to an admin-tier account. */
export function isAdminRole(raw: string): boolean {
  return normalizeRole(raw) === 'admin'
}

/** How many of these users hold an admin-tier role. */
export function adminCount(users: AdminUser[]): number {
  return users.reduce((n, u) => (isAdminRole(u.role) ? n + 1 : n), 0)
}

/** Per portal-role head-count over a user set (every bucket present, even at 0). */
export function perRoleCounts(users: AdminUser[]): Record<Role, number> {
  const counts: Record<Role, number> = { admin: 0, ai_team: 0, devops: 0, client: 0 }
  for (const u of users) counts[normalizeRole(u.role)] += 1
  return counts
}

/** Whether moving `from` → `to` strips a user of admin access. */
export function isDemotionFromAdmin(from: Role, to: Role): boolean {
  return from === 'admin' && to !== 'admin'
}

/** Everything the guard needs to rule on one dropdown option. */
export interface GuardContext {
  /** The user row whose role would change. */
  user: AdminUser
  /** The role the option would assign. */
  target: Role
  /** The signed-in admin's username, or null when it cannot be identified. */
  currentUsername: string | null
  /** All users currently in scope — used for the only-admin fallback. */
  users: AdminUser[]
}

/** The guard's verdict for one option: locked or not, and the honest reason. */
export interface GuardResult {
  disabled: boolean
  /** Why the option is locked, or null when it is assignable. */
  reason: string | null
}

/**
 * The self-lockout safety guard — a faithful mirror of the backend rule. An
 * option that would demote an admin out of admin is locked when either:
 *
 *  1. It is the signed-in admin's OWN account (identified by username) — an
 *     accidental self-demotion is exactly the lockout to prevent; or
 *  2. That account is the ONLY admin-tier user in scope — demoting it would leave
 *     the tenant with no one able to delegate.
 *
 * Rule (2) is the honest fallback when the current user cannot be identified:
 * without a session we cannot know whose row this is, so we protect the last
 * admin for everyone. It can still under-protect across tenant scopes the client
 * has not loaded — the authoritative check lives on the backend; this is the
 * front-of-house guard rail.
 *
 * Any move that is not a demotion out of admin (a promotion, a same-tier change,
 * or a lateral move between the focused roles) is always assignable.
 */
export function roleOptionGuard(ctx: GuardContext): GuardResult {
  const current = normalizeRole(ctx.user.role)
  if (!isDemotionFromAdmin(current, ctx.target)) return { disabled: false, reason: null }

  if (ctx.currentUsername != null && ctx.user.username === ctx.currentUsername) {
    return {
      disabled: true,
      reason: 'You cannot remove your own admin access — this prevents an accidental self-lockout.',
    }
  }

  if (adminCount(ctx.users) <= 1) {
    return {
      disabled: true,
      reason: 'This is the only admin in scope — demoting it would leave no one able to delegate.',
    }
  }

  return { disabled: false, reason: null }
}
