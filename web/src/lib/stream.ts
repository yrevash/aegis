/**
 * SSE stream event contract — a faithful TypeScript mirror of the backend's
 * `StreamEvent` discriminated union in `backend/src/app/api/schemas.py`.
 *
 * This file is the single source of truth on the frontend; it must stay
 * byte-for-byte aligned with the locked Pydantic models. Do not invent shapes.
 * Every event the frontend receives over the `POST /query` SSE stream is one of
 * the {@link StreamEvent} variants, discriminated on `type`.
 *
 * @see backend/src/app/api/schemas.py
 */

/**
 * Authenticated role; drives RBAC and which portal/surface is served. The four
 * portals are: `admin` (platform owner — sees everything), `ai_team` (builds and
 * tunes the agent), `devops` (runs the stack — versions, patches, ops), and
 * `client` (the tenant end-user — value, risk, read-only access).
 */
export type Role = 'admin' | 'ai_team' | 'devops' | 'client'

/** Terminal status of a query run. */
export type RunStatus = 'completed' | 'blocked' | 'awaiting_approval' | 'error'

/** Which guardrail rail produced a verdict. */
export type GuardStage = 'input' | 'output'

/** Outcome of a guardrail check. */
export type GuardVerdict = 'pass' | 'block' | 'redact'

/** Risk classification for an action or gate. */
export type RiskLevel = 'low' | 'medium' | 'high'

/** A retrieval origin that contributed candidates to a result. */
export type RetrievalOrigin = 'vector' | 'graph' | 'bm25' | 'cache'

/** How multiple retrieval origins were combined into one ranked list. */
export type FusionMethod = 'none' | 'rrf' | 'mix'

/**
 * Graded conformal-autonomy band (`autonomous` / `defer` / `abstain`). Mirrors
 * the backend `AutonomyBand` StrEnum, kept as a shared contract. Note: the live
 * backend does NOT route on this — by design the human gate is driven by the
 * action's TOOL RISK TIER, and ML is a solution signal that never gates. The
 * graded band is populated only by the frontend mock; treat it as supporting
 * evidence, not the flow decision.
 */
export type AutonomyBandKind = 'autonomous' | 'defer' | 'abstain'

/** Fields common to every streamed event. */
export interface BaseEvent {
  /** Correlates all events of one query run. */
  run_id: string
  /** Monotonic sequence number within the run. */
  seq: number
}

/** A node in the context knowledge graph (coloured by `kind`). */
export interface GraphNode {
  id: string
  label: string
  /** Entity kind/type for colouring the viz. */
  kind: string
}

/** A directed relationship between two graph nodes. */
export interface GraphEdge {
  source: string
  target: string
  relation: string
}

/** A single signed SHAP feature attribution. */
export interface ShapFeature {
  feature: string
  value: number
  /** Signed SHAP attribution. */
  contribution: number
}

/** One reranked retrieval source with its relevance score (0..1). */
export interface ScoredSource {
  id: string
  label: string
  /** Reranker relevance score in [0, 1]; higher is more relevant. */
  score: number
}

/** One redaction a guardrail applied (kind only — never the raw value). */
export interface Redaction {
  /** The class of PII masked, e.g. 'ssn', 'email', 'phone'. */
  kind: string
}

/** A run has begun; carries the trace id for observability correlation. */
export interface RunStarted extends BaseEvent {
  type: 'run_started'
  trace_id: string
}

/** The agent entered a graph node (a visible step in the plan). */
export interface NodeStarted extends BaseEvent {
  type: 'node_started'
  /** Node name, e.g. 'plan', 'retrieve', 'generate'. */
  node: string
  /** Human-readable step label for the trace panel. */
  label: string
}

/** The agent finished a graph node (glass-box: what it cost and used). */
export interface NodeFinished extends BaseEvent {
  type: 'node_finished'
  /** Node name, matching the earlier {@link NodeStarted}. */
  node: string
  /** Human-readable step label for the trace panel. */
  label: string
  /** Wall-clock time the node took, in milliseconds. */
  duration_ms: number
  /** Model deployment id the node used, or null for non-LLM nodes. */
  model: string | null
  /** Prompt tokens the node consumed. */
  prompt_tokens: number
  /** Completion tokens the node produced. */
  completion_tokens: number
  /** Marginal cost attributed to this node, in USD. */
  cost_usd: number
}

