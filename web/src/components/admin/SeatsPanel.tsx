'use client'

import { ShieldCheck, ShieldOff } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { Badge } from '@/components/primitives/badge'
import { Figure } from '@/components/primitives/Figure'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { errorSentence } from '@/lib/api/apiError'
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
            message: errorSentence(
              e,
              'The seats did not load. Check the backend is reachable, then retry.',
            ),
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
        setRefusal(errorSentence(e, 'The seat could not be changed. Try it again.'))
      },
    )
  }

  if (load.status === 'loading') {
    return (
      <Card className="rounded-lg">
        <CardContent className="pt-5">
          <LoadingState rows={4} label="Reading the seats…" />
        </CardContent>
      </Card>
    )
  }

  if (load.status === 'error') {
    return (
      <Card className="rounded-lg">
        <CardContent className="pt-5">
          <ErrorState error={load.message} />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="rounded-lg">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4 shrink-0" aria-hidden />
          Named seats
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p id="seats-direction" className="max-w-prose text-sm leading-relaxed text-muted-foreground">
          A seat names what one person may do inside the role they already hold. Every toggle
          here <span className="text-foreground">removes</span> a capability — the role guard
          runs first, so a seat can narrow it and never widen it. Turning one back on restores
          what your tenant already permits and can never exceed it.
        </p>

        {refusal ? <ErrorState error={refusal} /> : null}

        <div className="space-y-3">
          {load.rows.map((seat) => (
            <div key={seat.userId} className="rounded-lg border border-border bg-card p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Figure className="text-foreground">{seat.username}</Figure>
                <label htmlFor={`seat-name-${seat.userId}`} className="sr-only">
                  Seat name for {seat.username}
                </label>
                <input
                  id={`seat-name-${seat.userId}`}
                  className="min-w-[12rem] rounded-lg border border-border bg-surface-2 px-2 py-1 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  placeholder="Name this seat — e.g. Support Lead…"
                  autoComplete="off"
                  value={draftLabel[seat.userId] ?? seat.label}
                  onChange={(e) =>
                    setDraftLabel((d) => ({ ...d, [seat.userId]: e.target.value }))
                  }
                />
                <button
                  type="button"
                  className="touch-manipulation rounded-lg border border-border px-2 py-1 text-xs text-foreground transition-colors outline-none hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
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

              <fieldset className="mt-3 grid gap-2 sm:grid-cols-2">
                <legend className="sr-only">What {seat.username}’s seat may do</legend>
                {seat.capabilities.map((cap) => {
                  const marker = `${seat.userId}:${cap.key}`
                  return (
                    <label
                      key={cap.key}
                      htmlFor={`seat-${seat.userId}-${cap.key}`}
                      className={cn(
                        'flex items-start gap-2 rounded-lg border px-2 py-2 text-sm',
                        cap.allowed
                          ? 'border-border bg-card'
                          : 'border-l-2 border-risk bg-surface-2',
                      )}
                    >
                      <input
                        id={`seat-${seat.userId}-${cap.key}`}
                        type="checkbox"
                        aria-describedby="seats-direction"
                        className="mt-1 size-3.5 rounded border-border accent-[color:var(--primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)]"
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
                            <span className="inline-flex items-center gap-1 text-xs text-risk-ink">
                              <ShieldOff className="size-3" aria-hidden />
                              revoked
                            </span>
                          )}
                        </span>
                        <Figure className="mt-1 block text-muted-foreground">{cap.key}</Figure>
                      </span>
                    </label>
                  )
                })}
              </fieldset>
            </div>
          ))}
          {load.rows.length === 0 ? (
            <EmptyState
              icon={ShieldCheck}
              title="Nothing to seat yet"
              body="A seat names what one person may do inside the role they already hold, so it appears here once the tenant has a user. Create one on the roster above."
            />
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}
