'use client'

import type { ReactElement } from 'react'

import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'

import {
  RESIDUAL_META,
  residualSignal,
  type ExposureBand,
  type MatrixCell,
  type RiskMatrix,
} from './riskMatrix'

/** Soft grid tint per inherent-exposure band (before mitigation). */
const CELL_TINT: Record<ExposureBand, string> = {
  low: 'bg-ok/8',
  medium: 'bg-risk/12',
  high: 'bg-block/12',
}

interface RiskMatrixGridProps {
  matrix: RiskMatrix
  /** Currently selected / focused risk id, or null. */
  selectedId: string | null
  /** Toggle selection when a plotted marker is activated. */
  onSelect: (id: string | null) => void
}

/**
 * The risk **matrix** itself: a likelihood(→) × impact(↑) grid with every risk
 * plotted in its cell, coloured by residual band. The grid tint shows inherent
 * exposure (worst = hot top-right corner); each marker's colour shows what is
 * left after the mitigating control.
 */
export function RiskMatrixGrid({ matrix, selectedId, onSelect }: RiskMatrixGridProps): ReactElement {
  const cols = matrix.likelihoods.length
  return (
    <div className="flex gap-2">
      {/* Impact axis label, rotated up the left edge. */}
      <div className="flex items-center">
        <span className="eyebrow rotate-180 whitespace-nowrap [writing-mode:vertical-rl]">
          Impact →
        </span>
      </div>

      <div className="min-w-0 flex-1">
        <div
          className="grid gap-1.5"
          style={{ gridTemplateColumns: `1.25rem repeat(${cols}, minmax(0, 1fr))` }}
        >
          {matrix.rows.map((row) => (
            <RowCells key={`i${row[0].impact}`} row={row} selectedId={selectedId} onSelect={onSelect} />
          ))}

          {/* Bottom likelihood tick row: empty corner + one tick per column. */}
          <span aria-hidden />
          {matrix.likelihoods.map((l) => (
            <span key={`lt${l}`} className="eyebrow pt-1 text-center tabular">
              {l}
            </span>
          ))}
        </div>

        <p className="eyebrow mt-1.5 text-center">Likelihood →</p>
      </div>
    </div>
  )
}

/** One matrix row: the impact tick then its cells left→right. */
function RowCells({
  row,
  selectedId,
  onSelect,
}: {
  row: MatrixCell[]
  selectedId: string | null
  onSelect: (id: string | null) => void
}): ReactElement {
  return (
    <>
      <span className="eyebrow flex items-center justify-end pr-1 tabular">{row[0].impact}</span>
      {row.map((cell) => (
        <Cell
          key={`${cell.likelihood}x${cell.impact}`}
          cell={cell}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      ))}
    </>
  )
}

/** A single grid cell with the risks plotted into it. */
function Cell({
  cell,
  selectedId,
  onSelect,
}: {
  cell: MatrixCell
  selectedId: string | null
  onSelect: (id: string | null) => void
}): ReactElement {
  return (
    <div
      className={cn(
        'flex aspect-square flex-wrap content-center items-center justify-center gap-1 rounded-md border border-border/60 p-1',
        CELL_TINT[cell.band],
        cell.worstCorner && 'ring-1 ring-block/40',
      )}
    >
      {cell.risks.map((risk) => {
        const token = SIGNALS[residualSignal(risk.residual)]
        const selected = selectedId === risk.id
        const dimmed = selectedId != null && !selected
        return (
          <button
            key={risk.id}
            type="button"
            onClick={() => onSelect(selected ? null : risk.id)}
            title={`${risk.title} — ${RESIDUAL_META[risk.residual].label} residual`}
            aria-label={`${risk.title}: likelihood ${risk.likelihood}, impact ${risk.impact}, ${RESIDUAL_META[risk.residual].label} residual`}
            aria-pressed={selected}
            className={cn(
              'inline-flex items-center rounded-full border px-1.5 py-0.5 font-mono text-[0.6rem] font-medium tabular transition-[opacity,box-shadow] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              token.border,
              token.bg,
              token.text,
              selected && 'ring-2 ring-ring',
              dimmed && 'opacity-45',
            )}
          >
            {risk.id}
          </button>
        )
      })}
    </div>
  )
}
