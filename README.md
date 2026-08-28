<div align="center">

<img src="web/public/brand/falcon-source.jpg" alt="Aegis" width="120">

# Aegis

**Autonomy you can audit.**

A domain-agnostic enterprise agentic-AI platform. Every autonomous action is
uncertainty-bounded, explainable, guarded, human-approved and fully traced.

</div>

![The Aegis console streaming an agent run](web/public/shots/console.png)

> The console mid-run: live reasoning, the orchestration graph with per-node
> timings and cost, retrieval provenance, and the human gate. Screenshots in this
> README are captured on **offline demo data** — the console's own red banner in
> each shot says so, and it is left uncropped on purpose.

---

## What Aegis is

Most agent stacks are a framework plus glue: they can call tools, but they cannot
tell you *why* they acted, *what* they read, *who* approved it, or *what it cost*.
Aegis is built the other way around — the instrumentation is the product.

- **Cheap enough to scale.** Every model call funnels through one gateway with
  per-tenant budgets enforced *before* spend, role→model routing, and a durable
  usage ledger.
- **Measurable enough to trust.** Calibrated conformal intervals and SHAP drivers
  on the ML signal; an offline evaluation gate and a CI regression gate on
  retrieval quality; OpenTelemetry traces on every run.
- **Secure enough to buy.** Multi-tenant RBAC with Postgres row-level security,
  six guardrail layers, and an append-only audit log.
- **It takes real actions** — behind a risk-tiered human gate.

**The core is a package you import, not an application you fork.** Point Aegis at
a new domain by writing one adapter; the core never learns the domain.

---

## Quickstart

```bash
git clone https://github.com/yrevash/aegis.git && cd aegis

# macOS / Linux
./scripts/bootstrap.sh && ./scripts/dev-native.sh
cd web && npm run dev

# Windows (elevated PowerShell) — toolchain, native stores, dependencies
.\scripts\install-windows.ps1
.\backend\scripts\start-windows.ps1
```

Then open **http://localhost:3000**. Seed the accounts first — `cd backend &&
PYTHONPATH=src:../aegis/src .venv/bin/python -m app.seed` — there is no fallback login
table, so an unseeded backend answers 503 and says exactly that. Logins:
`admin` / `ai` / `devops` / `client` (platform staff) and `northwind.admin` /
`vertex.admin` (the two seeded tenants), password `demo`.

Three run modes, so a demo never depends on infrastructure being healthy:

| Mode | What runs | Needs |
|---|---|---|
| `safe` | Console only, in-browser mock transport | nothing |
| `lite` | Real agent, no databases (SQLite audit) | a model API key |
| `full` | Everything, all four server stores | key + Postgres, Neo4j, Redis, Qdrant |

No Docker, no GPU, no WSL anywhere. Every store is a native local install.
On Windows, Redis is **Memurai** — same wire protocol, same port, no config change.

---

## The twelve modules

Every capability is a first-class **Aegis module**: a branded name paired with its
**honest underlying tech**. Branding, never hiding. This table mirrors the live
manifest in `backend/src/app/capabilities.py`, served publicly at
`GET /platform/capabilities`.

| Module | Tech underneath | What it is |
|---|---|---|
| **Aegis Gateway** | LiteLLM | Single model chokepoint: routing, budgets, timeout, retry, usage ledger |
| **Aegis Router** | LangGraph | Multi-agent supervisor — routes a turn to the right specialist |
| **Aegis Memory** | Postgres + Qdrant | Episodic · semantic · procedural, bitemporal, consolidated |
| **Aegis Cache** | Redis / Memurai | Semantic response cache |
| **Aegis Retrieval** | Neo4j/LightRAG + Qdrant | Hybrid RAG: vector + graph + BM25 → RRF → LLM rerank |
| **Aegis Signal** | XGBoost + MAPIE + SHAP | Ensemble + calibrated conformal intervals + SHAP |
| **Aegis Guardrails** | programmatic + NeMo Colang | Input/output rails: injection, PII, schema, content |
| **Aegis Evals** | Deterministic proxies offline, real `ragas` metrics live | Trace-level and answer evaluation |
| **Aegis Loop** | native | LLM-Ops self-improvement: trace → eval → diagnose → tiered release |
| **Aegis Governance** | Postgres RLS + JWT | Multi-tenant RBAC, budgets, RLS, audit log |
| **Aegis Trace** | OpenTelemetry → Phoenix | End-to-end glass-box tracing |
| **Aegis Tools / MCP** | native + MCP SDK | Risk-tiered tool registry + human gate, exposed over MCP |

---

## Architecture

```mermaid
flowchart TB
    B["<b>Browser</b>"]
    L1["<b>1 · Console</b> — web/<br/>Next.js 15 · React 19 · TypeScript<br/>four role portals · REST + SSE client"]
    L2["<b>2 · Composition root</b> — backend/src/app<br/>FastAPI · app factory · background sweepers<br/>routes.py — endpoints · JWT · RBAC · tenant scoping"]
    L3["<b>3 · Importable core</b> — aegis/src/aegis<br/>agent · gateway · guardrails · retrieval · memory · ml<br/>governance · ops · evals · observability · redteam · data · core"]
    L4["<b>4 · Stores and sinks</b><br/>Postgres · Qdrant · Neo4j · Redis · Arize Phoenix"]
    AD["<b>Domain adapter</b> — app/adapter/<br/>schema · tools · prompts · ML target · corpus"]

    B -->|"HTTPS · JWT · SSE"| L1
    L1 -->|"fetch + SSE"| L2
    L2 -->|"imports · injected deps"| L3
    L3 -->|"async drivers"| L4
    AD -.->|"the only seam that<br/>changes per domain"| L2
