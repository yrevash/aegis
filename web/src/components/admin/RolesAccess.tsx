'use client'

import { Building2, Check, Globe2, KeyRound, Loader2, Lock, Plus, ShieldCheck } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { assignUserRole, getUsers } from '@/lib/api/client'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { errorSentence } from '@/lib/api/apiError'
import { useAuth } from '@/lib/auth/AuthContext'
import { adminScopeCaption } from '@/lib/auth/tier'
import { cn } from '@/lib/utils'
import type { AdminUser } from '@/lib/api/types'
import type { Role } from '@/lib/stream'

import { AdminControls, type AccessWrite } from './AdminControls'
import { DelegationMap } from './DelegationMap'
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
 *
 * It renders beside the **portal legend**, which is the half of the screen that was
 * missing. `ROLE_CATALOG` has always carried a `sees` line per role — the delegation
 * contract each grant hands over — and no screen showed it, so the roster asked an
 * operator to choose between four words with nothing to choose on. The counts and the
 * contracts now sit next to the control that assigns them.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: AdminUser[] }

export function RolesAccess({
  token,
  reloadKey = 0,
  onAddUser,
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
  /** Opens the write drawer on the user tab, from the panel's own controls. */
  onAddUser?: () => void
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
            message: errorSentence(
              e,
              'The roster did not load. Check the backend is reachable, then retry.',
            ),
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
    <div className="space-y-6">
      {/*
        The permission model, before the roster that assigns it. A reader who has not
        seen what a portal *buys* cannot read a column of role words, and the roster
        used to open with exactly that column.
      */}
      <DelegationMap users={load.status === 'ready' ? load.rows : null} total={total} />

      <div className="grid gap-6 xl:grid-cols-3 [&>*]:min-w-0">
        <DataPanel
          className="rounded-lg xl:col-span-2"
          eyebrow="aegis.admin · /admin/users"
          title="Who has access"
          maxHeight={520}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              {total != null && (
                <Badge tone="neutral" className="gap-1.5">
                  <KeyRound className="size-3" aria-hidden />
                  <Figure>{total}</Figure> {total === 1 ? 'user' : 'users'}
                </Badge>
              )}
              {onAddUser && (
                <Button type="button" size="sm" variant="outline" onClick={onAddUser}>
                  <Plus className="size-4" aria-hidden /> Add user
                </Button>
              )}
            </div>
          }
        >
          {load.status === 'loading' && <LoadingState rows={5} label="Reading the roster…" />}

          {load.status === 'error' && <ErrorState error={load.message} />}

          {load.status === 'ready' && load.rows.length === 0 && (
            <EmptyState
              icon={KeyRound}
              title="No users in scope"
              body="Everyone this sign-in may administer appears here with the portal their role grants them."
              action={
                onAddUser && (
                  <Button type="button" size="sm" onClick={onAddUser}>
                    Create the first user
                  </Button>
                )
              }
            />
          )}

          {load.status === 'ready' && load.rows.length > 0 && (
            <Table>
              <THead>
                <TH className="text-left">User</TH>
                <TH className="text-left">Scope</TH>
                <TH className="text-left">Holds</TH>
                <TH className="text-left">Assign portal</TH>
              </THead>
              <TBody>
                {load.rows.map((u) => {
                  const current = normalizeRole(u.role)
                  const isSelf = currentUsername != null && u.username === currentUsername
                  // First locked reason for this row (all demotions share it).
                  const lock = PORTAL_ROLES.map((target) =>
                    roleOptionGuard({ user: u, target, currentUsername, users: load.rows }),
                  ).find((g) => g.disabled)
                  const isSaving = saving.has(u.id)

                  return (
                    <TR key={u.id}>
                      <TD className="whitespace-nowrap">
                        <span className="flex items-center gap-2.5">
                          <span
                            aria-hidden
                            className="grid size-7 shrink-0 place-items-center rounded-md bg-blue-100/70 font-mono text-[0.72rem] font-medium text-blue-800 uppercase"
                          >
                            {u.username.slice(0, 2)}
                          </span>
                          <span className="min-w-0">
                            <span className="block text-sm font-medium text-foreground">
                              {u.username}
                              {isSelf && (
                                <span className="ml-1.5 align-middle text-[0.6rem] tracking-wide text-muted-foreground uppercase">
                                  you
                                </span>
                              )}
                            </span>
                            <Figure className="block text-[0.68rem] text-muted-foreground">
                              {u.email ?? 'no email recorded'}
                            </Figure>
                          </span>
                        </span>
                      </TD>
                      <TD className="whitespace-nowrap">
                        {u.tenant_id == null ? (
                          <span className="flex items-center gap-1.5 text-[0.72rem] text-muted-foreground">
                            <Globe2 className="size-3.5 shrink-0" aria-hidden />
                            Platform — no tenant
                          </span>
                        ) : (
                          <span className="flex items-center gap-1.5 text-[0.72rem] text-muted-foreground">
                            <Building2 className="size-3.5 shrink-0" aria-hidden />
                            <Figure>{`tenant #${u.tenant_id}`}</Figure>
                          </span>
                        )}
                      </TD>
                      <TD>
                        <div className="flex items-center gap-1.5 whitespace-nowrap">
                          <Badge tone="neutral">{ROLE_CATALOG[current].label}</Badge>
                          <Figure className="text-[0.68rem] text-muted-foreground">{u.role}</Figure>
                        </div>
                      </TD>
                      <TD>
                        <div className="flex items-center gap-2">
                          <select
                            id={`assign-portal-${u.id}`}
                            value={current}
                            disabled={isSaving}
                            onChange={(e) => reassign(u, e.target.value as Role)}
                            className={cn(
                              // A fixed width, because a native select sizes to its
                              // longest option: the row whose options carry "— locked"
                              // grew half a column wider than every other row and was
                              // clipped by the panel that holds them.
                              'h-8 w-[8.5rem] rounded-md border border-border bg-surface px-2 text-[0.8125rem] text-foreground transition-colors duration-[--dur-fast] motion-reduce:transition-none hover:border-input',
                              'focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
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

                          {isSaving && (
                            <Loader2
                              aria-hidden
                              className="size-3.5 animate-spin text-muted-foreground motion-reduce:animate-none"
                            />
                          )}
                          {!isSaving && flash.has(u.id) && (
                            <span
                              role="status"
                              className="flex items-center gap-1 text-[0.68rem] text-ok-ink"
                            >
                              <Check className="size-3.5" aria-hidden /> updated
                            </span>
                          )}
                          {!isSaving && !flash.has(u.id) && lock && (
                            <InfoTip label="Why this role is locked">
                              <span className="flex items-start gap-1.5">
                                <Lock className="mt-px size-3 shrink-0" aria-hidden /> {lock.reason}
                              </span>
                            </InfoTip>
                          )}
                        </div>
                      </TD>
                    </TR>
                  )
                })}
              </TBody>
            </Table>
          )}
        </DataPanel>

        <PortalLegend counts={counts} total={total} />
      </div>
    </div>
  )
}

/**
 * The four portals, what each one grants, and how many people hold it.
 *
 * This is the "why" the roster's dropdown never had. `sees` is the delegation contract
 * — least privilege stated as a sentence a non-engineer can check — and the count beside
 * it is the current shape of that delegation. A bar under each row shows the share of
 * the roster holding it, in the one blue at a single intensity: it compares four parts
 * of one whole, which is magnitude, not status.
 */
function PortalLegend({
  counts,
  total,
}: {
  counts: Record<Role, number> | null
  total: number | null
}): ReactElement {
  return (
    <Card className="rounded-lg">
      <CardHeader
        eyebrow="aegis.admin · RBAC"
        title="The four portals"
        actions={
          <InfoTip label="Why this matters">
            In an enterprise the admin’s real power is delegation. Each team should see only
            its own portal — build, ops, or outcomes — never the whole platform. This is where
            that least-privilege line is drawn, granted, and revoked.
          </InfoTip>
        }
      />
      <CardBody className="pt-0">
        <ul className="space-y-3">
          {PORTAL_ROLES.map((r) => {
            const meta = ROLE_CATALOG[r]
            const n = counts?.[r] ?? null
            const share = counts && total != null && total > 0 ? (counts[r] / total) * 100 : 0
            return (
              <li
                key={r}
                className="min-w-0 rounded-md border border-border bg-surface-2/50 p-3 transition-colors duration-[--dur-fast] motion-reduce:transition-none hover:border-input"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-2">
                    <span
                      aria-hidden
                      className="grid size-6 shrink-0 place-items-center rounded-md bg-blue-100/70 text-blue-800"
                    >
                      <ShieldCheck className="size-3.5" />
                    </span>
                    <span className="truncate text-sm font-medium text-foreground">
                      {meta.label}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-baseline gap-1">
                    <Figure className="text-sm font-semibold text-foreground">{n ?? '—'}</Figure>
                    <span className="text-[0.68rem] text-muted-foreground">
                      {n === 1 ? 'holder' : 'holders'}
                    </span>
                  </span>
                </div>
                <div
                  className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface"
                  role="img"
                  aria-label={
                    n == null || total == null
                      ? `${meta.label}: head count not loaded`
                      : `${meta.label}: ${n} of ${total} users in scope`
                  }
                >
                  <div
                    className="h-full rounded-full bg-blue-600 transition-[width] duration-[--dur-base] motion-reduce:transition-none"
                    style={{ width: `${Math.max(share > 0 ? 3 : 0, share)}%` }}
                  />
                </div>
                <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{meta.sees}</p>
              </li>
            )
          })}
        </ul>
        <Receipt
          className="mt-4"
          origin="aegis.admin · /admin/users"
          detail={
            total == null
              ? 'the roster has not answered yet, so no share is drawn'
              : `${total} users in scope, each counted once against the role it holds`
          }
        />
      </CardBody>
    </Card>
  )
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
  // Which write the drawer is showing. `null` closes it — the screen reads as state
  // first, and every form on it is one deliberate click away.
  const [openWrite, setOpenWrite] = useState<AccessWrite | null>(null)

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      <div className="space-y-6">
          <PageHeader
            eyebrow="tenants · seats · caps"
            title="Roles & access"
            note="Who may do what, and what it may cost them."
            actions={
              <>
                <Badge tone="neutral" className="gap-1.5">
                  <ShieldCheck className="size-3 shrink-0" aria-hidden />
                  {adminScopeCaption(session)}
                </Badge>
                <Button type="button" onClick={() => setOpenWrite('tenant')}>
                  <Plus className="size-4" aria-hidden /> Manage access
                </Button>
              </>
            }
          />
          <AdminControls
            token={session?.token ?? null}
            tier={adminTier(session?.fineRole)}
            ownTenantId={session?.tenantId ?? null}
            onUsersChanged={() => setRosterKey((n) => n + 1)}
            openWrite={openWrite}
            onCloseWrite={() => setOpenWrite(null)}
            onOpenWrite={setOpenWrite}
          >
            <RolesAccess
              token={session?.token ?? null}
              reloadKey={rosterKey}
              onAddUser={() => setOpenWrite('user')}
            />
            {/*
              §7.8. The roster above says which coarse role a user holds; this says what
              their seat narrows it to. They belong on one screen because an operator
              answering "what can this person do?" needs both halves, and a permission
              answer split across two pages is one nobody trusts.
            */}
            <SeatsPanel
              token={session?.token ?? null}
              tenantId={session?.tenantId ?? null}
              canChooseTenant={adminTier(session?.fineRole) === 'platform'}
            />
        </AdminControls>
      </div>
    </BackendGate>
  )
}
