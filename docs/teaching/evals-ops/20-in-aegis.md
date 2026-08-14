# Evals & LLM-Ops — our exact implementation

Two packages, one depending on the other in one direction only.

**`aegis/src/aegis/evals/`** — the pure, importable evaluation library:

| File | Lines | What it owns |
|---|---|---|
| `__init__.py` | 79 | Public surface |
| `corpus.py` | 142 | The frozen seed corpus + labelled cases |
| `metrics.py` | 279 | The deterministic proxies + `MetricConfig` |
| `harness.py` | 391 | `evaluate`, thresholds, `EvalReport` |
| `judge.py` | 218 | LLM-as-judge + `JudgeUnavailableError` |
| `regression.py` | 412 | The DeepEval-pattern per-metric gate |
| `stream.py` | 85 | AG-UI streaming |

**`aegis/src/aegis/ops/`** — the closed loop:

| File | Lines | What it owns |
|---|---|---|
| `__init__.py` | 65 | Public surface |
| `models.py` | 99 | `EvalResult` / `PromptVersion` / `PromptStatus` ORM |
| `config.py` | 234 | `LoopParams`, `configure_ops`, the injected seams |
| `registry.py` | 265 | Versions, promote, rollback, the active cache |
| `trace_eval.py` | 396 | Grade a completed run, per facet |
| `diagnose.py` | 312 | Cluster failures, propose a DRAFT |
| `release.py` | 447 | Change-risk classifier + the tiered gate |
| `gate.py` | 372 | The real `eval_fn` + the durable approval inbox |
| `stream.py` | 226 | AG-UI streaming |

`aegis.ops` depends on `aegis.evals` — **one-directional**
(`ops/__init__.py:12-13`). Importing either pulls `sqlalchemy` but **no** `fastapi` or
`litellm`; there are isolation tests pinning that (`evals/__init__.py:12-14`,
`ops/__init__.py:15-16`).

---

## How you import them

```python
from aegis.evals import evaluate, DEFAULT_THRESHOLDS, run_regression_gate
from aegis.ops import (
    configure_ops, evaluate_run, diagnose, release, apply_release_decision,
)

report = await evaluate()                       # offline, deterministic, no network
report = await evaluate(complete=my_complete)   # + the LLM-as-judge pass
```

Or from the command line:

```
python -m aegis.evals.harness      # human-readable report + POSIX exit code
python -m aegis.evals.regression   # the DeepEval-pattern gate
```

`configure_ops(...)` (`ops/config.py:120`) injects everything host-specific: the prompt
**floor renderer**, the session factory, the `set_tenant_scope` binder, the durable
`enqueue_approval` writer, and the host-owned `Approval` ORM class plus its status enum.

---

## Layer 1 — the deterministic gate

### The corpus (`corpus.py`)

`SEED_CORPUS` (`corpus.py:45`) is five documents, and the last two are **deliberate
distractors** so retrieval has to discriminate — *"a query about refunds must out-rank the
login runbook, not merely return every chunk"* (`corpus.py:3-7`).

`EvalCase` (`corpus.py:29`) carries `query`, `gold_doc_ids` (the documents a correct
retrieval must surface) and `claims` (keywords a grounded answer should be able to cite).

*"Everything here is a constant: the gate is deterministic because its inputs are frozen."*

### The metrics (`metrics.py`)

`score_case(case, result, *, precision_k)` (`metrics.py:204`):

- **precision** (`:220-222`) — fraction of the top-`k` retrieved sources whose document is
  gold. `0.0` when nothing was retrieved.
- **recall** (`:226-230`) — fraction of gold docs present anywhere in the results, or
  **`None`** when the case carries no gold docs.
- **groundedness** (`:232-238`) — fraction of expected claims present in the normalised
  `answer_context`, or **`None`** when the case carries no claims.

`_normalize` (`metrics.py:46`) lowercases and collapses every non-alphanumeric run to one
space. The docstring at `:48-51` names the reason: it neutralises **spotlight
datamarking** (`original▁payment▁method`) so a security control does not silently tank the
quality metric.

`aggregate(scores)` (`metrics.py:249`) averages **only over the cases that carried each
label** (`:270-278`) and reports `recall_cases` / `groundedness_cases` alongside. The
`CaseScore` docstring at `:73-77` explains why the `None`s are load-bearing.

