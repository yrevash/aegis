'use client'

import { Building2, ShieldCheck, ShieldOff } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { DataPanel } from '@/components/ui/DataPanel'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { errorSentence } from '@/lib/api/apiError'
import { getTenants } from '@/lib/api/client'
import { getSeats, putSeat, type Seat } from '@/lib/api/seats'
import type { Tenant } from '@/lib/api/types'
import { cn } from '@/lib/utils'

/**
 * Admin — Named seats (§7.8).
 *
 * Tenant sub-roles were cut. A seat is a **named grant**: `seat.label` gives it a name,
 * five revoke-only toggles say what it may do, and the settings row already records who
 * changed it and when. This panel is that table, and it sits under the roster on
 * Roles & Access because the two answer halves of one question — the coarse role a user
 * holds, and what their seat narrows it to.
 *
 * **Every toggle here can only take capability away, and the screen says so rather than
 * pretending otherwise.** The server folds a write against the enclosing scopes and the
 * strictest value wins, so switching a capability back on restores what the tenant
 * already permits and can never exceed it. A refusal is shown with the server's own
 * sentence — it is the authority, and paraphrasing it here would be a second policy.
 *
 * The `source` badge is the other half of "who gave it to them": `platform` means
 * nobody has touched it, `tenant` means it is off for everybody, `user` means it was
 * set for this person specifically.
 *
 * **A platform operator names the tenant rather than reading a refusal.** `GET
 * /admin/seats` answers a platform principal with *"Seats belong to a tenant. Name the
 * tenant to read"* — which is correct, and was the whole panel for the one operator who
 * opens this screen most. The refusal was asking for an argument, so the panel now
 * carries the control that supplies it; the sentence still renders whenever the server
 * says it, and nothing about who may write what moved off the server.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: Seat[] }

/** How a `source` reads to an operator deciding where to go and change something. */
const SOURCE_LABEL: Record<string, string> = {
  platform: 'Platform default',
  tenant: 'Set for the whole tenant',
  user: 'Set for this seat',
}

