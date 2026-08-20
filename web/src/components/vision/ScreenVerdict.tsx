'use client'

import { ScanEye, ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react'
import type { ReactElement } from 'react'

import type { VisionScreenVerdict } from '@/lib/api/types'
import { cn } from '@/lib/utils'

/**
 * The image-injection screen's verdict, rendered as the loudest thing on the page.
 *
 * This is the differentiator, so it gets three visually distinct states and never
 * two. The one that matters most is the third:
 *
 * - **cleared** — a vision model read the image and found nothing addressed to an AI.
 * - **blocked** — it found rendered instructions, and the analysis model was never called.
 * - **not screened** — the control could not run, so the image was blocked
 *   *fail-closed*. Collapsing this into "blocked" would hide that no model looked
 *   at the image at all; collapsing it into "cleared" would be a lie. It gets its
 *   own state, its own colour and its own sentence.
 */
export function ScreenVerdictPanel({
  verdict,
}: {
  verdict: VisionScreenVerdict | null
}): ReactElement {
  if (verdict == null) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface-2/40 px-5 py-6">
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <ShieldQuestion className="size-4 shrink-0" />
          The injection screen was not reached — payload hygiene refused this image first.
        </p>
      </div>
    )
  }

  const state = !verdict.screened ? 'unscreened' : verdict.injection ? 'blocked' : 'cleared'

  const chrome = {
    cleared: {
      wrap: 'border-ok/60 bg-ok/10',
      ink: 'text-ok-ink',
      Icon: ShieldCheck,
      title: 'Injection screen cleared this image',
      lead: 'A vision model read the image before the analysing model did, and found no instructions aimed at an AI system.',
    },
    blocked: {
      wrap: 'border-block/60 bg-block/10',
      ink: 'text-block-ink',
      Icon: ShieldAlert,
      title: 'Blocked by the injection screen',
      lead: 'The image carries text addressed to an AI system. It was refused before the analysing model was called.',
    },
    unscreened: {
      wrap: 'border-risk/60 bg-risk/15',
      ink: 'text-risk-ink',
      Icon: ScanEye,
      title: 'Could not screen — blocked (fail-closed)',
      lead: 'No vision model looked at this image. Pixels have no offline signature backstop, so an unscreened image is blocked rather than passed.',
    },
  }[state]

  const { Icon } = chrome

  return (
    <div className={cn('rounded-lg border px-5 py-5 md:px-6', chrome.wrap)}>
      <div className="flex items-start gap-3">
        <Icon className={cn('mt-0.5 size-5 shrink-0', chrome.ink)} />
        <div className="min-w-0 space-y-2">
          <p className={cn('text-base font-semibold', chrome.ink)}>{chrome.title}</p>
          <p className="text-sm leading-snug text-foreground">{chrome.lead}</p>
          {verdict.reason ? (
            <p className="rounded-lg bg-card/70 px-3 py-2 text-[0.78rem] leading-snug text-muted-foreground">
              {verdict.reason}
            </p>
          ) : null}
          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[0.7rem] uppercase tracking-wide text-muted-foreground">
            <span>screened · {verdict.screened ? 'yes' : 'no'}</span>
            <span>text in image · {verdict.contains_text ? 'yes' : 'no'}</span>
            <span>injection · {verdict.injection ? 'yes' : 'no'}</span>
          </p>
        </div>
      </div>
    </div>
  )
}
