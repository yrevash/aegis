/**
 * Render-smoke for the recharts-free risk matrix grid. Runs under a plain Node
 * environment via `renderToStaticMarkup` — enough to prove it mounts and emits
 * the axis labels, cells and plotted markers. (Charts hang SSR, so the
 * chart-bearing views are covered by their pure logic + build/typecheck.)
 */

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { RiskEntry } from '@/types/api'

import { buildMatrix } from './riskMatrix'
import { RiskMatrixGrid } from './RiskMatrixGrid'

const scale = { likelihood: [1, 2, 3, 4, 5], impact: [1, 2, 3, 4, 5] }

const risks: RiskEntry[] = [
  {
    id: 'AA-01',
    title: 'Excessive agency',
    category: 'Autonomy',
    likelihood: 5,
    impact: 5,
    mitigation: 'gate',
    control_ref: 'approval gate',
    residual: 'high',
  },
  {
    id: 'AA-02',
    title: 'Budget runaway',
    category: 'Governance',
    likelihood: 1,
    impact: 1,
    mitigation: 'budgets',
    control_ref: 'budgets',
    residual: 'low',
  },
]

describe('RiskMatrixGrid render-smoke', () => {
  const matrix = buildMatrix(scale, risks)
  const html = renderToStaticMarkup(
    createElement(RiskMatrixGrid, { matrix, selectedId: null, onSelect: () => {} }),
  )

  it('renders both axis labels', () => {
    expect(html).toContain('Impact →')
    expect(html).toContain('Likelihood →')
  })

  it('plots each risk id as a marker with an accessible label', () => {
    expect(html).toContain('AA-01')
    expect(html).toContain('AA-02')
    expect(html).toContain('likelihood 5, impact 5, High residual')
  })

  it('marks the worst corner cell', () => {
    expect(html).toContain('ring-block/40')
  })

  it('reflects a selection by dimming the unselected marker', () => {
    const selected = renderToStaticMarkup(
      createElement(RiskMatrixGrid, { matrix, selectedId: 'AA-01', onSelect: () => {} }),
    )
    expect(selected).toContain('opacity-45') // AA-02 dimmed while AA-01 is selected
    expect(selected).toContain('aria-pressed="true"')
  })
})
