/**
 * In-browser fixtures backing mock mode for the `/memory/*` and `/ops/*`
 * surfaces (the Memory glass-box and the Self-improvement loop).
 *
 * These stand in for the FastAPI routes so both views render fully with no
 * backend — the offline demo the stage screenshot is taken against. All data is
 * coherent with the console scenario (M. Reed, account A-771, duplicate charge
 * $4,200, refund flow) so the story reads end-to-end. Timestamps are anchored to
 * "now" so ages/recency read as fresh in the demo.
 */

import type {
  MemoryFactsResponse,
  MemoryMessagesResponse,
  MemoryProfileResponse,
  MemorySessionsResponse,
  MemoryWritesResponse,
  RecallDebugResponse,
} from '@/types/memory'
import type {
  OpsActivePromptResponse,
  OpsDiagnoseRequest,
  OpsDiagnoseResponse,
  OpsEvalsResponse,
  OpsPendingReleasesResponse,
  OpsPromptsResponse,
  OpsReleaseDecisionResponse,
  OpsReleaseRequest,
  OpsReleaseResponse,
  OpsRollbackResponse,
} from '@/types/ops'

const HOUR = 3_600_000
const DAY = 86_400_000
const iso = (agoMs: number): string => new Date(Date.now() - agoMs).toISOString()

// ── Memory · semantic facts (bitemporal) ────────────────────────────────────

/** Mock `GET /memory/facts`. Rich bitemporal set incl. one invalidated fact. */
export function mockMemoryFacts(subject: string, includeInvalid: boolean): MemoryFactsResponse {
  const all: MemoryFactsResponse['rows'] = [
    {
      id: 4101,
      subject_id: subject,
      fact_type: 'preference',
      subject: 'M. Reed',
      predicate: 'prefers_contact_via',
      object: 'email',
      text: 'Prefers email over phone for support contact.',
      confidence: 0.94,
      importance: 7,
      access_count: 12,
      valid_at: iso(9 * DAY),
      invalid_at: null,
      created_at: iso(9 * DAY),
      expired_at: null,
      source_turn_ids: [1841, 1990],
      supersedes_id: 4090,
      is_valid: true,
    },
    {
      id: 4102,
      subject_id: subject,
      fact_type: 'entitlement',
      subject: 'Account A-771',
      predicate: 'is_in_segment',
      object: 'Premium',
      text: 'Account A-771 is a Premium-tier account (entitled to the Premium SLA).',
      confidence: 0.99,
      importance: 9,
      access_count: 31,
      valid_at: iso(180 * DAY),
      invalid_at: null,
      created_at: iso(180 * DAY),
      expired_at: null,
      source_turn_ids: [402],
      supersedes_id: null,
      is_valid: true,
    },
    {
      id: 4103,
      subject_id: subject,
      fact_type: 'event',
      subject: 'Charge $4,200',
      predicate: 'flagged_as',
      object: 'duplicate',
      text: 'A $4,200 charge on A-771 was flagged as a duplicate settlement.',
      confidence: 0.88,
      importance: 8,
      access_count: 7,
      valid_at: iso(2 * DAY),
      invalid_at: null,
      created_at: iso(2 * DAY),
      expired_at: null,
      source_turn_ids: [4821],
      supersedes_id: null,
      is_valid: true,
    },
    {
      id: 4104,
      subject_id: subject,
      fact_type: 'attribute',
      subject: 'M. Reed',
      predicate: 'located_in',
      object: 'Austin, TX',
      text: 'Customer is located in Austin, TX (US-Central timezone).',
      confidence: 0.9,
      importance: 4,
      access_count: 5,
      valid_at: iso(210 * DAY),
      invalid_at: null,
      created_at: iso(210 * DAY),
      expired_at: null,
      source_turn_ids: [88],
      supersedes_id: null,
      is_valid: true,
    },
    {
      id: 4105,
      subject_id: subject,
      fact_type: 'history',
      subject: 'M. Reed',
      predicate: 'prior_refunds_90d',
      object: '2',
      text: 'Has 2 prior refunds in the last 90 days (chargeback-risk signal).',
      confidence: 0.96,
      importance: 6,
      access_count: 9,
      valid_at: iso(6 * DAY),
      invalid_at: null,
      created_at: iso(6 * DAY),
      expired_at: null,
      source_turn_ids: [3390, 4821],
      supersedes_id: null,
      is_valid: true,
    },
    // The invalidated (superseded) belief — the bitemporal timeline in action.
    {
      id: 4090,
      subject_id: subject,
      fact_type: 'preference',
      subject: 'M. Reed',
      predicate: 'prefers_contact_via',
      object: 'phone',
      text: 'Prefers phone over email for support contact.',
      confidence: 0.71,
      importance: 7,
      access_count: 3,
      valid_at: iso(160 * DAY),
      invalid_at: iso(9 * DAY),
      created_at: iso(160 * DAY),
      expired_at: iso(9 * DAY),
      source_turn_ids: [211],
      supersedes_id: null,
      is_valid: false,
    },
  ]
  const rows = includeInvalid ? all : all.filter((r) => r.is_valid)
  return { subject, rows }
}

