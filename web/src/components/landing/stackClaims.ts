/**
 * The technology band's claims — one per mechanism, each with the file it lives in.
 *
 * ## The rule this file is written under
 *
 * **A padded stack list is the same defect as a padded compliance table.** Every entry
 * below was checked against the code before it was typed, and
 * `web/tests/landing/stackClaims.test.mjs` resolves each `path` against the real
 * repository on every test run — so a module that is renamed, merged or deleted breaks
 * the suite instead of quietly leaving a marketing page claiming a capability that is
 * no longer there. That is the same discipline `backend/tests/api/test_compliance.py`
 * applies to the compliance table, and it exists here for the same reason: this list is
 * the easiest thing on the page to fake.
 *
 * ## What was deliberately left out, and why
 *
 * - **Postgres row-level security.** Real, enforced, `FORCE ROW LEVEL SECURITY` with a
 *   `NOSUPERUSER NOBYPASSRLS` serving role — and already the page's own drawn exhibit
 *   in `GovernanceSection`. A page should not make its central argument twice
 *   (DESIGN.md §9).
 * - **LiteLLM, Redis, the module names.** `LivePlatform` already renders the branded
 *   module manifest with the tech under each name, live from
 *   `GET /platform/capabilities`. This band is the *mechanisms* — the specific hard
 *   things — not a second copy of that grid.
 * - **"A 17-node graph."** The graph builder makes eighteen `add_node` calls, the
 *   topology endpoint that could settle it is authenticated, and a public page cannot
 *   derive the figure. A number this page cannot source is a number it does not print,
 *   so the LangGraph row claims the durability instead — which is the harder thing and
 *   the one with a test behind it.
 *
 * Pure data, no React: the component renders it, the test resolves it.
 */

/** One verified capability: what it runs on, what it does, and where that lives. */
export interface StackClaim {
  /** The technology's own name, set as a wordmark. Never a logo. */
  mark: string
  /** What it actually does here. One line, no adjectives, no benchmark. */
  mechanism: string
  /** Repository path, relative to the root. Resolved on disk by the test. */
  path: string
  /**
   * A pytest node id proving the claim, where one test carries it outright. Present
   * on the two rows where "it is wired up" and "it survives a restart" differ.
   */
  proof?: string
}

/**
 * The mechanisms, in the order a run meets them.
 *
 * Retrieval first because it is what a question hits, then what constrains the
 * answer, then what records it, then what a person reads afterwards.
 */
export const STACK_CLAIMS: readonly StackClaim[] = [
  {
    mark: 'LangGraph',
    mechanism: 'A parked run resumes on a fresh worker, from a durable Postgres checkpoint',
    path: 'backend/src/app/agent/checkpointer.py',
    proof:
      'backend/tests/agent/test_durable_approvals.py::test_fresh_worker_rehydrates_and_resumes_by_thread_id',
  },
  {
    mark: 'Temporal',
    mechanism: 'Ingestion, reindex and reconcile run as durable workflows',
    path: 'backend/src/app/jobs/flows/ingest.py',
  },
  {
    mark: 'Qdrant',
    mechanism: 'The vector arm of a retrieve — one engine, never an in-memory dict',
    path: 'aegis/src/aegis/retrieval/vector_store.py',
  },
  {
    mark: 'LightRAG + Neo4j',
    mechanism: 'The graph arm, over an entity graph extracted from the corpus',
    path: 'aegis/src/aegis/retrieval/lightrag_backend.py',
  },
  {
    mark: 'Reciprocal Rank Fusion',
    mechanism: 'One ranking out of the arms, with each passage keeping its origin',
    path: 'aegis/src/aegis/retrieval/fusion.py',
  },
  {
    mark: 'ONNX cross-encoder',
    mechanism: 'Reorders the fused pool locally — deterministic, and off the gateway',
    path: 'aegis/src/aegis/retrieval/local_reranker.py',
  },
  {
    mark: 'Presidio',
    mechanism: 'PII detected and masked on input, on output and on tool results',
    path: 'aegis/src/aegis/guardrails/pii.py',
  },
  {
    mark: 'NeMo Guardrails',
    mechanism: 'Colang rails layered over the always-on programmatic pipeline',
    path: 'aegis/src/aegis/guardrails/nemo.py',
  },
  {
    mark: 'MAPIE',
    mechanism: 'Conformal intervals whose coverage is measured, not asserted',
    path: 'aegis/src/aegis/ml/model.py',
  },
  {
    mark: 'SHAP',
    mechanism: 'Per-prediction drivers, so a signal can be argued with',
    path: 'aegis/src/aegis/ml/model.py',
  },
  {
    mark: 'Offline eval gate',
    mechanism: 'Real ragas metrics, every judge call metered through our own gateway',
    path: 'aegis/src/aegis/evals/metrics.py',
  },
  {
    mark: 'OpenTelemetry + OpenInference',
    mechanism: 'GenAI semantic-convention spans across every run',
    path: 'aegis/src/aegis/observability/semconv.py',
  },
  {
    mark: 'Apache Superset',
    mechanism: 'Embedded dashboards behind a guest token that carries the tenant RLS',
    path: 'aegis/src/aegis/analytics/rls.py',
  },
]
