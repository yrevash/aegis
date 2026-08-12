/**
 * In-browser mock transport — the scripted, glass-box money-shot.
 *
 * Runs the full trajectory client-side with realistic pacing so `pnpm dev`
 * demonstrates the whole story with no backend: guardrails screen (and redact
 * PII from) the input, the graph animates as retrieval touches and *reranks*
 * nodes, the planner streams its chain-of-thought, an ML score arrives with its
 * conformal interval, SHAP attribution *and* its bounded-autonomy gate verdict,
 * every node reports what it cost, and a high-risk action either pauses at the
 * human gate or is denied outright — depending on the persona.
 *
 * The scenario is split into a **pure** {@link buildRunScript} (an ordered list
 * of `StreamEventBody`s the transport merely paces out) so it is deterministic
 * and unit-testable, and the persona branch that fixes the "multi-user is
 * cosmetic" gap:
 *
 * - `client` (an end customer) — own-scope retrieval only, and the action tool
 *   is **denied** (not in the persona's allowlist); the run never reaches the
 *   human gate.
 * - `operations_lead` / any admin persona — full retrieval, a bounded-autonomy
 *   ML gate, and a high-risk action that pauses for **human approval**.
 *
 * Every emitted object is a real {@link StreamEvent}, identical in shape to what
 * the live backend sends.
 */

import type { ApprovalDecision } from '@/lib/api/types'
import type { GraphEdge, GraphNode, StreamEvent, StreamEventBody } from '@/lib/stream'

import { BASE_EDGES, BASE_NODES } from './fixtures'
import type { RunController, RunTransport } from '@/lib/api/transport'

const node = (id: string): GraphNode => BASE_NODES.find((n) => n.id === id)!
const edge = (source: string, target: string): GraphEdge =>
  BASE_EDGES.find((e) => e.source === source && e.target === target)!

/** One scripted beat: an event body plus the pause to hold *before* emitting it. */
export interface ScriptStep {
  /** Milliseconds to wait before emitting {@link body}. */
  delayMs: number
  /** The event to emit (the transport stamps `run_id`/`seq`). */
  body: StreamEventBody
}

/**
 * A fully scripted run, split around the (optional) human-approval gate so the
 * transport can pause between the two halves.
 */
export interface RunScript {
  /** Events up to (and including) the gate, or the whole run when not gated. */
  lead: ScriptStep[]
  /** Whether the run pauses for a human decision after {@link lead}. */
  gated: boolean
  /** Events after the human decides — only meaningful when {@link gated}. */
  tail: (decision: ApprovalDecision) => ScriptStep[]
}

/** A customer/end-user persona — restricted scope, no action tools. */
function isClientPersona(persona: string | null): boolean {
  return persona !== null && /\b(client|customer)\b/i.test(persona)
}

/** Query asks for the abstain path (model too uncertain to act). */
function isAbstainQuery(query: string | null): boolean {
  return query !== null && /\b(abstain|insufficient|too uncertain|borderline|sparse)\b/i.test(query)
}

/** Query trips a governance budget / rate cap. */
function isBudgetQuery(query: string | null): boolean {
  return query !== null && /\b(budget|cap|exceed|over[-\s]?limit|spend limit)\b/i.test(query)
}

/** The PII-redaction guardrail used in both branches (masked text only). */
const REDACTION_GUARDRAIL: StreamEventBody = {
  type: 'guardrail',
  stage: 'input',
  verdict: 'redact',
  layer: 'pii',
  reason: 'Masked customer PII (SSN, email) before the model saw the query.',
  redactions: [{ kind: 'ssn' }, { kind: 'email' }],
  // Never carries raw PII: `before` is already partially masked, `after` fully.
  before_masked: 'Refund case #4821 for M. Reed — SSN ***-**-6789, email m****@example.com.',
  after: 'Refund case #4821 for M. Reed — SSN [REDACTED_SSN], email [REDACTED_EMAIL].',
}

/**
 * Build the scripted run for a persona + query (pure — no timers, no I/O).
 *
 * The query can steer to two extra glass-box outcomes so the offline demo shows
 * the whole trust surface: an **abstain** run (model too uncertain to act) and a
 * **budget-exceeded** run (governance cap tripped at the model chokepoint).
 * Otherwise the persona decides: `client`/`customer` gets the restricted,
 * tool-denied trajectory; anything else gets the full admin trajectory.
 *
 * @param persona - Adapter persona id.
 * @param query - The user query (used only to select the abstain/budget demos).
 */
