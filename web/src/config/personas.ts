/**
 * Persona registry — part of the swappable domain adapter (hackathon.md §5).
 *
 * Personas drive what a portal exposes: which sample queries seed the console
 * and how the surface is framed. On the day only this file (and the backend
 * adapter) changes. Nothing in the core imports domain logic — only this config.
 *
 * **Every `id` here must exist in the backend adapter's persona table**
 * (`backend/src/app/adapter/personas.py`). `POST /query` resolves the persona id
 * through `get_persona()` and answers **400 Unknown persona** when it cannot, so
 * an invented id does not degrade — it stops the console from running at all.
 * The adapter declares exactly two: `operations_lead` and `client`.
 *
 * `roles` is a list because one adapter persona legitimately serves several
 * portals: the admin and devops consoles drive the same operations persona the
 * AI-team console does. Duplicating the entry per portal would mean two
 * registry rows with the same id, which {@link getPersona} could not tell apart.
 */

import type { Role } from '@/lib/stream'

/** A persona the console can operate as. */
export interface Persona {
  /**
   * Stable id sent to the backend as `QueryRequest.persona`. Must be a persona
   * the backend adapter declares.
   */
  id: string
  /** Display name. */
  name: string
  /** Which portals may operate as this persona. */
  roles: Role[]
  /** One-line description of who this persona is / who they serve. */
  blurb: string
  /** Seed queries surfaced as one-click prompts on the console. */
  sampleQueries: string[]
}

/**
 * The built-in personas. Swap wholesale per domain on the day.
 *
 * The sample queries name only things that exist: the three registered tools —
 * `update_request_status` (HIGH risk, so it gates), `assign_request` (MEDIUM) and
 * `add_case_note` (LOW) — and topics the seeded corpus actually covers (request
 * closure, escalation/SLA, login-failure runbook). They deliberately carry no
 * hard-coded `req-…` id: the adapter generates its request ids at seed time, so a
 * literal id here would name a record that does not exist.
 */
export const PERSONAS: Persona[] = [
  {
    id: 'operations_lead',
    name: 'Operations Lead',
    roles: ['ai_team', 'admin', 'devops'],
    blurb: 'Support operations lead who triages, assigns and resolves service requests.',
    sampleQueries: [
      'Which open requests are at risk of breaching their SLA, and what should happen to them?',
      'What has to be true before a request can be set to resolved?',
      'Assign the oldest unassigned request to an agent with capacity',
    ],
  },
  {
    id: 'client',
    name: 'Customer',
    roles: ['client'],
    blurb: 'End customer tracking and annotating their own service requests.',
    sampleQueries: [
      'What is the status of my open request?',
      'Add a note to my request: the issue is still not resolved',
      'What is the resolution SLA for my request, and when does it escalate?',
    ],
  },
]

/** Personas visible to a given role. */
export function personasForRole(role: Role): Persona[] {
  return PERSONAS.filter((p) => p.roles.includes(role))
}

/** Look up a persona by id, or `undefined`. */
export function getPersona(id: string | null | undefined): Persona | undefined {
  return PERSONAS.find((p) => p.id === id)
}
