'use client'

import { Ban, ChevronDown, Eraser, ShieldCheck, type LucideIcon } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'
import type { Guardrail, GuardVerdict } from '@/lib/stream'

/** The guardrail fields this panel renders (envelope-free friendly). */
export type GuardrailEntry = Pick<
  Guardrail,
  'stage' | 'verdict' | 'reason' | 'layer' | 'redactions' | 'before_masked' | 'after'
>

export interface GuardrailRevealProps {
  /** Guardrail verdicts in order. Each carries only already-masked text. */
  guardrails: GuardrailEntry[]
}

/** Per-verdict presentation (icon + badge variant + label). */
const VERDICT_META: Record<
  GuardVerdict,
  { icon: LucideIcon; variant: 'ok' | 'risk' | 'block'; label: string }
> = {
  pass: { icon: ShieldCheck, variant: 'ok', label: 'pass' },
  redact: { icon: Eraser, variant: 'risk', label: 'redact' },
  block: { icon: Ban, variant: 'block', label: 'block' },
}

/**
 * Rail layers whose raw name would read as an accusation, and what to show instead.
 *
 * `injection_unavailable` is the backend's way of saying "the prompt-injection screen
 * could not be completed, so the request was refused unexamined" — a fact about the
 * deployment, not a finding about the user's text. Rendering the bare token beside a
 * BLOCK badge would read as `INJECTION`, which is the accusation the separate layer
 * exists to avoid making.
 *
 * @see aegis/src/aegis/guardrails/pipeline.py — `INJECTION_UNAVAILABLE_LAYER`
 */
const LAYER_LABEL: Record<string, string> = {
  injection_unavailable: 'injection · screen unavailable',
}

/**
 * The guardrail glass box: every rail verdict, its layer (pii / injection /
 * schema…), and — when a rail redacted — the before→after masked diff so the
 * jury sees exactly what was caught and how it was neutralised. A running
 * "guardrails fired" counter tallies the rails that took action (redact/block).
 *
 * Safety: this only ever renders the already-masked strings from props
 * (`before_masked`, `after`) and redaction *kinds*. It never receives or shows
 * raw PII.
 */
export function GuardrailReveal({ guardrails }: GuardrailRevealProps): ReactElement {
  const fired = useMemo(
    () => guardrails.filter((g) => g.verdict !== 'pass').length,
    [guardrails],
  )
  const checks = guardrails.length
  // Default open when a rail took action (there's something to inspect),
  // collapsed to a pass summary when everything cleared.
  const [open, setOpen] = useState(fired > 0)

  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <ShieldCheck className={cn('size-4', fired > 0 ? 'text-block-ink' : 'text-ok-ink')} />
        <CardTitle>Guardrails</CardTitle>
        <InfoTip label="About Guardrails">
          Input and output rails — prompt-injection, PII, and schema checks — run
          on every request. A fired rail redacted or blocked something.
        </InfoTip>
        <Badge variant={fired > 0 ? 'block' : 'ok'} className="ml-auto">
          {checks === 0 ? 'idle' : fired > 0 ? `${fired} fired` : `${checks} checks ✓`}
        </Badge>
        {checks > 0 && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="inline-grid size-5 place-items-center rounded-md text-muted-foreground/70 transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} />
          </button>
        )}
      </CardHeader>
      {open && (
        <CardContent className="space-y-2">
          {checks === 0 ? (
            <div className="flex min-h-20 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
              <ShieldCheck className="size-6 text-muted-foreground/50" />
              <p>Input and output rails report their verdicts here.</p>
            </div>
          ) : (
            <ol className="animate-reveal space-y-2">
              {guardrails.map((g, i) => (
                <GuardrailRow key={`${g.stage}-${g.layer ?? 'rail'}-${i}`} entry={g} />
              ))}
            </ol>
          )}
        </CardContent>
      )}
    </Card>
  )
}

/** One guardrail verdict, with a before→after diff when it redacted. */
function GuardrailRow({ entry }: { entry: GuardrailEntry }): ReactElement {
  const meta = VERDICT_META[entry.verdict]
  const Icon = meta.icon
  const hasDiff = entry.redactions.length > 0 && entry.before_masked != null && entry.after != null

  return (
    <li className="rounded-lg border border-border bg-surface-2/60 p-2.5">
      <div className="flex items-center gap-2">
        <Icon
          className={
            meta.variant === 'ok'
              ? 'size-3.5 text-ok-ink'
              : meta.variant === 'risk'
                ? 'size-3.5 text-risk-ink'
                : 'size-3.5 text-block-ink'
          }
        />
        <span className="font-mono text-[0.7rem] tracking-wide text-foreground uppercase">
          {entry.stage}
          {entry.layer ? ` · ${LAYER_LABEL[entry.layer] ?? entry.layer}` : ''}
        </span>
        <Badge variant={meta.variant} className="ml-auto uppercase">
          {meta.label}
        </Badge>
      </div>

      <p className="mt-1.5 text-[0.74rem] leading-snug text-muted-foreground">{entry.reason}</p>

      {hasDiff && (
        <div className="mt-2 space-y-1">
          <MaskedLine label="before" text={entry.before_masked!} tone="muted" />
          <MaskedLine label="after" text={entry.after!} tone="ok" />
          <div className="flex flex-wrap gap-1 pt-0.5">
            {entry.redactions.map((r, i) => (
              <Badge key={`${r.kind}-${i}`} variant="secondary" className="uppercase">
                {r.kind}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </li>
  )
}

/** A single masked before/after line; masked tokens read in a mono face. */
function MaskedLine({
  label,
  text,
  tone,
}: {
  label: string
  text: string
  tone: 'muted' | 'ok'
}): ReactElement {
  return (
    <div className="grid grid-cols-[3.2rem_1fr] items-baseline gap-2">
      <span className="eyebrow">{label}</span>
      <code
        className={
          tone === 'ok'
            ? 'rounded bg-ok/10 px-1.5 py-0.5 font-mono text-[0.7rem] break-words text-foreground'
            : 'rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.7rem] break-words text-muted-foreground'
        }
      >
        {text}
      </code>
    </div>
  )
}