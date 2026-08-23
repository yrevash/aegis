# Aegis Evaluation Strategy — three layers, honestly mapped

This is how Aegis knows whether an answer is *good* — before it ships (offline gate),
when the code changes (regression gate), and while it runs in production (live traces).
The industry has three well-known reference tools for these three jobs — **RAGAS**,
**DeepEval**, and **Arize Phoenix / Langfuse**. Aegis implements the *ideas* of each in a
way that stays offline, deterministic, and dependency-light, and is explicit about where
it uses a **proxy/pattern** rather than the named third-party product.

> **The one honesty rule for this doc:** where Aegis ships a *proxy* for a named library,
> it says so and says why. `ragas`, `deepeval`, `langfuse`, and `patronus` are **not**
> dependencies of this repo (`backend/pyproject.toml`). `arize-phoenix`,
> `arize-phoenix-otel`, and `opentelemetry-sdk`/`-api` **are**.

---

## The three layers at a glance

| Layer | Job | Reference tool | What Aegis actually ships | Where in the repo |
|---|---|---|---|---|
| **1. Metrics** | Score retrieval/answer quality on labelled cases | **RAGAS** (conceptual metrics) | RAGAS-*style* **deterministic proxies** (lexical overlap), no `ragas` lib | `backend/src/app/eval/metrics.py`, `harness.py`, `corpus.py`, `judge.py` |
| **2. CI regression gate** | Fail the build when quality regresses | **DeepEval** (pytest-native CI/CD) | The DeepEval **pattern**: pytest-native, per-metric thresholds | `backend/tests/eval/test_eval_gate.py`; `backend/src/app/eval/regression.py` and `aegis/src/aegis/evals/regression.py` — both shipped |
| **3. Production traces** | Grade live runs, keep a glass-box trail | **Arize Phoenix** / **Langfuse** | Per-run + per-step online eval, exported over **OpenTelemetry → Phoenix** | `backend/src/app/ops/trace_eval.py`, `backend/src/app/observability/*` |

The three are complementary, not redundant: **RAGAS = what "good" means** (the metric
definitions), **DeepEval = a gate that blocks a regression in CI**, **Phoenix/Langfuse =
seeing and scoring what actually happened in production**.

---

## Layer 1 — Metrics (RAGAS-style proxies)