// ── Memory · structured profile ─────────────────────────────────────────────

/** Mock `GET /memory/profile` — the structured "what the agent knows" doc. */
export function mockMemoryProfile(subject: string): MemoryProfileResponse {
  return {
    subject,
    updated_at: iso(2 * HOUR),
    data: {
      name: 'Marcus Reed',
      account: 'A-771',
      tier: 'premium',
      customer_since: '2023-02-11',
      contact_preference: 'email',
      location: 'Austin, TX',
      language: 'en-US',
      open_cases: 1,
      lifetime_value_usd: 18_400,
      sentiment: 'neutral',
      risk_flags: ['duplicate_charge_dispute', 'elevated_refund_rate'],
      entitlements: ['premium_sla', 'priority_routing'],
      interests: ['billing_accuracy', 'fast_resolution'],
    },
  }
}

// ── Memory · episodic sessions + messages ───────────────────────────────────

/** Mock `GET /memory/sessions`. */
export function mockMemorySessions(subject: string): MemorySessionsResponse {
  return {
    subject,
    rows: [
      {
        id: 'sess_4821',
        subject_id: subject,
        persona: 'operations_lead',
        turn_count: 8,
        summary:
          'Customer disputes a $4,200 duplicate charge on A-771. Confirmed duplicate against the processor; refund drafted and routed to the human gate (amount over the auto-approval ceiling).',
        created_at: iso(2 * DAY),
        last_active_at: iso(3 * HOUR),
      },
      {
        id: 'sess_3390',
        subject_id: subject,
        persona: 'risk-reviewer',
        turn_count: 5,
        summary:
          'Incident INC-1190 escalation review. Predicted SLA-breach risk elevated; escalation to tier 3 proposed and queued for approval.',
        created_at: iso(5 * DAY),
        last_active_at: iso(5 * DAY),
      },
      {
        id: 'sess_2210',
        subject_id: subject,
        persona: 'operations_lead',
        turn_count: 4,
        summary:
          'Onboarding + entitlement check. Confirmed Premium tier and Premium SLA; captured email as the preferred contact channel (superseding an earlier phone preference).',
        created_at: iso(9 * DAY),
        last_active_at: iso(9 * DAY),
      },
    ],
  }
}

/** Mock `GET /memory/sessions/{id}/messages`. */
export function mockMemoryMessages(sessionId: string): MemoryMessagesResponse {
  const base = [
    { role: 'user', origin: 'chat', content: 'There are two identical $4,200 charges on my account A-771 — I was billed twice.', importance: 8 },
    { role: 'assistant', origin: 'agent', content: 'I can see account A-771. Let me pull the recent settlements from the payment processor and check for a duplicate.', importance: 5 },
    { role: 'tool', origin: 'retrieval', content: 'processor.settlements(A-771) → 2 charges of $4,200 with matching auth code within 40s.', importance: 6 },
    { role: 'assistant', origin: 'agent', content: 'Confirmed: two identical settlements 40 seconds apart — this is a duplicate charge under Refund Policy v3.', importance: 7 },
    { role: 'assistant', origin: 'agent', content: 'Drafting a $4,200 refund. The amount is over the $2,000 auto-approval ceiling, so I am routing it to a human approver.', importance: 8 },
    { role: 'user', origin: 'chat', content: 'How long will the refund take once approved?', importance: 4 },
    { role: 'assistant', origin: 'agent', content: 'Once a reviewer approves, the refund settles in 3–5 business days. I have flagged it Premium-SLA priority.', importance: 5 },
    { role: 'assistant', origin: 'summary', content: 'Session summary updated: duplicate confirmed, refund drafted, awaiting human approval.', importance: 6 },
  ]
  return {
    session_id: sessionId,
    subject: 'cust-mreed',
    rows: base.map((m, i) => ({
      id: 9000 + i,
      session_id: sessionId,
      turn_index: i,
      role: m.role,
      origin: m.origin,
      content: m.content,
      importance: m.importance,
      created_at: iso(3 * HOUR - i * 90_000),
    })),
  }
}

