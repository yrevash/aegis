'use client'

import type { ReactElement } from 'react'

import { formatSeconds } from '@/components/voice/wav'
import type { VoiceSegmentRow } from '@/lib/api/types'

/** A segment that actually carries a span, so it can be placed on a track. */
export interface TimedSegment {
  index: number
  chunk: number
  start: number
  end: number
  text: string
}

/**
 * The segments that can be drawn, in clip order.
 *
 * `start` and `end` are both nullable — a provider that returns text without
 * timings is a real case — and a segment without them has no position on a
 * track. Those rows are dropped here rather than pinned to zero, and the caller
 * says how many were dropped.
 */
export function timedSegments(segments: readonly VoiceSegmentRow[]): TimedSegment[] {
  return segments
    .filter((s) => s.start != null && s.end != null && (s.end as number) >= (s.start as number))
    .map((s) => ({
      index: s.index,
      chunk: s.chunk,
      start: s.start as number,
      end: s.end as number,
      text: s.text,
    }))
    .sort((a, b) => a.start - b.start)
}

/**
 * The span the track is scaled to: the reported clip duration, or — when the
 * provider reported none — the last segment's end, which is a measured value
 * rather than a guessed one.
 */
export function timelineSpan(
  segments: readonly TimedSegment[],
  durationSeconds: number | null,
): number {
  if (durationSeconds != null && durationSeconds > 0) return durationSeconds
  return segments.reduce((max, s) => Math.max(max, s.end), 0)
}

/**
 * Where each chunk after the first begins, as a 0..1 fraction of the span.
 *
 * The server splits a long recording on silence and transcribes each piece as its
 * own request; `chunk` records which piece a segment came from. Those boundaries
 * are the only structure the *capture* waveform can honestly carry, which is what
 * {@link Waveform}'s `marks` prop is for.
 */
export function chunkBoundaries(segments: readonly TimedSegment[], span: number): number[] {
  if (span <= 0) return []
  const firstStart = new Map<number, number>()
  for (const s of segments) {
    const current = firstStart.get(s.chunk)
    if (current == null || s.start < current) firstStart.set(s.chunk, s.start)
  }
  return [...firstStart.entries()]
    .filter(([chunk]) => chunk > 0)
    .sort((a, b) => a[0] - b[0])
    .map(([, start]) => start / span)
    .filter((fraction) => fraction > 0 && fraction < 1)
}

/**
 * The one true within-clip series this product has: speech laid out over time.
 *
 * Every other figure on this screen is a snapshot of the whole recording, but
 * `segments[]` carries a real `start`/`end` per line, so the clip has structure
 * a reader can see — where the speech is, where the silence is, and where the
 * server split the recording into separate requests.
 *
 * It is deliberately not a chart-library plot. There is no quantity on a second
 * axis; the mark *is* the interval, and drawing it as bars against a value axis
 * would invent one. Adjacent segments are contiguous, so they alternate between
 * two intensities of the one hue — that separates neighbours without claiming
 * they differ in kind, and the row below every block carries the same facts as
 * text in the segment table.
 */
export function SegmentTimeline({
  segments,
  span,
  chunkCount,
}: {
  segments: readonly TimedSegment[]
  span: number
  chunkCount: number
}): ReactElement {
  const marks = chunkBoundaries(segments, span)

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div
        role="img"
        aria-label={`${segments.length} spoken segments across ${formatSeconds(span)}${
          chunkCount > 1 ? `, split into ${chunkCount} transcription requests` : ''
        }`}
        className="relative h-14 w-full overflow-hidden rounded-lg border border-border bg-surface-2/40"
      >
        {segments.map((s, i) => (
          <div
            key={s.index}
            title={`${formatSeconds(s.start)}–${formatSeconds(s.end)} · ${s.text}`}
            className="absolute top-2 bottom-2 rounded-[3px]"
            style={{
              left: `${(s.start / span) * 100}%`,
              // A visible floor, so a one-word segment is a mark rather than a
              // sliver that reads as a rendering fault.
              width: `max(3px, calc(${Math.max(0, ((s.end - s.start) / span) * 100)}% - 1px))`,
              background: 'var(--blue-600)',
              opacity: i % 2 === 0 ? 1 : 0.72,
            }}
          />
        ))}
        {marks.map((at, i) => (
          <span
            key={`chunk-${i}`}
            aria-hidden
            className="absolute top-0 bottom-0 w-px bg-muted-foreground/70"
            style={{ left: `${at * 100}%` }}
          />
        ))}
      </div>

      <div className="flex items-center justify-between">
        <span className="eyebrow">0:00</span>
        {marks.length > 0 ? (
          <span className="eyebrow">
            {marks.length} chunk {marks.length === 1 ? 'boundary' : 'boundaries'}
          </span>
        ) : null}
        <span className="eyebrow">{formatSeconds(span)}</span>
      </div>
    </div>
  )
}
