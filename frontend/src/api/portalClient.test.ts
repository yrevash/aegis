/**
 * The new per-role client functions, exercised through the mock transport. The
 * boot probe is forced to fail (node env: no backend), so `isMock()` is true and
 * each call resolves from the in-browser fixtures — pinning the exact response
 * shapes the backend agent must mirror.
 */

import { beforeAll, describe, expect, it } from 'vitest'

import {
  assignUserRole,
  checkPatches,
  getRiskMap,
  getSavings,
  getStack,
  login,
} from '@/api/client'
import { isMock, probeBackend } from '@/api/mode'

const failingFetch = (async () => {
  throw new Error('offline')
}) as unknown as typeof fetch

beforeAll(async () => {
  await probeBackend({ fetchImpl: failingFetch })
})

describe('login — mock role derivation', () => {
  it('maps usernames to the four portal roles', async () => {
    expect(isMock()).toBe(true)
    expect((await login({ username: 'admin', password: 'x' })).role).toBe('admin')
    expect((await login({ username: 'ai', password: 'x' })).role).toBe('ai_team')
    expect((await login({ username: 'aiteam', password: 'x' })).role).toBe('ai_team')
    expect((await login({ username: 'devops', password: 'x' })).role).toBe('devops')
    expect((await login({ username: 'jordan', password: 'x' })).role).toBe('client')
  })
})

describe('getStack — SBOM inventory', () => {
  it('returns categorised components with the pinned shape', async () => {
    const res = await getStack(null)
    expect(typeof res.generated_at).toBe('string')
    expect(res.components.length).toBeGreaterThan(0)
    for (const c of res.components) {
      expect(['runtime', 'backend', 'frontend', 'infra']).toContain(c.category)
      expect(typeof c.package).toBe('string')
    }
  })
})

describe('checkPatches — freshness check', () => {
  it('returns results and honestly marks the offline sample', async () => {
    const res = await checkPatches(undefined, null)
    expect(res.online).toBe(false)
    expect(res.results.length).toBeGreaterThan(0)
    for (const r of res.results) {
      expect(['current', 'outdated', 'unknown']).toContain(r.status)
    }
  })

  it('narrows to the requested packages', async () => {
    const res = await checkPatches(['langgraph'], null)
    expect(res.results.map((r) => r.name)).toEqual(['langgraph'])
  })
})

describe('getRiskMap — OWASP-Agentic heat-map', () => {
  it('returns risks on a 1..5 grid with residual bands', async () => {
    const res = await getRiskMap(null)
    expect(res.risks.length).toBeGreaterThanOrEqual(6)
    expect(res.scale.likelihood).toEqual([1, 2, 3, 4, 5])
    for (const r of res.risks) {
      expect(r.likelihood).toBeGreaterThanOrEqual(1)
      expect(r.likelihood).toBeLessThanOrEqual(5)
      expect(r.impact).toBeGreaterThanOrEqual(1)
      expect(r.impact).toBeLessThanOrEqual(5)
      expect(['low', 'medium', 'high']).toContain(r.residual)
    }
  })
})

describe('getSavings — baseline vs actual', () => {
  it('keeps saved = baseline − actual and a coherent percentage', async () => {
    const res = await getSavings(null)
    expect(res.saved_usd).toBe(res.baseline_cost_usd - res.actual_cost_usd)
    expect(res.saved_pct).toBeGreaterThan(0)
    expect(res.saved_pct).toBeLessThanOrEqual(1)
    expect(res.breakdown.length).toBeGreaterThan(0)
  })
})

describe('assignUserRole — reassignment echo', () => {
  it('echoes the user with the new role mapped to an RBAC label', async () => {
    const updated = await assignUserRole(3, 'devops', null)
    expect(updated.id).toBe(3)
    expect(typeof updated.role).toBe('string')
  })
})
