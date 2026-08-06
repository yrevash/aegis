/**
 * Unit tests for the conformal-interval domain auto-scaling (the bug fix).
 *
 * The regression these lock down: the band used to render against a hardcoded
 * `[0, 1]` track, so any non-probability target (e.g. a 0–80h regression)
 * collapsed against the right edge. The domain must now derive from the data.
 */

import { describe, expect, it } from 'vitest'

import { conformalDomain, conformalSignals, formatValue, toPercent } from './conformalScale'

describe('conformalDomain — auto-scaling', () => {
  it('scales to a non-probability (0–80h) target instead of [0,1]', () => {
    const [min, max] = conformalDomain([32, 48], 40)
    // Domain must be well outside [0,1] so the band does not collapse.
    expect(min).toBeGreaterThan(1)
    expect(max).toBeLessThan(80)
    // With 12% padding on a span of 16 → 1.92 each side.
    expect(min).toBeCloseTo(32 - 1.92, 5)
    expect(max).toBeCloseTo(48 + 1.92, 5)
  })

  it('always contains both interval bounds and the prediction', () => {
    const interval: [number, number] = [10, 22]
    const prediction = 14
    const [min, max] = conformalDomain(interval, prediction)
    expect(min).toBeLessThanOrEqual(Math.min(...interval, prediction))
    expect(max).toBeGreaterThanOrEqual(Math.max(...interval, prediction))
  })

  it('expands the domain when the prediction falls outside the interval', () => {
    // Prediction above hi — domain must stretch to include it.
    const [min, max] = conformalDomain([10, 20], 30)
    expect(max).toBeGreaterThan(30)
    expect(min).toBeLessThan(10)
  })

  it('is order-insensitive to the interval bounds', () => {
    expect(conformalDomain([48, 32], 40)).toEqual(conformalDomain([32, 48], 40))
  })

  it('produces a centred window for a degenerate (zero-span) interval', () => {
    const [min, max] = conformalDomain([5, 5], 5)
    expect(min).toBeLessThan(5)
    expect(max).toBeGreaterThan(5)
    // Symmetric around the value so the dot centres.
    expect(5 - min).toBeCloseTo(max - 5, 5)
  })

  it('handles a probability-scale target correctly too', () => {
    const [min, max] = conformalDomain([0.6, 0.8], 0.72)
    expect(min).toBeCloseTo(0.6 - 0.2 * 0.12, 5)
    expect(max).toBeCloseTo(0.8 + 0.2 * 0.12, 5)
  })

  it('respects a custom pad ratio', () => {
    const [min, max] = conformalDomain([0, 100], 50, 0.5)
    expect(min).toBeCloseTo(-50, 5)
    expect(max).toBeCloseTo(150, 5)
  })
})

describe('toPercent — projection onto the track', () => {
  it('places interval bounds inside 0..100 (never flush at the edge)', () => {
    const domain = conformalDomain([32, 48], 40)
    const loPct = toPercent(32, domain)
    const hiPct = toPercent(48, domain)
    expect(loPct).toBeGreaterThan(0)
    expect(hiPct).toBeLessThan(100)
    expect(hiPct).toBeGreaterThan(loPct)
  })

  it('clamps out-of-domain values to [0,100]', () => {
    expect(toPercent(-999, [0, 10])).toBe(0)
    expect(toPercent(999, [0, 10])).toBe(100)
  })

  it('centres the midpoint of a symmetric domain', () => {
    expect(toPercent(50, [0, 100])).toBeCloseTo(50, 5)
  })
})

describe('formatValue — axis labels', () => {
  it('drops decimals as magnitude grows and appends the unit', () => {
    expect(formatValue(40, 'h')).toBe('40.0h')
    expect(formatValue(120)).toBe('120')
    expect(formatValue(0.72)).toBe('0.72')
  })
})

describe('conformalSignals — deferral labels', () => {
  it('labels the interval width and (non-singleton) prediction-set size', () => {
    expect(conformalSignals(0.29, 2)).toEqual({
      widthLabel: 'width 0.29',
      setLabel: 'set size 2',
    })
  })

  it('calls a set size of 1 a singleton', () => {
    expect(conformalSignals(0.09, 1).setLabel).toBe('singleton set')
  })

  it('appends the unit to the width figure', () => {
    expect(conformalSignals(6, null, 'h').widthLabel).toBe('width 6.00h')
  })

  it('is honest: null / undefined values yield null labels (never fabricated)', () => {
    expect(conformalSignals(null, null)).toEqual({ widthLabel: null, setLabel: null })
    expect(conformalSignals(undefined, undefined)).toEqual({
      widthLabel: null,
      setLabel: null,
    })
  })
})
