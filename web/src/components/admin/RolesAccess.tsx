'use client'

import { Check, KeyRound, Loader2, Lock } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { assignUserRole, getUsers } from '@/lib/api/client'
import { Badge } from '@/components/primitives/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { useAuth } from '@/lib/auth/AuthContext'
import { adminScopeCaption } from '@/lib/auth/tier'
import { cn } from '@/lib/utils'
import type { AdminUser } from '@/lib/api/types'
import type { Role } from '@/lib/stream'

import { AdminControls } from './AdminControls'
import { SeatsPanel } from './SeatsPanel'
import { adminTier } from './adminForms'
import {
  PORTAL_ROLES,
  ROLE_CATALOG,
  normalizeRole,
  perRoleCounts,
  roleOptionGuard,
} from './roleCatalog'

/**
 * Admin — Roles & Access.
 *
 * The delegation surface for the enterprise admin: a live user roster
 * (`GET /admin/users`) with per-row portal-role assignment
 * (`POST /admin/users/{id}/role`). Assignment is optimistic with an honest
 * rollback on failure, and a self-lockout guard mirrors the backend rule so an
 * admin can never accidentally strip the last admin access. The pure catalog /
 * counts / guard live in `roleCatalog.ts` and are unit-tested.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: AdminUser[] }

export function RolesAccess({
  token,
  reloadKey = 0,
}: {
  token: string | null
  /**
   * Bumped by whoever writes a user, to refetch the roster.
   *
   * A create form that posts and then leaves the roster below it unchanged reads as
   * a broken control — the operator's only evidence that the write landed is a
   * sentence, and a console that will not show you what you just did is exactly the
   * defect this phase exists to remove.
   */
  reloadKey?: number
}): ReactElement {
  // The signed-in admin, so the self-lockout guard knows *who* is acting. With a
  // constant `null` here the guard degraded to protecting the last admin in
  // scope for everyone; the real username makes it precise.
  const { session } = useAuth()
  const currentUsername: string | null = session?.username ?? null

  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  /** Ids with an in-flight assignment (select disabled while saving). */
  const [saving, setSaving] = useState<Set<number>>(new Set())
  /** Ids showing a transient "updated" confirm after a successful save. */
  const [flash, setFlash] = useState<Set<number>>(new Set())

  useEffect(() => {
    let alive = true
    setLoad({ status: 'loading' })
    getUsers(token)
      .then((res) => alive && setLoad({ status: 'ready', rows: res.rows }))
      .catch(
        (e: unknown) =>
          alive &&
          setLoad({
            status: 'error',
            message: e instanceof Error ? e.message : 'Failed to load users',
          }),
      )
    return () => {
      alive = false
    }
  }, [token, reloadKey])

  const reassign = (user: AdminUser, target: Role): void => {
    if (load.status !== 'ready') return
    if (normalizeRole(user.role) === target) return // no-op — same role

    // Guard again at the point of action (defence in depth; options are already
    // disabled). Never let an admin strip the last admin access.
    const guard = roleOptionGuard({ user, target, currentUsername, users: load.rows })
    if (guard.disabled) return

    const previous = load.rows
    // Optimistic write. We stamp the target portal role directly; the server
    // response replaces it with its stored label, which is the authority.
    setLoad({
      status: 'ready',
      rows: previous.map((u) => (u.id === user.id ? { ...u, role: target } : u)),
    })
    setSaving((s) => new Set(s).add(user.id))

    void assignUserRole(user.id, target, token).then(
      (updated) => {
        setSaving((s) => {
          const next = new Set(s)
          next.delete(user.id)
          return next
        })
        setLoad((prev) =>
          prev.status === 'ready'
            ? { status: 'ready', rows: prev.rows.map((u) => (u.id === updated.id ? updated : u)) }
            : prev,
        )
        setFlash((f) => new Set(f).add(user.id))
        window.setTimeout(
          () =>
            setFlash((f) => {
              const next = new Set(f)
              next.delete(user.id)
              return next
            }),
          2500,
        )
      },
      () => {
        // Honest rollback — restore the roster exactly as it was.
        setSaving((s) => {
          const next = new Set(s)
          next.delete(user.id)
          return next
        })
        setLoad({ status: 'ready', rows: previous })
      },
    )
  }

  const counts = load.status === 'ready' ? perRoleCounts(load.rows) : null
  const total = load.status === 'ready' ? load.rows.length : null

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
        <KeyRound className="size-4 text-agent" />
        <CardTitle>Roles &amp; Access</CardTitle>
        <Badge variant="secondary">RBAC</Badge>
        {/* `GET /admin/users` is tenant-scoped server-side (`_scope_tenant`), so a
            tenant admin's roster is a subset. The caption is driven by the session's
            fine tier so the page never presents one tenant's users as everyone. */}
        <Badge variant="outline">{adminScopeCaption(session)}</Badge>
        <InfoTip label="Why this matters">
          Why this matters: in an enterprise the admin&apos;s real power is delegation. Each team should
          see only its own portal — build, ops, or outcomes — never the whole platform. This is where
          that least-privilege line is drawn, granted, and revoked.
        </InfoTip>
      </CardHeader>

      <CardContent>
        {/* Header stat band — total + per-role head-count. */}
        {counts && total != null && (
          <div className="mb-5 flex flex-wrap items-stretch gap-2">
            <StatCell label="Users" value={total} tone="neutral" />
            {PORTAL_ROLES.map((r) => (
              <StatCell key={r} label={ROLE_CATALOG[r].label} value={counts[r]} chip={r} />
            ))}
          </div>
        )}

        {load.status === 'loading' && (
          <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading users…
          </div>
        )}

        {load.status === 'error' && (
          <div className="py-10 text-sm text-destructive">Could not load users. {load.message}</div>
        )}

        {load.status === 'ready' && load.rows.length === 0 && (
          <p className="py-10 text-sm text-muted-foreground">No users to manage.</p>
        )}

        {load.status === 'ready' && load.rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-sm">
              <thead>
                <tr className="border-b border-border/70 text-left">
                  <th className="eyebrow pb-2 font-normal">User</th>
                  <th className="eyebrow pb-2 font-normal">Email · tenant</th>
                  <th className="eyebrow pb-2 font-normal">Current role</th>
                  <th className="eyebrow pb-2 font-normal">Assign portal</th>
                </tr>
              </thead>
              <tbody>
                {load.rows.map((u) => {
                  const current = normalizeRole(u.role)
                  const isSelf = currentUsername != null && u.username === currentUsername
                  // First locked reason for this row (all demotions share it).
                  const lock = PORTAL_ROLES.map((target) =>
                    roleOptionGuard({ user: u, target, currentUsername, users: load.rows }),
                  ).find((g) => g.disabled)
                  const isSaving = saving.has(u.id)

                  return (
                    <tr key={u.id} className="border-b border-border/40 last:border-0">
                      <td className="py-2.5 font-medium text-foreground">
                        {u.username}
                        {isSelf && (
                          <span className="ml-1.5 align-middle text-[0.6rem] uppercase tracking-wide text-muted-foreground">
                            you
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 font-mono text-[0.72rem] text-muted-foreground">
                        {u.email ?? '—'}
                        {u.tenant_id != null && (
                          <span className="text-muted-foreground/70"> · t#{u.tenant_id}</span>
                        )}
                      </td>
                      <td className="py-2.5">
                        <div className="flex items-center gap-1.5">
                          <Badge variant={ROLE_CATALOG[current].chip}>{ROLE_CATALOG[current].label}</Badge>
                          <span className="font-mono text-[0.62rem] text-muted-foreground/60">{u.role}</span>
                        </div>
                      </td>
                      <td className="py-2.5">
                        <div className="flex items-center gap-2">
                          <select
                            value={current}
                            disabled={isSaving}
                            onChange={(e) => reassign(u, e.target.value as Role)}
                            className={cn(
                              'h-7 rounded-md border border-border bg-card px-2 font-mono text-[0.68rem] text-foreground',
                              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                              isSaving && 'opacity-60',
                            )}
                            aria-label={`Assign portal role for ${u.username}`}
                          >
                            {PORTAL_ROLES.map((r) => {
                              const g = roleOptionGuard({
                                user: u,
                                target: r,
                                currentUsername,
                                users: load.rows,
                              })
                              return (
                                <option key={r} value={r} disabled={g.disabled}>
                                  {ROLE_CATALOG[r].label}
                                  {g.disabled ? ' — locked' : ''}
                                </option>
                              )
                            })}
                          </select>

                          {isSaving && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
                          {!isSaving && flash.has(u.id) && (
                            <span className="flex items-center gap-1 text-[0.68rem] text-ok-ink">
                              <Check className="size-3.5" /> updated
                            </span>
                          )}
                          {!isSaving && !flash.has(u.id) && lock && (
                            <InfoTip label="Why this role is locked" className="text-risk-ink/80">
                              <span className="flex items-start gap-1.5">
                                <Lock className="mt-px size-3 shrink-0" aria-hidden /> {lock.reason}
                              </span>
                            </InfoTip>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/** One tile in the header stat band. */
function StatCell({
  label,
  value,
  chip,
  tone,
}: {
  label: string
  value: number
  chip?: Role
  tone?: 'neutral'
}): ReactElement {
  return (
    <div
      className={cn(
        'flex min-w-[5.5rem] flex-col gap-0.5 rounded-lg border border-border/60 bg-card px-3 py-2',
        tone === 'neutral' && 'bg-surface-2/40',
      )}
    >
      <div className="flex items-center gap-1.5">
        {chip && <span className={cn('size-2 rounded-full', chipDot(chip))} aria-hidden />}
        <span className="eyebrow">{label}</span>
      </div>
      <span className="font-mono text-lg font-semibold tabular-nums text-foreground">{value}</span>
    </div>
  )
}

/** Role → the dot colour used in the stat band (mirrors the badge tone). */
function chipDot(role: Role): string {
  switch (ROLE_CATALOG[role].chip) {
    case 'risk':
      return 'bg-risk'
    case 'ml':
      return 'bg-ml'
    case 'agent':
      return 'bg-agent'
    case 'graph':
      return 'bg-graph'
    default:
      return 'bg-muted-foreground'
  }
}

/** Client entry for the Roles & Access section — gated on a reachable backend. */
export function RolesAccessMount(): ReactElement {
  // `/admin/users` is admin-only: hand the child the real session bearer, and
  // hold it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()
  // Bumped when a user is created above, so the roster below shows them without a
  // reload. The forms and the roster read the same endpoint; only this keeps them
  // from disagreeing about who exists.
  const [rosterKey, setRosterKey] = useState(0)

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      <TooltipProvider>
        <div className="space-y-4">
          <div>
            <p className="eyebrow mb-1">Tenants, seats and caps</p>
            <h1 className="t-hero text-foreground">Roles &amp; Access</h1>
            <p className="mt-1 max-w-prose text-sm text-muted-foreground">
              Everything on this screen is a write. Onboard a client, provision a seat, set what
              either may spend — and see the result on the same page, without a deploy.
            </p>
          </div>
          <AdminControls
            token={session?.token ?? null}
            tier={adminTier(session?.fineRole)}
            ownTenantId={session?.tenantId ?? null}
            onUsersChanged={() => setRosterKey((n) => n + 1)}
          />
          <RolesAccess token={session?.token ?? null} reloadKey={rosterKey} />
          {/*
            §7.8. The roster above says which coarse role a user holds; this says what
            their seat narrows it to. They belong on one screen because an operator
            answering "what can this person do?" needs both halves, and a permission
            answer split across two pages is one nobody trusts.
          */}
          <SeatsPanel
            token={session?.token ?? null}
            tenantId={session?.tenantId ?? null}
          />
        </div>
      </TooltipProvider>
    </BackendGate>
  )
}
