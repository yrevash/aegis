# IMPROVEMENTS.md — TAIF S2 Prioritized Improvement Map

> **Scope.** A principal-reviewer improvement map answering one question: *"how can this
> be much more improved?"* — highest-leverage moves across **every** dimension so the
> platform is more impressive, more correct, more scalable, and wins with enterprise
> **leadership**.
>
> **Method.** Grounded in a full read of the shipped code (`backend/src/app/**`,
> `frontend/src/**`) plus `ARCHITECTURE_REVIEW.md`, `AUDIT.md`, `hackathon.md`,
> `backend.md`, `security.md`, `RUNBOOK.md`. Every "current state" claim cites a file;
> every SOTA claim cites a source URL. Constraints honored throughout: **16 GB Windows,
> no Docker, no GPU, local-or-API only, blind on-the-day, domain logic only in
> `adapter/`.**
>
> **Reading key per item:** `Impact H/M/L` (on rubric + leadership) · `Effort S/M/L` ·
> `Domain-agnostic yes/no` (stays in core, never touches the adapter contract).

---

## 0. What is already excellent (do NOT rebuild)

This codebase is unusually mature. Everything in `ARCHITECTURE_REVIEW.md`'s five concerns
is **shipped** (Waves 1–3, 287 backend tests, ruff-clean): durable Postgres-checkpointed
approvals + async inbox + SLA sweeper (`data/approvals.py`, `agent/orchestrator.py`),
multi-tenant RBAC + RLS + hierarchical budget enforcement at the LiteLLM chokepoint
(`core/llm.py`, `data/governance.py`), RRF hybrid retrieval + BM25 + provenance
(`retrieval/fusion.py`, `pipeline.py`), a genuine XGBoost+MAPIE+SHAP spine
(`ml/model.py`), and a polished live-first money-shot console (`console/MoneyShotConsole.tsx`).

**Therefore this map is the *next frontier* — the gap between "impressive, done demo" and
"SOTA agentic platform that survives an AI-grader and an enterprise probe."** Several items
below are also **honesty gaps**: places where a doc/claim outruns the enforced code (Colang
not wired, "RAGAS" is overlap proxies, "fully traced" is LLM-spans-only, ML predicts on
`records[0]`). Under a rubric where *an AI reader grades the code and a jury probes across
two days*, closing claim-vs-reality gaps is worth as much as new capability.

---

## A. Agent quality (planning · tool use · reflection · self-repair)

*The stated separator is "actions, not answers — a real agent, not a smart search box."
Today the graph is **single-pass**: `guard→retrieve→ml_predict→plan→gate→act→generate`
runs each node once, with no cycle back to `plan` (`agent/graph.py:458-486`). This is the
thinnest part of an otherwise deep platform, and it's rubric axis #1 (solution quality &
innovation).*

**A1 — Closed-loop self-repair: replan on tool failure & low groundedness.** `[Impact H · Effort M · Domain-agnostic yes]`
Today `act` swallows a tool exception into an `ok=False` summary and proceeds straight to
`generate` (`agent/graph.py:355-378`); the model never sees the failure and never recovers.
Add a bounded **reflect→replan** cycle: after `act` (or after a self-check on the drafted
answer), a critic step decides *retry / replan / proceed*, looping back to `plan` up to
`max_iters` (2–3). This is the Reflexion pattern — verbal self-reflection on a failure
signal fed into the next attempt — and it is *the* thing that makes a jury say "this is an
agent that recovers," not a pipeline. Wire as a new `reflect` node + a conditional edge
`act → {reflect → plan | generate}`; bound iterations in `AgentConfig`. Keep it a LangGraph
cycle (native `add_conditional_edges` back to `plan`). *SOTA: Reflexion —
https://arxiv.org/abs/2303.11366*

