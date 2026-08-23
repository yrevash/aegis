# Learning Aegis, module by module

> All 29 files were written against the running source on 2026-08-21 and
> re-verified against it on **2026-08-23**, after two days that changed a great
> deal: durable Postgres checkpointing, the graph retrieval arm's entity vectors
> and chunk KV, notifications over Redis pub/sub and SSE, both guardrail engines
> running, append-only audit privileges, and a compliance surface. Where a file
> described the older behaviour it was corrected; where a claim could not be
> verified it says so rather than repeating itself.

One file per module. Each is a **parent file** — everything about that module in
one place, written for someone who has never seen it before and read end to end.

There is no second or third file per module. If a thing is worth knowing about
`aegis.guardrails`, it is in `guardrails.md`.

## The shape every file follows

Read one and you can navigate all of them, because they are all built the same way:

| Section | What it gives you |
|---|---|
| **What it is** | The module in plain language, before any code. Jargon is expanded where it first appears |
| **Why it exists here** | The specific problem in *this* platform that the module answers |
| **Diagram** | The real flow, using the real names from the source |
| **The architecture** | The files, with one line each, so you can walk from the doc into the code |
| **What is actually in Aegis** | The mechanism, with real functions and real measured numbers |
| **How it runs** | One request through the module, in order |
| **What is not here** | The absences, stated plainly. This section is the point, not an apology |

## The rule these files obey

**Every claim describes what is actually in this repository.** Not what a platform
like this usually has, not what the roadmap says, not what the library *could* do.
If Aegis uses NeMo Guardrails, the file says which rails are defined and where the
`.co` files are. If something is absent, the file says it is absent.

Each file also carries a **What is not here** section, and that section is the point
rather than an apology: a learner who cannot tell the implemented parts from the
intended ones has not learned the system.

The same rule applies to the bugs. Several files spend a paragraph on a defect this
platform actually shipped — a rail that documented itself as advisory and refused, a
retrieval arm that was inert by construction, a flag that was decorative. They are
there because the shape of a real failure teaches the mechanism better than a clean
description of it does, and because a reader who knows how a control failed once can
recognise the same failure the next time.

---

## The modules

### The contract
| | |
|---|---|
| [`core`](core.md) | Protocols, shared types, the registry, `require()`. Zero heavy dependencies |
| [`data`](data.md) | Portable SQLAlchemy base; cross-dialect JSON, vector and UTC column types |
| [`pipelines`](pipelines.md) | The stage spec every pipeline is read from, and what each stage emits |

### Governance and safety
| | |
|---|---|
| [`governance`](governance.md) | Tenants, RBAC, Postgres row-level security, budgets, audit |
| [`guardrails`](guardrails.md) | The input and output rails — schema, PII, injection, topic, content safety, grounding |
| [`security`](security.md) | Threats mapped to the controls that are actually wired |
| [`redteam`](redteam.md) | The harness that attacks the rails and reports what got through |
| [`conformance`](conformance.md) | The suite that proves a domain swap is complete |
| [`settings`](settings.md) | Prompt versions, seats, guardrail configuration, the LLM-Ops loop |
| [`dbadmin`](dbadmin.md) | The read-only database console and the role that cannot write |

### The agent
| | |
|---|---|
| [`agent`](agent.md) | Plan → gate → act → reflect, fan-out, and the graph every run walks |
| [`memory`](memory.md) | Semantic facts, episodic sessions, consolidation, and how scope is enforced |
| [`skills`](skills.md) | `SKILL.md` documents, when an agent reaches for one, and who may write them |

### Knowledge
| | |
|---|---|
| [`ingestion`](ingestion.md) | Parse, chunk, enrich, embed, index — and the quality gate |
| [`retrieval`](retrieval.md) | Hybrid vector + graph recall, fusion, reranking |

### The chokepoint
| | |
|---|---|
| [`gateway`](gateway.md) | Every model call: routing, cost, budget, fallback, the limiter |
| [`jobs`](jobs.md) | Durable work on Temporal, and what survives a crash |
| [`runs`](runs.md) | The run record, folded from its own events |

### Measurement
| | |
|---|---|
| [`ml`](ml.md) | Prediction, SHAP explanation, conformal intervals |
| [`forecast`](forecast.md) | Time-series forecasting with measured, calibrated intervals |
| [`evals`](evals.md) | Metrics and the LLM-judge harness |
| [`analytics`](analytics.md) | The `analytics_*` views, their RLS, and the Superset integration |
| [`ops`](ops.md) | Diagnose, eval-gated release, promotion |
| [`observability`](observability.md) | OTel / OpenInference spans and where they go |
| [`reports`](reports.md) | Generated reports and their sourcing |

### Multimodal and outside data
| | |
|---|---|
| [`media`](media.md) | Typed payloads and payload hygiene for non-text input |
| [`vision`](vision.md) | Image understanding, with the injection screen ahead of the model |
| [`voice`](voice.md) | Speech to text, guarded by the full text rail before an agent sees it |
| [`websearch`](websearch.md) | Reaching outside the tenant's own corpus |

---

## Related, and deliberately not duplicated here

- [`../module/MODULE_REFERENCE.md`](../module/MODULE_REFERENCE.md) — the Module
  Contract and the map. This course explains; that document specifies.
- [`../module/PIPELINES.md`](../module/PIPELINES.md) — **generated** from
  `aegis.pipelines.spec`, so a stage cannot be documented there and absent in code.
- [`../security/`](../security/) — the threat model and the OWASP-Agentic mapping.
- [`../install/`](../install/README.md) — getting it running.
- [`../architecture/system-architecture.md`](../architecture/system-architecture.md) —
  the whole platform in one file. Read it first if you want the shape before the parts.
- [`../compliance/README.md`](../compliance/README.md) — the control-by-control
  evidence map, and the live surface behind `GET /v1/compliance`.

**Two subsystems have no file here, because they are not `aegis.*` modules.** Both
live in `backend/src/app/` and are documented in `system-architecture.md`:

- **Notifications** (`backend/src/app/notifications.py`,
  `backend/src/app/data/notifications.py`) — durable row first, Redis pub/sub second,
  SSE to the browser. §5 of the architecture doc.
- **The compliance surface** (`backend/src/app/platform/compliance.py`) — §6, and
  `../compliance/README.md` in full.
