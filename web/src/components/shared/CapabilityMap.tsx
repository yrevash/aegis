'use client'

import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

export type CapabilityStatus = 'live' | 'idle' | 'pending' | 'off'

export interface Capability {
  /** Human module / step name, e.g. "Memory" or "Diagnose". */
  name: string
  /** Honest technical subtitle, e.g. "Postgres + pgvector". Never hidden. */
  tech?: string
  status: CapabilityStatus
}

interface CapabilityMapProps {
  items: Capability[]
  /** `row` = vertical strip (Loop steps); `grid` = bento of module tiles. */
  layout?: 'row' | 'grid'
  className?: string
}

/** status → { dot colour var, pulse, label } — colour is paired with a label. */
const STATUS: Record<CapabilityStatus, { hex: string; label: string; pulse: boolean }> = {
  live: { hex: 'var(--ok-ink)', label: 'live', pulse: true },
  pending: { hex: 'var(--risk-ink)', label: 'pending', pulse: false },
  idle: { hex: 'var(--muted-foreground)', label: 'idle', pulse: false },
  off: { hex: 'var(--border)', label: 'off', pulse: false },
}

function StatusDot({ status }: { status: CapabilityStatus }): ReactElement {
  const s = STATUS[status]
  return (
    <span
      aria-hidden
      className={cn('inline-block size-2 shrink-0 rounded-full', s.pulse && 'animate-pip')}
      style={{ background: s.hex, ['--pip-color' as string]: s.hex }}
    />
  )
}

/**
 * The Aegis module / step status grid (§5) — used for the Loop capability
 * strip, the Access-demo "who can do what" row, and sidebar module dots. Each
 * item shows its name, honest tech subtitle, and a status dot + word (so the
 * state never rides on colour alone).
 */
export function CapabilityMap({
  items,
  layout = 'row',
  className,
}: CapabilityMapProps): ReactElement {
  return (
    <ul
      className={cn(
        layout === 'grid'
          ? 'grid grid-cols-2 gap-3 sm:grid-cols-3'
          : 'flex flex-col gap-1',
        className,
      )}
    >
      {items.map((item) => {
        const s = STATUS[item.status]
        return (
          <li
            key={item.name}
            className={cn(
              'flex items-center gap-2.5 rounded-lg',
              layout === 'grid'
                ? 'border border-border/80 bg-card p-3'
                : 'px-2 py-1.5',
            )}
          >
            <StatusDot status={item.status} />
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="t-label truncate text-foreground">{item.name}</span>
              {item.tech && (
                <span className="truncate font-mono text-[0.62rem] text-muted-foreground/80">
                  {item.tech}
                </span>
              )}
            </span>
            <span
              className="eyebrow ml-auto shrink-0 text-[0.56rem]"
              style={{ color: s.hex }}
            >
              {s.label}
            </span>
          </li>
        )
      })}
    </ul>
  )
}