'use client'

import { Check, KeyRound, Loader2, Lock, WifiOff } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { assignUserRole, getUsers } from '@/lib/api/client'
import { Badge } from '@/components/primitives/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'
import { cn } from '@/lib/utils'
import type { AdminUser } from '@/lib/api/types'
import type { Role } from '@/lib/stream'

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

export function RolesAccess({ token }: { token: string | null }): ReactElement {
  // Without a live auth session, the current admin cannot be identified — the
  // guard falls back to protecting the last admin in scope for everyone.
  const currentUsername: string | null = null

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
  }, [token])

  const reassign = (user: AdminUser, target: Role): void => {
    if (load.status !== 'ready') return
    if (normalizeRole(user.role) === target) return // no-op — same role

    // Guard again at the point of action (defence in depth; options are already
    // disabled). Never let an admin strip the last admin access.
    const guard = roleOptionGuard({ user, target, currentUsername, users: load.rows })
    if (guard.disabled) return

    const previous = load.rows
    // Optimistic write. We stamp the target portal role directly; the server
    // response replaces it with its stored label (which the mock backend may
    // collapse — e.g. ai_team → member → shown as Client — an honest artifact of
    // the fixture, not the product).
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

        {/* Role legend — what each portal grants (the "why"). */}
        {load.status === 'ready' && load.rows.length > 0 && (
          <ul className="mb-5 grid gap-1.5 rounded-lg border border-border/60 bg-surface-2/40 p-3 sm:grid-cols-2">
            {PORTAL_ROLES.map((r) => (
              <li key={r} className="flex items-start gap-2 text-xs leading-relaxed">
                <Badge variant={ROLE_CATALOG[r].chip} className="mt-px shrink-0">
                  {ROLE_CATALOG[r].label}
                </Badge>
                <span className="text-muted-foreground">{ROLE_CATALOG[r].sees}</span>
              </li>
            ))}
          </ul>
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

/**
 * Client entry for the Roles & Access section. Runs the boot probe once
 * (live-first, mock fallback), shows the honest offline banner, then renders the
 * roster wired to the typed client.
 */
export function RolesAccessMount(): ReactElement {
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div className="space-y-4">
        <div>
          <p className="eyebrow mb-1">RBAC grants</p>
          <h1 className="t-hero text-foreground">Roles &amp; Access</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Who can reach which portal — the front line of least-privilege access.
          </p>
        </div>
        {mode.mode === 'mock' && (
          <div
            role="status"
            className="flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
          >
            <WifiOff className="size-3.5 shrink-0" />
            <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
          </div>
        )}
        <RolesAccess token={null} />
      </div>
    </TooltipProvider>
  )
}