**A2 — Verify-before-answer (self-consistency / groundedness self-check).** `[Impact H · Effort M · Domain-agnostic yes]`
Before `guard_output`, add a cheap self-critique that checks the drafted answer is grounded
in the retrieved context and actually addresses the query; on failure, trigger A1's replan
or an honest hedge. This closes the "explainable & trustworthy" loop end-to-end (right now
nothing checks the final answer's faithfulness before it streams). Route the check to a
reasoning model only when the first draft is low-confidence (ties to E-dimension cost). Emit
a `self_check` SSE event so the *verification* is visible on stage — a strong leadership
"wow" (the agent audits itself). *SOTA: self-consistency —
https://arxiv.org/abs/2203.11171 ; semantic-entropy hallucination detection (Nature 2024) —
https://www.nature.com/articles/s41586-024-07421-0*

**A3 — Query decomposition / plan object for multi-part requests.** `[Impact M · Effort M · Domain-agnostic yes]`
The planner emits one shot of tool calls from the raw query; a compound request ("compare X
and Y, then escalate the riskier") cannot be decomposed. Add an explicit plan-first step
that emits a typed **sub-goal list** the executor walks (true plan-and-execute), making the
plan *visible and auditable* — which is exactly the money-shot's selling point and improves
hard-query correctness. Plan lives in `AgentState`; render each sub-goal as a `reasoning`
beat. *SOTA: Plan-and-Solve — https://arxiv.org/abs/2305.04091*

**A4 — Fix ML subject resolution (stop predicting on `records[0]`).** `[Impact M · Effort S · Domain-agnostic yes]`
`_default_features_for → _resolve_subject` falls back to the **first record in the store**
when the query names no id (`agent/deps.py:429-457`), and passes `agent=None, customer=None`
so those features are never populated. On stage the SHAP/conformal panel is then explaining
a *prediction about an arbitrary record*, disconnected from the question — an AI reader or a
sharp juror will catch it. Resolve the subject semantically (embed-match the query against
record summaries) or, if unresolved, **skip ML** rather than predict on a random row (an
honest no-op is better than a confident-but-irrelevant SHAP panel). *No external SOTA —
correctness fix.*

**A5 — Dynamic model routing by query difficulty (agent-side).** `[Impact M · Effort M · Domain-agnostic yes]`
Planner and generate always use `ModelRole.GENERATION` (`agent/graph.py:229,407`); there is
no per-query escalation to a reasoning model for hard steps or de-escalation to a cheap
model for trivial ones. A lightweight difficulty classifier (or a cascade: try cheap →
escalate on low self-confidence) both raises quality on hard queries and cuts cost on easy
ones — a double rubric win (innovation + measured efficiency). *See F2 (same mechanism,
cost lens). SOTA: RouteLLM — https://lmsys.org/blog/2024-07-01-routellm/*

---

## B. Retrieval quality (rewriting · HyDE · reranking · graph reasoning)

*RRF + BM25 + LLM-rerank + spotlight is solid, but recall uses the **raw query verbatim** —
no query transformation anywhere (grep-confirmed) — and the reranker is one cheap-LLM JSON
call. These are the standard 2024–2026 recall/precision levers, all API-only and
domain-agnostic.*

**B1 — Corrective RAG (CRAG): grade retrieval, self-correct on weak context.** `[Impact H · Effort M · Domain-agnostic yes]`
Add a lightweight **retrieval evaluator** after fusion/rerank that scores whether the
context actually answers the query and emits Correct / Ambiguous / Incorrect. On
Ambiguous/Incorrect, trigger a corrective action — query rewrite + re-retrieve (B2), widen
recall, or abstain — instead of generating from bad context. This is the retrieval half of
the self-correcting loop (pairs with A1/A2), directly lifts faithfulness, and gives the
agent a *visible* "my sources are weak, retrying" beat. Plugs into `retrieval/pipeline.py`
after `_recall_and_fuse`, surfaced as a `retrieval` sub-event. *SOTA: CRAG —
https://arxiv.org/abs/2401.15884*

**B2 — Query transformation: rewrite + multi-query + HyDE.** `[Impact H · Effort M · Domain-agnostic yes]`
One cheap-model call to (a) rewrite the query into a cleaner search form, (b) fan out to 2–3
paraphrases whose recalled lists RRF-fuse (you already have RRF — this is nearly free once
the lists exist), and (c) optionally HyDE — generate a hypothetical answer and embed *that*
for dense recall. Meaningfully raises recall on under-specified/blind-domain queries (the
on-the-day reality). Plugs in before `_recall_lists` in `pipeline.py`; the multi-query lists
drop straight into `reciprocal_rank_fusion`. *SOTA: HyDE —
https://arxiv.org/abs/2212.10496 ; multi-query/RAG-Fusion pattern.*

**B3 — Reranker upgrade: reasoning-model rerank on hard queries + listwise + MMR.** `[Impact M · Effort S · Domain-agnostic yes]`
`rerank(...)` always defaults to `ModelRole.CHEAP` and the pipeline never passes anything
else (`retrieval/reranker.py:75`, `pipeline.py:110-116`), so the `role` param is dead. Route
hard/ambiguous queries (from B1's grade) to `ModelRole.REASONING` for listwise reranking,
and add **MMR** diversity so the top-K isn't near-duplicate passages (raises effective
context coverage). Cheap, isolated, measurable via the eval gate. *SOTA: listwise LLM
rerankers (RankGPT) — https://arxiv.org/abs/2304.09542*

**B4 — Real multi-hop graph reasoning surfaced into the prompt.** `[Impact M · Effort M · Domain-agnostic partial]`
Graph "reasoning" today = LightRAG `mode="mix"` (it owns the Cypher) in full mode, and a
Jaccard co-occurrence walk in lite mode (`retrieval/memory.py:206`); neither injects an
explicit **relationship path** into the generation prompt. For relationship/"why" queries,
extract the k-hop path between query entities and pass it as a stated chain ("A →depends_on→
B →breaches→ SLA"). This is where GraphRAG visibly beats vector-only and it makes the
animated knowledge graph *mean something* in the answer. Core pipeline; the corpus stays in
the adapter. *SOTA: GraphRAG — https://arxiv.org/abs/2404.16130*

**B5 — Fix `LightRAGBackend.ingest_chunks` hardcoded `(0,0)`.** `[Impact M · Effort S · Domain-agnostic yes]`
`ingest_chunks` returns a hardcoded `return (0, 0)` for entities/relations
(`retrieval/lightrag_backend.py:160`), so every `IngestReport` says "0 entities, 0
relations" in full mode — a fabricated metric an AI reader will flag and a demo tile that
reads empty. Parse the real counts from LightRAG, or drop the metric. Also: the whole
Neo4j/pgvector path is lazy-imported with "verified during live integration" comments and is
**not exercised in CI** — schedule a real full-mode smoke test before the day (see I2). *No
external SOTA — correctness fix.*

**B6 — Semantic cache: replace O(N) linear scan with a vector index.** `[Impact L · Effort M · Domain-agnostic yes]`
The semantic tier does `smembers` + per-key cosine (`retrieval/cache.py:131-144`) — O(N) per
lookup, fine for a demo, won't scale and undercuts the "scalable" claim if probed. Use
RediSearch vector index (full) / a small ANN structure (lite). Lower priority — it's a
scale story, not a demo blocker. *SOTA: Redis vector similarity —
https://redis.io/docs/latest/develop/interact/search-and-query/advanced-concepts/vectors/*

---

## C. Evals & quality gates (the "measurable to trust" axis)

*The rubric explicitly scores a measurable eval + token/cost dashboard, and wiring the eval
as a **quality gate** is a stated goal. Today the gate is real but **shallow**: it runs the
hybrid retriever over `SEED_CASES` with **hash-fake embeddings and a pass-through reranker**
(`eval/harness.py:96-134`), so it measures RRF *ordering*, not real retrieval or answer
quality; "RAGAS" is substring/overlap proxies (`eval/metrics.py`); the LLM-judge is off by
default and not in the gate; the `EvalResult` table is never written. This is a claim-vs-
reality gap on the axis the platform most wants to own.*

**C1 — End-to-end answer eval in the gate (real RAGAS quartet + judge).** `[Impact H · Effort M · Domain-agnostic yes]`
Extend the gate beyond retrieval to grade **generated answers**: faithfulness + answer-
relevancy (the generation half of RAGAS) alongside the existing context precision/recall.
Use the real `ragas` library where it runs offline, or keep deterministic proxies but *label
them honestly* and add an opt-in real-model pass. Persist every run to the `EvalResult` table
(`data/models.py:288`) so there's a trend, not a point. This makes the money-shot's "live
quality score" (today a grounding proxy, `routes.py:364-401`) an actual measured number.
*SOTA: RAGAS — https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/*

**C2 — Agent-trajectory eval: tool-selection & gate-decision accuracy.** `[Impact H · Effort M · Domain-agnostic yes]`
Nothing evaluates whether the *agent* did the right thing — only whether retrieval was good.
Add labelled trajectory cases ("this query should call `escalate`, should DEFER to the
gate") and score tool-selection accuracy, gate precision/recall, and abstain-correctness.
This is what proves the bounded-autonomy story is *right*, not just present — the single most
credible artifact for an enterprise "can we trust it to act" question. *SOTA: agent/tool
eval — https://arxiv.org/abs/2604.16706 (AgentProp-Bench, judge reliability for tool agents)*

**C3 — Conformal-coverage regression check in CI.** `[Impact M · Effort S · Domain-agnostic yes]`
The ML spine's entire claim is a *guaranteed* coverage rate; a test verifies empirical
coverage once, but the CI gate doesn't assert it stays ≥ target across changes. Add a gate
threshold on empirical conformal coverage so a regression in calibration trips the build.
Cheap, and it hardens the differentiator. *SOTA: MAPIE conformal coverage —
https://mapie.readthedocs.io/*

**C4 — Multi-judge / bias-controlled LLM-as-judge.** `[Impact M · Effort S · Domain-agnostic yes]`
`eval/judge.py` is a single reasoning-model judge — subject to position, verbosity, and
self-preference bias. When enabled for the gate, use a small panel (2–3 models) and/or swap
order to control position bias, and report agreement. Turns "we LLM-judge our answers" into
"we LLM-judge with known-bias controls" — the difference an AI grader notices. *SOTA:
position bias in LLM judges — https://arxiv.org/abs/2406.07791*

**C5 — Garak red-team run as a saved artifact + a gate.** `[Impact M · Effort M · Domain-agnostic yes]`
`security.md` §3 and its checklist call for **Garak** against the API endpoint ("blocked X%
of injection probes"); there is **no Garak integration anywhere** in the repo. Run it against
the running API (no local model needed), save the report, and surface the block-rate on the
security panel. This is a concrete, scored security *and* documentation artifact and a strong
"we tested adversarially" leadership line. *SOTA: Garak — https://github.com/NVIDIA/garak*

---

## D. Observability (the "fully traced" claim)

*The unifying pitch is "every autonomous action is uncertainty-bounded, explainable,
guarded, **and fully traced**." But `genai_span`/`set_usage` are emitted **only** in
`core/llm.py`; the enum is just `CHAT / EMBEDDINGS / TEXT_COMPLETION` (`semconv.py:36-40`);
the only non-LLM span is one generic `agent.run`. So Phoenix shows a flat list of LLM calls,
not the agent tree — the weakest-supported word in the whole pitch is "traced."*

**D1 — Full OTel span tree: RETRIEVER / RERANKER / GUARDRAIL / TOOL / AGENT spans.** `[Impact H · Effort M · Domain-agnostic yes]`
Emit dedicated spans for each stage — retrieval (with candidate counts + provenance),
rerank, each guardrail rail, each tool call, and a parent agent/graph span — with
OpenInference span-kind attributes so Phoenix renders the **trust-stack as a waterfall
tree**. This is the highest-leverage *low-effort* win: it makes "fully traced" literally
true, gives a jaw-dropping enterprise-observability "wow" (leadership buys observability),
and an AI reader sees the OTel GenAI conventions done properly. Add span helpers in
`observability/`, wrap the graph nodes (the `_timed` decorator is already the perfect seam).
*SOTA: OpenInference — https://github.com/Arize-ai/openinference ; OTel GenAI semconv —
https://opentelemetry.io/docs/specs/semconv/gen-ai/*

**D2 — Durable, multi-worker metrics (move MetricsStore/GraphStore off process-globals).** `[Impact M · Effort M · Domain-agnostic yes]`
`MetricsStore`/`GraphStore` are in-memory process-globals (`routes.py:444-445`): numbers
reset on restart and are *wrong* under >1 uvicorn worker — contradicting the horizontal-scale
story. Back them with Postgres (you already write the usage ledger) so the dashboard is
durable and correct at scale. *No external SOTA — scalability fix.*

**D3 — Trace-linked audit: one click from an audit row to its Phoenix trace.** `[Impact M · Effort S · Domain-agnostic yes]`
The audit log already stores `trace_id`; surface it as a deep link into Phoenix so "show me
exactly what this autonomous action did" is one click. Tiny effort, big "auditable &
reviewable" payoff for a procurement-minded jury. *No external SOTA — integration.*

---

## E. Security & trust

*Security is real, not theatre (fail-closed rails, allowlist-before-side-effect, spotlighting,
validate-before-write). The gaps are (i) the declarative artifact isn't the enforced path,
(ii) detection is coarse, (iii) a couple of live-config footguns.*

**E1 — Wire NeMo Colang as the enforced path (or delete it and stop claiming it).** `[Impact H · Effort M · Domain-agnostic yes]`
`guardrails/config/*.co` + `config.yml` exist and are valid, but `nemo.build_rails()` is
**never called** — the runtime is exclusively the programmatic `rails.py`. The Colang policy
is a "jury-readable security artifact" that can silently **drift** from what's enforced —
exactly the kind of doc-vs-reality gap the rubric's AI reader punishes. Either drive the
runtime through NeMo (so the readable policy *is* the enforcement) or remove the artifact and
present `rails.py` as the policy. Honesty > a nicer-looking file. *SOTA: NeMo Guardrails —
https://github.com/NVIDIA/NeMo-Guardrails*

**E2 — Stronger injection defense: Azure Prompt Shields / dedicated classifier + heuristics.** `[Impact M · Effort M · Domain-agnostic yes]`
Injection detection is an **LLM-as-guardrail** (a cheap-model JSON verdict,
`guardrails/classifier.py`) — itself promptable, and it adds a full LLM round-trip to every
request. Add a fast deterministic heuristic pre-filter (cheap, catches the obvious) and, if
available on the Azure fleet, **Azure Prompt Shields** as a purpose-built detector; reserve
the LLM classifier for the ambiguous middle. Better detection *and* lower latency/cost.
*SOTA: Azure AI Content Safety Prompt Shields —
https://learn.microsoft.com/azure/ai-services/content-safety/concepts/jailbreak-detection*

**E3 — PII coverage beyond regex (names/addresses/DOB).** `[Impact M · Effort M · Domain-agnostic yes]`
PII is 7 anchored regexes + Luhn (`guardrails/pii.py`) — no NER, so names, physical
addresses, DOB, and account numbers pass through. A local model is forbidden (RAM), but a
cheap API NER pass on the outbound path (gated to when redaction matters) closes the biggest
disclosure hole. Frame honestly on the security panel ("structured PII: deterministic;
entity PII: model-assisted"). *SOTA: OWASP LLM02 Sensitive Info Disclosure —
https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/*

**E4 — Close the two governance footguns.** `[Impact M · Effort S · Domain-agnostic yes]`
(a) `_DEMO_USERS = {"admin":"admin", ...}` is a plaintext credential table in source
(`routes.py:121-124`) — guard it behind an explicit `ENABLE_DEMO_LOGIN` flag that is off
unless set, so it can never ship live. (b) Unscoped/platform principals get an **empty**
governance context (`routes.py:281-282`), so the demo admin **bypasses all budget
enforcement** — attribute platform admins to a platform tenant with its own cap so the "every
call is governed" claim holds even for admins. *No external SOTA — hardening.*

**E5 — Structured output-schema validation (finish LLM05).** `[Impact L · Effort S · Domain-agnostic yes]`
Output "schema" validation is length + control-char + a 3-entry denylist
(`guardrails/schema.py`); the brief calls for real schema validation. Where the agent emits
structured output (tool args, JSON answers), validate against a Pydantic/JSON schema and
reject-or-repair. *SOTA: OWASP LLM05 Improper Output Handling —
https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/*

---

## F. Cost & efficiency (visible, measured — a scored axis)

*The jury can see tokens/cost; efficiency must be measured, not claimed. `small_model_share`
and cost are now genuinely measured (good). The remaining levers are per-request call count
and per-query model choice.*

**F1 — Cut the guardrail tax: one LLM call per request, not two-plus.** `[Impact M · Effort S · Domain-agnostic yes]`
Every guarded request pays an injection-classifier LLM call *plus* the main call (and rerank
is another). Gate the classifier behind the deterministic heuristic pre-filter (E2) so the
extra model call fires only on suspicious input — a direct, measurable cost/latency drop the
dashboard will show. *No external SOTA — efficiency.*

**F2 — Cascade / difficulty-routed generation (RouteLLM-style).** `[Impact M · Effort M · Domain-agnostic yes]`
Route each generation to the cheapest model likely to succeed, escalating only on low self-
confidence — published results show 40–85% fewer frontier-model calls at ~95% quality. This
is the strongest *measured-efficiency* story you can put on the token dashboard, and it reads
as sophistication to a technical jury. (Same mechanism as A5, cost framing.) *SOTA: RouteLLM
— https://lmsys.org/blog/2024-07-01-routellm/ , https://github.com/lm-sys/RouteLLM*

**F3 — Prompt/context caching on the LiteLLM chokepoint.** `[Impact M · Effort S · Domain-agnostic yes]`
System prompts + spotlighted context are large and repeated; enable provider prompt-caching
(or a local prefix cache) at `core/llm.py` and surface cache-hit tokens on the dashboard —
another honest efficiency number beside the semantic-cache hit-rate. *SOTA: prompt caching
(provider feature) — https://platform.openai.com/docs/guides/prompt-caching*

---

## G. Performance & latency

**G1 — Parallelize independent stages + speculative retrieval.** `[Impact M · Effort M · Domain-agnostic yes]`
The graph is strictly serial. `ml_predict` (local CPU, no gateway) can run concurrently with
`retrieve`; the injection classifier can overlap with embedding. LangGraph supports parallel
branches — shaving a visible second or two makes the live stream feel snappier for the jury.
*No external SOTA — orchestration.*

**G2 — Ship a pre-trained ML artifact (kill cold-start).** `[Impact M · Effort S · Domain-agnostic yes]`
`get_model()` **trains a fresh synthetic model on the first `/ml/explain` call** (no
`ml/artifacts/*.joblib` exists — `ml/model.py:55`, `ml/__init__.py:100-116`): first request
eats SHAP/XGBoost JIT + fit latency and is non-reproducible. Train once during
`bootstrap`/`start`, pickle the artifact, load at boot. Faster first demo query, reproducible
SHAP. *No external SOTA — DX/latency.*

**G3 — Stream real generation tokens, not post-hoc word-chunking.** `[Impact L · Effort M · Domain-agnostic yes]`
`stream_answer` splits the *finished* answer into word chunks (`graph.py:429-439`) — the
"streaming" is cosmetic. True token streaming from the gateway (with the documented buffer-
then-output-guard approach) is more honest and more impressive live. Lower priority; the
current effect is convincing enough. *No external SOTA — fidelity.*

---

## H. Demo narrative & "wow" moments (win the room)

**H1 — Add the visible blocked-injection beat (the missing trust-stack payoff).** `[Impact H · Effort S · Domain-agnostic yes]`
The single clearest demo gap: **no scenario shows an injection being caught**. All three mock
scripts always emit guardrail `verdict:'pass'` (`mock/mockTransport.ts:130,651,732`); the
reducer/motion already support a `block` verdict. `security.md` §6.1 lists a "live blocked-
injection demo" as a top scored moment. Add a ~15-line mock branch + a seed "attack" query
(and confirm it fires live) so the audience *watches an attack get stopped* — the highest-ROI
demo change on the board. *Ties to E2. No external SOTA — demo.*

**H2 — Put the Phoenix trust-trace on the projector.** `[Impact H · Effort S (after D1)]`
Once D1 emits the full span tree, show the live Phoenix waterfall (guardrail → retriever →
rerank → tool → gate) beside the money-shot. "Here is every step of an autonomous action,
traced" is the enterprise-observability mic-drop. *Depends on D1.*

**H3 — A single "trust-stack" moment that fires all five guarantees at once.** `[Impact M · Effort S · Domain-agnostic yes]`
Script one query that visibly triggers conformal-defer → human gate → SHAP explanation →
guardrail → traced-and-audited, and narrate the winning sentence over it. The pieces exist;
choreograph them into one 30-second beat with a scripted seed query in the adapter. *No
external SOTA — choreography.*

**H4 — Before/after ROI framing tied to real measured numbers.** `[Impact M · Effort S · Domain-agnostic yes]`
The ROI panel exists; feed it the *measured* small-model share, cache-hit rate, and cascade
savings (F2) so the business case is "here's the cost curve we actually produced," not a
static slide. Leadership scores business value. *No external SOTA — narrative.*

---

## I. Developer experience / day-of readiness

*The runbook, lite mode, and fallback ladder are genuinely excellent. The risks are the
unexercised full-mode path and a couple of reproducibility gaps.*

**I1 — CI smoke test for full mode (Neo4j/pgvector/Redis) before the day.** `[Impact M · Effort M · Domain-agnostic yes]`
The LightRAG/Neo4j/pgvector path is entirely lazy-imported with "verified during live
integration" comments and **never run in CI** (only the in-memory lite path is tested). If
you ever demo full mode, the first time it runs is on stage. Add a gated integration test (or
a `preflight` deep-check) that actually round-trips ingest→recall against local stores. *No
external SOTA — de-risking.*

**I2 — ADR + threat-model consistency pass.** `[Impact M · Effort S · Domain-agnostic yes]`
Eight ADRs now exist (great). Do a final scrub so the enforced code matches every ADR/claim:
align `security.md` OWASP IDs to the 2025 list (AUDIT L5), reconcile the "RAGAS"/Colang/
"fully traced" language with what C/D/E land, and update `governance.py`'s stale docstring
("nothing reads it yet" — it does). An AI reader cross-reads docs and code; consistency is
scored. *No external SOTA — documentation.*

**I3 — Seed "demo script" queries in the adapter (blind-day accelerator).** `[Impact M · Effort S · Domain-agnostic yes]`
Ship a small set of adapter-owned canonical queries (happy path, high-risk→gate,
injection→block, abstain, multi-hop) so that within the on-the-day 2 hours the team points
the weapon and has a *rehearsed narrative* immediately, not an improvised one. Stays in the
adapter (domain), so it's swappable. *No external SOTA — readiness.*

---

## J. SOTA frontier (genuinely differentiating, optional)

**J1 — Governed agent memory with validate-before-write.** `[Impact M · Effort M · Domain-agnostic yes]`
`security.md` names memory poisoning as a risk "if memory added." Adding a *governed*
episodic memory (Reflexion's reflections from A1, persisted per tenant with validate-before-
write) both improves the agent over a session and lets you demo the *memory-poisoning
defense* — a rare, advanced security beat. *SOTA: Reflexion memory —
https://arxiv.org/abs/2303.11366 ; OWASP Agentic memory poisoning.*

**J2 — Conformal risk control at the tool/step level.** `[Impact M · Effort M · Domain-agnostic yes]`
Extend conformal guarantees from the ML prediction to the *agent's actions* — a calibrated
accept-or-intervene rule per tool step (already cited in `ARCHITECTURE_REVIEW`). Deepens the
"statistically-bounded autonomy" story beyond a single prediction. *SOTA: conformal risk
control for agents — https://arxiv.org/abs/2606.18467*

**J3 — Cite-as-you-answer (inline source attribution).** `[Impact M · Effort S · Domain-agnostic yes]`
Have `generate` attach the source id behind each claim (you have `Source.id` on every reranked
passage). Inline citations are the current bar for trustworthy RAG and a clean, credible UI
upgrade that pairs with the faithfulness eval (C1). *SOTA: attributed QA —
https://arxiv.org/abs/2212.08037*

---

## TOP 10 — do these next (best-first)

| # | Item | One-line rationale | Impact/Effort |
|---|------|--------------------|---------------|
| 1 | **A1 — Closed-loop self-repair (Reflexion)** | Turns a single-pass pipeline into a *real agent that recovers* — rubric axis #1, the stated separator. | H / M |
| 2 | **D1 — Full OTel span tree + Phoenix trust-trace** | Makes "fully traced" literally true; low-effort, huge enterprise-observability wow, AI-reader-visible. | H / M |
| 3 | **H1 — Visible blocked-injection beat** | The one advertised trust-stack element with no on-screen payoff today; ~15 lines for the biggest demo ROI. | H / S |
| 4 | **C1+C2 — Real end-to-end + trajectory evals in the gate** | "Measurable to trust" is a scored axis; today's gate is retrieval-only on fake embeddings. | H / M |
| 5 | **B1+B2 — CRAG self-correction + query rewrite/HyDE/multi-query** | Biggest retrieval-quality lift, all API-only, and it *shows* the agent fixing weak context live. | H / M |
| 6 | **E1 — Wire (or remove) NeMo Colang** | Closes a doc-vs-code honesty gap an AI grader punishes; make the readable policy the enforced one. | H / M |
| 7 | **A4 + G2 — Fix ML subject resolution + ship the model artifact** | Stops SHAP explaining a *random record*; kills cold-start; both cheap, both protect the differentiator. | M / S |
| 8 | **F1+F2 — Cut guardrail tax + cascade routing** | Turns the visible token dashboard into a measured 40–85% efficiency story; leadership scores cost. | M / M |
| 9 | **C5 — Garak red-team artifact + block-rate** | A required-but-absent security artifact; "we tested adversarially, here's the number." | M / M |
| 10 | **E4 + I1 — Governance footguns + full-mode CI smoke** | Removes the two ways the live demo silently contradicts its own claims (ungoverned admin, untested full mode). | M / S–M |

### Suggested sequencing (parallel-friendly, collision-light)

- **Wave 1 (visible wins, low risk):** D1 → H1 → H2, plus A4 + G2 + E4. These are mostly
  additive, land the biggest demo/honesty payoff fast, and unblock the projector story.
- **Wave 2 (agent + retrieval depth, parallel):** A1 (+A2) and B1+B2 run in parallel (agent
  loop vs retrieval pipeline — distinct modules). B3 rides along with B1.
- **Wave 3 (measurement, after 1–2 land so there's something real to measure):** C1+C2+C3,
  then C5 (Garak) and F1+F2. E1 slots here (evals prove the rewired guardrail path).
- **Wave 4 (hardening/polish):** I1, I2, D2/D3, then the J-items if time remains.

*Adapter isolation is preserved throughout: every Top-10 item is core plumbing; only I3's
seed queries and B4's corpus touch adapter-owned domain content.*

---

## The single highest-leverage improvement

**A1 — the closed-loop self-repairing agent (Reflexion-style replan on failure + CRAG-style
retrieval self-correction + verify-before-answer).**

Everything else in this platform — durable approvals, tenancy, conformal bands, RRF, the
trust stack — is already built to a high bar, but they all wrap a **single-pass** core that
plans once, acts once, and never reacts to its own failures. Making the agent *close the
loop* — notice a failed tool call or weak context, reflect, and retry with a better plan — is
the one change that moves rubric axis #1 (solution quality & innovation) the most, converts
"impressive pipeline" into "a real agent that recovers" (the exact separator from the "smart
search box" teams the brief warns about), and creates the most compelling live moment for
leadership: *watching the system catch its own mistake and fix it.* It is domain-agnostic
core plumbing, it composes with the retrieval self-correction (B1) and the self-check (A2)
into one coherent "self-correcting agent" narrative, and it is buildable within the
constraints as native LangGraph cycles. If only one thing ships next, ship the loop.

*(Highest-leverage **low-effort** win, if effort is the constraint: **D1** — the full OTel
span tree — which makes "fully traced" true and puts the trust stack on the projector for a
fraction of A1's cost.)*