// ── Memory · write-log changelog ────────────────────────────────────────────

/** Mock `GET /memory/writes` — the ADD/UPDATE/INVALIDATE changelog. */
export function mockMemoryWrites(subject: string): MemoryWritesResponse {
  return {
    subject,
    rows: [
      {
        id: 7050,
        op: 'ADD',
        fact_id: 4103,
        before: {},
        after: { predicate: 'flagged_as', object: 'duplicate', confidence: 0.88 },
        reason: 'Processor confirmed two identical settlements 40s apart on A-771.',
        model: 'genailab-maas-llama-3.3-70b',
        trace_id: 'trace-4821',
        ts: iso(2 * DAY),
      },
      {
        id: 7042,
        op: 'ADD',
        fact_id: 4105,
        before: {},
        after: { predicate: 'prior_refunds_90d', object: '2', confidence: 0.96 },
        reason: 'Rollup of refund events in the trailing 90-day window.',
        model: 'genailab-maas-llama-3.1-8b',
        trace_id: 'trace-3390',
        ts: iso(6 * DAY),
      },
      {
        id: 7020,
        op: 'INVALIDATE',
        fact_id: 4090,
        before: { object: 'phone', is_valid: true },
        after: { object: 'phone', is_valid: false, invalid_at: iso(9 * DAY) },
        reason: 'Superseded — customer stated they prefer email going forward.',
        model: 'genailab-maas-llama-3.3-70b',
        trace_id: 'trace-2210',
        ts: iso(9 * DAY),
      },
      {
        id: 7019,
        op: 'UPDATE',
        fact_id: 4101,
        before: { object: 'email', confidence: 0.82 },
        after: { object: 'email', confidence: 0.94, supersedes_id: 4090 },
        reason: 'Reinforced across two turns; confidence raised, supersedes the phone preference.',
        model: 'genailab-maas-llama-3.3-70b',
        trace_id: 'trace-2210',
        ts: iso(9 * DAY),
      },
      {
        id: 7001,
        op: 'ADD',
        fact_id: 4102,
        before: {},
        after: { predicate: 'is_in_segment', object: 'Premium', confidence: 0.99 },
        reason: 'Entitlement check at onboarding.',
        model: 'genailab-maas-llama-3.1-8b',
        trace_id: 'trace-0402',
        ts: iso(180 * DAY),
      },
    ],
  }
}

// ── Memory · recall debug ───────────────────────────────────────────────────

