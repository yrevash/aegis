'use client'

import { useId, type ReactElement } from 'react'

interface SparklineProps {
  /** Chronological values, oldest → newest. Non-finite entries are ignored. */
  values: number[]
  /** Line/area/head colour (hex or CSS var expression). */
  color: string
  /** Fixed value domain; defaults to the data's own min/max with headroom. */
  domain?: [number, number]
}

const W = 120
const H = 32
const PAD = 3

/**
 * A compact, dependency-free trend line for one live metric. Renders the real
 * polled history as a smooth area + stroke with a pulsing "head" at the latest
 * sample. Below two points it shows a quiet baseline rather than inventing a
 * shape — the numbers stay honest until there is real history to draw.
 */
export function Sparkline({ values, color, domain }: SparklineProps): ReactElement {
  const gradientId = useId()
  const pts = values.filter((v) => Number.isFinite(v))

  if (pts.length < 2) {
    return (
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-8 w-full" aria-hidden>
        <line
          x1="0"
          y1={H - PAD}
          x2={W}
          y2={H - PAD}
          stroke={color}
          strokeOpacity="0.25"
          strokeWidth="1"
          strokeDasharray="2 3"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    )
  }

  const [lo, hi] = domain ?? [Math.min(...pts), Math.max(...pts)]
  const span = hi - lo || 1
  const stepX = W / (pts.length - 1)
  const y = (v: number): number => H - PAD - ((v - lo) / span) * (H - PAD * 2)
  const coords = pts.map((v, i) => [i * stepX, y(v)] as const)
  const line = coords.map(([x, cy], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${cy.toFixed(1)}`).join(' ')
  const area = `${line} L${W} ${H} L0 ${H} Z`
  const headTopPct = (y(pts[pts.length - 1]) / H) * 100

  return (
    <div className="relative w-full">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-8 w-full" aria-hidden>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.24" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gradientId})`} />
        <path
          d={line}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {/* Live head — the latest sample. Sits at the right edge; only its
          vertical position varies, so a plain overlay stays crisp. */}
      <span
        className="animate-pip absolute size-1.5 -translate-y-1/2 rounded-full"
        style={{ right: '-1px', top: `${headTopPct}%`, background: color, ['--pip-color' as string]: color }}
      />
    </div>
  )
}