```

### The request path

`POST /query` → `guard_input` → `route` → `recall_memory` → `retrieve` →
`ml_predict` → `plan` → `gate` → *(approval interrupt)* → `act` → `reflect` →
`generate` → `guard_output` → `persist_memory`

Two rules the whole design hangs on:

1. **ML informs, it never gates.** The prediction and its conformal interval are
   evidence injected into the plan. The human gate fires on a **tool's risk
   tier** — never on model confidence.
2. **A gated run checkpoints durably** and resumes on any worker from a persisted
   approvals-inbox row.

---

## The trust stack

Six checkpoints stand between the model and a real action:

| # | Checkpoint | Mechanism |
|---|---|---|
| 01 | Input rails | injection classification · PII · schema · topical scope, fail-closed |
| 02 | Retrieval | vector + graph + BM25 → RRF → rerank, every claim cited |
| 03 | Signal | conformal interval + SHAP drivers |
| 04 | Human gate | by tool risk tier |
| 05 | Governance | budget enforced before spend · row-level security |
| 06 | Audit | OpenTelemetry trace + append-only audit row |

---

## Standards and compliance readiness

> **Compliance-readiness evidence — not certification.**
> Aegis holds **no** ISO 27001 certificate, **no** ISO/IEC 42001 certificate, **no**
> SOC 2 report and **no** EU AI Act conformity assessment. Nobody independent has
> audited any of it. What follows is a control-by-control map from published
> frameworks to **files, routes and tests in this repository** — the kind of thing a
> buyer's security reviewer can open and check, not a badge.

Twelve frameworks are mapped, India's law first because for a deployment in India the
DPDP Act is *law* and the rest is practice:

| Jurisdiction | Frameworks |
|---|---|
| **India** | DPDP Act 2023 + Rules 2025 · CERT-In Directions · MeitY IAGG, RBI ITGRCA, SEBI CSCRF, BIS IS 17428 |
| **International** | OWASP LLM Top 10 · OWASP Top 10 · MITRE ATLAS · NIST AI RMF · ISO/IEC 42001 · ISO/IEC 27001 · EU AI Act · SOC 2 TSC · GDPR |

Every control sits in one of **four** states — `enforced`, `partial`,
`not_implemented`, `not_applicable` — and the last two are stated plainly rather than
dressed up. `enforced` costs the most to claim: it needs a *file* reference **and** a
*test* reference, and the test suite refuses one without the other.

**The totals are deliberately not printed here.** They change whenever a control
changes state, and a number typed into a README is a number nobody re-derives. Read
them from the surfaces that count them on every request:

| Where | What it gives | Who can read it |
|---|---|---|
| [`docs/compliance/README.md`](docs/compliance/README.md) | The written authority — every control, its state, its evidence and what is missing | anyone with the repo |
| `GET /platform/standards` | Framework names, jurisdictions and the four derived counts | **public**, no token — this is what the landing page renders |
| `GET /compliance` | The full control-by-control map with every file, route and test | platform staff only — a public gap map is a target list |

`backend/tests/api/test_compliance.py` resolves **every** evidence reference against
the real filesystem, the real served route table and the real pytest node ids on each
run, so a claim naming a file that no longer exists fails the suite rather than
reaching a reviewer.

---

## What the stack actually runs on

The mechanisms behind the pitch, each with the file it lives in. Every row but the
last is also a cell on the landing page, where `web/tests/landing/stackClaims.test.mjs`
resolves its path against the repository on each run — so a renamed module breaks a
test instead of leaving a claim standing. Row-level security is off that band only
because the page already draws it as its own exhibit, and a page should not make one
argument twice.

| | Mechanism | In the repository |
|---|---|---|
| **LangGraph** | A parked run resumes on a fresh worker, from a durable Postgres checkpoint | `backend/src/app/agent/checkpointer.py` |
| **Temporal** | Ingestion, reindex and reconcile run as durable workflows | `backend/src/app/jobs/flows/ingest.py` |
| **Qdrant** | The vector arm of a retrieve — one engine, never an in-memory dict | `aegis/src/aegis/retrieval/vector_store.py` |
| **LightRAG + Neo4j** | The graph arm, over an entity graph extracted from the corpus | `aegis/src/aegis/retrieval/lightrag_backend.py` |
| **Reciprocal Rank Fusion** | One ranking out of the arms, each passage keeping its origin | `aegis/src/aegis/retrieval/fusion.py` |
| **ONNX cross-encoder** | Reorders the fused pool locally — deterministic, and off the gateway | `aegis/src/aegis/retrieval/local_reranker.py` |
| **Presidio** | PII detected and masked on input, on output and on tool results | `aegis/src/aegis/guardrails/pii.py` |
| **NeMo Guardrails** | Colang rails layered over the always-on programmatic pipeline | `aegis/src/aegis/guardrails/nemo.py` |
| **MAPIE** | Conformal intervals whose coverage is measured, not asserted | `aegis/src/aegis/ml/model.py` |
| **SHAP** | Per-prediction drivers, so a signal can be argued with | `aegis/src/aegis/ml/model.py` |
| **Offline eval gate** | Deterministic lexical proxies — no LLM call, no network | `aegis/src/aegis/evals/metrics.py` |
| **Live eval** | **Real `ragas` metrics**, every judge call metered through the Aegis gateway | `aegis/src/aegis/evals/libs/gateway_adapters.py` |
| **OpenTelemetry + OpenInference** | GenAI semantic-convention spans across every run | `aegis/src/aegis/observability/semconv.py` |
| **Apache Superset** | Embedded dashboards behind a guest token carrying the tenant RLS | `aegis/src/aegis/analytics/rls.py` |
| **Postgres RLS** | `FORCE ROW LEVEL SECURITY`, and a `NOSUPERUSER NOBYPASSRLS` serving role | `aegis/src/aegis/governance/rls.py` |

The proof for the first row is
`backend/tests/agent/test_durable_approvals.py::test_fresh_worker_rehydrates_and_resumes_by_thread_id`
— park the run, restart the worker, resume from the checkpoint.

Both of these sections are rendered on the public landing page, and **both are
removable by one constant** in `web/src/components/landing/bands.config.ts`, whose
docstring says exactly what each switch takes off the page.

---

## The console

Four role-scoped portals — `admin`, `ai_team`, `devops`, `client` — each a focused
subset of surfaces. Every claim the platform makes has a screen behind it.

| | |
|---|---|
| ![Admin overview](web/public/shots/overview.png) | ![Knowledge graph](web/public/shots/graph.png) |
| **Command centre** — spend, approvals, security posture, latency | **Knowledge graph** — the entities a run touched, from Neo4j |
| ![Guardrails](web/public/shots/guardrails.png) | ![Memory](web/public/shots/memory.png) |
| **Guardrails** — six layers, each with its own pass/block record | **Memory** — episodic, semantic and procedural recall |

---

## Pointing Aegis at a new domain

Only `backend/src/app/adapter/` changes. The core reaches the domain exclusively
through injected callables, so the seam stays isolated. **The full procedure —
which file to edit, in what order, and the command that proves each step — is
[`SKILL.md`](SKILL.md).**

Ten pieces: eight modules plus two content directories.

| # | Piece | What it defines |
|---|---|---|
| 1 | `schema.py` | Entities and enums — the shared vocabulary |
| 2 | `ml_spec.py` | `FEATURES` / `TARGET`, the latent signal, and the prediction narrative |
| 3 | `generator.py` | Synthetic records: procedural draws + LLM fabrication + templated fallback |
| 4 | `tools.py` | Domain actions — typed, idempotent, reversible, audited, risk-tiered |
| 5 | `personas.py` | Who is served: data scope + tool allowlist per persona |
| 6 | `prompts.py` | The system prompt per persona (paired with 5) |
| 7 | `memory_spec.py` | What counts as a durable fact, and who it is scoped to |
| 8 | `roster.py` | Which specialists the supervisor may route to |
| 9 | `corpus/` | Seed knowledge documents |
| 10 | `skills/` | Procedural how-to-act playbooks |

`__init__.py` is not a piece — it is the registry, and its `__all__` is the
contract to keep stable. Domain logic never leaks into the core;
`backend/tests/adapter/test_piece_manifest.py` counts the pieces on disk and
fails if any document disagrees with it.

---

## Repository layout

```
aegis/          # the importable core — 30 packages, 2268 tests
backend/        # FastAPI composition root — 121 endpoints, 1174 tests
  src/app/
    api/        # routes + Pydantic contracts + SSE event schema
    agent/      # LangGraph orchestration
    adapter/    # the domain seam
    platform/   # role-portal read surfaces
