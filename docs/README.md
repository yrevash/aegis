# Aegis documentation

**New here? Start with [`learn/00-what-aegis-is.md`](learn/00-what-aegis-is.md).**

Aegis is a domain-agnostic agentic-AI platform: an importable Python core (`aegis/`), a
FastAPI composition root (`backend/`), and a Next.js console (`web/`). This directory
holds the documentation for all three.

There are two reading paths and one shelf of reference.

- **[`learn/`](learn/00-what-aegis-is.md)** — six files, read in order, that take you from
  zero to understanding the whole *system*: the layers, the backend, the console, the
  pipelines, and how to run and retarget it.
- **[`teaching/`](teaching/README.md)** — a course, one folder per *module*, that takes you
  from no prior knowledge of agents or RAG to being able to defend every design decision.
- Everything else is reference: deeper detail on one subject, consulted as needed.

---

## Path 1 — `learn/`: the system, end to end

| File | What it covers |
|---|---|
| [`learn/00-what-aegis-is.md`](learn/00-what-aegis-is.md) | The problem, what "domain-agnostic agentic platform" means, the Aegis modules with their honest underlying tech, the agentic-AI vocabulary in plain language, and the honesty principle |
| [`learn/10-architecture.md`](learn/10-architecture.md) | The four layers browser → console → backend → core → stores, why `aegis/` is a separate importable package, the strangler shims that glue it to `backend/`, and which store holds what |
| [`learn/20-backend.md`](learn/20-backend.md) | The FastAPI composition root, every endpoint and its guard, JWT auth and the two role vocabularies, each Aegis module's home, the data layer, and the background sweepers |
| [`learn/30-frontend.md`](learn/30-frontend.md) | The App Router tree, the four role portals and their sections, session hydration and `PortalGuard`, the live-first probe with its labelled mock fallback, the SSE-to-state pipeline, and the design system |
| [`learn/40-pipelines.md`](learn/40-pipelines.md) | **The most important one.** A query traced end to end through every graph node, the human-approval gate, the memory read/write/consolidation path, and the LLM-Ops self-improvement loop |
| [`learn/50-run-and-extend.md`](learn/50-run-and-extend.md) | Prerequisites, the three run modes, bring-up, verification, the environment variables that matter, and how to point Aegis at a new domain through the adapter seam |

## Path 2 — `teaching/`: the modules, in depth

[`teaching/README.md`](teaching/README.md) is the syllabus and the reading order. Start
with [`teaching/00-foundations/`](teaching/00-foundations/10-guide.md) — tokens,
embeddings, vector search, what RAG actually means, and what makes something an *agent*.
Then one folder per module, each with the same three files: `10-guide.md` (the module in
ten minutes), `40-diagrams.md` (the flows), and `50-interview.md` (the questions you will
be asked, with answers). [`teaching/STYLE.md`](teaching/STYLE.md) is the writing contract.

Every guide is also rendered to HTML beside its Markdown, built by
`scripts/build-teaching-html.mjs`.

---

## Reference

### `module/` — the importable core

[`module/MODULE_REFERENCE.md`](module/MODULE_REFERENCE.md) — the Module Contract (three
pillars), the AG-UI streaming spine, the whole-platform diagram, the module map with each
module's install extra, and the honest debt list. Per-module depth lives in `teaching/`.

### `architecture/` — subsystem deep dives

| File | Subject |
|---|---|
| [`architecture/backend.md`](architecture/backend.md) | Backend design context: orchestration, retrieval, the ML spine, guardrails, observability |
| [`architecture/memory-spec.md`](architecture/memory-spec.md) | The authoritative long-term-memory and context-engineering specification |
| [`architecture/eval-strategy.md`](architecture/eval-strategy.md) | The three evaluation layers: offline gate, CI regression gate, live trace-eval |
| [`architecture/synthetic-data.md`](architecture/synthetic-data.md) | A practical guide to generating training- and demo-grade synthetic data |

### `adr/` — architecture decision records

Nine numbered decisions, each with its context, options and consequences. Superseded ADRs
are kept and marked, not deleted — the record is the point.

| ADR | Decision | Status |
|---|---|---|
| [`0001`](adr/0001-litellm-as-gateway.md) | LiteLLM as the single model gateway | Accepted |
| [`0002`](adr/0002-nemo-guardrails.md) | NeMo Guardrails as the Colang policy engine | Accepted |
| [`0003`](adr/0003-lightrag-over-graphrag.md) | LightRAG over GraphRAG | Accepted; vector half superseded by `0009` |
| [`0004`](adr/0004-conformal-prediction-uncertainty.md) | Conformal prediction for calibrated uncertainty | Accepted |
| [`0005`](adr/0005-postgressaver-durable-execution.md) | `PostgresSaver` for durable, resumable execution | Accepted |
| [`0006`](adr/0006-rrf-hybrid-retrieval.md) | Reciprocal Rank Fusion for hybrid retrieval | Accepted |
| [`0007`](adr/0007-conformal-autonomy-bands.md) | Conformal autonomy bands | **Superseded** — engine removed, wire enum survives |
| [`0008`](adr/0008-multi-tenant-rls-governance.md) | Multi-tenant governance with Postgres RLS | Accepted |
| [`0009`](adr/0009-embedded-vector-store.md) | A server-free, embedded vector store | Accepted |

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

[`hackathon/brief.md`](hackathon/brief.md) — mission context: what is being built, why,
the six weighted scoring areas, the hard environment constraints, and the domain-adapter
seam that changes on the day.

---

## Outside this directory

| File | Subject |
|---|---|
| [`../README.md`](../README.md) | Repository overview, the stack table, and the module manifest |
| [`../INSTALL.md`](../INSTALL.md) | The long-form setup manual and the full environment-variable reference |
| [`../aegis/README.md`](../aegis/README.md) | The importable package's own README |
| [`../web/README.md`](../web/README.md) | Console-specific quick reference, including its honest caveats |
| [`../backend/README.md`](../backend/README.md) | Backend quick reference |

Live, self-describing surfaces worth knowing about: `GET /docs` (auto-generated OpenAPI),
`GET /platform/capabilities` (the module manifest as data), and `GET /about`.
