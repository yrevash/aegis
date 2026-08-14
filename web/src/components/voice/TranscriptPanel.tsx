'use client'

import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { formatSeconds } from '@/components/voice/wav'
import type { VoiceTranscribeResponse } from '@/lib/api/types'

/**
 * The transcript, its time-aligned segments and their confidence.
 *
 * `confidence` is shown only where the provider actually reported one. The fleet's
 * hosted Whisper deployment reports none today — the gateway's segment parser
 * carries id/start/end/text only — so the panel says "confidence not reported"
 * once, at the top, rather than printing a plausible number per row. That
 * distinction is the whole point: a fabricated 0.94 next to every line would read
 * as measurement.
 */
export function TranscriptPanel({
  result,
}: {
  result: VoiceTranscribeResponse
}): ReactElement {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="agent" className="font-mono uppercase">
          {result.language ?? 'language not reported'}
        </Badge>
        <Badge tone="neutral" className="font-mono">
          {formatSeconds(result.duration_seconds)}
        </Badge>
        <Badge tone="neutral" className="font-mono">
          {result.chunk_count} {result.chunk_count === 1 ? 'request' : 'requests'}
        </Badge>
        <Badge tone={result.has_confidence ? 'ml' : 'neutral'} className="font-mono">
          {result.has_confidence ? 'confidence reported' : 'confidence not reported'}
        </Badge>
        <span className="ml-auto font-mono text-[0.72rem] tabular-nums text-muted-foreground">
          {result.model || 'model not reported'} · ${result.cost_usd.toFixed(6)} ·{' '}
          {result.audio_seconds_billed.toFixed(1)}s billed
        </span>
      </div>

      <p className="rounded-lg border border-border bg-surface-2/30 px-3.5 py-3 text-sm leading-relaxed text-foreground">
        {result.transcript || (
          <span className="text-muted-foreground">
            Nothing was transcribed — see the rail verdict for why.
          </span>
        )}
      </p>

      {result.chunking && (
        <p className="text-xs text-muted-foreground">
          <span className="eyebrow mr-1.5">chunking</span>
          {result.chunking}
        </p>
      )}

      {result.segments.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-2/50 text-left">
                <th className="px-3 py-2 font-medium text-muted-foreground">Time</th>
                <th className="px-3 py-2 font-medium text-muted-foreground">Segment</th>
                <th className="px-3 py-2 text-right font-medium text-muted-foreground">
                  Confidence
                </th>
              </tr>
            </thead>
            <tbody>
              {result.segments.map((seg) => (
                <tr key={seg.index} className="border-b border-border/60 last:border-0">
                  <td className="px-3 py-2 align-top font-mono text-[0.72rem] tabular-nums text-muted-foreground">
                    {formatSeconds(seg.start)}–{formatSeconds(seg.end)}
                    {result.chunk_count > 1 && (
                      <span className="ml-1.5 opacity-70">#{seg.chunk}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 align-top text-foreground">{seg.text}</td>
                  <td className="px-3 py-2 text-right align-top font-mono text-[0.72rem] tabular-nums">
                    {seg.confidence == null ? (
                      <span className="text-muted-foreground">not reported</span>
                    ) : (
                      <span className="text-ml-ink">{Math.round(seg.confidence * 100)}%</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
