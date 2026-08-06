/**
 * Render + logic smoke tests for the Wave 0 shared component library (§5).
 *
 * The suite runs in a plain Node environment (no jsdom), so components are
 * exercised with `renderToStaticMarkup` — enough to prove they mount without
 * throwing and emit the expected structure/tokens. Effects (RAF count-up,
 * IntersectionObserver reveal) do not run under static rendering; those paths
 * fall back to their honest initial/visible state, which is what we assert.
 */

import { createElement, type ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import { gaugeDisplay } from '@/components/ui/gaugeDisplay'
import { InfoTip } from '@/components/ui/InfoTip'

import { BentoGrid, BentoTile } from './BentoGrid'
import { CapabilityMap } from './CapabilityMap'
import { ComparisonCard } from './ComparisonCard'
import { CountUp } from './CountUp'
import { KpiHero } from './KpiHero'
import { MiniTrend } from './MiniTrend'
import { RevealOnScroll } from './RevealOnScroll'
import { StatDelta } from './StatDelta'

const render = (el: ReactElement): string => renderToStaticMarkup(el)

describe('StatDelta', () => {
  it('infers up + good tone from a positive value', () => {
    const html = render(createElement(StatDelta, { value: 12, suffix: '%' }))
    expect(html).toContain('text-success')
    expect(html).toContain('12')
    expect(html).toContain('%')
  })

  it('infers down + bad tone from a negative value', () => {
    const html = render(createElement(StatDelta, { value: -8 }))
    expect(html).toContain('text-danger')
    expect(html).toContain('8')
  })

  it('lets tone be decoupled from direction (down but good)', () => {
    const html = render(createElement(StatDelta, { value: 5, direction: 'down', tone: 'good' }))
    expect(html).toContain('text-success')
  })
})

describe('CountUp', () => {
  it('renders the formatted target on static render', () => {
    const html = render(createElement(CountUp, { value: 1234, format: (n) => `$${Math.round(n)}` }))
    expect(html).toContain('$1234')
  })
})

describe('MiniTrend', () => {
  it('draws a quiet baseline below two points', () => {
    const html = render(createElement(MiniTrend, { data: [5] }))
    expect(html).toContain('stroke-dasharray')
  })

  it('draws a path for a real series and accepts {value}[]', () => {
    const html = render(createElement(MiniTrend, { data: [{ value: 1 }, { value: 4 }, { value: 2 }] }))
    expect(html).toContain('<path')
  })
})

describe('KpiHero', () => {
  it('renders label, value and a sample marker', () => {
    const html = render(
      createElement(KpiHero, {
        label: 'Cost saved',
        value: 128400,
        format: (n) => `$${Math.round(n).toLocaleString()}`,
        delta: { value: 12, suffix: 'vs last month' },
        trend: [1, 2, 3, 2, 4],
        signal: 'ok',
        sample: true,
      }),
    )
    expect(html).toContain('Cost saved')
    expect(html).toContain('$128,400')
    expect(html).toContain('sample')
  })
})

describe('BentoGrid / BentoTile', () => {
  it('is a 12-column grid', () => {
    // eslint-disable-next-line react/no-children-prop -- createElement props form
    const html = render(createElement(BentoGrid, { children: 'x' }))
    expect(html).toContain('grid-cols-12')
  })

  it('maps span 8 to the right responsive classes and hero elevation', () => {
    const html = render(
      // eslint-disable-next-line react/no-children-prop -- createElement props form
      createElement(BentoTile, { span: 8, rows: 2, hero: true, children: 'hero' }),
    )
    expect(html).toContain('lg:col-span-8')
    expect(html).toContain('lg:row-span-2')
    expect(html).toContain('shadow-pop')
  })
})

describe('RevealOnScroll', () => {
  it('renders children visible when it cannot observe (Node/reduced-motion)', () => {
    // eslint-disable-next-line react/no-children-prop -- createElement props form
    const html = render(createElement(RevealOnScroll, { children: 'reveal-me' }))
    expect(html).toContain('reveal-me')
    expect(html).toContain('animate-reveal')
  })
})

describe('ComparisonCard', () => {
  it('renders a titled table with a differs marker', () => {
    const html = render(
      createElement(ComparisonCard, {
        title: 'Access demo',
        columns: ['Operations lead', 'Client'],
        rows: [
          { label: 'Action', a: 'executed', b: 'proposed', diff: true },
          { label: 'Cost', a: '$0.004', b: '$0.002' },
        ],
      }),
    )
    expect(html).toContain('Access demo')
    expect(html).toContain('Operations lead')
    expect(html).toContain('differs')
    expect(html).toContain('<table')
  })
})

describe('CapabilityMap', () => {
  it('renders names, honest tech and a pulsing live dot', () => {
    const html = render(
      createElement(CapabilityMap, {
        items: [
          { name: 'Memory', tech: 'Postgres + pgvector', status: 'live' },
          { name: 'Gate', tech: 'human approval', status: 'pending' },
        ],
      }),
    )
    expect(html).toContain('Memory')
    expect(html).toContain('Postgres + pgvector')
    expect(html).toContain('animate-pip')
    expect(html).toContain('pending')
  })
})

describe('Gauge', () => {
  // The recharts chart requires a DOM (not SSR-safe), so the render is covered
  // by typecheck/build + Wave 2 visual QA; here we prove the value logic.
  it('clamps out-of-range values and formats the read-out', () => {
    expect(gaugeDisplay(1.4)).toEqual({ clamped: 1, pct: 100, readout: '100%' })
    expect(gaugeDisplay(-0.5)).toEqual({ clamped: 0, pct: 0, readout: '0%' })
    expect(gaugeDisplay(0.923)).toEqual({ clamped: 0.923, pct: 92, readout: '92%' })
    expect(gaugeDisplay(0.5, '½')).toEqual({ clamped: 0.5, pct: 50, readout: '½' })
    expect(gaugeDisplay(Number.NaN).pct).toBe(0)
  })
})

describe('InfoTip', () => {
  it('renders an accessible ⓘ trigger', () => {
    const html = render(
      createElement(TooltipProvider, {
        // eslint-disable-next-line react/no-children-prop -- createElement props form
        children: createElement(InfoTip, { children: 'Hybrid search — vector + graph + keyword' }),
      }),
    )
    expect(html).toContain('aria-label="More information"')
  })
})
