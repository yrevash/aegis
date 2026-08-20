'use client'

import { Ban, Check, CircleSlash, Eraser, ShieldOff } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ReactElement } from 'react'

import type { VisionControlOutcome, VisionControlReport, VisionStage } from '@/lib/api/types'
import { cn } from '@/lib/utils'

/** Human label per stage — the ladder reads top to bottom in execution order. */
const STAGE_LABEL: Record<VisionStage, string> = {
  hygiene: 'Payload hygiene',
  injection_screen: 'Image-injection screen',
  image_pii: 'Image PII',
  vision_model: 'Vision model',
  output_rails: 'Output rails',
}

/**
 * Per-outcome chrome. `not_run` and `failed_closed` look different on purpose:
 * one is a control nobody enabled, the other is a control that was supposed to
 * run and didn't — so the result was refused rather than passed.
 */
const OUTCOME: Record<
  VisionControlOutcome,
  { label: string; icon: LucideIcon; dot: string; ink: string }
> = {
  passed: { label: 'passed', icon: Check, dot: 'bg-ok', ink: 'text-ok-ink' },
  blocked: { label: 'blocked', icon: Ban, dot: 'bg-block', ink: 'text-block-ink' },
  redacted: { label: 'redacted', icon: Eraser, dot: 'bg-blue-100', ink: 'text-blue-800' },
  not_run: {
    label: 'did not run',
    icon: CircleSlash,
    dot: 'bg-muted-foreground/40',
    ink: 'text-muted-foreground',
  },
  failed_closed: {
    label: 'failed closed',
    icon: ShieldOff,
    dot: 'bg-risk',
    ink: 'text-risk-ink',
  },
}

/**
 * The ordered control ladder — one row per control, in execution order,
 * **including the ones that did not run**.
 *
 * The ordering is the whole product: an image must clear the injection screen
 * before the model sees it. Showing the stages as a ladder rather than a set of
 * badges is what makes that visible, and showing the absent stages is what stops
 * a page full of green ticks from implying coverage nobody provided.
 */
export function ControlLadder({
  controls,
}: {
  controls: VisionControlReport[]
}): ReactElement {
  if (controls.length === 0) {
    return <p className="text-sm text-muted-foreground">No controls reported.</p>
  }

  return (
    <ol className="relative space-y-0">
      {controls.map((c, i) => {
        const chrome = OUTCOME[c.outcome]
        const Icon = chrome.icon
        const last = i === controls.length - 1
        return (
          <li key={c.stage} className="relative flex gap-3 pb-5 last:pb-0">
            {!last ? (
              <span className="absolute left-[11px] top-6 h-full w-px bg-border" aria-hidden />
            ) : null}
            <span
              className={cn(
                'relative z-10 mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full',
                chrome.dot,
                chrome.ink,
              )}
            >
              <Icon className="size-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-sm font-medium text-foreground">
                  {STAGE_LABEL[c.stage] ?? c.stage}
                </span>
                <span
                  className={cn(
                    'font-mono text-[0.68rem] uppercase tracking-wide',
                    chrome.ink,
                  )}
                >
                  {chrome.label}
                </span>
              </p>
              {c.detail ? (
                <p className="mt-0.5 text-[0.78rem] leading-snug text-muted-foreground">
                  {c.detail}
                </p>
              ) : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
