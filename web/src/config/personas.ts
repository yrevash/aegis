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
 *
 * ## Each seed demonstrates a different capability, and each was measured
 *
 * A chip under the composer is the first thing a viewer clicks, so "a reasonable
 * question" is not the bar — the bar is that the run it starts *shows something*. The
 * three below were chosen by sending candidates at the running backend and reading the
 * event stream, not by guessing, and the width and route of all three are decided by
 * `aegis.agent.router`'s **deterministic** pass rather than by a model call, so they
 * behave the same way every time:
 *
 * 1. **Grounded retrieval.** 13 words, one clause: `_deterministic_depth` returns
 *    SINGLE, and the turn runs the full recall → retrieve → rerank pipeline. Measured:
 *    46 candidates reranked, a `provenance` event naming three origins, a cited answer.
 *    First in the list on purpose — `QueryBar` seeds its input with `sampleQueries[0]`
 *    on three other screens, so entry one has to be the cheap, reliable one.
 * 2. **The fan-out.** Four `?`/`;` clauses, so `_subquestion_count` is 4 and the width
 *    is `clamp(4)` = the tenant's cap. Measured: `depth=team fanout=4`, all four lanes
 *    of the sub-agent roster reporting (Research · Knowledge · Data · Policy), one real
 *    `add_case_note` tool call, and a synthesised answer over the four.
 * 3. **Long-term memory.** Matches the `memory` specialist's own keyword hints, so the
 *    supervisor hands the turn to a *different* specialist — one that answers from the
 *    memory subsystem and skips RAG and tools entirely. Measured: `role=memory`, and
 *    the console's recall chip fills in.
 *
 * ## What is deliberately NOT here: a chip that reaches the approval gate
 *
 * The gate is the product's signature moment and it has no honest seed question. Ten
 * candidate phrasings were run — "resolve the oldest open request", "set the
 * longest-waiting one to resolved", the same asks inside a fan-out where the Data lane
 * holds `update_request_status` in its allowlist — and **not one of them ever proposed
 * that tool**, so the gate never raised. The cause is structural rather than a matter
 * of wording: `UpdateStatusArgs` requires a `request_id`, the persona's tool roster is
 * write-only (there is no listing or lookup tool), and the operations-lead system
 * prompt says *never fabricate request ids* — so a planner that obeys the prompt has no
 * id it can justify and correctly declines. Naming the tool outright
 * (`Call update_request_status to set req-000003 to resolved`) does reach it, and is
 * then **blocked by the injection rail**, which is the rail behaving correctly.
 *
 * A chip that promises the gate and delivers a paragraph is worse than no chip, so
 * there is none. Reaching it needs a read-side tool in
 * `backend/src/app/adapter/tools.py` (a LOW-risk `find_requests`), which is an adapter
 * change and not a console one.
 */
export const PERSONAS: Persona[] = [
  {
    id: 'operations_lead',
    name: 'Operations Lead',
    roles: ['ai_team', 'admin', 'devops'],
    blurb: 'Support operations lead who triages, assigns and resolves service requests.',
    sampleQueries: [
      'What has to be true before a request can be set to resolved?',
      'Which requests are breaching SLA? What does our escalation policy require? What does the runbook say? Who approves it?',
      'What do you know about me and how I like requests handled?',
    ],
  },
  {
    id: 'client',
    name: 'Customer',
    roles: ['client'],
    blurb: 'End customer tracking and annotating their own service requests.',
    // Left as they were: this persona holds one tool (`add_case_note`, LOW) and no
    // fan-out remit, so there is no third capability for a third chip to demonstrate,
    // and these were not re-measured under a client sign-in.
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
