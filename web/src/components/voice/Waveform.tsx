'use client'

import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

interface WaveformProps {
  /** Normalised 0..1 amplitudes, oldest first. */
  values: number[]
  /** Bar colour (a CSS colour or var). */
  hex?: string
  /** Track height in px. */
  height?: number
  /** Optional 0..1 progress marker (segment playback / a chunk boundary). */
  marks?: number[]
  className?: string
  /** Accessible label — the waveform is decorative without one. */
  label?: string
}

/**
 * A bar waveform, drawn as plain divs.
 *
 * Deliberately not a chart library and not a canvas: this renders in SSR, adds no
 * bundle, and stays visually quiet inside a dense page. It draws exactly the
 * values it is given — an empty array is an empty track, never a placeholder
 * animation that implies audio nobody has recorded yet.
 */
export function Waveform({
  values,
  hex = 'var(--blue-200)',
  height = 72,
  marks = [],
  className,
  label,
}: WaveformProps): ReactElement {
  return (
    <div
      className={cn(
        'relative flex w-full items-center gap-px overflow-hidden rounded-lg border border-border bg-surface-2/40 px-2',
        className,
      )}
      style={{ height }}
      role="img"
      aria-label={label ?? 'Audio waveform'}
    >
      {values.length === 0 ? (
        <span className="w-full text-center text-xs text-muted-foreground">
          No audio yet
        </span>
      ) : (
        values.map((value, i) => (
          <span
            key={i}
            className="flex-1 rounded-full"
            style={{
              // A visible floor of 2px so a silent stretch reads as "silence
              // recorded", not as "the waveform stopped rendering".
              height: `${Math.max(2, Math.min(1, value) * (height - 16))}px`,
              background: hex,
              opacity: 0.35 + Math.min(1, value) * 0.65,
            }}
          />
        ))
      )}
      {marks.map((at, i) => (
        <span
          key={`mark-${i}`}
          className="absolute top-0 bottom-0 w-px bg-block/60"
          style={{ left: `${Math.max(0, Math.min(1, at)) * 100}%` }}
        />
      ))}
    </div>
  )
}
