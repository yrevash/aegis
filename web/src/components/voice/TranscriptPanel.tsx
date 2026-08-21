'use client'

import type { ReactElement } from 'react'

import { Receipt } from '@/components/primitives/Receipt'
import { Badge } from '@/components/ui/Badge'
import type { VoiceTranscribeResponse } from '@/lib/api/types'

/**
 * The transcript itself — the evidence, never the input.
 *
 * The counts this panel used to restate (duration, requests, cost, the billed
 * seconds) are the screen's KPI band now, and the per-segment table is its own
 * `DataPanel` below the timeline. What is left is the one thing this card is
 * for: the words that were recognised, and the honest line on whether the
 * recording was split before they were.
 */
export function TranscriptPanel({
  result,
}: {
  result: VoiceTranscribeResponse
}): ReactElement {
  return (
    <div className="min-w-0 space-y-4">
      <Badge tone="agent" className="font-mono uppercase">
        {result.language ?? 'language not reported'}
      </Badge>

      <p className="rounded-lg border border-border bg-surface-2/30 px-3.5 py-3 text-sm leading-relaxed break-words text-foreground">
        {result.transcript || (
          <span className="text-muted-foreground">
            Nothing was transcribed — see the rail verdict for why.
          </span>
        )}
      </p>

      {result.chunking ? <Receipt label="Chunking" origin={result.chunking} /> : null}
    </div>
  )
}