`MetricConfig` (`metrics.py:135`) is the single dashboard-facing view of a metric —
name, threshold, direction, value, verdict, contributing cases, and **`computed`**, whose
`False` marks *"an honestly-not-computed metric such as RAGAS answer relevancy, whose
`value` is then `None`."* It is the one authoritative per-metric number; the stream payload
and the persisted rows are both derived from it.

### The harness (`harness.py`)

`DEFAULT_THRESHOLDS` (`harness.py:68-73`): precision ≥ 0.66, recall ≥ 0.95, groundedness ≥
0.85, at `precision_k = 1`. The comment at `:64-67` states the calibration rule: *"set
conservatively below the seed corpus's observed scores so normal runs pass, but high enough
that a real regression in fusion, assembly, or the corpus mapping trips it."* Precision is
measured @1 because the cases are single-gold.

`build_eval_retriever()` (`harness.py:264`) constructs the **production** `Retriever` over
the real `InMemoryKnowledgeBackend` — genuine vector + graph + BM25 recall fused by RRF.
Only the embedding and the reranker are deterministic local fakes (`_fake_embed`, `:259`;
`_fake_complete`, `:242`) so the run needs no network. `_fake_complete` returns empty
content deliberately: the reranker falls back to recall order on an unparseable response,
which makes **the fused RRF ordering the thing measured**.

`evaluate(cases, thresholds, *, complete=None)` (`harness.py:283`) scores each case with a
**fresh retriever** (`:315`) *"so an earlier query can never semantic-hit a later one."*

`EvalReport.failures()` (`harness.py:96`) — note `:110-113` and `:118-121`: an **unmeasured**
metric (`None`) is reported as a failure, not a pass. And `passed` (`harness.py:347-353`)
requires each metric to be **not `None` and** above threshold.

`to_eval_rows()` (`harness.py:213`) projects only the *computed* metrics into
`EvalResult`-shaped dicts, *"ORM-free by design: the caller constructs the rows."*

### The regression gate (`regression.py`)

The DeepEval **pattern**, implemented natively. The docstring at `:22-31` gives the
reasoning: `deepeval` is heavy and most of its metrics call an external LLM judge, which is
slow, non-deterministic and network-dependent; this gate must run in CI with no infra, no
keys and no network.

`Metric` (`regression.py:59`) carries its own threshold and a `higher_is_better`
direction. `DEFAULT_METRICS` at `:241`. `run_regression_gate` at `:301`.

`run_tool_selection_eval` (`regression.py:273`) is the agentic case: with an injected
`route_fn` + `roster` it asserts the router picks the expected specialist for
representative queries (`ROUTER_EVAL_CASES`, `:260`), scored as
`tool_selection_accuracy`. **Inject-only** — with no router the case is skipped and the RAG
metrics stand alone, so this module never imports an agent layer.

---

## Layer 2 — the judge (`judge.py`)

`judge_answer(question, context, answer, *, complete)` (`judge.py:176`) — **inject-only**:
`complete=None` raises `ValueError` (`:198-202`). There is no lazy fallback to a host
completer.

It routes to `ModelRole.REASONING` with `response_format={"type":"json_object"}`
(`:212-217`) and parses with `_parse_verdict`.

**`JudgeUnavailableError`** (`judge.py:49`) is the centrepiece, and its docstring is the
best three-sentence statement of the whole module:

> *"This exists so a judge outage is distinguishable from a genuine `0.0`. Any caller that
> gates a release on the judge MUST let this propagate (fail closed): substituting `0.0`
> makes a draft and its baseline score identically `0.0`, which silently PASSES a
> `margin=0.0` eval gate and auto-promotes every candidate prompt. A control that cannot
> run must stop the release, not wave it through."*

**The two-tier parser.** `_json_candidates(content)` (`judge.py:109`) yields progressively
salvaged candidates: the raw text; the text with `<think>` blocks (`_THINK_BLOCK`, `:31`
— including an *unterminated* one) and markdown fences (`_FENCE`, `:34`) stripped; and the
first **balanced** `{...}` object found by a depth scan (`:126-137`).

`_parse_verdict` (`judge.py:141`) tries each candidate, and rejects **NaN and infinity**
explicitly (`:160-165`) with the comment: *"NaN/inf would silently poison every downstream
comparison (`nan < x` is always False, so a NaN score PASSES a gate) — treat it as
unparseable."* Anything genuinely unusable raises, with a bounded snippet
(`_SNIPPET_LEN = 240`, `:38`) so an outage cannot dump a whole model response into a log
line.

`judge_enabled()` (`judge.py:64`) reads `TAIF_EVAL_LLM_JUDGE`, so a normal `pytest` run
never touches the network.

