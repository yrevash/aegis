import { describe, expect, it } from 'vitest'

import type { AdminUser, Budget } from '@/types/api'

import { activeUserCount, budgetUtilisation, monthUsdCapTotal } from './governance'

const user = (id: number, is_active: boolean): AdminUser => ({
  id,
  username: `u${id}`,
  email: null,
  role: 'member',
  tenant_id: 1,
  is_active,
})

const budget = (window: 'day' | 'month', usd_cap: number | null): Budget => ({
  scope_type: 'tenant',
  scope_id: 1,
  window,
  token_cap: null,
  usd_cap,
  rpm: null,
  tpm: null,
})

describe('governance roll-up helpers', () => {
  it('counts only active users', () => {
    expect(activeUserCount([user(1, true), user(2, false), user(3, true)])).toBe(2)
  })

  it('sums month USD caps, ignoring day windows and null caps', () => {
    expect(monthUsdCapTotal([budget('month', 1000), budget('day', 50), budget('month', null), budget('month', 200)])).toBe(1200)
  })

  it('returns null when no month budget carries a USD cap', () => {
    expect(monthUsdCapTotal([budget('day', 50), budget('month', null)])).toBeNull()
  })

  it('computes clamped budget utilisation', () => {
    expect(budgetUtilisation(600, 1000)).toBeCloseTo(0.6)
    expect(budgetUtilisation(1500, 1000)).toBe(1)
  })

  it('returns null utilisation with no cap (no divide-by-zero)', () => {
    expect(budgetUtilisation(600, null)).toBeNull()
    expect(budgetUtilisation(600, 0)).toBeNull()
  })
})
