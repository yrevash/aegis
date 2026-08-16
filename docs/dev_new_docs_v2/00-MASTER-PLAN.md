# Aegis v2 — the master plan

**Written 2026-08-16. Target: the hackathon starts 2026-08-30 — 14 days.**

This file decides the order. The three documents in [`plans/`](plans/) are the deep research
behind it; the `phase-NN-*.md` files are what you actually work from.

---

## The goal, stated plainly

On 30 August, Aegis gets pointed at a problem statement nobody has seen, with documents
nobody has read. Everything below is ordered by one question:

> **What must be true for Aegis to be a weapon on that morning?**

Not "what makes the best architecture". Not "what demos well". What must be *true*.

Three things must be true, and none of them are today:

1. **A tenant's data cannot reach another tenant.** We claim this on stage. Right now it is
   false in the retrieval path.
2. **Aegis can ingest the documents it is handed.** There is no PDF ingestion in the
   codebase at all — not a weak one, none.
3. **The agents visibly do real work.** Concurrent, logged, with tool calls on screen — and
   only when the query warrants it.

Everything else is second.

---

## What we are NOT doing before 30 August

Named explicitly so nobody quietly starts one of these.

| Deferred | Why |
|---|---|
| Postgres-everywhere test migration | ~24 test entry points, 6.5 days, and it buys correctness we can also get from a targeted live-DB isolation test. Post-hackathon. |
| RLS fail-closed on unset scope | Needs a `SECURITY DEFINER` login path and two Postgres roles. Real work, not a predicate tweak. We close the *coverage* gap now and the *unset* gap later. |
| Alembic / migrations | Valuable, not load-bearing for 30 Aug. |
| MCP server + client | Genuinely excellent, genuinely not required to win a blind problem. |
| Skills subsystem | Same. Plan is written and good; it waits. |
| Per-tenant LLMOps surfaces | Same. |
| Knowledge-graph made load-bearing | The graph works today via RRF. Making it multi-hop is a quality win we cannot land safely in 14 days. |
| Tenant/sub-role hierarchy | The role model we have is sufficient for the demo. |

These are not cancelled. They are sequenced after, in [`backlog-post-hackathon.md`](backlog-post-hackathon.md).

---

## The phases, in order

| # | Phase | Days | Why it is here |
|---|---|---|---|
| 1 | [Tenant isolation](phase-01-tenant-isolation.md) | 3 | A live cross-tenant leak. Everything else is built on top of it, and we make the claim publicly. |
| 2 | [Strip ML and the demo fiction](phase-02-strip-ml-and-fiction.md) | 1 | Cheap, and it simplifies the graph *before* we make the graph concurrent. |
| 3 | [Real ingestion (Docling)](phase-03-ingestion.md) | 3 | The capability that does not exist and that 30 August requires. |
| 4 | [Adaptive multi-agent](phase-04-multi-agent.md) | 3 | The money shot, done honestly — a classifier decides whether to fan out. |
| 5 | [Unified console](phase-05-console.md) | 3 | The demo surface. Without it, phases 3 and 4 are invisible. |
| 6 | [Admin surfaces](phase-06-admin-surfaces.md) | 1 | Mostly frontend — the backend already works. |

**Total: 14 days.** That is the whole window with **zero buffer**, which is not a plan, it is
a hope. So:

### The honest cut order

If we slip — and we will slip somewhere — this is what drops, in this order. Decide it now,
not at 2am on the 29th.

1. **Phase 6** drops to the two forms only (add user, add tenant). Audit filters and report
   downloads go post-hackathon.
2. **Phase 4** drops from a 4-agent team to a 2-agent team (research + synthesise). Still
   genuinely concurrent, still genuinely visible, half the work.
3. **Phase 3** drops table-aware chunking and page/bbox provenance. Plain structure-preserving
   ingestion still beats having none.

**Freeze on 28 August.** The last two days are rehearsal and hardening, not features. A
feature landing on the 29th is a liability, not an asset.

---

## Principles for this build

Carried from what the codebase already gets right, and from what the audits found wrong.

**No silent fallbacks.** A control that cannot run fails closed and says so. Every bug worth
finding in this repo was a violation of this.

**Measured, never claimed.** If a number is on screen, something computed it. The jury
rewards this and so does an interviewer.

**Tenant scope is a parameter, not a convention.** The leak in Phase 1 exists because scope
was something you remembered to apply. After Phase 1 it is something the type system hands
you.

**Real or absent.** The user's instruction throughout the v2 doc: no gimmicks. If the cache
is not really caching, do not draw a cache. If memory is not really recalled, do not show a
memory panel.

**Simple query, simple answer.** The multi-agent fan-out must be *earned* by the query. Five
agents on "what is my budget" is not impressive, it is wasteful — and with $100 of credits
it is also expensive.

**A library's defaults are its author's trade-offs, not ours.** Every third-party tool we
adopt — Docling, the embedder, the reranker, the vector store — gets configured from evidence
about what produces the best result *for this system*, and every deviation from a default is
written down with the reason. Defaults are tuned for the average case, for fast first-run
experience, or for the maintainer's own benchmark; none of those is our goal.

The live example: Docling ships `heading_hierarchy_options.enabled = False`, so PDFs come out
with every heading flattened to level 1. Accepting that default would have silently destroyed
the hierarchical section context that the retrieval literature shows is one of the cheapest
real quality wins — and nothing would have errored. A plan that says "we used Docling" is not
a plan. A plan that says "we set these nine parameters, and here is the evidence for each" is.

---

## Budget reality

$100 of gateway credit. A 4-agent fan-out with research is roughly 8–12 model calls per
query versus 4–5 for the single path. Assume the demo is run 50–100 times between now and
the finals, plus development.

This is why Phase 4 leads with a **classifier**, not a fan-out, and why the Memurai cache
work in Phase 1 matters more than it looks: a cache hit on a rehearsed demo query costs
nothing.

---

## Checkpoints

Each phase ends in something you can show. If a phase does not end in something showable, it
is written wrong.

| After | You can demo |
|---|---|
| 1 | Two tenants, same question, provably different answers — with the isolation test as evidence |
| 2 | A clean graph trace with no dead ML step, and no invented domain in the copy |
| 3 | Drop a PDF in, watch it ingest live, ask a question, get a cited answer from it |
| 4 | A complex question fans out to real concurrent agents with live logs; a simple one does not |
| 5 | The whole thing in one console, with model choice, sources tab, and budget on screen |
| 6 | Create a tenant and a user in the UI, log in as that user |

---

## Source research

The deep analysis behind these phases, including the corrections to assumptions we held:

- [`plans/01-data-governance-tenancy.md`](plans/01-data-governance-tenancy.md)
- [`plans/02-agentic-core-console.md`](plans/02-agentic-core-console.md)
- [`plans/03-knowledge-ingestion-memory.md`](plans/03-knowledge-ingestion-memory.md)

Five corrections worth carrying in your head, because the v2 brief assumed otherwise:

1. **There is no refund domain.** The adapter is service-request/case-management
   (`add_case_note`, `assign_request`, `update_request_status`). "Refund" is example prose.
2. **There are no free/paid tiers.** Tenant + budget is already the model.
3. **There is no SQLite in production** — only in tests and in docs narrative.
4. **`POST /admin/users` already works.** The UI to call it does not exist.
5. **The RLS gap is coverage, not just the predicate** — 3 of ~12 tenant-scoped tables have a
   policy, and RLS has never been verified against a live database.