**Where the judge is pointed matters.** `harness.py:39-42` defines `_ANSWER_SYSTEM` with
the rule in the comment: *"The judge then grades that answer's groundedness against the
retrieved context — grading the context against itself measures nothing."* At
`harness.py:324-344` the harness **generates an answer first** under
`ModelRole.GENERATION`, then judges it — two `complete` calls per case.

---

## Layer 3 — trace evaluation (`ops/trace_eval.py`)

`evaluate_run(session, *, run_id, query, answer, contexts, steps, complete=None,
prompt_key=None, tenant_id=None, threshold=0.6)` (`trace_eval.py:271`).

`_KIND_METRIC` (`trace_eval.py:48-52`) maps span kinds to metric namespaces:
`RETRIEVER → step:retrieval`, `TOOL → step:tool`, `GUARDRAIL → step:guardrail`. Everything
else is skipped.

Four graders, each returning `(score, detail)`:

- `_grade_answer` (`:284`) — the reasoning-model judge when `complete` is injected, else a
  lexical `_overlap` proxy.
- `_grade_retrieval` (`:302`) — a cheap relevance judge, else `_overlap(query, contexts)`.
- `_grade_tool` (`:322`) — a cheap appropriateness judge, else the offline rule *a tool
  that executed without error is appropriate*.
- `_grade_guardrail` (`:344`) — a cheap appropriateness judge, else *a definite verdict is
  appropriate; an absent verdict is neutral (0.5)*.

The online per-step graders use `_cheap_score` (`trace_eval.py:140`) on `ModelRole.CHEAP` —
*"much cheaper than the reasoning-model answer judge"*.

**Best-effort and total** (`:12-16`, `:339-352`): `_grade` catches per-metric failures and
logs them, and an outer `try` swallows anything else, so the caller always gets a
`RunEval`. Rows are **flushed, not committed** — the caller owns the transaction boundary.

**The mean-per-facet detail** (`trace_eval.py:381-388`): a run usually has several steps of
the same kind, each persisting its own row. `dict(written)` would keep only the **last**
row of a repeated facet, so the returned map would contradict both the persisted rows and
the `overall` computed from all of them. It builds a mean per facet instead.

---

## The registry (`ops/registry.py`)

`PromptVersion` (`ops/models.py:68`) — `prompt_key`, `version`, `system_prompt`, `config`
(JsonB), `status`, `parent_version`, `created_by`, `notes`, `created_at`, `activated_at`,
with a **unique index on `(prompt_key, version)`** and an index on `(prompt_key, status)`
(`models.py:96-99`).

`PromptStatus` (`models.py:59`): `DRAFT` → `STAGED` → `ACTIVE` → `ARCHIVED`.

**The active cache.** `_ACTIVE_CACHE` (`registry.py:28`) maps `prompt_key →
(system_prompt, config, version)`. `get_cached_active` (`:35`) is **synchronous and
hot-path-safe**; `None` means the caller falls back to the injected floor.

**`_cache_on_commit`** (`registry.py:53`) is the important one. `promote`/`rollback`
deliberately leave the transaction open for the caller, so caching at *flush* time would
publish a prompt that may never be committed — *"a caller rollback (or a crash) would leave
`_ACTIVE_CACHE` serving a phantom system prompt to every run"* (`:56-61`). It binds a
one-shot `after_commit` listener on the session (`:74`) and snapshots the payload now,
because the ORM object is expired by the commit.

**`create_draft`** (`registry.py:94`) allocates `max(version) + 1`, which is check-then-act
against the unique index. A collision is retried inside a **SAVEPOINT**
(`begin_nested`, `:139`) with a freshly-read max, up to `_VERSION_COLLISION_RETRIES = 5`
(`:32`) — because the loser's `IntegrityError` would otherwise surface *after* its
optimiser LLM call was already paid for. A persistently contended key re-raises, never
swallows (`:153`).

**`promote`** (`registry.py:182`) archives any other ACTIVE row for the key in one UPDATE
(`:196-204`), sets this one ACTIVE with `activated_at = now()`, flushes, and registers the
cache publish on commit.

**`rollback`** (`registry.py:212`) — see [`30-deep-dive.md`](30-deep-dive.md). The
candidate query requires `status == ARCHIVED` **and `activated_at IS NOT NULL`**
(`:233-239`), and the version being rolled back **from** has its `activated_at` **cleared**
(`:250`), with the audit line appended to `notes` (`_note_rollback`, `:258`).

---

## Diagnose (`ops/diagnose.py`)

