import { describe, expect, it } from 'vitest'

import { slaCountdown } from './sla'

const NOW = 1_700_000_000_000

describe('slaCountdown', () => {
  it('returns "no SLA" for a null or unparseable deadline', () => {
    expect(slaCountdown(null, NOW)).toEqual({ text: 'no SLA', urgency: 'none', ms: null })
    expect(slaCountdown('not-a-date', NOW).urgency).toBe('none')
  })

  it('flags overdue once the deadline has passed', () => {
    const r = slaCountdown(new Date(NOW - 1000).toISOString(), NOW)
    expect(r.urgency).toBe('overdue')
    expect(r.text).toBe('overdue')
    expect(r.ms).toBeLessThanOrEqual(0)
  })

  it('warns when under five minutes remain', () => {
    const r = slaCountdown(new Date(NOW + 2 * 60_000).toISOString(), NOW)
    expect(r.urgency).toBe('warn')
    expect(r.text).toContain('left')
  })

  it('is ok with comfortable time remaining', () => {
    const r = slaCountdown(new Date(NOW + 30 * 60_000).toISOString(), NOW)
    expect(r.urgency).toBe('ok')
  })
})
