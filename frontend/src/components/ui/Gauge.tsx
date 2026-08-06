import type { ReactElement } from 'react'
import { PolarAngleAxis, RadialBar, RadialBarChart, ResponsiveContainer } from 'recharts'

import { chartHex } from '@/components/charts/palette'
import type { Signal } from '@/config/signals'
import { cn } from '@/lib/utils'

import { gaugeDisplay } from './gaugeDisplay'

export { gaugeDisplay } from './gaugeDisplay'

interface GaugeProps {
  /** Fraction to display, 0..1 (clamped). */
  value: number
  /** Small caption under the gauge (e.g. "Confidence"). */
  label?: string
  /** Large centred read-out (e.g. "92%"); defaults to the rounded percentage. */
  centerLabel?: string
  /** Arc hue, resolved through the signal palette (§2.4). Default agent. */
  color?: Signal
  /** Square size in px (default 128). */
  size?: number
  className?: string
}

/**
 * A thin recharts `RadialBar` wrapper for a single-value percentage
 * (Confidence, Budget used — §5). The only genuinely new chart type in the
 * foundation, added here so Console / Approvals / Governance can use it without
 * touching `charts/`. Stays inside the trust palette; light + dark aware.
 */
export function Gauge({
  value,
  label,
  centerLabel,
  color = 'agent',
  size = 128,
  className,
}: GaugeProps): ReactElement {
  const hex = chartHex(color)
  const { clamped, readout } = gaugeDisplay(value, centerLabel)
  // Scale the centre read-out to the gauge so it never collides with the ring:
  // small gauges (e.g. the 64px budget tile) shrink the number; large ones cap
  // at the original 24px so Confidence/Quality gauges look unchanged.
  const readoutPx = Math.round(Math.min(size * 0.3, 24))

  return (
    <div className={cn('flex flex-col items-center gap-1', className)}>
      <div className="relative" style={{ width: size, height: size }}>
        <ResponsiveContainer width="100%" height="100%">
          <RadialBarChart
            innerRadius="72%"
            outerRadius="100%"
            data={[{ name: label ?? 'value', value: clamped }]}
            startAngle={90}
            endAngle={-270}
          >
            <PolarAngleAxis type="number" domain={[0, 1]} angleAxisId={0} tick={false} />
            <RadialBar
              background={{ fill: 'var(--surface-2)' }}
              dataKey="value"
              cornerRadius={999}
              fill={hex}
              isAnimationActive
              animationDuration={700}
            />
          </RadialBarChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span
            className="tabular font-display font-semibold text-foreground"
            style={{ fontSize: readoutPx, lineHeight: 1 }}
          >
            {readout}
          </span>
        </div>
      </div>
      {label && <span className="eyebrow">{label}</span>}
    </div>
  )
}