`diagnose(session, *, prompt_key, complete, limit=50, render_floor_prompt=None)`
(`diagnose.py:148`).

1. Read up to `limit` recent **failing** `EvalResult` rows for this `prompt_key`
   (`:179-191`).
2. Tally failures by metric (`:193-195`).
3. **Compute the denominator** (`:202-223`). The comment at `:198-201` is the argument:
   *"A raw failure count is not a signal: a facet graded 500 times with 20 failures is
   healthier than one graded 25 times with 15, yet the bare tally ranks the first as the
   worse offender and points the optimizer at it."*
   - Windowed by **`id`, not `ts`** (`:204-208`): ids are monotonic and compare identically
     on every dialect, whereas a server-side `CURRENT_TIMESTAMP` stored as a naive string
     on SQLite does not compare against a tz-aware Python bind parameter.
   - Clamped: `totals[metric] = max(totals.get(metric, 0), count)` (`:222-223`), so a row
     written after the window query cannot produce a rate > 1.
4. **No failures → no draft** (`:229-235`).
5. Base prompt: the active version's, else the injected floor (`:237-245`).
6. Build the optimiser prompt. The breakdown is ordered by **rate** and always names the
   known facets (`_KNOWN_METRICS`, `:45-50`) *"so a clean facet reads as '0% of N' rather
   than as a silent absence"* (`:250-260`). The user prompt literally says *"fix the
   highest RATE, not the highest count"* (`:264-265`).
7. Call `ModelRole.REASONING` in JSON mode; **any** transport or parse failure yields **no
   draft**, never a crash (`:270-283`). `_parse_optimized_prompt` (`:128`) returns `None`
   for non-JSON, a non-dict, or a missing/blank `system_prompt`.
8. Write the result as a **DRAFT** only (`:295-303`), parented to the active version.

`_OPTIMIZER_SYSTEM` (`diagnose.py:55-63`) instructs: *"preserving all existing safety,
guardrail, tool, and scope instructions. Make the smallest change that plausibly fixes the
failures; do not remove constraints."* That is an instruction, not a guarantee — which is
exactly why the risk classifier counts safety terms independently.

---

## The gate and release (`ops/release.py`, `ops/gate.py`)

### The knobs — `LoopParams` (`ops/config.py:51`)

| Field | Default |
|---|---|
| `eval_margin` | `0.0` — strictly better |
| `high_diff_fraction` | `0.40` |
| `low_diff_fraction` | `0.15` |
| `safety_terms` | ignore, guardrail, safety, tool, approval, never, policy, system prompt |
| `critical_config_markers` | model, tool, permission, role, scope |
| `tunable_config_keys` | temperature, top_k, top_p |
| `tunable_max_delta` | temperature 0.5, top_k 5, top_p 0.3 |
| `auto_promote_ceiling` | `"low"` |

`RISK_ORDER` (`config.py:47`) is `{"low": 0, "medium": 1, "high": 2}`.

### `classify_change` (`release.py:153`)

Deterministic, no model call. HIGH if the changed-line fraction exceeds
`high_diff_fraction` (`:188-191`), **or** any safety term's whole-word count changed
(`_changed_safety_terms`, `:98`; applied `:193-196`), **or** a critical config key changed
(`_critical_config_changes`, `:107`; applied `:198-203`). LOW if the diff is small **and**
config changes are within bounds (`_config_changes_within_bounds`, `:125`; applied
`:208-217`). MEDIUM otherwise.

`_term_count` (`release.py:92`) uses `\b`-anchored regex — whole-word, case-insensitive.

### `release(...)` (`release.py:268`)

1. Load the draft; **only a DRAFT may be released** (`:322-328`) — never re-release an
   already-active, staged or archived version.
2. Baseline = the active version's prompt, else the injected floor (`_baseline`, `:257`).
3. Score both through `eval_fn`, each wrapped in `_require_score` (`release.py:231`), which
   raises on a non-numeric **or non-finite** score (`:245-253`). The docstring: *"`NaN`
   compares False against everything, so a `NaN` score would sail through
   `draft_score < baseline_score + margin` and be promoted."*
4. Classify the change.
5. **The eval gate** (`:346-359`): `draft < baseline + margin` → status `ARCHIVED`, outcome
   `"rejected"`.
6. **The tiered decision** (`:392-403`): `auto` promotes; `manual` stages; `tiered`
   promotes iff `RISK_ORDER[risk] <= RISK_ORDER[ceiling]`, else stages via
   `approval_enqueue`.

