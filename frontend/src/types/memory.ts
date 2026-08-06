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