/** Mock `GET /memory/recall_debug` — ranked recall + assembled context block. */
export function mockRecallDebug(subject: string, query: string): RecallDebugResponse {
  const facts = [
    { key: 'fact:4103', text: 'A $4,200 charge on A-771 was flagged as a duplicate settlement.', score: 0.91, importance: 8, age_days: 2, injected: true },
    { key: 'fact:4102', text: 'Account A-771 is a Premium-tier account (Premium SLA).', score: 0.74, importance: 9, age_days: 180, injected: true },
    { key: 'fact:4105', text: 'Has 2 prior refunds in the last 90 days (chargeback-risk signal).', score: 0.68, importance: 6, age_days: 6, injected: true },
    { key: 'fact:4101', text: 'Prefers email over phone for support contact.', score: 0.41, importance: 7, age_days: 9, injected: false },
    { key: 'fact:4104', text: 'Customer is located in Austin, TX.', score: 0.19, importance: 4, age_days: 210, injected: false },
  ]
  const episodic = [
    { key: 'sess_4821#turn3', text: 'Confirmed: two identical settlements 40 seconds apart — duplicate under Refund Policy v3.', score: 0.86, importance: 7, age_days: 0.1, injected: true },
    { key: 'sess_4821#turn4', text: 'Drafting a $4,200 refund; over the $2,000 ceiling, routing to a human approver.', score: 0.79, importance: 8, age_days: 0.1, injected: true },
    { key: 'sess_3390#turn2', text: 'Incident INC-1190 escalation to tier 3 proposed.', score: 0.28, importance: 6, age_days: 5, injected: false },
  ]
  const injectedFacts = facts.filter((f) => f.injected)
  const injectedEp = episodic.filter((e) => e.injected)
  const workingMemory = [
    `## Working memory for: ${subject}`,
    `Query: "${query || 'refund status for the duplicate charge'}"`,
    '',
    '### Semantic facts (ranked, injected)',
    ...injectedFacts.map((f) => `- (${f.score.toFixed(2)}) ${f.text}`),
    '',
    '### Recent episodic context',
    ...injectedEp.map((e) => `- (${e.score.toFixed(2)}) ${e.text}`),
    '',
    '### Structured profile',
    '- Marcus Reed · Premium · Austin, TX · prefers email · LTV $18,400',
    '',
    'Budget: 512 tokens · assembled by relevance × recency × importance.',
  ].join('\n')
  return {
    subject,
    query: query || 'refund status for the duplicate charge',
    facts,
    episodic,
    working_memory: workingMemory,
    tokens_used: 337,
    recalled_fact_count: injectedFacts.length,
    recalled_message_count: injectedEp.length,
  }
}

// ── Ops · prompt versions + active ──────────────────────────────────────────

/** Mock `GET /ops/prompts`. */
export function mockOpsPrompts(promptKey: string): OpsPromptsResponse {
  return {
    prompt_key: promptKey,
    rows: [
      { id: 618, prompt_key: promptKey, version: 6, status: 'staged', created_by: 'diagnose-bot', notes: 'Proposed: add explicit refund-ceiling check + cite Refund Policy version.', created_at: iso(1 * HOUR) },
      { id: 617, prompt_key: promptKey, version: 5, status: 'active', created_by: 'a.okafor', notes: 'Tightened tool-call guardrail phrasing; +grounding requirement.', created_at: iso(6 * DAY) },
      { id: 616, prompt_key: promptKey, version: 4, status: 'archived', created_by: 'diagnose-bot', notes: 'Auto-promoted (low risk): added citation format.', created_at: iso(19 * DAY) },
      { id: 615, prompt_key: promptKey, version: 3, status: 'archived', created_by: 'a.okafor', notes: 'Reworded escalation policy.', created_at: iso(31 * DAY) },
      { id: 614, prompt_key: promptKey, version: 2, status: 'archived', created_by: 'system', notes: 'Initial hardening pass.', created_at: iso(48 * DAY) },
      { id: 613, prompt_key: promptKey, version: 1, status: 'archived', created_by: 'system', notes: 'Seed prompt.', created_at: iso(70 * DAY) },
    ],
  }
}

/** Mock `GET /ops/prompts/active`. */
export function mockOpsActivePrompt(promptKey: string): OpsActivePromptResponse {
  return {
    prompt_key: promptKey,
    version: 5,
    status: 'active',
    system_prompt: [
      'You are the Payments Operations agent.',
      'Ground every claim in retrieved context; if you cannot retrieve backing context, say so and stop.',
      'For any refund or cancellation, verify the entitlement tier and the governing Refund Policy before acting.',
      'Only call allowlisted tools. Never issue a refund above $2,000 without human approval.',
      'Cite the source (KB article or policy) for any customer-facing statement.',
    ].join('\n'),
    config: { temperature: 0.2, max_tokens: 800, model: 'genailab-maas-llama-3.3-70b', grounding_required: true },
    created_by: 'a.okafor',
    notes: 'Tightened tool-call guardrail phrasing; +grounding requirement.',
    cached: true,
  }
}

// ── Ops · eval trend ────────────────────────────────────────────────────────