export function SeatsPanel({
  token,
  tenantId,
  canChooseTenant = false,
}: {
  token: string | null
  /** The tenant to read. Platform staff must name one; a tenant admin's own is implied. */
  tenantId: number | null
  /**
   * Whether this principal may target a tenant it is not pinned to. Platform staff
   * only — and it is a convenience, not a permission: `_scope_tenant` refuses a
   * cross-tenant read server-side whoever asks.
   */
  canChooseTenant?: boolean
}): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [saving, setSaving] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [draftLabel, setDraftLabel] = useState<Record<number, string>>({})
  const [tenants, setTenants] = useState<Tenant[]>([])
  // The tenant actually being read. A tenant admin's is pinned by the session; a
  // platform operator picks one, defaulting to the first the roster returns.
  const [chosen, setChosen] = useState<number | null>(tenantId)
  // Whether the roster call has settled, either way. Without this the seats read
  // below cannot tell "the picker has not answered yet" from "there is nothing to
  // pick", and those need different answers: wait, versus say so.
  const [rosterSettled, setRosterSettled] = useState(!canChooseTenant)

  useEffect(() => {
    if (!canChooseTenant) return
    let alive = true
    getTenants(token)
      .then((res) => {
        if (!alive) return
        setTenants(res.rows)
        setChosen((current) => current ?? res.rows[0]?.id ?? null)
        setRosterSettled(true)
      })
      .catch(() => {
        // A failed roster read leaves no picker — the server's own refusal on the
        // seats read below still says what is missing.
        if (alive) {
          setTenants([])
          setRosterSettled(true)
        }
      })
    return () => {
      alive = false
    }
  }, [token, canChooseTenant])

  const scope = canChooseTenant ? chosen : tenantId

  useEffect(() => {
    let alive = true
    setLoad({ status: 'loading' })

    // A platform operator holds no seat of their own, so `GET /admin/seats` with no
    // tenant is a request that cannot succeed — the server answers 400 saying exactly
    // that. It was firing on every load of this screen, before the tenant roster had
    // resolved and set `chosen`, and then firing again correctly a moment later. The
    // refusal was real but the request was ours to not make.
    if (scope == null && canChooseTenant) {
      if (!rosterSettled) return // the picker is still resolving; wait, do not guess
      setLoad({
        status: 'error',
        message:
          'Seats belong to a tenant, and no tenant is available to read. Create one first.',
      })
      return
    }

    getSeats(token, scope)
      .then((res) => alive && setLoad({ status: 'ready', rows: res.rows }))
      .catch(
        (e: unknown) =>
          alive &&
          setLoad({
            status: 'error',
            message: errorSentence(
              e,
              'The seats did not load. Check the backend is reachable, then retry.',
            ),
          }),
      )
    return () => {
      alive = false
    }
  }, [token, scope, canChooseTenant, rosterSettled])

  const apply = (userId: number, body: Parameters<typeof putSeat>[2], marker: string): void => {
    setSaving(marker)
    setRefusal(null)
    void putSeat(token, userId, body).then(
      (updated) => {
        setSaving(null)
        setLoad((prev) =>
          prev.status === 'ready'
            ? {
                status: 'ready',
                rows: prev.rows.map((s) => (s.userId === updated.userId ? updated : s)),
              }
            : prev,
        )
      },
      (e: unknown) => {
        setSaving(null)
        // The server's sentence, verbatim. A 409 here says the value was weaker than
        // the tenant already has in force — which is the whole tighten-only guarantee
        // speaking, and rewording it would hide which layer refused.
        setRefusal(errorSentence(e, 'The seat could not be changed. Try it again.'))
      },
    )
  }

  const chooser =
    canChooseTenant && tenants.length > 0 ? (
      <span className="flex items-center gap-2">
        <label htmlFor="seats-tenant" className="eyebrow">
          Tenant
        </label>
        <select
          id="seats-tenant"
          value={chosen == null ? '' : String(chosen)}
          onChange={(event) => setChosen(Number(event.target.value))}
          className="h-8 rounded-md border border-border bg-surface px-2 text-sm text-foreground outline-none transition-colors duration-[--dur-fast] motion-reduce:transition-none hover:border-input focus-visible:ring-2 focus-visible:ring-ring"
        >
          {tenants.map((t) => (
            <option key={t.id} value={String(t.id)}>
              {t.name}
            </option>
          ))}
        </select>
      </span>
    ) : null

  // The two non-ready states keep the panel's own title and eyebrow. A bare card
  // holding a refusal sentence names nothing, so a reader who scrolled to it could
  // not tell *which* region had failed — and the refusal here ("seats belong to a
  // tenant; name one") is a rule worth attributing to the surface it governs.
  if (load.status !== 'ready') {
    return (
      <DataPanel
        className="rounded-lg"
        eyebrow="aegis.admin · seats"
        title="Named seats"
        actions={chooser}
      >
        {load.status === 'loading' ? (
          <LoadingState rows={4} label="Reading the seats…" />
        ) : (
          <ErrorState error={load.message} />
        )}
      </DataPanel>
    )
  }

  const capsPerSeat = load.rows[0]?.capabilities.length ?? 0
  const narrowed = load.rows.reduce(
    (n, seat) => n + seat.capabilities.filter((c) => !c.allowed).length,
    0,
  )
  const tenantName = tenants.find((t) => t.id === scope)?.name ?? null

  return (
    <DataPanel
      className="rounded-lg"
      eyebrow="aegis.admin · seats"
      title="Named seats"
      maxHeight={640}
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {chooser}
          <Badge tone="neutral" className="gap-1.5">
            <ShieldCheck className="size-3" aria-hidden />
            <Figure>{load.rows.length}</Figure> {load.rows.length === 1 ? 'seat' : 'seats'}
          </Badge>
          {narrowed > 0 ? (
            <Badge tone="risk" className="gap-1.5">
              <ShieldOff className="size-3" aria-hidden />
              <Figure>{narrowed}</Figure> revoked
            </Badge>
          ) : null}
        </div>
      }
      toolbar={
        <p id="seats-direction" className="flex items-center gap-1.5 text-xs text-muted-foreground">
          Every toggle here <span className="font-medium text-foreground">removes</span> a
          capability — a seat narrows a role, never widens it.
          <InfoTip label="How a seat is folded">
            A seat names what one person may do inside the role they already hold. The role guard
            runs first, so the strictest value wins: turning a capability back on restores what
            your tenant already permits and can never exceed it. A refusal here is the server’s
            own sentence — it is the authority.
          </InfoTip>
        </p>
      }
      footer={
        <Receipt
          variant="inline"
          origin={`aegis.admin · /admin/seats${scope == null ? '' : ` · tenant #${scope}`}`}
          detail={
            tenantName == null
              ? `${load.rows.length} seats · ${capsPerSeat} capabilities each`
              : `${tenantName} · ${load.rows.length} seats · ${capsPerSeat} capabilities each`
          }
        />
      }
    >
      <div className="space-y-4">
        {refusal ? <ErrorState error={refusal} /> : null}

        <div className="space-y-3">
          {load.rows.map((seat) => {
            const off = seat.capabilities.filter((c) => !c.allowed).length
            return (
              <div key={seat.userId} className="overflow-hidden rounded-lg border border-border">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border bg-surface-2/70 px-3 py-2.5">
                  <span
                    aria-hidden
                    className="grid size-7 shrink-0 place-items-center rounded-md bg-blue-100/70 font-mono text-[0.72rem] font-medium text-blue-800 uppercase"
                  >
                    {seat.username.slice(0, 2)}
                  </span>
                  <span className="min-w-0">
                    <Figure className="block text-[0.8125rem] font-medium text-foreground">
                      {seat.username}
                    </Figure>
                    <span className="flex items-center gap-1.5 text-[0.68rem] text-muted-foreground">
                      <Building2 className="size-3 shrink-0" aria-hidden />
                      <Figure>{`tenant #${seat.tenantId}`}</Figure>
                    </span>
                  </span>

                  <span className="ml-auto flex flex-wrap items-center gap-2">
                    {off > 0 ? (
                      <Badge tone="risk" className="gap-1">
                        <ShieldOff className="size-3" aria-hidden />
                        <Figure>{`${off} of ${seat.capabilities.length}`}</Figure> revoked
                      </Badge>
                    ) : (
                      <Badge tone="neutral" className="gap-1">
                        <ShieldCheck className="size-3" aria-hidden />
                        full role
                      </Badge>
                    )}
                    <label htmlFor={`seat-name-${seat.userId}`} className="sr-only">
                      Seat name for {seat.username}
                    </label>
                    <input
                      id={`seat-name-${seat.userId}`}
                      className="h-8 min-w-[11rem] rounded-md border border-border bg-surface px-2 text-sm text-foreground outline-none transition-colors duration-[--dur-fast] motion-reduce:transition-none hover:border-input focus-visible:ring-2 focus-visible:ring-ring"
                      placeholder="Name this seat — e.g. Support Lead…"
                      autoComplete="off"
                      value={draftLabel[seat.userId] ?? seat.label}
                      onChange={(e) =>
                        setDraftLabel((d) => ({ ...d, [seat.userId]: e.target.value }))
                      }
                    />
                    <button
                      type="button"
                      className="h-8 touch-manipulation rounded-md border border-border bg-surface px-2.5 text-xs text-foreground transition-colors duration-[--dur-fast] outline-none motion-reduce:transition-none hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={saving === `label:${seat.userId}`}
                      onClick={() =>
                        apply(
                          seat.userId,
                          { label: draftLabel[seat.userId] ?? seat.label },
                          `label:${seat.userId}`,
                        )
                      }
                    >
                      {saving === `label:${seat.userId}` ? 'Saving…' : 'Save name'}
                    </button>
                  </span>
                </div>

                <fieldset className="grid gap-2 p-3 sm:grid-cols-2 xl:grid-cols-3">
                  <legend className="sr-only">What {seat.username}’s seat may do</legend>
                  {seat.capabilities.map((cap) => {
                    const marker = `${seat.userId}:${cap.key}`
                    return (
                      <label
                        key={cap.key}
                        htmlFor={`seat-${seat.userId}-${cap.key}`}
                        className={cn(
                          'flex min-w-0 items-start gap-2 rounded-md border px-2.5 py-2 text-sm transition-colors duration-[--dur-fast] motion-reduce:transition-none',
                          cap.allowed
                            ? 'border-border bg-surface hover:border-input'
                            : 'border-risk/60 bg-risk/[0.07]',
                        )}
                      >
                        <input
                          id={`seat-${seat.userId}-${cap.key}`}
                          type="checkbox"
                          aria-describedby="seats-direction"
                          className="mt-0.5 size-3.5 shrink-0 rounded border-border accent-[color:var(--blue-600)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)]"
                          checked={cap.allowed}
                          disabled={saving === marker}
                          onChange={(e) =>
                            apply(
                              seat.userId,
                              { capabilities: { [cap.key]: e.target.checked } },
                              marker,
                            )
                          }
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-start gap-1.5">
                            <span className="min-w-0 flex-1 text-[0.8125rem] leading-5 text-foreground">
                              {cap.title}
                            </span>
                            <InfoTip label={`Where ${cap.title} is enforced`}>
                              <span className="block">
                                The narrowing check that reads{' '}
                                <span className="font-mono">{cap.key}</span> lives at{' '}
                                <span className="font-mono">{cap.gates}</span>. Turning this off
                                does not hide the screen — it refuses the call.
                              </span>
                            </InfoTip>
                          </span>
                          <span className="mt-1 flex flex-wrap items-center gap-1">
                            {cap.allowed ? (
                              <Badge tone="neutral">{SOURCE_LABEL[cap.source] ?? cap.source}</Badge>
                            ) : (
                              <Badge tone="risk" className="gap-1">
                                <ShieldOff className="size-3" aria-hidden />
                                revoked · {SOURCE_LABEL[cap.source] ?? cap.source}
                              </Badge>
                            )}
                          </span>
                        </span>
                      </label>
                    )
                  })}
                </fieldset>
              </div>
            )
          })}
          {load.rows.length === 0 ? (
            <EmptyState
              icon={ShieldCheck}
              title="Nothing to seat yet"
              body="A seat names what one person may do inside the role they already hold, so it appears here once the tenant has a user. Create one on the roster above."
            />
          ) : null}
        </div>
      </div>
    </DataPanel>
  )
}