/** A chunk of the planner's chain-of-thought (glass-box reasoning trace). */
export interface Reasoning extends BaseEvent {
  type: 'reasoning'
  /** A fragment of the planner's thinking, streamed as it forms. */
  text: string
}

/** An input or output rail produced a verdict. */
export interface Guardrail extends BaseEvent {
  type: 'guardrail'
  stage: GuardStage
  verdict: GuardVerdict
  /** Why it passed/blocked/redacted (demoable). */
  reason: string
  /** Which rail layer produced the verdict (e.g. 'pii', 'injection'), or null. */
  layer: string | null
  /** Redactions applied (kind only — never raw PII). Empty when none. */
  redactions: Redaction[]
  /** Masked text *before* this rail's redaction, or null when nothing masked. */
  before_masked: string | null
  /** Text after this rail ran (already masked), or null. */
  after: string | null
}

/** Retrieval progress; carries the graph delta so the viz can animate. */
export interface RetrievalStep extends BaseEvent {
  type: 'retrieval'
  status: 'started' | 'candidates' | 'reranked' | 'done'
  num_candidates: number
  touched_nodes: GraphNode[]
  touched_edges: GraphEdge[]
  /** Reranked sources with relevance scores; populated on the 'reranked' step. */
  scored_sources: ScoredSource[]
}

/** The agent decided to call an action tool. */
export interface ToolCall extends BaseEvent {
  type: 'tool_call'
  call_id: string
  tool: string
  args: Record<string, unknown>
  risk: RiskLevel
}

/** An action tool returned (or failed). */
export interface ToolResult extends BaseEvent {
  type: 'tool_result'
  call_id: string
  ok: boolean
  summary: string
}

/** An ML prediction with its conformal interval and SHAP attribution. */
export interface MLExplanation extends BaseEvent {
  type: 'ml_explanation'
  prediction: number | string
  /** Calibrated bounds (guaranteed coverage), or null. */
  conformal_interval: [number, number] | null
  /** Target coverage rate, e.g. 0.9. */
  conformal_confidence: number | null
  /**
   * Conformal interval width (upper − lower) — the deferral signal: a wide band
   * means the model is uncertain. Null for non-numeric / no-interval predictions.
   */
  interval_width: number | null
  /**
   * Conformal prediction-set size for a classification target (1 ⇒ a confident
   * singleton; >1 ⇒ ambiguous). Null for regression / no-interval predictions.
   */
  prediction_set_size: number | null
  shap_attribution: ShapFeature[]
  /**
   * Legacy uncertainty readout carried for display, or null. Note: in the live
   * backend ML never gates — the human gate fires on the action's tool risk
   * tier, not on this flag; a true value is informational, not a flow decision.
   */
  gated: boolean | null
  /**
   * The graded autonomy band (§2.3), or null. The live backend does not populate
   * a graded band (it gates on tool risk tier); this is set only by the frontend
   * mock and shown as supporting evidence.
   */
  band: AutonomyBandKind | null
  /** Why the gate did (or did not) trigger, or null. */
  gate_reason: string | null
  /** Minimum confidence required to act autonomously, or null. */
  min_confidence: number | null
  /** Maximum relative interval width tolerated to act autonomously, or null. */
  max_rel_width: number | null
}

/** The run paused at the human-in-the-loop gate (bounded autonomy). */
export interface ApprovalRequired extends BaseEvent {
  type: 'approval_required'
  approval_id: string
  /** The proposed action awaiting approval. */
  action: string
  args: Record<string, unknown>
  risk: RiskLevel
  /** Why the gate triggered (risk/uncertainty). */
  rationale: string
}

/** A streamed chunk of the final answer text. */
export interface AnswerChunk extends BaseEvent {
  type: 'token'
  text: string
}

/** Terminal event; carries usage for the token/cost dashboard. */
export interface RunFinished extends BaseEvent {
  type: 'run_finished'
  status: RunStatus
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  cache_hit: boolean
}

