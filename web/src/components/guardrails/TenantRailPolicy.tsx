'use client'

import { ShieldCheck } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import {
  getGuardrailPolicy,
  type GuardrailControl,
  type GuardrailPolicyResponse,
  type GuardrailRail,
} from '@/lib/api/guardrails'
import { useAuth } from '@/lib/auth/AuthContext'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { InfoTip } from '@/components/primitives/InfoTip'
import {
  addedMembers,
  controlName,
  formatValue,
  provenanceOf,
} from '@/components/guardrails/railPolicy'

/** How a rail's enforcement reads as a badge. */
const ENFORCEMENT_TONE: Record<string, BadgeTone> = {
  block: 'block',
  redact: 'risk',
  advisory: 'neutral',
  off: 'neutral',
}

/**
 * One control: what the rails enforce, and which layer decided it.
 *
 * The floor is always shown, including when it is what is in force — "the platform
 * decided this and you cannot relax it" and "you tightened this" are different
 * sentences, and a screen that renders both as one bare value tells neither.
 */
function ControlRow({ row }: { row: GuardrailControl }): ReactElement {
  const type = row.control.type
  const name = controlName(row.key)
  const provenance = provenanceOf(row)
  const added = addedMembers(row)
  return (
    <div className="rounded-xl border border-border bg-surface-2/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="font-medium text-foreground">{name}</span>
          <InfoTip label={`About ${name}`}>
            {row.control.description}
          </InfoTip>
        </div>
        <Badge tone={provenance.mine ? 'ok' : 'neutral'} className="uppercase">
          {provenance.label}
        </Badge>
      </div>
      <p className="mt-2 text-sm text-foreground">
        <span className="text-muted-foreground">In force: </span>
        <span className="font-mono text-[0.78rem]">{formatValue(row.value, type)}</span>
      </p>
      <p className="mt-1 text-sm text-muted-foreground">
        Platform floor:{' '}
        <span className="font-mono text-[0.78rem]">
          {formatValue(row.platform_value, type)}
        </span>
        {added.length > 0 ? (
          <>
            {' · you added: '}
            <span className="font-mono text-[0.78rem] text-foreground">
              {added.join(', ')}
            </span>
          </>
        ) : null}
      </p>
      <p className="mt-1.5 font-mono text-[0.68rem] text-muted-foreground">
        {row.key} · {row.control.merge}
        {row.writable ? '' : ' · read-only for your role'}
      </p>
    </div>
  )
}

/** One rail, as the pipeline describes itself. */
function RailRow({ rail }: { rail: GuardrailRail }): ReactElement {
  return (
    <div className="grid gap-2 border-b border-border py-3 last:border-b-0 sm:grid-cols-[13rem_1fr_6rem]">
      <div className="min-w-0">
        <p className="font-medium text-foreground">{rail.name}</p>
        <p className="font-mono text-[0.68rem] text-muted-foreground">
          {rail.stage}
          {rail.threshold ? ` · ${rail.threshold}` : ''}
          {rail.model_backed ? ' · model-backed' : ''}
        </p>
      </div>
      <p className="text-sm text-muted-foreground">
        {rail.screens}
        {rail.settings.length > 0 ? (
          <span className="mt-0.5 block font-mono text-[0.68rem]">
            you control: {rail.settings.join(', ')}
          </span>
        ) : null}
      </p>
      <div className="sm:text-right">
        <Badge tone={ENFORCEMENT_TONE[rail.enforcement] ?? 'neutral'} className="uppercase">
          {rail.enforcement}
        </Badge>
      </div>
    </div>
  )
}

/**
 * The rails this tenant enforces, and where each value came from (§7.6).
 *
 * Read-only by design. Every control here is a settings-catalogue key, so the write is
 * the settings screen — one form, one validator, one audit row — and a second write
 * path beside it would be a second policy that can disagree with the first.
 */
export function TenantRailPolicy(): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [policy, setPolicy] = useState<GuardrailPolicyResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!hydrated) return
    let alive = true
    setLoading(true)
    void getGuardrailPolicy(token)
      .then((p) => {
        if (alive) {
          setPolicy(p)
          setError(null)
        }
      })
      .catch((e: unknown) => {
        // The server's own sentence, kept: a 503 here says the settings store is
        // unreadable, which is a different fact from "there is no policy".
        if (alive) setError(e instanceof Error ? e.message : 'The rail policy is unavailable.')
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  return (
    <Card>
      <CardBody>
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-xl bg-ok/12">
            <ShieldCheck className="size-5 text-ok-ink" />
          </span>
          <div className="flex min-w-0 flex-1 items-center gap-1.5">
            <h3 className="t-title text-foreground">Your rail policy</h3>
            <InfoTip label="About your rail policy">
              What the rails enforce for your tenant right now, read off the same folded
              policy a question meets. Your tenant may tighten any of these and can never
              go below the platform floor — the resolver takes the stricter value, so a
              weaker one loses by arithmetic rather than by a check.
            </InfoTip>
          </div>
          {policy ? (
            <Badge tone={policy.model_layer_wired ? 'ok' : 'risk'} className="uppercase">
              {policy.model_layer_wired ? 'model layers wired' : 'deterministic only'}
            </Badge>
          ) : null}
        </div>

        {loading ? (
          <p className="mt-4 text-sm text-muted-foreground">Resolving your rails…</p>
        ) : error ? (
          <div className="mt-4 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-4 text-center text-xs text-muted-foreground">
            {error}
          </div>
        ) : !policy ? null : (
          <>
            {!policy.resolved ? (
              <p className="mt-4 text-sm text-muted-foreground">
                No tenant layer was read for this session, so what follows is the platform
                floor and nothing else.
              </p>
            ) : null}

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {policy.controls.map((row) => (
                <ControlRow key={row.key} row={row} />
              ))}
            </div>

            <div className="mt-6">
              <p className="eyebrow mb-1">the stack · in the order it runs</p>
              <div className="mt-2">
                {policy.rails.map((rail) => (
                  <RailRow key={rail.id} rail={rail} />
                ))}
              </div>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}
