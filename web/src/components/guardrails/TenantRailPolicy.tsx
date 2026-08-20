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
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { errorSentence } from '@/lib/api/apiError'
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
    <div className="rounded-lg border border-border bg-surface-2/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="font-medium text-foreground">{name}</span>
          <InfoTip label={`About ${name}`}>{row.control.description}</InfoTip>
        </div>
        <Badge tone={provenance.mine ? 'ok' : 'neutral'} className="uppercase">
          {provenance.label}
        </Badge>
      </div>
      <dl className="mt-2 grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">In force</dt>
        <dd className="min-w-0">
          <Figure className="break-words text-foreground">{formatValue(row.value, type)}</Figure>
        </dd>
        <dt className="text-muted-foreground">Platform floor</dt>
        <dd className="min-w-0">
          <Figure className="break-words text-muted-foreground">
            {formatValue(row.platform_value, type)}
          </Figure>
        </dd>
        {added.length > 0 ? (
          <>
            <dt className="text-muted-foreground">You added</dt>
            <dd className="min-w-0">
              <Figure className="break-words text-foreground">{added.join(', ')}</Figure>
            </dd>
          </>
        ) : null}
      </dl>
      <Receipt
        label="Merge rule"
        origin={`${row.key} · ${row.control.merge}`}
        detail={row.writable ? null : 'read-only for your role'}
        className="mt-3 pt-2"
      />
    </div>
  )
}

/** One rail, as the pipeline describes itself. */
function RailRow({ rail }: { rail: GuardrailRail }): ReactElement {
  return (
    <div className="grid gap-2 border-b border-border py-3 last:border-b-0 sm:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_6rem]">
      <div className="min-w-0">
        <p className="font-medium text-foreground">{rail.name}</p>
        <Figure className="text-muted-foreground">
          {`${rail.stage}${rail.threshold ? ` · ${rail.threshold}` : ''}${rail.model_backed ? ' · model-backed' : ''}`}
        </Figure>
      </div>
      <p className="text-sm leading-relaxed text-muted-foreground">
        {rail.screens}
        {rail.settings.length > 0 ? (
          <span className="mt-0.5 block">
            <span className="eyebrow mr-1.5">you control</span>
            <Figure>{rail.settings.join(', ')}</Figure>
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
        if (alive) {
          setError(
            errorSentence(e, 'The rail policy could not be read. Check the backend is up.'),
          )
        }
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  return (
    <Card className="rounded-lg">
      <CardBody>
        <SectionHeader
          as="h2"
          eyebrow="your tenant · in force"
          title="Your rail policy"
          right={
            <>
              <InfoTip label="About your rail policy">
                What the rails enforce for your tenant right now, read off the same folded
                policy a question meets. Your tenant may tighten any of these and can never
                go below the platform floor — the resolver takes the stricter value, so a
                weaker one loses by arithmetic rather than by a check.
              </InfoTip>
              {policy ? (
                <Badge tone={policy.model_layer_wired ? 'ok' : 'risk'} className="gap-1 uppercase">
                  <ShieldCheck className="size-3" aria-hidden />
                  {policy.model_layer_wired ? 'model layers wired' : 'deterministic only'}
                </Badge>
              ) : null}
            </>
          }
        />

        {loading ? (
          <LoadingState rows={4} label="Resolving your rails…" className="mt-4" />
        ) : error ? (
          <ErrorState error={error} className="mt-4" />
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
