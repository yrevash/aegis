'use client'

import { Check, ShieldAlert, ShieldCheck, X } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge, type BadgeTone } from '@/components/ui/Badge'
import type { VoiceTranscribeResponse } from '@/lib/api/types'

const TONE: Record<VoiceTranscribeResponse['verdict'], BadgeTone> = {
  pass: 'ok',
  redact: 'risk',
  flag: 'risk',
  block: 'block',
}

/**
 * The rail verdict on the transcript, with its honest coverage.
 *
 * Two things are rendered that a normal verdict card would leave out, both
 * deliberately:
 *
 * - **What did not run.** `controls_skipped` is listed as prominently as
 *   `controls_run`, because a control that could not run must fail closed *and*
 *   say so. Speaker diarisation is always in that list — the fleet reports no
 *   speaker labels and policy forbids a local model, so it is stated as absent
 *   rather than quietly omitted.
 * - **Whether the agent may see this.** A transcript that the rails refused has no
 *   agent input at all; the card says so, and the "send to the agent" action is
 *   disabled by the same field.
 */
export function RailVerdict({
  result,
}: {
  result: VoiceTranscribeResponse
}): ReactElement {
  const blocked = result.verdict === 'block'
  return (
    <div className="@container min-w-0 space-y-4">
      <div className="flex items-center gap-3">
        <span
          className={`flex size-9 items-center justify-center rounded-lg ${
            blocked ? 'bg-block/12' : 'bg-ok/12'
          }`}
        >
          {blocked ? (
            <ShieldAlert className="size-5 text-block-ink" aria-hidden />
          ) : (
            <ShieldCheck className="size-5 text-ok-ink" aria-hidden />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="t-title text-foreground">Rails on the transcript</h3>
        </div>
        <Badge tone={TONE[result.verdict]} className="uppercase">
          {result.verdict}
        </Badge>
      </div>

      <p className="text-sm leading-relaxed text-foreground">{result.verdict_reason}</p>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {result.verdict_layer && (
          <span className="rounded-md bg-surface-2 px-2 py-0.5 font-mono text-muted-foreground">
            layer {result.verdict_layer}
          </span>
        )}
        {result.redactions.map((kind) => (
          <Badge key={kind} tone="risk" className="font-mono">
            redacted {kind}
          </Badge>
        ))}
      </div>

      <div className="grid min-w-0 gap-4 @md:grid-cols-2 [&>*]:min-w-0">
        <div>
          <p className="eyebrow mb-1.5">controls run</p>
          <ul className="space-y-1">
            {result.controls_run.map((c) => (
              <li key={c} className="flex gap-1.5 text-xs text-foreground">
                <Check className="mt-0.5 size-3.5 shrink-0 text-ok-ink" aria-hidden />
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="eyebrow mb-1.5">not run</p>
          {result.controls_skipped.length === 0 ? (
            <p className="text-xs text-muted-foreground">Every control ran.</p>
          ) : (
            <ul className="space-y-1">
              {result.controls_skipped.map((c) => (
                <li key={c} className="flex gap-1.5 text-xs text-muted-foreground">
                  <X className="mt-0.5 size-3.5 shrink-0 text-block-ink" aria-hidden />
                  <span>{c}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div
        className={`rounded-lg border px-3 py-2.5 text-xs ${
          result.agent_input == null
            ? 'border-block/30 bg-block/8 text-block-ink'
            : 'border-border bg-surface-2/40 text-muted-foreground'
        }`}
      >
        {result.agent_input == null ? (
          <>The rails refused this transcript, so there is no agent input.</>
        ) : result.agent_input === result.transcript ? (
          <>The rails cleared the transcript unchanged; it is what the agent would receive.</>
        ) : (
          <>
            The agent would receive the rails&apos; rewritten text, not the raw transcript:{' '}
            <span className="font-mono break-words text-foreground">{result.agent_input}</span>
          </>
        )}
      </div>
    </div>
  )
}
