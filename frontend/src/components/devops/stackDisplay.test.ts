/**
 * Unit tests for the pure DevOps display logic. Deterministic — no React, no
 * recharts, no timers — so grouping, counting and (critically) the honest
 * offline posture are provable in isolation.
 */

import { describe, expect, it } from 'vitest'

import type { PatchResult, StackComponent } from '@/types/api'

import {
  CATEGORY_ORDER,
  filterByName,
  groupByCategory,
  hasVersion,
  patchPosture,
  sortByStatus,
  summarizePatches,
  summarizeStack,
  versionLabel,
} from './stackDisplay'

const components: StackComponent[] = [
  { name: 'React', category: 'frontend', package: 'react', version: '19.1.0', aegis_module: 'Console' },
  { name: 'FastAPI', category: 'backend', package: 'fastapi', version: '0.115.0', aegis_module: 'Aegis API' },
  { name: 'Python', category: 'runtime', package: 'python', version: '3.12.4', aegis_module: null },
  { name: 'Redis', category: 'infra', package: 'redis', version: '7.4.0', aegis_module: 'Aegis Cache' },
  { name: 'Mystery', category: 'backend', package: 'mystery', version: null, aegis_module: null },
]

describe('groupByCategory', () => {
  it('groups into the canonical runtime → backend → frontend → infra order', () => {
    const groups = groupByCategory(components)
    expect(groups.map((g) => g.category)).toEqual(['runtime', 'backend', 'frontend', 'infra'])
  })

  it('drops categories with no components (no empty sections)', () => {
    const groups = groupByCategory([components[0]]) // frontend only
    expect(groups).toHaveLength(1)
    expect(groups[0].category).toBe('frontend')
    expect(groups[0].label).toBe('Frontend')
  })

  it('keeps input row order within a group', () => {
    const backend = groupByCategory(components).find((g) => g.category === 'backend')
    expect(backend?.rows.map((r) => r.name)).toEqual(['FastAPI', 'Mystery'])
  })

  it('CATEGORY_ORDER stays the source of truth for section order', () => {
    expect(CATEGORY_ORDER).toEqual(['runtime', 'backend', 'frontend', 'infra'])
  })
})

describe('summarizeStack', () => {
  it('counts total, known-version and distinct categories', () => {
    expect(summarizeStack(components)).toEqual({
      total: 5,
      withVersion: 4,
      unknownVersion: 1,
      categories: 4,
    })
  })

  it('is all-zero for an empty inventory', () => {
    expect(summarizeStack([])).toEqual({ total: 0, withVersion: 0, unknownVersion: 0, categories: 0 })
  })
})

describe('versionLabel / hasVersion', () => {
  it('renders a real pin verbatim and marks it known', () => {
    expect(versionLabel('1.2.3')).toEqual({ text: '1.2.3', known: true })
  })

  it('renders null and blank as an honest not-installed label', () => {
    expect(versionLabel(null)).toEqual({ text: 'not installed / n-a', known: false })
    expect(versionLabel('   ')).toEqual({ text: 'not installed / n-a', known: false })
  })

  it('hasVersion agrees', () => {
    expect(hasVersion('1.0.0')).toBe(true)
    expect(hasVersion(null)).toBe(false)
    expect(hasVersion('')).toBe(false)
  })
})

const results: PatchResult[] = [
  { name: 'react', installed: '19.1.0', latest: '19.1.0', status: 'current' },
  { name: 'vite', installed: '6.0.1', latest: '6.0.7', status: 'outdated' },
  { name: 'pgvector', installed: '0.7.4', latest: null, status: 'unknown' },
  { name: 'langgraph', installed: '0.2.28', latest: '0.2.44', status: 'outdated' },
]

describe('summarizePatches', () => {
  it('tallies each status', () => {
    expect(summarizePatches(results)).toEqual({ total: 4, current: 1, outdated: 2, unknown: 1 })
  })
})

describe('sortByStatus', () => {
  it('surfaces worst-first: outdated, then unknown, then current', () => {
    const order = sortByStatus(results).map((r) => r.name)
    expect(order).toEqual(['langgraph', 'vite', 'pgvector', 'react'])
  })

  it('does not mutate the input array', () => {
    const before = results.map((r) => r.name)
    sortByStatus(results)
    expect(results.map((r) => r.name)).toEqual(before)
  })
})

describe('filterByName', () => {
  it('matches a case-insensitive substring', () => {
    expect(filterByName(results, 'RE').map((r) => r.name)).toEqual(['react'])
  })

  it('returns everything for a blank query', () => {
    expect(filterByName(results, '   ')).toHaveLength(4)
  })
})

describe('patchPosture — honest offline handling', () => {
  it('is always unverified when offline, even with everything "current"', () => {
    const summary = summarizePatches([results[0]]) // one current row
    expect(patchPosture(summary, false)).toBe('unverified')
  })

  it('is action-needed when online with outdated packages', () => {
    expect(patchPosture(summarizePatches(results), true)).toBe('action-needed')
  })

  it('downgrades to unverified when online but some packages are unknown', () => {
    const summary = summarizePatches([results[0], results[2]]) // current + unknown
    expect(patchPosture(summary, true)).toBe('unverified')
  })

  it('is current only when online, none outdated and none unknown', () => {
    const summary = summarizePatches([results[0]]) // one current row
    expect(patchPosture(summary, true)).toBe('current')
  })
})
