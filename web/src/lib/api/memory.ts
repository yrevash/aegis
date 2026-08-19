/**
 * Memory endpoint contracts — a faithful TypeScript mirror of the `/memory/*`
 * Pydantic models in `backend/src/app/api/schemas.py`.
 *
 * The Harness memory is a three-tier store:
 *  - **semantic** facts (bitemporal, confidence + importance scored),
 *  - **episodic** sessions + messages (running summaries),
 *  - a **structured** profile ("what the agent knows about you"),
 * plus a **write-log** changelog and a **recall-debug** trace.
 *
 * @see backend/src/app/api/schemas.py
 */

/** One semantic fact row (bitemporal). Mirrors `MemoryFactRow`. */
export interface MemoryFactRow {
  id: number
  subject_id: string
  fact_type: string
  subject: string
  predicate: string
  object: string
  text: string
  /** Model confidence in the fact, 0..1. */
  confidence: number
  /** Importance weight (integer, higher = more salient). */
  importance: number
  access_count: number
  /** Bitemporal: when the fact became true. */
  valid_at: string | null
  /** Bitemporal: when it stopped being true (null ⇒ still valid). */
  invalid_at: string | null
  created_at: string | null
  expired_at: string | null
  source_turn_ids: number[]
  /** The fact id this one replaced, if any. */
  supersedes_id: number | null
  is_valid: boolean
}

/** Response from `GET /memory/facts`. */
export interface MemoryFactsResponse {
  subject: string
  rows: MemoryFactRow[]
}

/** Response from `GET /memory/profile` — the structured "what we know" doc. */
export interface MemoryProfileResponse {
  subject: string
  data: Record<string, unknown>
  updated_at: string | null
}

/** One episodic session summary. Mirrors `MemorySessionRow`. */
export interface MemorySessionRow {
  id: string
  subject_id: string
  persona: string | null
  turn_count: number
  summary: string | null
  created_at: string | null
  last_active_at: string | null
}

/** Response from `GET /memory/sessions`. */
export interface MemorySessionsResponse {
  subject: string
  rows: MemorySessionRow[]
}

/** One message turn within a session. Mirrors `MemoryMessageRow`. */
export interface MemoryMessageRow {
  id: number
  session_id: string
  turn_index: number
  role: string
  origin: string
  content: string
  importance: number
  created_at: string | null
}

/** Response from `GET /memory/sessions/{id}/messages`. */
export interface MemoryMessagesResponse {
  session_id: string
  subject: string
  rows: MemoryMessageRow[]
}

/** The op recorded for one memory write. */
export type MemoryWriteOp = 'ADD' | 'UPDATE' | 'INVALIDATE' | 'NOOP'

/** One changelog entry. Mirrors `MemoryWriteRow`. */
export interface MemoryWriteRow {
  id: number
  op: MemoryWriteOp | string
  fact_id: number | null
  before: Record<string, unknown>
  after: Record<string, unknown>
  reason: string | null
  model: string | null
  trace_id: string | null
  ts: string | null
}

/** Response from `GET /memory/writes`. */
export interface MemoryWritesResponse {
  subject: string
  rows: MemoryWriteRow[]
}

/** One ranked item in the recall trace (fact or episodic). */
export interface RecallDebugItem {
  key: string
  text: string
  /** Relevance / similarity score in [0,1]. */
  score: number
  importance: number
  age_days: number
  /** Whether this item was injected into the working-memory block. */
  injected: boolean
}

/** Response from `GET /memory/recall_debug`. */
export interface RecallDebugResponse {
  subject: string
  query: string
  facts: RecallDebugItem[]
  episodic: RecallDebugItem[]
  /** The assembled working-memory block handed to the model. */
  working_memory: string
  tokens_used: number
  recalled_fact_count: number
  recalled_message_count: number
}

// ── The control plane: who may be managed, the write, and the retention horizon ──
//
// Mirrors `backend/src/app/api/routes_memory.py`. Note what is *not* here: a helper
// that composes a subject string. The subject is an opaque key the server derives from
// the caller's identity through the `memory_subject_for` adapter seam, and the browser's
// only correct relationship with it is to echo back one it was given by
// `GET /memory/subjects`. A domain that scopes memory to an account rather than a person
// changes that seam, and nothing here has to change with it.

/** One subject the signed-in caller may manage. Mirrors `MemorySubjectRow`. */
export interface MemorySubjectRow {
  /** The opaque memory key. Never composed in the browser. */
  subject: string
  /** Who the subject is, in a name a person recognises. */
  label: string
  /** Whether this is the caller's own record. */
  is_self: boolean
  tenant_id: number | null
  /** Currently-valid durable facts. */
  fact_count: number
  session_count: number
  /** ISO 8601, or null when this subject has never spoken to the agent. */
  last_active: string | null
}

/** Response from `GET /memory/subjects`. */
export interface MemorySubjectsResponse {
  rows: MemorySubjectRow[]
  self_subject: string | null
  /** Whether this caller administers subjects other than their own. */
  may_manage_others: boolean
}

/** Body of `POST /memory/facts`. Omit `subject` to write about yourself. */
export interface MemoryFactWriteRequest {
  text: string
  subject?: string | null
  fact_type?: string | null
  predicate?: string
  object?: string
  importance?: number
}

/** Body of `PATCH /memory/facts/{id}` — a correction, which supersedes the old row. */
export interface MemoryFactCorrectionRequest {
  text: string
  predicate?: string | null
  object?: string | null
  importance?: number | null
}

/**
 * Response from `POST /memory/facts` / `PATCH /memory/facts/{id}`.
 *
 * `text` is what was **actually stored**, which is not always what was sent: the input
 * rail redacts PII on the way in. Rendering the request's own text back would tell the
 * operator a lie about what the agent will now recall.
 */
export interface MemoryFactWriteResponse {
  subject: string
  fact_id: number
  /** The row this one replaced, on a correction. */
  supersedes_id: number | null
  text: string
  /** The input rail's verdict: `pass`, `redact` or `flag`. */
  verdict: string
  /** Detector kinds redacted — kinds only, never raw values. */
  redactions: string[]
  /** Whether a recall vector was computed. False means recency-only recall. */
  embedded: boolean
}

/** Per-tier row counts, shared by the retention preview and the sweep receipt. */
export interface MemoryRetentionCounts {
  messages: number
  sessions: number
  facts: number
  jobs: number
}

/** Response from `GET /memory/retention`. */
export interface MemoryRetentionResponse {
  episodic_days: number
  closed_fact_days: number
  /** Where the horizons came from: `platform`, `tenant` or `user`. */
  source: string
  /** How wide a sweep would reach: `tenant` or `platform`. */
  scope: string
  subject: string | null
  at_risk: MemoryRetentionCounts
  /** The fact-write log is never swept; it is the evidence trail. */
  keeps_audit: boolean
}

/** Response from `POST /memory/retention/sweep`. */
export interface MemoryRetentionSweepResponse {
  scope: string
  subject: string | null
  removed: MemoryRetentionCounts
  total: number
}

/** Response from `POST /memory/forget` — the counts a full erasure removed. */
export interface MemoryForgetResponse {
  subject: string
  deleted_facts: number
  deleted_messages: number
  deleted_sessions: number
  deleted_profiles: number
  deleted_writes: number
  deleted_jobs: number
}

/** Response from `DELETE /memory/facts/{id}`. */
export interface MemoryFactDeleteResponse {
  fact_id: number
  deleted: boolean
}