/** A fatal error terminated the run. */
export interface ErrorEvent extends BaseEvent {
  type: 'error'
  message: string
}

/**
 * An action was written to the durable approvals inbox (the async, scalable
 * approval path). Unlike {@link ApprovalRequired} — which pauses the live socket
 * for an in-run decision — this event means the run's decision can be resolved
 * out-of-band from the inbox, with an SLA and an escalation tier.
 */
export interface ApprovalQueued extends BaseEvent {
  type: 'approval_queued'
  approval_id: string
  /** Human-readable description of the proposed action. */
  action: string
  args: Record<string, unknown>
  risk: RiskLevel
  /** Why the action needs approval (risk/uncertainty). */
  rationale: string
  /** ISO 8601 deadline the decision must be made by, or null when unbounded. */
  sla_deadline: string | null
  /** Approver tier the row is assigned to (e.g. 'tenant_admin'), or null. */
  assignee_tier: string | null
}

/**
 * A terminal outcome: the model's conformal confidence was too low (degenerate
 * / no-coverage) to act, so the agent abstained rather than guess. Rendered as
 * an "insufficient confidence — did not act" terminal state.
 */
export interface Abstained extends BaseEvent {
  type: 'abstained'
  /** Always the 'abstain' autonomy band. */
  band: 'abstain'
  /** Why the model abstained (degenerate interval, empty set, no coverage). */
  reason: string
  /** The (untrusted) prediction, surfaced for context only, or null. */
  prediction: number | string | null
  /** Target coverage the interval failed to satisfy, or null. */
  conformal_confidence: number | null
}

/**
 * Provenance of the retrieval result — which origins contributed, how they were
 * fused, and whether the answer was served from cache. Turns the cache from an
 * opaque shortcut into an honest, auditable efficiency signal.
 */
export interface Provenance extends BaseEvent {
  type: 'provenance'
  /** Retrieval origins that contributed candidates. */
  origins: RetrievalOrigin[]
  /** How the origins were combined. */
  fusion: FusionMethod
  /** Whether this result was served from the cache. */
  cache_hit: boolean
  /** The kind of cache hit (e.g. 'exact', 'near-exact'), or null. */
  cache_kind: string | null
  /** On a cache hit, the original query the cached answer came from, or null. */
  original_query: string | null
  /** On a cache hit, when the cached answer was stored (ISO 8601), or null. */
  cached_at: string | null
}

/**
 * A tenant/user budget or rate limit was exceeded at the model chokepoint — a
 * run-terminal governance signal that degrades gracefully to "budget exceeded"
 * instead of runaway spend.
 */
export interface BudgetExceeded extends BaseEvent {
  type: 'budget_exceeded'
  /** The scope the cap applies to, e.g. 'tenant' or 'user'. */
  scope: string
  /** The id of the scoped tenant/user, or null. */
  scope_id: number | null
  /** Which cap was hit, e.g. 'usd', 'tokens', 'rpm', 'tpm'. */
  limit_type: string
  /** The configured cap value, or null. */
  limit: number | null
  /** How much was used against the cap, or null. */
  used: number | null
  /** Human-readable explanation for the banner. */
  message: string
}

/** Any event the frontend may receive over the `/query` SSE stream. */
export type StreamEvent =
  | RunStarted
  | NodeStarted
  | NodeFinished
  | Reasoning
  | Guardrail
  | RetrievalStep
  | ToolCall
  | ToolResult
  | MLExplanation
  | ApprovalRequired
  | ApprovalQueued
  | Abstained
  | Provenance
  | BudgetExceeded
  | AnswerChunk
  | RunFinished
  | ErrorEvent

/** The discriminant literal of every {@link StreamEvent}. */
export type StreamEventType = StreamEvent['type']

/** `Omit` that distributes across each member of a union. */
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never

/**
 * A {@link StreamEvent} without the envelope fields the transport stamps on
 * (`run_id`, `seq`). Emitters build one of these; the transport adds the rest.
 */
export type StreamEventBody = DistributiveOmit<StreamEvent, 'run_id' | 'seq'>
