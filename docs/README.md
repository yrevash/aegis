# Aegis documentation

**New here? Start with [`learn/00-what-aegis-is.md`](learn/00-what-aegis-is.md).**

Aegis is a domain-agnostic agentic-AI platform: an importable Python core (`aegis/`), a
FastAPI composition root (`backend/`), and a Next.js console (`web/`). This directory
holds the documentation for all three.

The docs are split into two kinds. **`learn/`** is a teaching path — six files, read in
order, that take you from zero knowledge to understanding the whole system. Everything
else is **reference**: deeper detail on one subject, consulted as needed.

---

## Start here — `learn/`

Six files, in order. Each opens with what you'll learn and carries mermaid diagrams of
real components.

| File | What it covers |
|---|---|
| [`learn/00-what-aegis-is.md`](learn/00-what-aegis-is.md) | The problem, what "domain-agnostic agentic platform" means, the twelve Aegis modules with their honest underlying tech, the agentic-AI vocabulary in plain language, and the honesty principle |
| [`learn/10-architecture.md`](learn/10-architecture.md) | The four layers browser → console → backend → core → stores, why `aegis/` is a separate importable package, the strangler shims that glue it to `backend/`, and which store holds what |
| [`learn/20-backend.md`](learn/20-backend.md) | The FastAPI composition root, every endpoint and its guard, JWT auth and the two role vocabularies, each Aegis module's home, the data layer, and the background sweepers |
| [`learn/30-frontend.md`](learn/30-frontend.md) | The App Router tree, the four role portals and their sections, session hydration and `PortalGuard`, the live-first probe with its labelled mock fallback, the SSE-to-state pipeline, and the design system |
| [`learn/40-pipelines.md`](learn/40-pipelines.md) | **The most important one.** A query traced end to end through every graph node, the human-approval gate, the memory read/write/consolidation path, and the LLM-Ops self-improvement loop |
| [`learn/50-run-and-extend.md`](learn/50-run-and-extend.md) | Prerequisites, the three run modes, bring-up, verification, the environment variables that matter, and how to point Aegis at a new domain through the adapter seam |

---

## Reference

### `architecture/` — subsystem deep dives

| File | Subject |
|---|---|
| [`architecture/backend.md`](architecture/backend.md) | Backend design context: orchestration, retrieval, the ML spine, guardrails, observability |
| [`architecture/memory-spec.md`](architecture/memory-spec.md) | The authoritative long-term-memory and context-engineering specification |
| [`architecture/eval-strategy.md`](architecture/eval-strategy.md) | The three evaluation layers: offline gate, CI regression gate, live trace-eval |
| [`architecture/synthetic-data.md`](architecture/synthetic-data.md) | A practical guide to generating training- and demo-grade synthetic data |

### `module/` — per-module reference for the importable core

Fifteen files documenting `aegis/src/aegis/`. Start with
[`module/README.md`](module/README.md) (the index) or
[`module/00-overview.md`](module/00-overview.md) (what "modular" means here). Then one
file per module: [`aegis-core`](module/aegis-core.md),
[`aegis-data`](module/aegis-data.md), [`aegis-gateway`](module/aegis-gateway.md),
[`aegis-guardrails`](module/aegis-guardrails.md),
[`aegis-retrieval`](module/aegis-retrieval.md), [`aegis-memory`](module/aegis-memory.md),
[`aegis-ml`](module/aegis-ml.md), [`aegis-agent`](module/aegis-agent.md),
[`aegis-governance`](module/aegis-governance.md),
[`aegis-observability`](module/aegis-observability.md), and
[`aegis-evals-ops`](module/aegis-evals-ops.md).
[`module/MODULE_REFERENCE.md`](module/MODULE_REFERENCE.md) is the consolidated reference
and the Module Contract; [`module/VERIFICATION.md`](module/VERIFICATION.md) is the
whole-platform extraction verification report.

### `adr/` — architecture decision records

Nine numbered decisions, each with its context, options and consequences.

| ADR | Decision |
|---|---|
| [`0001`](adr/0001-litellm-as-gateway.md) | LiteLLM as the single model gateway |
| [`0002`](adr/0002-nemo-guardrails.md) | NeMo Guardrails as the Colang policy engine |
| [`0003`](adr/0003-lightrag-over-graphrag.md) | LightRAG over GraphRAG |
| [`0004`](adr/0004-conformal-prediction-uncertainty.md) | Conformal prediction for calibrated uncertainty |
| [`0005`](adr/0005-postgressaver-durable-execution.md) | `PostgresSaver` for durable, resumable execution |
| [`0006`](adr/0006-rrf-hybrid-retrieval.md) | Reciprocal Rank Fusion for hybrid retrieval |
| [`0007`](adr/0007-conformal-autonomy-bands.md) | Conformal autonomy bands |
| [`0008`](adr/0008-multi-tenant-rls-governance.md) | Multi-tenant governance with Postgres RLS |
| [`0009`](adr/0009-embedded-vector-store.md) | A server-free, embedded vector store (supersedes the vector half of `0003`) |

### `security/`

| File | Subject |
|---|---|
| [`security/overview.md`](security/overview.md) | The security design: what is built, how it maps to standards, how to demonstrate it |
| [`security/owasp-agentic.md`](security/owasp-agentic.md) | Mapping to the OWASP Top 10 for Agentic Applications |
| [`security/threat-model.md`](security/threat-model.md) | One-page threat model: OWASP LLM Top 10, OWASP Agentic (ASI) Top 10, the lethal trifecta |

### `operations/`

[`operations/runbook.md`](operations/runbook.md) — the day-of operations page: three
commands and the fallback ladder for when something is red.

### `design/`

| File | Subject |
|---|---|
| [`design/design-reference.md`](design/design-reference.md) | The neutral light-dashboard design system the console is built on |
| [`design/ui-clarity-brief.md`](design/ui-clarity-brief.md) | The UI clarity redesign brief |

### `hackathon/`

| File | Subject |
|---|---|
| [`hackathon/brief.md`](hackathon/brief.md) | Mission context: what is being built, why, and how it is judged |
| [`hackathon/jury-rubric.md`](hackathon/jury-rubric.md) | The six weighted scoring areas and their performance levels |

### `sessions/` and `HANDOFF.md`

[`HANDOFF.md`](HANDOFF.md) is the current state-of-play for whoever picks the work up
next; [`sessions/`](sessions/) holds the dated working logs behind it.

---

## Outside this directory

| File | Subject |
|---|---|
| [`../README.md`](../README.md) | Repository overview, the stack table, and the module manifest |
| [`../INSTALL.md`](../INSTALL.md) | The long-form setup manual and the full environment-variable reference |
| [`../web/README.md`](../web/README.md) | Console-specific quick reference, including its honest caveats |
| `../backend/README.md` | Backend quick reference |

Live, self-describing surfaces worth knowing about: `GET /docs` (auto-generated OpenAPI),
`GET /platform/capabilities` (the twelve-module manifest as data), and `GET /about`.
