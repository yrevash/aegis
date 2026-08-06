# how_to_approach.md — How This Agent Should Work

> This is the operating manual for building this project. It governs *behavior*: when to ask, when to research, how to write, and what "done well" means. When in doubt about anything, this file's defaults win: **ask, research, confirm — don't guess.**

---

## 1. Core operating principles

1. **Ask when in doubt.** If a requirement is ambiguous, if domain details are missing, or if a decision has real rework cost, **stop and ask a specific question** rather than guessing. A wrong assumption baked deep is expensive; a question is cheap.
2. **Research before implementing.** The stack (LangGraph, LightRAG, MAPIE, LiteLLM, Guardrails AI/NeMo, OpenTelemetry/Phoenix, react-force-graph, Tremor) moves fast. **Verify the current API/version from official docs before writing against it.** Do not rely on memory for library signatures.
3. **Confirm before major moves.** Before a big architectural choice, a new dependency, or anything that deviates from `hackathon.md`/`frontend.md`/`backend.md`/`security.md`, confirm first.
4. **De-risk first.** Validate the riskiest assumption before building on it — specifically the **tool-calling spike** (`backend.md` §9). Never build the agent loop against an unverified gateway.
5. **Interfaces first.** Agree on the contracts between layers (API shapes, SSE event schema, ML return shape) *before* building in parallel. Build against mocks until the real thing lands.
6. **Don't over-engineer.** The stable core is finalized. Resist adding tools, abstractions, or cleverness not called for in the context files. Under a maintainability-scored rubric, unnecessary complexity loses points.

---

## 2. When to ASK (raise a specific question)

- The problem/domain details are unspecified (schema, tools, personas, ML target, corpus — the five adapter pieces).
- An interface between frontend and backend is unclear (event schema, endpoint shape, auth flow).
- A tradeoff has real consequences (e.g., local reranker model vs API rerank given RAM; buffering vs post-hoc output scanning for streaming).
- A requested change conflicts with a decision in the context files.
- The tool-calling spike fails and the agent design must pivot.

Ask **one precise question at a time**, with the options and the tradeoff, not an open-ended "what should I do?"

---

## 3. When to RESEARCH (verify before writing)

- Any library API/version you're not 100% current on — check official docs.
- Any "is this still the best/current way" question — the ecosystem changed in the last few months.
- Framework-specific patterns (LangGraph human-in-the-loop edges, LightRAG Neo4j backend config, MAPIE calibration, OTel GenAI semantic conventions, Guardrails AI/NeMo Colang policies).
- Anything you'd otherwise implement from a possibly-stale memory. **Prefer a 2-minute docs check over a subtly-wrong implementation.**

---

## 4. Quality bars (these are scored — treat them as features)

**Remember: an AI reader grades the code, docs, and presentations, and a human jury observes across both days.** Structure and clarity are worth points.

### Code quality
- Types everywhere: Pydantic (Python) / TypeScript strict (frontend).
- One linter/formatter enforced: Ruff (Python), Biome or ESLint+Prettier (TS).
- Small, single-responsibility modules; **no god-files**; clear module boundaries (`api/`, `agent/`, `retrieval/`, `ml/`, `guardrails/`, `data/`, `observability/`, `adapter/`).
- The **domain adapter is isolated** — domain logic never leaks into the core.

### Maintainability
- Config-driven where it matters; the five swappable pieces live behind clean interfaces.
- Consistent naming across the repo (the AI reader rewards consistency).
- Idempotent, reversible actions; everything auditable.

### Documentation (produced continuously, never at the end)
- **README** states the architecture in the first screen (what it is, the stack, how to run, the diagram).
- Docstrings on public functions/modules.
- **FastAPI OpenAPI** page kept meaningful (real descriptions, typed schemas).
- **2–3 ADRs** — short "why this, not that" records for: LightRAG vs Microsoft GraphRAG; conformal prediction for uncertainty; heterogeneous model routing; OTel-native observability. ADRs demonstrate engineering *judgment*, which is what the rubric is trying to detect.
- Architecture diagram + system design kept in sync with reality.

### Testing
- Real tests on the **critical path** (agent loop, retrieval, ML), not 100% coverage.
- The **eval suite is a quality gate** — quality must not regress on a change.

### Commits
- Conventional commit messages. Clean history. The AI reader reads this.

---

## 5. Build sequence (recommended)

1. **Day-0 spikes:** LiteLLM connection → **tool-calling spike** → confirm local infra (Postgres+pgvector, Neo4j, Redis, Phoenix) all run.
2. **Pre-build #1: the synthetic-data generator** (schema + LLM fabrication + seeding). This is the single highest-value pre-build — it turns "problem revealed" into "realistic data flowing" in minutes on the day.
3. **Vertical slice by end of Day 1:** query → guardrail → retrieval → agent → one action → streamed answer, end to end, however thin. Something must *work* at the Day-1 checkpoint.
4. **Day 2:** depth (ML spine, conformal+SHAP, evals, more tools) + polish (graph animation, dashboards, security demo, docs, deck, video).

Build **vertically first** (thin end-to-end), then deepen. Never build one layer fully in isolation while the others are stubs — you need a working slice at every checkpoint.

---

## 6. Security-first mindset (see `security.md`)

- Every model input and output passes a guardrail. No exceptions.
- Tools are least-privilege and allowlisted per persona.
- Treat the LLM as a hostile user: rate-limit and scope agent functions like external traffic.
- Log everything to the audit table. You can't secure or demo what you can't see.

---

## 7. The prime rule

If a task doesn't serve a **rubric axis** or the winning sentence — *"cheap to scale, measurable to trust, secure to buy, and it takes real actions explainably"* — question whether it should be done at all. Focus effort where it scores.
