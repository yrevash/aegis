# Aegis module docs — index

Learning-oriented documentation for the Aegis modular platform (`aegis/src/aegis/`). Start with
the overview, then read whichever module you need — each doc is self-contained (what it is, how
it works, its architecture and runtime-flow diagrams, its real public API with a standalone usage
snippet, its install extra, the AG-UI events it emits, and its honest-infra design notes).

| Doc | Hook |
|---|---|
| [`00-overview.md`](./00-overview.md) | The Module Contract (importable, shows-its-work, honest infra), the AG-UI streaming spine, and a whole-platform diagram — read this first. |
| [`aegis-core.md`](./aegis-core.md) | The dependency-free contract every other package builds on: Protocols, shared types, the registry, `require()`, `AegisMode`, and `AegisEmitter`. |
| [`aegis-data.md`](./aegis-data.md) | The portable SQLAlchemy base (`AegisBase`) and pgvector-or-JSON column types that let durable modules run on Postgres in prod and SQLite in tests. |
| [`aegis-guardrails.md`](./aegis-guardrails.md) | The pilot module: LLM-agnostic input/output rails — schema, regex-based PII redaction, two-tier fail-closed injection detection. |
| [`aegis-ml.md`](./aegis-ml.md) | Predictions that know how much to trust themselves: an ensemble model wrapped in conformal intervals and a SHAP explanation. |
| [`aegis-retrieval.md`](./aegis-retrieval.md) | Hybrid vector + graph RAG: chunk, recall, RRF-fuse, rerank, and assemble citations. |
| [`aegis-gateway.md`](./aegis-gateway.md) | The one async door every model call in Aegis walks through: role-based routing, cost/budget accounting, fallback. |
| [`aegis-memory.md`](./aegis-memory.md) | Three-tier long-term memory — working, episodic, consolidated — so an agent remembers without re-reading everything every turn. |
| [`aegis-governance.md`](./aegis-governance.md) | Who's allowed to do what, how much they can spend, and whether you can prove it afterward: tenants, RBAC, RLS, budgets, audit. |
| [`aegis-evals-ops.md`](./aegis-evals-ops.md) | Two halves of one loop: `aegis.evals` measures quality, `aegis.ops` gates releases on it. |
| [`aegis-observability.md`](./aegis-observability.md) | The OTel/OpenInference tracing stack that exports the same span vocabulary every module stamps its events with. |
| [`aegis-agent.md`](./aegis-agent.md) | The finale: the LangGraph plan→gate→act→reflect graph that composes every other module through the `AgentDeps` injection seam. |
