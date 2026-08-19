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

/**
 * Which guardrail rail produced a verdict.
 *
 * `tool_result` mirrors `GuardStage.TOOL_RESULT` in aegis/src/aegis/core/types.py:
 * a tool's output screened *before* it reaches any agent's context (web-search
 * content in particular), so a planted instruction in a third-party page is
 * blocked and shown rather than read by the model.
 */
export type GuardStage = 'input' | 'output' | 'tool_result'

/** Outcome of a guardrail check. */
export type GuardVerdict = 'pass' | 'block' | 'redact'

/** Risk classification for an action or gate. */
export type RiskLevel = 'low' | 'medium' | 'high'

/** A retrieval origin that contributed candidates to a result. */
export type RetrievalOrigin = 'vector' | 'graph' | 'bm25' | 'cache'

/** How multiple retrieval origins were combined into one ranked list. */
export type FusionMethod = 'none' | 'rrf' | 'mix'

/** Fields common to every streamed event. */
export interface BaseEvent {
  /** Correlates all events of one query run. */
  run_id: string
  /** Monotonic sequence number within the run. */
  seq: number
  /**
   * The sub-agent that emitted this event; absent for the supervisor and for
   * graph-level nodes, which is every event a single-pass run produces. Present on
   * every event of a fan-out, and it is what groups a lane's log rather than a UI
   * guess based on node names.
   */
  agent_id?: string | null
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

/** An input, output or tool-result rail produced a verdict. */
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
  /**
   * Every call this one approval authorises, highest risk first.
   *
   * A fan-out can have several sub-agents each propose a consequential write in one
   * turn, and `action` names only the representative — so a dialog that renders
   * `action` alone would be asking the person to authorise more than it shows them.
   * Render this list. A single-action run carries one entry.
   */
  actions: { id: string; name: string; args: Record<string, unknown>; risk: RiskLevel }[]
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

/**
 * One round of the bounded self-repair loop (Reflexion-style).
 *
 * Emitted by the `reflect` node once per planning round: it judges whether the goal
 * is met from the executed {@link ToolResult} outcomes and decides whether to loop
 * back to `plan` or finalise. It has been on the wire since phase 5 and was dropped
 * on the floor by a reducer that had no branch for it — the agent repairing itself
 * is the single most demoable thing it does, and it was invisible.
 */
export interface Reflection extends BaseEvent {
  type: 'reflection'
  /** 1-based planning round this reflection follows (hard-capped). */
  iteration: number
  /** The configured iteration budget (hard cap on planning rounds). */
  max_iterations: number
  /** Whether the goal was judged met (all actions ok). */
  done: boolean
  /** Whether the agent loops back to plan for another round. */
  will_retry: boolean
  /** Demoable explanation of the self-repair decision. */
  reason: string
}

/**
 * The supervisor's routing decision — the visible hand-off.
 *
 * `decided_by` is what keeps the trace honest: the width shown always names whether
 * the classifier, the user, the tenant default or the platform cap chose it. A width
 * with no explanation is exactly the kind of number an audience stops trusting.
 */
export interface RoutingEvent extends BaseEvent {
  type: 'routing'
  /** The specialist role the turn was dispatched to. */
  role: string
  /** Demoable explanation of the routing decision. */
  reason: string
  /** Whether the cheap-LLM tiebreak was consulted (else deterministic). */
  used_llm: boolean
  /** How WIDE the turn runs: 'single' (one lane) or 'team' (a fan-out). */
  depth: string
  /** How many sub-agents a team turn fans out to (0 for single). */
  fanout: number
  /** Who chose the width: 'auto' | 'user' | 'tenant_default' | 'platform_cap'. */
  decided_by: string
}

/**
 * One sub-agent's lifecycle beat in a concurrent fan-out.
 *
 * `status: 'timeout'` is a **designed** terminal state, not an error: the run degrades
 * gracefully, names the omitted agent in {@link SynthesisEvent}, and finishes. A card
 * that renders it as a stuck spinner is reading the protocol wrong.
 */
export interface AgentStatus extends BaseEvent {
  type: 'agent_status'
  /** Stable id of the sub-agent this beat belongs to. */
  agent_id: string
  /** The sub-agent's kind, e.g. 'research' | 'knowledge'. */
  role: string
  /** Human label for the agent's lane in the console. */
  label: string
  /** queued | started | thinking | acting | done | failed | timeout. */
  status: string
  /** Short human detail for this beat. */
  detail: string
}

/** One agent's line in a {@link SynthesisEvent} roster (agent_id/role/label + status). */
export type SynthesisMember = Record<string, unknown>

/**
 * The fan-out's merge, naming which agents contributed **and which were omitted**.
 *
 * The omitted list is a first-class field rather than an absence the client infers:
 * partial failure that is not named reads as a bug, and naming it is what turns a
 * timed-out lane into visible, graceful degradation.
 */
export interface SynthesisEvent extends BaseEvent {
  type: 'synthesis'
  /** The agents whose findings are in the answer. */
  contributing: SynthesisMember[]
  /** The agents that produced nothing usable, each with its terminal status. */
  omitted: SynthesisMember[]
  /** The honest one-liner, e.g. 'Synthesised from 3 of 4 agents; …'. */
  summary: string
}

/**
 * Long-term memory recall for one turn — the proof a follow-up was remembered.
 *
 * Emitted once by `recall_memory`, and **only** when the run carries a `session_id`.
 * That is why it gets its own visible line rather than a trace row: its absence is
 * the observable difference between a console that has multi-turn memory and one
 * that merely claims to.
 */
export interface MemoryEvent extends BaseEvent {
  type: 'memory'
  /** Semantic facts recalled into working memory. */
  recalled_fact_count: number
  /** Episodic/raw turns recalled into working memory. */
  recalled_message_count: number
  /** Token size of the assembled working-memory block. */
  tokens_used: number
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
  | ApprovalRequired
  | ApprovalQueued
  | Provenance
  | BudgetExceeded
  | Reflection
  | RoutingEvent
  | AgentStatus
  | SynthesisEvent
  | MemoryEvent
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
