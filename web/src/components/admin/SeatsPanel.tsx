'use client'

import { Loader2, ShieldCheck, ShieldOff } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { Badge } from '@/components/primitives/badge'
import { getSeats, putSeat, type Seat } from '@/lib/api/seats'
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
}: {
  token: string | null
  /** The tenant to read. Platform staff must name one; a tenant admin's own is implied. */
  tenantId: number | null
}): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [saving, setSaving] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [draftLabel, setDraftLabel] = useState<Record<number, string>>({})

  useEffect(() => {
    let alive = true
    setLoad({ status: 'loading' })
    getSeats(token, tenantId)
      .then((res) => alive && setLoad({ status: 'ready', rows: res.rows }))
      .catch(
        (e: unknown) =>
          alive &&
          setLoad({
            status: 'error',
            message: e instanceof Error ? e.message : 'Failed to load seats',
          }),
      )
    return () => {
      alive = false
    }
  }, [token, tenantId])

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
        setRefusal(e instanceof Error ? e.message : 'The seat could not be changed.')
      },
    )
  }

  if (load.status === 'loading') {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          Loading seats…
        </CardContent>
      </Card>
    )
  }

  if (load.status === 'error') {
    return (
      <Card>
        <CardContent className="py-8 text-sm text-muted-foreground">{load.message}</CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4" aria-hidden />
          Named seats
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="max-w-prose text-sm text-muted-foreground">
          A seat names what one person may do inside the role they already hold. Every toggle
          here <span className="text-foreground">removes</span> a capability — the role guard
          runs first, so a seat can narrow it and never widen it. Turning one back on restores
          what your tenant already permits and can never exceed it.
        </p>

        {refusal ? (
          <p className="rounded-lg border border-border bg-surface-2/60 px-3 py-2 text-sm text-foreground">
            {refusal}
          </p>
        ) : null}

        <div className="space-y-3">
          {load.rows.map((seat) => (
            <div key={seat.userId} className="rounded-xl border border-border bg-surface-1 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-foreground">{seat.username}</span>
                <input
                  aria-label={`Seat name for ${seat.username}`}
                  className="min-w-[12rem] rounded-md border border-border bg-surface-2 px-2 py-1 text-sm text-foreground"
                  placeholder="Name this seat — e.g. Support Lead"
                  value={draftLabel[seat.userId] ?? seat.label}
                  onChange={(e) =>
                    setDraftLabel((d) => ({ ...d, [seat.userId]: e.target.value }))
                  }
                />
                <button
                  type="button"
                  className="rounded-md border border-border px-2 py-1 text-xs text-foreground"
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
              </div>

              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {seat.capabilities.map((cap) => {
                  const marker = `${seat.userId}:${cap.key}`
                  return (
                    <label
                      key={cap.key}
                      className={cn(
                        'flex items-start gap-2 rounded-lg border border-border px-2 py-2 text-sm',
                        cap.allowed ? 'bg-surface-1' : 'bg-surface-2',
                      )}
                    >
                      <input
                        type="checkbox"
                        className="mt-1"
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
                      <span className="min-w-0">
                        <span className="block text-foreground">{cap.title}</span>
                        <span className="mt-0.5 flex flex-wrap items-center gap-1">
                          <Badge>{SOURCE_LABEL[cap.source] ?? cap.source}</Badge>
                          {cap.allowed ? null : (
                            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                              <ShieldOff className="h-3 w-3" aria-hidden />
                              revoked
                            </span>
                          )}
                        </span>
                        <span className="mt-1 block font-mono text-[0.68rem] text-muted-foreground">
                          {cap.key}
                        </span>
                      </span>
                    </label>
                  )
                })}
              </div>
            </div>
          ))}
          {load.rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No users in this tenant yet, so there is nothing to seat.
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}