export function buildRunScript(persona: string | null, query: string | null = null): RunScript {
  if (isBudgetQuery(query)) return buildBudgetScript()
  if (isAbstainQuery(query)) return buildAbstainScript()
  return isClientPersona(persona) ? buildClientScript() : buildOperationsScript()
}

/** Hybrid-retrieval provenance (vector + graph + BM25 fused by RRF). */
const HYBRID_PROVENANCE: StreamEventBody = {
  type: 'provenance',
  origins: ['vector', 'graph', 'bm25'],
  fusion: 'rrf',
  cache_hit: false,
  cache_kind: null,
  original_query: null,
  cached_at: null,
}

/** Full admin trajectory: retrieval → ML gate → high-risk action → human gate. */
function buildOperationsScript(): RunScript {
  const lead: ScriptStep[] = [
    { delayMs: 300, body: { type: 'run_started', trace_id: 'trace-mock-ops' } },

    // ── Input rails: screen + redact PII ──────────────────────────────────
    { delayMs: 350, body: { type: 'node_started', node: 'guard_input', label: 'Screening input' } },
    { delayMs: 400, body: REDACTION_GUARDRAIL },
    {
      delayMs: 300,
      body: {
        type: 'guardrail',
        stage: 'input',
        verdict: 'pass',
        layer: 'injection',
        reason: 'No prompt-injection or jailbreak pattern detected.',
        redactions: [],
        before_masked: null,
        after: null,
      },
    },
    {
      delayMs: 250,
      body: {
        type: 'node_finished',
        node: 'guard_input',
        label: 'Screening input',
        duration_ms: 70,
        model: 'gpt-4o-mini',
        prompt_tokens: 140,
        completion_tokens: 6,
        cost_usd: 0.0001,
      },
    },

    // ── Retrieval: candidates → rerank (graph animates) ───────────────────
    { delayMs: 300, body: { type: 'node_started', node: 'retrieve', label: 'Searching knowledge base' } },
    {
      delayMs: 350,
      body: { type: 'retrieval', status: 'started', num_candidates: 0, touched_nodes: [], touched_edges: [], scored_sources: [] },
    },
    {
      delayMs: 600,
      body: {
        type: 'retrieval',
        status: 'candidates',
        num_candidates: 9,
        touched_nodes: [node('case-4821'), node('acct-771'), node('charge-dup'), node('policy-refund'), node('kb-duplicate'), node('tier-premium'), node('sla-premium')],
        touched_edges: [
          edge('case-4821', 'acct-771'),
          edge('case-4821', 'charge-dup'),
          edge('case-4821', 'policy-refund'),
          edge('charge-dup', 'kb-duplicate'),
          edge('acct-771', 'tier-premium'),
          edge('tier-premium', 'sla-premium'),
        ],
        scored_sources: [],
      },
    },
    {
      delayMs: 650,
      body: {
        type: 'retrieval',
        status: 'reranked',
        num_candidates: 9,
        touched_nodes: [node('kb-refund-flow'), node('proc-stripe')],
        touched_edges: [edge('policy-refund', 'kb-refund-flow'), edge('charge-dup', 'proc-stripe')],
        scored_sources: [
          { id: 'kb-duplicate', label: 'KB: Duplicate Charges', score: 0.94 },
          { id: 'policy-refund', label: 'Refund Policy v3', score: 0.89 },
          { id: 'kb-refund-flow', label: 'KB: Refund Flow', score: 0.86 },
          { id: 'sla-premium', label: 'Premium SLA', score: 0.71 },
          { id: 'proc-stripe', label: 'Payment Processor', score: 0.52 },
        ],
      },
    },
    {
      delayMs: 450,
      body: { type: 'retrieval', status: 'done', num_candidates: 9, touched_nodes: [], touched_edges: [], scored_sources: [] },
    },
    // Provenance: three retrievers fused by RRF, then reranked — no cache shortcut.
    { delayMs: 200, body: HYBRID_PROVENANCE },
    {
      delayMs: 250,
      body: {
        type: 'node_finished',
        node: 'retrieve',
        label: 'Searching knowledge base',
        duration_ms: 420,
        model: null,
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0.0002,
      },
    },

    // ── Plan: stream the chain-of-thought ─────────────────────────────────
    { delayMs: 300, body: { type: 'node_started', node: 'plan', label: 'Planning approach' } },
    { delayMs: 350, body: { type: 'reasoning', text: 'Duplicate $4,200 charge on a premium account — Refund Policy v3 likely applies. ' } },
    { delayMs: 350, body: { type: 'reasoning', text: 'KB: Duplicate Charges (0.94) and the policy (0.89) corroborate a full reversal. ' } },
    { delayMs: 350, body: { type: 'reasoning', text: 'Amount is above the $2,000 auto-approval ceiling, so I will propose the refund but route it to the human gate.' } },
    {
      delayMs: 250,
      body: {
        type: 'node_finished',
        node: 'plan',
        label: 'Planning approach',
        duration_ms: 540,
        model: 'gpt-4o-mini',
        prompt_tokens: 680,
        completion_tokens: 172,
        cost_usd: 0.0004,
      },
    },

    // ── ML spine: prediction + conformal interval + SHAP + gate ───────────
    { delayMs: 300, body: { type: 'node_started', node: 'ml', label: 'Scoring refund eligibility' } },
    {
      delayMs: 550,
      body: {
        type: 'ml_explanation',
        prediction: 0.82,
        conformal_interval: [0.64, 0.93],
        conformal_confidence: 0.9,
        interval_width: 0.29,
        prediction_set_size: 2,
        shap_attribution: [
          { feature: 'duplicate_charge_confirmed', value: 1, contribution: 0.34 },
          { feature: 'account_tenure_months', value: 41, contribution: 0.18 },
          { feature: 'premium_tier', value: 1, contribution: 0.12 },
          { feature: 'prior_refunds_90d', value: 2, contribution: -0.15 },
          { feature: 'amount_usd', value: 4200, contribution: -0.09 },
          { feature: 'chargeback_risk', value: 0.22, contribution: -0.06 },
        ],
        gated: true,
        band: 'defer',
        gate_reason: 'Conformal interval (0.64–0.93) is wider than the 0.20 tolerance and its lower bound is below the 0.85 confidence floor — too uncertain to act alone.',
        min_confidence: 0.85,
        max_rel_width: 0.2,
      },
    },
    {
      delayMs: 250,
      body: {
        type: 'node_finished',
        node: 'ml',
        label: 'Scoring refund eligibility',
        duration_ms: 130,
        model: null,
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0.0,
      },
    },

    // ── Action: propose the high-risk tool, then pause at the gate ────────
    { delayMs: 300, body: { type: 'node_started', node: 'act', label: 'Preparing action' } },
    {
      delayMs: 400,
      body: {
        type: 'tool_call',
        call_id: 'call_refund_01',
        tool: 'issue_refund',
        args: { account: 'A-771', amount_usd: 4200, reason: 'duplicate charge' },
        risk: 'high',
      },
    },
    // Durable inbox: the same decision is persisted with an SLA + assignee tier,
    // so it survives a crash and can be resolved async — the scalable path.
    {
      delayMs: 300,
      body: {
        type: 'approval_queued',
        approval_id: 'appr_4821',
        action: 'Issue $4,200 refund to account A-771',
        args: { account: 'A-771', amount_usd: 4200, reason: 'duplicate charge' },
        risk: 'high',
        rationale:
          'Deferred by the conformal autonomy policy; persisted to the approvals inbox with a 10-minute SLA.',
        sla_deadline: new Date(Date.now() + 10 * 60_000).toISOString(),
        assignee_tier: 'tenant_admin',
      },
    },
    {
      delayMs: 400,
      body: {
        type: 'approval_required',
        approval_id: 'appr_4821',
        action: 'Issue $4,200 refund to account A-771',
        args: { account: 'A-771', amount_usd: 4200, reason: 'duplicate charge' },
        risk: 'high',
        rationale:
          'Amount exceeds the $2,000 auto-approval ceiling and the conformal interval (0.64–0.93) does not clear the 0.85 confidence gate. Bounded autonomy requires a human decision.',
      },
    },
  ]

  const tail = (decision: ApprovalDecision): ScriptStep[] => {
    if (decision === 'reject') {
      return [
        {
          delayMs: 300,
          body: {
            type: 'tool_result',
            call_id: 'call_refund_01',
            ok: false,
            summary: 'Refund withheld — rejected at the human approval gate.',
          },
        },
        {
          delayMs: 250,
          body: {
            type: 'node_finished',
            node: 'act',
            label: 'Preparing action',
            duration_ms: 300,
            model: 'gpt-4o',
            prompt_tokens: 320,
            completion_tokens: 48,
            cost_usd: 0.0011,
          },
        },
        {
          delayMs: 350,
          body: { type: 'run_finished', status: 'blocked', prompt_tokens: 1660, completion_tokens: 178, cost_usd: 0.0018, cache_hit: false },
        },
      ]
    }

    const answer = [
      'Confirmed a duplicate $4,200 charge on account A-771 (premium tier). ',
      'Per Refund Policy v3 and the premium SLA, I issued a full refund ',
      '(confirmation RF-77120) after human approval, and logged the action ',
      'to the audit trail with its trace id. The customer will see the ',
      'reversal within 3–5 business days.',
    ]
    return [
      {
        delayMs: 300,
        body: {
          type: 'tool_result',
          call_id: 'call_refund_01',
          ok: true,
          summary: 'Refund of $4,200 issued to A-771; confirmation RF-77120 recorded.',
        },
      },
      {
        delayMs: 250,
        body: {
          type: 'node_finished',
          node: 'act',
          label: 'Preparing action',
          duration_ms: 300,
          model: 'gpt-4o',
          prompt_tokens: 320,
          completion_tokens: 48,
          cost_usd: 0.0011,
        },
      },
      { delayMs: 300, body: { type: 'node_started', node: 'generate', label: 'Drafting response' } },
      ...answer.map((text): ScriptStep => ({ delayMs: 260, body: { type: 'token', text } })),
      {
        delayMs: 200,
        body: {
          type: 'node_finished',
          node: 'generate',
          label: 'Drafting response',
          duration_ms: 900,
          model: 'gpt-4o',
          prompt_tokens: 1840,
          completion_tokens: 96,
          cost_usd: 0.0038,
        },
      },
      {
        delayMs: 300,
        body: {
          type: 'guardrail',
          stage: 'output',
          verdict: 'pass',
          layer: 'pii',
          reason: 'Output scanned post-generation: no PII leak, no policy violation.',
          redactions: [],
          before_masked: null,
          after: null,
        },
      },
      {
        delayMs: 300,
        body: { type: 'run_finished', status: 'completed', prompt_tokens: 3190, completion_tokens: 328, cost_usd: 0.0057, cache_hit: false },
      },
    ]
  }

  return { lead, gated: true, tail }
}

