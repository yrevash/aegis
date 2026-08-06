import { describe, expect, it } from 'vitest'

import type { AdminUser } from '@/types/api'
import type { Role } from '@/types/stream'

import {
  PORTAL_ROLES,
  ROLE_CATALOG,
  ROLE_RANK,
  adminCount,
  isAdminRole,
  isDemotionFromAdmin,
  normalizeRole,
  perRoleCounts,
  roleOptionGuard,
} from './roleCatalog'

const user = (id: number, username: string, role: string): AdminUser => ({
  id,
  username,
  email: null,
  role,
  tenant_id: 1,
  is_active: true,
})

describe('role catalog', () => {
  it('lists the four portal roles admin-first', () => {
    expect(PORTAL_ROLES).toEqual(['admin', 'ai_team', 'devops', 'client'])
  })

  it('carries a label, chip tone and "sees" line for every role', () => {
    for (const role of PORTAL_ROLES) {
      const meta = ROLE_CATALOG[role]
      expect(meta.role).toBe(role)
      expect(meta.label.length).toBeGreaterThan(0)
      expect(meta.sees.length).toBeGreaterThan(0)
      expect(meta.chip).toBeTruthy()
    }
  })

  it('ranks admin strictly above the focused roles', () => {
    expect(ROLE_RANK.admin).toBeGreaterThan(ROLE_RANK.ai_team)
    expect(ROLE_RANK.admin).toBeGreaterThan(ROLE_RANK.devops)
    expect(ROLE_RANK.admin).toBeGreaterThan(ROLE_RANK.client)
  })
})

describe('normalizeRole', () => {
  it('folds legacy mock labels onto portal roles', () => {
    expect(normalizeRole('platform_admin')).toBe('admin')
    expect(normalizeRole('tenant_admin')).toBe('admin')
    expect(normalizeRole('member')).toBe('client')
  })

  it('passes exact portal roles through unchanged', () => {
    const roles: Role[] = ['admin', 'ai_team', 'devops', 'client']
    for (const r of roles) expect(normalizeRole(r)).toBe(r)
  })

  it('buckets an unknown role into the least-privileged client tier', () => {
    expect(normalizeRole('something-new')).toBe('client')
  })

  it('recognises admin-tier roles', () => {
    expect(isAdminRole('platform_admin')).toBe(true)
    expect(isAdminRole('admin')).toBe(true)
    expect(isAdminRole('member')).toBe(false)
  })
})

describe('perRoleCounts', () => {
  it('tallies every portal bucket, including empties, off the raw role', () => {
    const users = [
      user(1, 'a', 'platform_admin'),
      user(2, 'b', 'tenant_admin'),
      user(3, 'c', 'member'),
      user(4, 'd', 'ai_team'),
    ]
    expect(perRoleCounts(users)).toEqual({ admin: 2, ai_team: 1, devops: 0, client: 1 })
  })

  it('counts admin-tier users for the only-admin guard', () => {
    expect(adminCount([user(1, 'a', 'platform_admin'), user(2, 'b', 'member')])).toBe(1)
    expect(adminCount([user(1, 'a', 'admin'), user(2, 'b', 'tenant_admin')])).toBe(2)
  })
})

describe('isDemotionFromAdmin', () => {
  it('flags only moves that strip admin', () => {
    expect(isDemotionFromAdmin('admin', 'client')).toBe(true)
    expect(isDemotionFromAdmin('admin', 'devops')).toBe(true)
    expect(isDemotionFromAdmin('admin', 'admin')).toBe(false)
    expect(isDemotionFromAdmin('client', 'admin')).toBe(false)
    expect(isDemotionFromAdmin('ai_team', 'client')).toBe(false)
  })
})

describe('roleOptionGuard — self-lockout safety', () => {
  const admins = [
    user(1, 'platform.admin', 'platform_admin'),
    user(2, 'nw.admin', 'tenant_admin'),
  ]

  it('locks demoting your OWN admin account', () => {
    const res = roleOptionGuard({
      user: admins[0],
      target: 'client',
      currentUsername: 'platform.admin',
      users: admins,
    })
    expect(res.disabled).toBe(true)
    expect(res.reason).toMatch(/self-lockout/i)
  })

  it('allows demoting another admin while more than one admin remains', () => {
    const res = roleOptionGuard({
      user: admins[1],
      target: 'client',
      currentUsername: 'platform.admin',
      users: admins,
    })
    expect(res.disabled).toBe(false)
    expect(res.reason).toBeNull()
  })

  it('falls back to protecting the only admin when the user is unknown', () => {
    const soleAdmin = [user(1, 'platform.admin', 'platform_admin'), user(2, 'nw.analyst', 'member')]
    const res = roleOptionGuard({
      user: soleAdmin[0],
      target: 'client',
      currentUsername: null,
      users: soleAdmin,
    })
    expect(res.disabled).toBe(true)
    expect(res.reason).toMatch(/only admin/i)
  })

  it('never guards a promotion or a lateral move', () => {
    const users = [user(1, 'nw.analyst', 'member')]
    expect(
      roleOptionGuard({ user: users[0], target: 'admin', currentUsername: null, users }).disabled,
    ).toBe(false)
    expect(
      roleOptionGuard({ user: users[0], target: 'devops', currentUsername: null, users }).disabled,
    ).toBe(false)
  })

  it('leaves admin assignable when several admins exist and it is not your row', () => {
    const many = [
      user(1, 'platform.admin', 'platform_admin'),
      user(2, 'nw.admin', 'tenant_admin'),
      user(3, 'contoso.admin', 'tenant_admin'),
    ]
    const res = roleOptionGuard({
      user: many[1],
      target: 'ai_team',
      currentUsername: 'platform.admin',
      users: many,
    })
    expect(res.disabled).toBe(false)
  })
})
