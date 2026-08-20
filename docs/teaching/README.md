# Learning Aegis, module by module

> **STATUS: the module files are not written yet.** The previous course — three
> files per module plus HTML twins, 103 files — was removed on 2026-08-21 because
> it had drifted (it still described Chroma, deleted days earlier) and because the
> shape was wrong: a reader had to open three files to learn one module.
>
> This index is the contract for the rewrite. **Every link below is currently
> dead.** Until the files exist, the accurate sources are
> [`../module/MODULE_REFERENCE.md`](../module/MODULE_REFERENCE.md) for the contract
> and map, [`../module/PIPELINES.md`](../module/PIPELINES.md) for the stages
> (generated from `aegis.pipelines.spec`, so it cannot drift), and
> [`../security/`](../security/) for the threat model.
>
> The old course is recoverable from git history if any of it is wanted back.

One file per module. Each is a **parent file** — everything about that module in
one place, written for someone who has never seen it before and read end to end.

There is no second or third file per module. If a thing is worth knowing about
`aegis.guardrails`, it is in `guardrails.md`.

## The rule these files obey

**Every claim describes what is actually in this repository.** Not what a platform
like this usually has, not what the roadmap says, not what the library *could* do.
If Aegis uses NeMo Guardrails, the file says which rails are defined and where the
`.co` files are. If something is absent, the file says it is absent.

Each file also carries a **What is not here** section, and that section is the point
rather than an apology: a learner who cannot tell the implemented parts from the
intended ones has not learned the system.

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
- [`../dev_new_docs_v2/install/`](../dev_new_docs_v2/install/) — getting it running.