/** Restricted customer trajectory: own-scope retrieval, action tool denied. */
function buildClientScript(): RunScript {
  const answer = [
    'I can only see your own requests, so here is what I found on case #4821: ',
    'the $4,200 charge does look like a duplicate. I am not able to issue the ',
    'refund myself — that action is outside my permissions — so I have logged ',
    'your request and flagged it for a support operations lead to review. ',
    'You will get an update once they approve the reversal.',
  ]
  const lead: ScriptStep[] = [
    { delayMs: 300, body: { type: 'run_started', trace_id: 'trace-mock-client' } },

    // ── Input rails: screen + redact the customer's own PII ───────────────
    { delayMs: 350, body: { type: 'node_started', node: 'guard_input', label: 'Screening input' } },
    { delayMs: 400, body: REDACTION_GUARDRAIL },
    {
      delayMs: 250,
      body: {
        type: 'node_finished',
        node: 'guard_input',
        label: 'Screening input',
        duration_ms: 70,
        model: 'gpt-4o-mini',
        prompt_tokens: 130,
        completion_tokens: 6,
        cost_usd: 0.0001,
      },
    },

    // ── Own-scope retrieval: only the customer's own records ──────────────
    { delayMs: 300, body: { type: 'node_started', node: 'retrieve', label: 'Searching your requests' } },
    {
      delayMs: 350,
      body: { type: 'retrieval', status: 'started', num_candidates: 0, touched_nodes: [], touched_edges: [], scored_sources: [] },
    },
    {
      delayMs: 600,
      body: {
        type: 'retrieval',
        status: 'candidates',
        num_candidates: 3,
        touched_nodes: [node('case-4821'), node('acct-771'), node('charge-dup')],
        touched_edges: [edge('case-4821', 'acct-771'), edge('case-4821', 'charge-dup')],
        scored_sources: [],
      },
    },
    {
      delayMs: 600,
      body: {
        type: 'retrieval',
        status: 'reranked',
        num_candidates: 3,
        touched_nodes: [],
        touched_edges: [],
        scored_sources: [
          { id: 'case-4821', label: 'Case #4821', score: 0.91 },
          { id: 'charge-dup', label: 'Duplicate charge $4,200', score: 0.88 },
          { id: 'acct-771', label: 'Account A-771', score: 0.62 },
        ],
      },
    },
    {
      delayMs: 400,
      body: { type: 'retrieval', status: 'done', num_candidates: 3, touched_nodes: [], touched_edges: [], scored_sources: [] },
    },
    // Provenance: served from a near-exact cache hit — surfaced honestly (the
    // original query + when it was cached), never a silent substitution.
    {
      delayMs: 200,
      body: {
        type: 'provenance',
        origins: ['cache'],
        fusion: 'none',
        cache_hit: true,
        cache_kind: 'near-exact',
        original_query: 'Is the $4,200 charge on case #4821 a duplicate?',
        cached_at: new Date(Date.now() - 42 * 60_000).toISOString(),
      },
    },
    {
      delayMs: 250,
      body: {
        type: 'node_finished',
        node: 'retrieve',
        label: 'Searching your requests',
        duration_ms: 260,
        model: null,
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0.0001,
      },
    },

    // ── Plan (own-scope) ──────────────────────────────────────────────────
    { delayMs: 300, body: { type: 'node_started', node: 'plan', label: 'Planning approach' } },
    { delayMs: 350, body: { type: 'reasoning', text: "This is the customer's own request, so I'm scoped to their records only. " } },
    { delayMs: 350, body: { type: 'reasoning', text: 'They want the duplicate charge reversed, but issuing refunds is not in my allowlist — I can only note and escalate.' } },
    {
      delayMs: 250,
      body: {
        type: 'node_finished',
        node: 'plan',
        label: 'Planning approach',
        duration_ms: 480,
        model: 'gpt-4o-mini',
        prompt_tokens: 520,
        completion_tokens: 120,
        cost_usd: 0.0003,
      },
    },

    // ── ML spine: confident, NOT gated (the block is RBAC, not uncertainty) ─
    { delayMs: 300, body: { type: 'node_started', node: 'ml', label: 'Scoring duplicate-charge confidence' } },
    {
      delayMs: 500,
      body: {
        type: 'ml_explanation',
        prediction: 0.93,
        conformal_interval: [0.88, 0.97],
        conformal_confidence: 0.9,
        interval_width: 0.09,
        prediction_set_size: 1,
        shap_attribution: [
          { feature: 'duplicate_charge_confirmed', value: 1, contribution: 0.41 },
          { feature: 'same_merchant_same_day', value: 1, contribution: 0.19 },
          { feature: 'amount_usd', value: 4200, contribution: -0.05 },
        ],
        // Narrow interval well above the floor: the model is confident. The
        // action is still denied downstream — by RBAC (allowlist), not the gate.
        gated: false,
        band: 'autonomous',
        gate_reason: 'Confident (0.88–0.97 clears the 0.85 floor within tolerance); no uncertainty pause needed.',
        min_confidence: 0.85,
        max_rel_width: 0.2,
      },
    },
    {
      delayMs: 250,
      body: {
        type: 'node_finished',
        node: 'ml',
        label: 'Scoring duplicate-charge confidence',
        duration_ms: 110,
        model: null,
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0.0,
      },
    },

    // ── Action: tool DENIED (not in the customer allowlist) ───────────────
    { delayMs: 300, body: { type: 'node_started', node: 'act', label: 'Preparing action' } },
    {
      delayMs: 400,
      body: {
        type: 'tool_call',
        call_id: 'call_refund_client_01',
        tool: 'issue_refund',
        args: { account: 'A-771', amount_usd: 4200, reason: 'duplicate charge' },
        risk: 'high',
      },
    },
    {
      delayMs: 400,
      body: {
        type: 'tool_result',
        call_id: 'call_refund_client_01',
        ok: false,
        summary: "Denied: 'issue_refund' is not in the customer persona's allowlist (own-scope: may only append notes).",
      },
    },
    {
      delayMs: 250,
      body: {
        type: 'node_finished',
        node: 'act',
        label: 'Preparing action',
        duration_ms: 180,
        model: 'gpt-4o-mini',
        prompt_tokens: 200,
        completion_tokens: 20,
        cost_usd: 0.0002,
      },
    },

    // ── Generate the scoped answer + output rail ──────────────────────────
    { delayMs: 300, body: { type: 'node_started', node: 'generate', label: 'Drafting response' } },
    ...answer.map((text): ScriptStep => ({ delayMs: 260, body: { type: 'token', text } })),
    {
      delayMs: 200,
      body: {
        type: 'node_finished',
        node: 'generate',
        label: 'Drafting response',
        duration_ms: 520,
        model: 'gpt-4o-mini',
        prompt_tokens: 900,
        completion_tokens: 70,
        cost_usd: 0.0009,
      },
    },
    {
      delayMs: 300,
      body: {
        type: 'guardrail',
        stage: 'output',
        verdict: 'pass',
        layer: 'pii',
        reason: 'Output scanned post-generation: no PII leak, scoped to the customer.',
        redactions: [],
        before_masked: null,
        after: null,
      },
    },
    {
      delayMs: 300,
      body: { type: 'run_finished', status: 'completed', prompt_tokens: 1750, completion_tokens: 216, cost_usd: 0.0015, cache_hit: true },
    },
  ]

  return { lead, gated: false, tail: () => [] }
}

