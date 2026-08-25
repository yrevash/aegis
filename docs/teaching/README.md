# Aegis, module by module

Twenty-nine modules, one document each. Read a module's file and you know that module —
what it does, how it works, what it stores in the database, what security it enforces,
what routes it exposes. You should not need to open a second file.

Read in the order below and you know Aegis.

---

## The shape every file follows

They are all built the same way, so reading one teaches you how to read the rest.

| Section | What it gives you |
|---|---|
| **What it is** | The module in plain language, before any code |
| **Why it exists** | Why an enterprise agentic platform needs it |
| **Diagram** | The real flow, using the real names from the source |
| **How it works** | The mechanism, step by step |
| **What it stores** | The database tables it owns, and what each column is for |
| **Security and tenant isolation** | What it enforces, and who is allowed to call it |
| **API surface** | The routes, who may call them, what they return |
| **Configuration** | The environment variables that change its behaviour |
| **Where it lives** | The files, so you can walk from the doc into the code |
| **What it does not do** | The boundaries, stated plainly |

## The rule these files obey

**Every claim describes what is in this repository today.** Not what a platform like this
usually has, not what the roadmap says, not what a library *could* do. If Aegis uses NeMo
Guardrails, the file says which rails are defined and where the `.co` files are. If
something is out of scope, the file says so.

The **What it does not do** section is part of the teaching, not an apology. A reader who
cannot tell what the system covers from what it does not has not learned the system.

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
| [`security`](security.md) | Threats mapped to the controls that are wired |
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
| [`retrieval`](retrieval.md) | Vector, graph and keyword recall, fused and reranked |

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

## If you are demoing rather than learning

Four companion guides walk every screen of every portal, in order, with what each panel
shows and what to say about it:

| | |
|---|---|
| [`persona-platform-admin`](persona-platform-admin.md) | The operator who sees every tenant |
| [`persona-tenant-admin`](persona-tenant-admin.md) | One tenant's administrator |
| [`persona-ai-team`](persona-ai-team.md) | The builders — console, harness, evals, guardrails |
| [`persona-client`](persona-client.md) | The end user, and the narrowest portal |

---

## Related, and deliberately not duplicated here

- [`../module/MODULE_REFERENCE.md`](../module/MODULE_REFERENCE.md) — the Module Contract
  and the map. This course explains; that document specifies.
- [`../architecture/system-architecture.md`](../architecture/system-architecture.md) —
  every layer and store as one picture. Start here if you want the whole platform before
  any single module.
- [`../compliance/README.md`](../compliance/README.md) — the control-by-control position
  against twelve published frameworks.
- [`../install/README.md`](../install/README.md) — getting it running.

Notifications and compliance are not `aegis.*` modules and so have no file here; both are
documented in `../architecture/system-architecture.md`.
