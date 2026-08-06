import { useId, type ReactElement } from 'react'

import { chartHex } from '@/components/charts/palette'
import type { Signal } from '@/config/signals'

interface MiniTrendProps {
  /** Chronological values, oldest → newest. Accepts raw numbers or {value}. */
  data: number[] | { value: number }[]
  /** Series hue, resolved through the signal palette (§2.4). Default neutral. */
  color?: Signal
  /** Rendered height in px (default 40). Width is fluid. */
  height?: number
  /** `area` draws a filled gradient under the line; `line` is stroke-only. */
  variant?: 'line' | 'area'
}

const VIEW_W = 120

/**
 * A small inline trend for a KPI hero or stat tile — a dependency-free,
 * SSR/test-safe SVG that honours the signal palette. Below two finite points it
 * shows a quiet baseline rather than inventing a shape (numbers stay honest).
 * Self-contained so any surface can use it without touching `charts/` or
 * `metrics/`; the fuller live `Sparkline` remains available for polled history.
 */
export function MiniTrend({
  data,
  color = 'neutral',
  height = 40,
  variant = 'area',
}: MiniTrendProps): ReactElement {
  const gradientId = useId()
  const hex = chartHex(color)
  const values = (data as Array<number | { value: number }>)
    .map((d) => (typeof d === 'number' ? d : d.value))
    .filter((v) => Number.isFinite(v))

  const pad = 3
  const H = height

  if (values.length < 2) {
    return (
      <svg
        viewBox={`0 0 ${VIEW_W} ${H}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height: H }}
        aria-hidden
      >
        <line
          x1="0"
          y1={H - pad}
          x2={VIEW_W}
          y2={H - pad}
          stroke={hex}
          strokeOpacity="0.25"
          strokeWidth="1"
          strokeDasharray="2 3"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    )
  }

  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  const stepX = VIEW_W / (values.length - 1)
  const y = (v: number): number => H - pad - ((v - lo) / span) * (H - pad * 2)
  const line = values
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * stepX).toFixed(1)} ${y(v).toFixed(1)}`)
    .join(' ')
  const area = `${line} L${VIEW_W} ${H} L0 ${H} Z`

  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${H}`}
      preserveAspectRatio="none"
      className="w-full"
      style={{ height: H }}
      aria-hidden
    >
      {variant === 'area' && (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={hex} stopOpacity="0.18" />
              <stop offset="100%" stopColor={hex} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gradientId})`} />
        </>
      )}
      <path
        d={line}
        fill="none"
        stroke={hex}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}