/**
 * Abstain trajectory: the conformal policy finds the prediction degenerate
 * (no coverage), so the agent does NOT act — it returns an honest "insufficient
 * confidence" terminal state rather than guessing.
 */
function buildAbstainScript(): RunScript {
  const lead: ScriptStep[] = [
    { delayMs: 300, body: { type: 'run_started', trace_id: 'trace-mock-abstain' } },
    { delayMs: 300, body: { type: 'node_started', node: 'guard_input', label: 'Screening input' } },
    {
      delayMs: 300,
      body: {
        type: 'guardrail',
        stage: 'input',
        verdict: 'pass',
        layer: 'injection',
        reason: 'No prompt-injection or jailbreak pattern detected.',
        redactions: [],
        before_masked: null,
        after: null,
      },
    },
    { delayMs: 300, body: { type: 'node_started', node: 'retrieve', label: 'Searching knowledge base' } },
    {
      delayMs: 500,
      body: {
        type: 'retrieval',
        status: 'reranked',
        num_candidates: 4,
        touched_nodes: [node('case-4821'), node('acct-771')],
        touched_edges: [edge('case-4821', 'acct-771')],
        scored_sources: [
          { id: 'case-4821', label: 'Case #4821 (sparse history)', score: 0.41 },
          { id: 'acct-771', label: 'Account A-771', score: 0.33 },
        ],
      },
    },
    { delayMs: 200, body: HYBRID_PROVENANCE },
    { delayMs: 300, body: { type: 'node_started', node: 'plan', label: 'Planning approach' } },
    { delayMs: 350, body: { type: 'reasoning', text: 'Very little corroborating evidence — the top source scores only 0.41. ' } },
    { delayMs: 350, body: { type: 'reasoning', text: 'I will score it, but if the model cannot cover this case I should abstain rather than guess.' } },
    { delayMs: 300, body: { type: 'node_started', node: 'ml', label: 'Scoring eligibility' } },
    {
      delayMs: 500,
      body: {
        type: 'ml_explanation',
        prediction: 0.5,
        conformal_interval: [0.08, 0.94],
        conformal_confidence: 0.9,
        interval_width: 0.86,
        prediction_set_size: 3,
        shap_attribution: [
          { feature: 'account_tenure_months', value: 1, contribution: 0.04 },
          { feature: 'prior_cases', value: 0, contribution: -0.03 },
        ],
        gated: true,
        band: 'abstain',
        gate_reason: 'Prediction set is non-singleton and the interval spans almost the full range — no usable coverage.',
        min_confidence: 0.85,
        max_rel_width: 0.2,
      },
    },
    {
      delayMs: 400,
      body: {
        type: 'abstained',
        band: 'abstain',
        reason:
          'Conformal interval (0.08–0.94) is degenerate — the model has no reliable coverage for this sparse case, so the agent did not act.',
        prediction: 0.5,
        conformal_confidence: 0.9,
      },
    },
    {
      delayMs: 300,
      body: { type: 'run_finished', status: 'blocked', prompt_tokens: 640, completion_tokens: 40, cost_usd: 0.0006, cache_hit: false },
    },
  ]
  return { lead, gated: false, tail: () => [] }
}

