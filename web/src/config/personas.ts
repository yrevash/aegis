/**
 * Persona registry — part of the swappable domain adapter (hackathon.md §5).
 *
 * Personas drive what a portal exposes: which sample queries seed the console
 * and how the surface is framed. They are intentionally domain-neutral here so
 * the "weapon" reads as generic; on the day only this file (and the backend
 * adapter) changes. Nothing in the core imports domain logic — only this config.
 */

import type { Role } from '@/lib/stream'

/** A persona the console can operate as. */
export interface Persona {
  /** Stable id sent to the backend as `QueryRequest.persona`. */
  id: string
  /** Display name. */
  name: string
  /** Which portal this persona belongs to. */
  role: Role
  /** One-line description of who this persona is / who they serve. */
  blurb: string
  /** Seed queries surfaced as one-click prompts on the console. */
  sampleQueries: string[]
}

/** The built-in personas. Swap wholesale per domain on the day. */
export const PERSONAS: Persona[] = [
  {
    id: 'ops-analyst',
    name: 'Operations Analyst',
    role: 'ai_team',
    blurb: 'Front-line operator resolving inbound cases against policy and SLA.',
    sampleQueries: [
      'Resolve case #4821 — customer reports a duplicate charge on a premium account',
      'What is the fastest compliant path to close ticket TCK-338?',
      'Draft a customer response for the delayed shipment on order 90142',
    ],
  },
  {
    id: 'risk-reviewer',
    name: 'Risk Reviewer',
    role: 'ai_team',
    blurb: 'Approves high-value actions the agent proposes; owns the human gate.',
    sampleQueries: [
      'Should we auto-approve the refund of $4,200 on account A-771?',
      'Assess exposure before escalating incident INC-1190 to tier 3',
      'Assess a borderline case with sparse history — should the model abstain?',
    ],
  },
  {
    id: 'platform-admin',
    name: 'Platform Admin',
    role: 'admin',
    blurb: 'Owns model routing, guardrail policy, audit and observability.',
    sampleQueries: [
      'Trace the last high-risk action end to end and show the audit entry',
      'Replay run for case #4821 and show token + cost attribution',
      'Show what happens when a tenant exceeds its monthly budget cap',
    ],
  },
]

/** Personas visible to a given role. */
export function personasForRole(role: Role): Persona[] {
  return PERSONAS.filter((p) => p.role === role)
}

/** Look up a persona by id, or `undefined`. */
export function getPersona(id: string | null | undefined): Persona | undefined {
  return PERSONAS.find((p) => p.id === id)
}