/**
 * Mock `GET /ops/evals`. A time series across the four metric families so the
 * per-metric trend chart renders. Scores drift upward over the window (the loop
 * is improving), with the odd fail so the pass/fail split reads honestly.
 */
export function mockOpsEvals(promptKey: string, limit: number): OpsEvalsResponse {
  const metrics: { m: string; base: number; slope: number }[] = [
    { m: 'answer', base: 0.82, slope: 0.012 },
    { m: 'retrieval', base: 0.88, slope: 0.006 },
    { m: 'tool', base: 0.79, slope: 0.016 },
    { m: 'guardrail', base: 0.93, slope: 0.004 },
  ]
  const points = 14
  const rows: OpsEvalsResponse['rows'] = []
  let id = 8800
  for (let d = points - 1; d >= 0; d -= 1) {
    for (const { m, base, slope } of metrics) {
      const wobble = Math.sin((d + m.length) / 2.3) * 0.02
      const score = Math.min(0.995, Math.max(0.5, base + slope * (points - 1 - d) + wobble))
      rows.push({
        id: id++,
        run_id: `evalrun_${points - d}`,
        metric: m,
        score: Number(score.toFixed(3)),
        passed: score >= 0.75,
        detail: { prompt_key: promptKey, window_day: points - d },
        ts: iso(d * DAY),
      })
    }
  }
  return { rows: rows.slice(0, limit) }
}

// ── Ops · pending releases (the tiered gate) ────────────────────────────────

/** Mock `GET /ops/releases/pending`. */
export function mockOpsPendingReleases(limit: number): OpsPendingReleasesResponse {
  const rows: OpsPendingReleasesResponse['rows'] = [
    {
      approval_id: 'rel_618',
      prompt_key: 'payments_ops_agent',
      draft_version_id: 618,
      risk: 'high',
      reason: 'Draft v6 changes the refund-ceiling instruction (a guardrail-adjacent edit); eval delta +0.031 but a policy-touching change requires human sign-off.',
      created_at: iso(1 * HOUR),
    },
    {
      approval_id: 'rel_631',
      prompt_key: 'router_policy',
      draft_version_id: 631,
      risk: 'medium',
      reason: 'Draft alters model-routing thresholds; reversible but crosses a cost boundary — staged for approval.',
      created_at: iso(4 * HOUR),
    },
  ]
  return { rows: rows.slice(0, limit) }
}

// ── Ops · mutations (in-session, side-effect-free) ──────────────────────────

/** Mock `POST /ops/diagnose`. */
export function mockOpsDiagnose(body: OpsDiagnoseRequest): OpsDiagnoseResponse {
  return {
    draft_version_id: 618,
    failure_summary:
      'Across the last ' +
      String(body.limit ?? 50) +
      ' eval failures, the agent under-cited the governing Refund Policy version and twice approached the refund ceiling without an explicit check. Proposed draft v6 adds an explicit ceiling check and requires a policy-version citation.',
    failures_considered: body.limit ?? 50,
    metric_breakdown: { tool: 6, answer: 4, guardrail: 2, retrieval: 1 },
  }
}

/** Mock `POST /ops/release` — tiered gate: high-risk stages for approval. */
export function mockOpsRelease(body: OpsReleaseRequest): OpsReleaseResponse {
  return {
    outcome: 'staged_for_approval',
    risk_level: 'high',
    risk_reasons: ['policy-touching edit (refund ceiling)', 'guardrail-adjacent instruction changed'],
    eval_score: 0.861,
    baseline_score: 0.83,
    reason: 'Eval improved (+0.031) but the change touches a guardrail instruction — the tiered gate routes it to a human.',
    approval_id: `rel_${body.draft_version_id}`,
  }
}

/** Mock `POST /ops/rollback`. */
export function mockOpsRollback(promptKey: string): OpsRollbackResponse {
  return { prompt_key: promptKey, reverted: true, active_version: 4 }
}

/** Mock `POST /ops/releases/{id}/decide`. */
export function mockOpsReleaseDecision(approvalId: string, approved: boolean): OpsReleaseDecisionResponse {
  return {
    approval_id: approvalId,
    approved,
    outcome: approved ? 'promoted' : 'archived',
    prompt_key: 'payments_ops_agent',
    active_version: approved ? 6 : 5,
  }
}