/**
 * Budget-exceeded trajectory: a governance cap is tripped at the model
 * chokepoint before the expensive call runs — the system degrades to
 * "budget exceeded" instead of runaway spend.
 */
function buildBudgetScript(): RunScript {
  const lead: ScriptStep[] = [
    { delayMs: 300, body: { type: 'run_started', trace_id: 'trace-mock-budget' } },
    { delayMs: 300, body: { type: 'node_started', node: 'guard_input', label: 'Screening input' } },
    {
      delayMs: 300,
      body: {
        type: 'guardrail',
        stage: 'input',
        verdict: 'pass',
        layer: 'injection',
        reason: 'No prompt-injection or jailbreak pattern detected.',
        redactions: [],
        before_masked: null,
        after: null,
      },
    },
    { delayMs: 300, body: { type: 'node_started', node: 'plan', label: 'Planning approach' } },
    {
      delayMs: 500,
      body: {
        type: 'budget_exceeded',
        scope: 'tenant',
        scope_id: 2,
        limit_type: 'usd',
        limit: 1200,
        used: 1200.42,
        message:
          "Tenant 'Northwind Financial' has reached its $1,200 monthly spend cap. Further model calls are blocked until the window resets or the cap is raised.",
      },
    },
    {
      delayMs: 300,
      body: { type: 'run_finished', status: 'blocked', prompt_tokens: 210, completion_tokens: 0, cost_usd: 0.0, cache_hit: false },
    },
  ]
  return { lead, gated: false, tail: () => [] }
}

