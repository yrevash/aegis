/**
 * Role → portal routing. `homePathFor` is the single mapping the router, the
 * root redirect and the login redirect all share, so pinning it here guards the
 * four-portal RBAC contract (each role owns exactly one portal path).
 */

import { describe, expect, it } from 'vitest'

import { homePathFor } from './RequireRole'

describe('homePathFor — role → portal path', () => {
  it('sends each of the four roles to its own portal', () => {
    expect(homePathFor('admin')).toBe('/admin')
    expect(homePathFor('ai_team')).toBe('/ai-team')
    expect(homePathFor('devops')).toBe('/devops')
    expect(homePathFor('client')).toBe('/client')
  })

  it('maps every role to a distinct path (no two roles collide)', () => {
    const paths = (['admin', 'ai_team', 'devops', 'client'] as const).map(homePathFor)
    expect(new Set(paths).size).toBe(paths.length)
  })
})