The `Raises:` section at `:309-315` is explicit that anything `eval_fn` raises — *notably
`JudgeUnavailableError`* — abandons the release with the draft left DRAFT: *"a control that
could not run never promotes."*

`apply_release_decision` (`release.py:406`) is the mirror: **only a STAGED version may be
decided** (`:437-442`). The docstring at `:414-420` spells out the failure being closed: a
second approve re-promotes and archives whatever legitimately replaced it; a reject
arriving after an approve archives the now-ACTIVE version, *"leaving the `prompt_key` with
no active version, so every run silently drops to the floor prompt."*

### The real scorer — `make_eval_fn` (`gate.py:67`)

Returns an async `eval_fn(system_prompt) -> float`. For each of the first `limit` seed cases
(`DEFAULT_EVAL_SUBSET = 3`, `gate.py:54`):

1. Retrieve with a **fresh** offline hybrid retriever (`:117-118`).
2. **Generate an answer under the candidate `system_prompt`** (`:119-126`).
3. Judge that answer for groundedness + relevance (`:130`).

The mean blended score is returned. *"Because `system_prompt` is the system message every
answer is generated under, the score moves with the prompt"* (`:81-85`) — that is what makes
the gate a real comparison rather than a constant.

**Fail-closed, in a comment you cannot miss** (`gate.py:128-129`):

```python
# An unparseable judge reply raises out of here on purpose — see the
# docstring. Do NOT wrap this in a try/except that yields 0.0.
```

And zero graded cases raises `RuntimeError` (`:133-137`): *"the gate cannot pass on an empty
measurement (fail closed)."*

### The durable inbox — `gate.py`

`enqueue_release_approval` (`gate.py:143`) writes a host `Approval` row with
`action="prompt_release"` (`RELEASE_ACTION`, `:64`) and a **synthetic**
`run_id`/`thread_id` of `prompt_release:<draft_id>` — deliberately decoupled from the agent
resume machinery, so no LangGraph checkpoint or live socket is ever involved
(`:155-158`).

`list_pending_releases` (`gate.py:213`) and `decide_release` (`gate.py:266`) are the read
and resolve path.

**`decide_release` is exactly-once** (`:274-283`). The durable row is claimed with a
conditional `UPDATE ... WHERE status = PENDING` (`:310-321`) **before** the draft is
touched, and the whole thing is one transaction. `rowcount == 0` → the row was already
decided → return `outcome="already_decided"` carrying the **recorded** decision, not the
requested one (`:322-342`). And if `apply_release_decision` raises because the version is
not STAGED, the transaction — including the claim — rolls back, so the row stays PENDING
and decidable (`:346-350`).

---

## How the backend composes it

**`backend/src/app/ops/__init__.py`** wires the host seams into `aegis.ops` once at import
via `configure_ops`: the floor renderer `_default_render_floor_prompt` (`:25`), which
renders the adapter persona prompt; the session factory; the `set_tenant_scope` binder; the
durable `enqueue_approval`; and the host `Approval` ORM + status enum.

**`backend/src/app/eval/__init__.py`** re-exports `aegis.evals` and adds two backend
conveniences: the judge's `complete` defaults to `app.core.llm.complete`, and the
regression gate's router defaults to `app.agent.router.route_query`.

**The endpoints** (`backend/src/app/api/routes.py`):

| Route | Line | Notes |
|---|---|---|
| `GET /ops/prompts` | 2038 | version listing |
| `GET /ops/prompts/active` | 2075 | the live version |
| `GET /ops/evals` | 2121 | the trend, filterable by `prompt_key` |
| `POST /ops/diagnose` | 2170 | writes a DRAFT |
| `POST /ops/release` | 2208 | the eval gate + tiered decision |
| `POST /ops/rollback` | 2278 | one-call revert |
| `GET /ops/params` | 2466 | the effective `LoopParams` as data |

`POST /ops/release` (`routes.py:2208`) injects the **real** scorer and the **real** durable
enqueue (`:2225-2238`), commits the transaction (`:2248`), and audits the outcome with the
scores and the approval id (`:2254-2265`). Access is `require_admin_or_ai_team`.

**Startup.** `backend/src/app/main.py:168-177` warms the registry's active cache with
`registry.refresh_cache(session)` so the harness reads a live promoted prompt synchronously
on the hot path. Best-effort; a failure never blocks startup.

**Next:** [`30-deep-dive.md`](30-deep-dive.md) — four bugs, one of which auto-promoted
every candidate prompt.
