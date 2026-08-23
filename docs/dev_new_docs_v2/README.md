# The surviving v2 planning record

The Aegis v2 plan was 28 files: a master plan, a roadmap, eleven phase documents, six
deep-research plans, six technology surveys, a frontend-redesign set and the owner's
original requirements dump. Phases 1–10 shipped. On **2026-08-23** everything that had
become history was deleted, because a finished plan is not context — it is a second,
older description of a system that has moved on, and reading it costs an agent more
than it gives.

All of it is recoverable from git history (the last commit that still contains the full
set is `2d8b84d`).

**Seven files survived, each for a stated reason.**

| File | Why it is still here |
|---|---|
| [`backlog-post-hackathon.md`](backlog-post-hackathon.md) | Forward-looking, and **cited as compliance evidence** — `backend/src/app/platform/compliance.py` and `docs/compliance/README.md` name it as where owed work (audit retention, Alembic, Postgres-everywhere tests) is recorded |
| [`phase-03-platform-spine.md`](phase-03-platform-spine.md) | Cited from source: `aegis/jobs/stages.py` and `backend/src/app/config.py` point here for the Temporal sandbox boundary and the job substrate's measurements |
| [`phase-04-ingestion.md`](phase-04-ingestion.md) | Cited from source and from an ADR: `aegis/evals/ir_metrics.py`, `aegis/ingestion/blocks.py`, `aegis/retrieval/local_reranker.py`, `backend/src/app/retrieval/NOTES.md` and `docs/adr/0006` all point at §D6 and D1 for measured numbers that exist nowhere else |
| [`phase-09-scale-hardening.md`](phase-09-scale-hardening.md) | `docs/adr/0009` names §9.1 as the decision that superseded it — the Qdrant vector tier |
| [`phase-11-langflow.md`](phase-11-langflow.md) | **Future scope, deliberately unimplemented.** Parked by the owner: the no-code flow builder is a later conversation. Nothing may take a dependency on it |
| [`research/langflow-and-observability.md`](research/langflow-and-observability.md) | The source of every claim in phase 11, with each fact marked `[MEASURED]`, `[SOURCE-1.11.3]` or `[DOC]` |
| [`frontend-redesign/`](frontend-redesign/) | The per-screen briefs `DESIGN.md` §10 points at. The redesign has shipped; these are the record of what each portal pass changed and the corrections it made to its own earlier claims |

**The links inside these files are not all live.** They were written against the full
set and several point at plans and surveys that no longer exist. The bodies are intact;
only the cross-references are broken, and they are broken loudly rather than silently.

`00-DESIGN-DIRECTION.md` was deleted from `frontend-redesign/` for a different reason:
it was a **byte-identical copy** of `DESIGN.md`. Two copies of a design authority is how
a design system drifts, and `DESIGN.md` is the one the workflow names.