/** Resolves after `ms`, or rejects if aborted. */
function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('aborted', 'AbortError'))
      return
    }
    const timer = setTimeout(resolve, ms)
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        reject(new DOMException('aborted', 'AbortError'))
      },
      { once: true },
    )
  })
}

/** A {@link RunTransport} that plays the scripted scenario in the browser. */
export function createMockTransport(): RunTransport {
  return {
    start(query, persona, _token, handlers): RunController {
      const abort = new AbortController()
      const runId = `run_${Math.random().toString(36).slice(2, 8)}`
      let seq = 0

      // Promise the approval gate awaits; resolved by resolveApproval().
      let resolveGate: ((decision: ApprovalDecision) => void) | null = null

      const script = buildRunScript(persona, query)

      const emit = (body: StreamEventBody): void => {
        seq += 1
        handlers.onEvent({ ...body, run_id: runId, seq } as StreamEvent)
      }

      const playSteps = async (steps: ScriptStep[], s: AbortSignal): Promise<void> => {
        for (const step of steps) {
          await sleep(step.delayMs, s)
          emit(step.body)
        }
      }

      const play = async (): Promise<void> => {
        const s = abort.signal
        await playSteps(script.lead, s)
        if (!script.gated) return

        // Pause until the human decides (or the run is aborted → treat as reject).
        const decision = await new Promise<ApprovalDecision>((resolve) => {
          resolveGate = resolve
          s.addEventListener('abort', () => resolve('reject'), { once: true })
        })
        if (s.aborted) return

        await playSteps(script.tail(decision), s)
      }

      play()
        .catch((error: unknown) => {
          if (abort.signal.aborted) return
          handlers.onError(error instanceof Error ? error : new Error(String(error)))
        })
        .finally(() => handlers.onClose())

      return {
        resolveApproval(_approvalId: string, decision: ApprovalDecision): void {
          resolveGate?.(decision)
          resolveGate = null
        },
        abort(): void {
          abort.abort()
        },
      }
    },
  }
}