**What RAGAS is.** [RAGAS](https://github.com/explodinggradients/ragas) is a widely-used
open-source library for reference-free RAG evaluation. Its headline metrics are
**faithfulness** (are the answer's claims supported by the retrieved context?),
**context precision** (are the retrieved passages relevant / well-ranked?),
**context recall** (was the context the answer needs actually retrieved?), and
**answer relevancy** (does the answer address the question?). RAGAS computes these with an
LLM + embedding model.

**What Aegis ships — and the honest gap.** Aegis computes **RAGAS-*style* deterministic
proxies**, *not* the `ragas` library. This is a deliberate choice, documented in the code:

- `backend/src/app/eval/metrics.py` — three transparent, offline, token/substring-overlap
  proxies:
  - **context-precision proxy @k** — fraction of the top-k retrieved sources whose source
    doc is one of the case's gold docs. Proxy for RAGAS *context precision*.
  - **context-recall proxy** — fraction of the case's gold docs that appear anywhere in the
    retrieved sources. Proxy for RAGAS *context recall*.
  - **groundedness / faithfulness proxy** — fraction of the case's expected claim keywords
    present (normalized substring match) in the assembled `answer_context`. A *lexical*
    proxy for RAGAS *faithfulness*.
- The module docstring states plainly: *"these are RAGAS-style deterministic proxies …
  with no external LLM and no `ragas` library … They are proxies, not the RAGAS-the-library
  metrics they are named after."*

**Why proxies instead of the library.** The Layer-1 gate must run **fully offline,
deterministically, with no network, no keys, and no Postgres/Neo4j/Redis** — so the same
input always yields the same score and CI can't flake. `ragas` needs an LLM + embedding
call per case; that is neither deterministic nor offline. The proxies trade semantic depth
for reproducibility, which is the right trade for a *build gate*.

**RAGAS answer-relevancy is not faked.** The one RAGAS metric that genuinely needs a model
— *answer relevancy* — is **not** approximated with a lexical trick. Instead it is provided
by an **optional LLM-as-judge** (`backend/src/app/eval/judge.py`): a reasoning-model judge
(routed through the `ModelRole.REASONING` role at the LiteLLM gateway) that grades
groundedness **and** relevance. It is off by default (env flag `TAIF_EVAL_LLM_JUDGE`, see
`judge.judge_enabled`) so the default gate never touches the network; `harness.evaluate`
runs it only when a `complete` callable is injected.

**The real pipeline is what's measured.** The proxies score the output of the *actual*
hybrid retriever (`app.retrieval.pipeline.Retriever` over the databaseless
`InMemoryKnowledgeBackend`), with genuine RRF fusion and spotlight assembly — only the
embedding and reranker are deterministic local fakes (`harness.build_eval_retriever`). A
regression in fusion or assembly moves the numbers. The fixed labelled cases and distractor
corpus live in `backend/src/app/eval/corpus.py` (`SEED_CASES`, `SEED_CORPUS`).

---

## Layer 2 — CI regression gate (DeepEval-style)

**What DeepEval is.**
[DeepEval](https://github.com/confident-ai/deepeval) (Confident AI) is an open-source LLM
evaluation framework whose differentiators are exactly what a CI gate wants:
**pytest-native** (`assert_test`, `@pytest.mark`-style flows), **built for CI/CD**, and —
unlike pure-RAG scorers — able to evaluate **agents, multi-turn conversations, and tool-use
/ MCP** trajectories, not just a single RAG answer. It ships metric classes with
per-metric pass thresholds.

**What Aegis ships.** Aegis implements the **DeepEval *pattern***, not the `deepeval`
package (`deepeval` is not a dependency):

- **Live today:** `backend/tests/eval/test_eval_gate.py` — a **pytest-native** gate that
  runs the Layer-1 eval and *fails the build* when any aggregate metric falls below its
  floor. It asserts the seed corpus clears `DEFAULT_THRESHOLDS`, asserts determinism (two
  runs are byte-identical), and includes a **negative test** that an impossibly high bar
  *trips* the gate — proving it can actually catch a regression. The thresholds
  (`min_context_precision=0.66`, `min_context_recall=0.95`, `min_groundedness=0.85`) live in
  `backend/src/app/eval/harness.py` (`DEFAULT_THRESHOLDS`).
- **Being added (design-level):** `backend/src/app/eval/regression.py` — a dedicated
  DeepEval-style regression module that generalises the gate beyond RAG to **agentic /
  tool-use** evaluation (per-metric thresholds over trajectory facets, not just the final
  answer). It formalises what `test_eval_gate.py` does today into a reusable, CI-invokable
  surface. *This module is described here at the design level; treat its internals as
  forthcoming.*

**Honest framing.** Real `deepeval` is a **droppable backend** here: because the gate is
pytest-native and threshold-based, swapping the in-repo proxies for `deepeval`'s metric
classes (or running `deepeval` alongside) is an additive change, not a rewrite. Aegis owns
the *pattern* so the gate stays offline by default; `deepeval` is the natural upgrade path
when a networked, model-graded CI run is acceptable.

---

## Layer 3 — Production trace layer (Phoenix / Langfuse)

**What the reference tools are.**
[Arize Phoenix](https://github.com/Arize-ai/phoenix) and
[Langfuse](https://github.com/langfuse/langfuse) are LLM **observability / tracing**
platforms: they ingest per-run traces (spans for retrieval, tool calls, guardrails, LLM
calls), let you inspect the trajectory as a tree, and attach **online evaluations** to live
runs.

**What Aegis ships — and what's actually wired.**

- **Phoenix is wired for real.** `backend/src/app/observability/otel.py` registers an
  OpenTelemetry tracer provider that exports `gen_ai.*` spans to a **local, in-process**
  Phoenix instance (`phoenix.otel.register`), degrading to a console exporter when Phoenix
  is absent. `arize-phoenix` (>=5.0) and `arize-phoenix-otel` are **real dependencies** in
  `backend/pyproject.toml`. The GenAI semantic-convention attribute keys and the
  OpenInference span kinds Phoenix renders (AGENT / CHAIN / TOOL / RETRIEVER / RERANKER /
  GUARDRAIL / LLM / EMBEDDING) are centralised in
  `backend/src/app/observability/semconv.py`. Note the honest detail there: the
  `openinference-*` instrumentation packages are **not** a dependency — Aegis sets the
  `openinference.span.kind` string attribute directly.
- **Online eval on every run.** `backend/src/app/ops/trace_eval.py` (`evaluate_run`) grades
  a *completed* run **off the hot path**: it scores the **final answer** and each
  **trajectory step** (RETRIEVER → `step:retrieval`, TOOL → `step:tool`, GUARDRAIL →
  `step:guardrail`) and writes one `EvalResult` row per graded facet, keyed by `run_id`.
  When a `complete` callable is injected it uses the reasoning-model answer judge + cheap
  per-step judges; when it is `None` it **degrades to deterministic lexical proxies** and
  never calls a model. It is best-effort and total — a failure grading one facet is caught
  and skipped, never raised into the caller. Those `EvalResult` rows are the substrate the
  **Diagnose** stage of the LLM-Ops loop (`backend/src/app/ops/*`) clusters over.
- **Langfuse: honest status.** Langfuse is **not** a dependency and is **not** integrated.
  It is cited here because it occupies the *same category* as Phoenix (trace + online-eval
  backend). Because the trace layer speaks vendor-neutral **OpenTelemetry** GenAI
  conventions (`semconv.py`), Langfuse is a **droppable alternative/additional exporter**
  to Phoenix, not a second thing that runs today.

---

## Where each reference tool maps in the repo

| Reference tool | Category | Integrated? | Aegis realisation | File(s) |
|---|---|---|---|---|
| **RAGAS** | Conceptual metrics (faithfulness, context precision/recall, answer relevancy) | Proxy only (`ragas` not a dep) | Deterministic lexical proxies + optional LLM-judge for relevance | `app/eval/metrics.py`, `judge.py`, `harness.py`, `corpus.py` |
| **DeepEval** | Pytest-native CI/CD gate; agent/multi-turn/tool-use eval | Pattern only (`deepeval` not a dep) | Pytest gate plus a dedicated regression module | `tests/eval/test_eval_gate.py`; `app/eval/regression.py` |
| **Arize Phoenix** | Production traces + online eval | **Yes** (real dep) | OTel → local Phoenix; per-run/per-step online eval | `app/observability/otel.py`, `semconv.py`; `app/ops/trace_eval.py` |
| **Langfuse** | Production traces + online eval | No (not integrated) | Droppable OTel alternative to Phoenix | (would attach to `app/observability/*`) |
| **Patronus AI** | Responsible-AI detectors (hallucination/bias) | **Not currently integrated** | Optional add-on — see below | — |

### Optional add-on: Patronus AI (not currently integrated)

[Patronus AI](https://www.patronus.ai/) provides managed **responsible-AI detectors**
(e.g. hallucination and bias/safety scorers). It would slot into Aegis as an **additional
online-eval facet** in `app/ops/trace_eval.py` — a networked detector called alongside the
in-repo judges to add hallucination/bias scores to each run's `EvalResult` rows. **It is
not wired today**; it is listed as a clean extension point, clearly labelled so nobody
mistakes the intent for the implementation.

---

## How to run

Everything below is offline and needs no keys. Run from `backend/`.

```bash
# Layer 1 — the offline metrics gate, human-readable report (exit 0 = PASS):
cd backend && python -m app.eval.harness

# Layer 2 — the pytest regression gate (fails the build on a regression):
cd backend && python -m pytest tests/eval -q
#   direct human-readable runner (DeepEval-pattern gate, POSIX exit code):
cd backend && python -m app.eval.regression
#   (the whole suite: python -m pytest tests -q ; lint: ruff check src tests)

# Optional model-graded judge pass (Layer 1 + relevance), needs the gateway:
cd backend && TAIF_EVAL_LLM_JUDGE=1 python -m pytest tests/eval -q
```

> There is **no** GitHub Actions workflow or `Makefile` in this repo today — the "CI gate"
> is the pytest suite above, run as the project's standing verification command:
> `python -m pytest tests -q` and `ruff check src tests` must stay green (see
> `docs/learn/50-run-and-extend.md` §4). Layer 3 (`trace_eval`) runs automatically, post-run and best-effort, as
> part of the live LLM-Ops loop; Phoenix's local UI is launched by
> `app.observability.otel.init_observability` when `arize-phoenix` is installed.

---

## References (for the citations above)

- **RAGAS** — Es et al., *RAGAS: Automated Evaluation of Retrieval Augmented Generation*
  (EACL 2024 demo); library: <https://github.com/explodinggradients/ragas>.
- **DeepEval** — Confident AI, open-source LLM eval framework:
  <https://github.com/confident-ai/deepeval>.
- **Arize Phoenix** — <https://github.com/Arize-ai/phoenix> (a real dependency here).
- **Langfuse** — <https://github.com/langfuse/langfuse> (cited; not integrated).
- **Patronus AI** — <https://www.patronus.ai/> (cited; not integrated).
- **OpenTelemetry GenAI semantic conventions** — the vendor-neutral span schema Aegis emits
  (`app/observability/semconv.py`).