web/            # Next.js console — landing page + five role portals
docs/           # learn path, teaching course, ADRs, module reference, threat model
scripts/        # bootstrap · preflight · start  (.sh and .ps1)
```

## Verification

```bash
cd aegis   && PYTHONPATH=src ../backend/.venv/bin/python -m pytest -q   # 2268 passed
cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest -q # 1174 passed
cd web     && npx tsc --noEmit && npx next lint --dir src && npm test   # 158 passed
cd web     && npx next build                                           # 65/65 pages
```

## Documentation

Start with
**[`docs/architecture/system-architecture.md`](docs/architecture/system-architecture.md)** —
the whole platform, top to bottom, in one file. `docs/install/` is the setup path;
[`docs/README.md`](docs/README.md) indexes everything else.

| | |
|---|---|
| [`docs/architecture/system-architecture.md`](docs/architecture/system-architecture.md) | The system, end to end — layers, stores, gateway, security model, one question traced through |
| [`docs/install/`](docs/install/README.md) | Bring-up: prerequisites, the ordered runbook, the demo seeders |
| [`docs/teaching/`](docs/teaching/README.md) | The course — one file per `aegis.*` module, from no prior knowledge to defending every design decision |
| [`docs/module/MODULE_REFERENCE.md`](docs/module/MODULE_REFERENCE.md) | The Module Contract, the streaming spine, and the module map for the importable core |
| [`docs/adr/`](docs/adr/) | Nine architecture decision records |
| [`docs/security/`](docs/security/) | Threat model and OWASP Agentic Top-10 mapping |
| [`docs/operations/runbook.md`](docs/operations/runbook.md) | One-page operations guide and fallback ladder |

Live, self-describing surfaces: `GET /docs` (OpenAPI), `GET /platform/capabilities`
(the module manifest as data, public), `GET /about`.
