# Phase 5 audit — real `ragas` metrics, and "every judge call is metered"

**Audited commits:** `915252c` (core) + the `ragas_suite.py` / route / UI half added in `7578a26`.
**Plan:** `docs/dev_new_docs_v2/sota/09-evals-ragas-deepeval.md`
**Branch:** `docs/wow-pass-plan` · **Date:** 2026-08-27
**Method:** live measurement against a running backend + Postgres `usage_ledger`, plus source
reading. Every `[MEASURED]` claim below was produced by running the shipped code path.

---

## VERDICT: **FAIL**

The phase's headline claim — *"every judge call ragas makes goes through
`aegis.gateway.complete`, so it is budget-checked, rate-limited, traced, and written to the
usage ledger"* — is **false for the route that ships it**. `POST /v1/evals/live-run` never
binds a `GovernanceContext`, so every judge call runs with `ctx = None`: **no budget check, no
usage-ledger row, no tenant/user attribution.** Seven HTTP invocations of the route, ~108 model
calls, produced **zero** `usage_ledger` rows. The same suite, run in-process with a governance
context bound, produced all nine rows per case with correct `tenant_id`/`user_id`.

The API response and the evals screen both *state* the false claim to the reader:
`source: "ragas, judged through the Aegis gateway (metered in usage_ledger)"` and
*"Press the button; the run is metered like any other call."*

That is the exact failure the commit message says the adapter exists to prevent, arrived at by
a different route than the `base_url` shortcut it argues against. On a platform whose pitch is
that every model call is attributable, this is a FAIL, not a finding.

Secondary: 11 of the 13 user-visible claims the plan marked **"not optional cleanup; it lands
in the same commit"** were never retired, including the load-bearing false sentence in
`docs/architecture/eval-strategy.md`. And the whole feature — 283 lines of new production code
and a new money-spending route — ships with **zero tests**, which is why C-1 shipped.

| Area | Result |
|---|---|
| A. Metering claim | **FAIL** — 0 ledger rows, no attribution, no budget check |
| B. Budget enforcement | **FAIL** — unreachable on the route; and mislabelled when it does fire |
| C. Blanket `except` | **FINDINGS** — swallows `BudgetExceededError`, and passes ragas's own NaN/0.0 through |
| D. Cost & safety | **FINDINGS** — ~54 calls / ~$0.044 per press at `limit=6`, unrate-limited, uncapped, unlogged |
| E. Dependency damage | **PASS with findings** — suites green; `uv.lock` never regenerated; `rich` silently downgraded |
| F. Honesty of claims | **FAIL** — 11/13 planned retirements not done; the landing-page claim now points at a file that contradicts it |
| G. The UI | **FINDINGS** — dishonest Receipt; unfaked-metric card now shows a tautological 1.000; 500s at the 30 s dev-proxy ceiling |

---

## Environment note

The backend that was listening on `:8110` at the start of this audit exited mid-audit (not
caused by anything I did to the repo; a second uvicorn had been started on `:8000` by another
lane). I started a backend on `:8110` from `backend/.venv` to complete the UI-path checks and
**left it running**, because `web/next.config.mjs:71` proxies the console to `127.0.0.1:8110`
and the console is otherwise dead. No repo source was modified by this audit.

---

# A — The metering claim is false  🔴 **CRITICAL**

**`backend/src/app/api/routes.py:3953-3994`**

### The measurement `[MEASURED]`

```
# 1. snapshot
$ psql taif -c "select max(id), count(*) from usage_ledger;"
  max  | count
-------+-------
 69026 | 19493

# 2. invoke the shipped route as northwind.analyst (ai_team, tenant 1)
$ curl -X POST "http://127.0.0.1:8110/v1/evals/live-run?limit=2" -H "Authorization: Bearer $TOK"
{"metrics":[{"name":"ragas:faithfulness","value":1.0,"cases":2,"library":"ragas@0.4.3","note":""},
            {"name":"ragas:answer_relevancy","value":0.5426966569103093,"cases":2,...}],
 "source":"ragas, judged through the Aegis gateway (metered in usage_ledger)"}
HTTP 200

# 3. the ledger, after ~18 real model calls
$ psql taif -c "select count(*) from usage_ledger where id > 69026;"
 count
-------
     0
```

Repeated across **seven** successful HTTP invocations of the route (1 × `limit=2` on `:8110`,
3 × concurrent `limit=2` on `:8000`, 1 × `limit=2` on `:8000`, 1 × `limit=1` direct on `:8110`,
1 × `limit=1` through the web proxy) — roughly **108 model calls, ≈ $0.088 of real spend**:

```
$ psql taif -c "select count(*) from usage_ledger where id > 69035;"
 count
-------
     0
```

### The control — the same suite, with a context bound `[MEASURED]`

Running `run_ragas_suite` in-process against the *real* `app.core.llm.complete` /
`aegis.gateway.embed`, with one line added (`set_governance_context(GovernanceContext(
tenant_id=1, user_id=7, role=Role.AI_TEAM))`), scoring **one** case:

```
elapsed 15.2
calls {'complete': 5, 'embed': 4}

  id   | tenant_id | user_id |         model          | prompt | compl | cost_usd
-------+-----------+---------+------------------------+--------+-------+-----------
 69027 |         1 |       7 | DeepSeek-V4-Flash      |    539 |   146 | 0.0012353
 69028 |         1 |       7 | DeepSeek-V4-Flash      |   1160 |   491 | 0.0034364
 69029 |         1 |       7 | DeepSeek-V4-Flash      |    650 |    25 | 0.000825
 69030 |         1 |       7 | DeepSeek-V4-Flash      |    650 |    25 | 0.000825
 69031 |         1 |       7 | DeepSeek-V4-Flash      |    650 |    25 | 0.000825
 69032 |         1 |       7 | text-embedding-3-large |     11 |     0 | 1.43e-06
 69033 |         1 |       7 | text-embedding-3-large |      7 |     0 | 9.1e-07
 69034 |         1 |       7 | text-embedding-3-large |      7 |     0 | 9.1e-07
 69035 |         1 |       7 | text-embedding-3-large |      7 |     0 | 9.1e-07
(9 rows)
```

**All nine calls land, with correct tenant and user, embeddings included.** The adapters are
right. The gateway is right. The *route* is the hole.

### The cause `[SOURCE]`

`GovernanceContext` is a `ContextVar` whose default is `None`
(`aegis/src/aegis/governance/context.py:33-36`), bound explicitly at the edge of each
model-touching route. `routes.py` binds it in exactly one place —
`routes.py:1797` (`set_governance_context(governance)` inside the `/query` SSE source) — and
`evals_live_run` (`routes.py:3954-3994`) does neither that nor `_resolve_governance(auth)`.
`auth` is consumed for authorization only; the principal never reaches the chokepoint.

At the chokepoint both gates are `ctx`-conditional:

```python
# aegis/src/aegis/gateway/llm.py:1792-1804 (embed; complete is identical)
gov_ctx = _governance.get_context()
if gov_ctx is not None:
    await _governance.enforce(gov_ctx)      # ← skipped: no budget check
...
await _record_usage(gov_ctx, ...)           # ← `if ctx is None: return`  (llm.py:1526)
```

So the three of the four claimed properties that need a principal are all absent. Only the
OTel span survives (`_observability.span` is unconditional) — spend is *traceable in Phoenix*
but not *reconcilable against the ledger*, which is the property the cost dashboard reads.

### Why nothing caught it

There is **no test anywhere** that exercises `ragas_suite.py`, `gateway_adapters.py`, or
`POST /v1/evals/live-run` `[MEASURED]`:

```
$ grep -rln "ragas_suite|gateway_adapters|live_run|LiveEvalResponse" aegis/tests backend/tests
(no matches)
```

The commit message's *"three metered rows in `usage_ledger`"* was, on this evidence, measured
from a script that bound a context — not from the route that shipped.

### Fix

```python
# backend/src/app/api/routes.py, inside evals_live_run
governance = await _resolve_governance(auth)
token = set_governance_context(governance)
try:
    metrics = await run_ragas_suite(complete=complete, embed=gateway_embed,
                                    limit=max(1, min(limit, 6)))
finally:
    reset_governance_context(token)
```

and a test that asserts it — `POST /v1/evals/live-run` with a fake completer must write N
`usage_ledger` rows carrying the caller's `tenant_id`/`user_id`. Until such a test exists, the
`source` string must not say "metered in usage_ledger".

---

# B — Budget enforcement  🔴 **CRITICAL** (B-1) + 🟠 **HIGH** (B-2)

## B-1. A budget cap cannot refuse this route at all `[MEASURED]` + `[SOURCE]`

Because `gov_ctx is None` (finding A), `_GovernanceHook.enforce` is **never called**
(`aegis/src/aegis/gateway/llm.py:1794-1795`). There is no cap value — zero, negative, or
otherwise — that can stop `POST /v1/evals/live-run`. The claim under test ("a budget refusal
makes a metric report *not run* rather than 0.0") is not merely unproven on this route; the
refusal is unreachable.

Tenant 1 carries `usd_cap = 50` / day and user 8 carries `usd_cap = 5` (`budgets` table). The
route spends against neither, and records against neither, so the spend does not even *count
toward* the cap for the tenant's other traffic. Two effects compound:

* the money is invisible on `/v1/admin/usage`, `/v1/me/budget`, `/v1/reports/budget.csv`;
* every dollar spent here silently *raises* the tenant's real headroom, because the ledger the
  cap is computed from never saw it.

*(I was unable to set a cap to a tiny value to demonstrate the refusal end-to-end — both the
`psql UPDATE` and reading the admin-budgets OpenAPI schema were refused by the sandbox
classifier. The source path is unambiguous and the zero-row measurement is conclusive, so the
demonstration would add nothing.)*

**Fix:** the same fix as A. Enforcement and metering share one cause.

## B-2. When a budget refusal *does* fire, the value is `None` — but the note lies `[MEASURED]`

Forcing `BudgetExceededError` from both `complete` and `embed` into `run_ragas_suite`:

```
BudgetExceededError MRO: ['BudgetExceededError', 'Exception', 'BaseException', 'object']
LiveMetric(name='ragas:faithfulness',     value=None, cases=0, library='ragas@0.4.3',
           note='the judge returned nothing usable; not scored')
LiveMetric(name='ragas:answer_relevancy', value=None, cases=0, library='ragas@0.4.3',
           note='the judge or the embedder was unavailable; not scored')
```

**Good:** `value` is `None`, never `0.0`, and never a 500. That half of the claim holds.

**Bad:** the note is wrong, and wrong in the specific way the code's own comment says it must
never be. `ragas_suite.py:108-114` documents that catch:

> *"This exact catch already lied once: `ascore()` was called with an argument it does not
> take, and the panel said 'the judge or the embedder was unavailable' — sending a reader to
> check a model deployment that was working perfectly."*

A budget refusal now produces exactly that sentence, sending an operator to check a model
deployment that is working perfectly, when the truth is "your tenant is over its cap".

Worse, `gateway_adapters.py:23-25` asserts a mechanism that does not exist:

> *"That exception is **allowed to propagate** so the metric is reported as *not run*."*

It is not allowed to propagate. `ragas_suite.py:115-116` catches `Exception`, and
`BudgetExceededError` is a plain `Exception` subclass (`aegis/src/aegis/gateway/types.py:25`).

**Fix:** catch it by name, before the blanket, and carry the reason out:

```python
except BudgetExceededError as exc:
    return _NotRun(f"refused by the {exc.scope} {exc.limit_type} cap "
                   f"({exc.used} of {exc.limit}); not scored")
```

and give `LiveMetric` a per-metric note that survives to the panel, rather than a single
corpus-level string chosen by `if faiths else`.

---

# C — What else the blanket `except` swallows

**`aegis/src/aegis/evals/libs/ragas_suite.py:115-116`**

`asyncio.CancelledError` is safe (it derives from `BaseException` in 3.8+, so a client
disconnect or shutdown is not mislabelled). Beyond `BudgetExceededError` (B-2), three real
cases:

## C-1. `SlotUnavailableError` → "the judge was unavailable"  🟡 MEDIUM `[SOURCE]`

`aegis/src/aegis/gateway/limiter.py:111,411` — a fleet-wide rate-limit refusal is a
`RuntimeError` subclass, swallowed and reported as a judge outage. Arguably the least-wrong of
the mislabels, but it is still "check the model deployment" for "the fleet limiter said wait".

## C-2. `asyncio.run()` inside a running loop → "the judge was unavailable"  🟡 MEDIUM `[SOURCE]`

`gateway_adapters.py:114` and `:141` are sync bridges calling `asyncio.run(...)`. If any ragas
path ever reaches them from inside the event loop they raise
`RuntimeError: asyncio.run() cannot be called from a running event loop` — a hard programming
error, swallowed and reported as an outage. The docstring calls it "a bridge, not the hot
path", which is a reason to `raise NotImplementedError` there, not a reason to leave a trap.

## C-3. ragas's own NaN reaches the panel as a blank cell with **no note**  🟠 HIGH `[MEASURED]`

`ragas 0.4.3` `Faithfulness` returns `MetricResult(value=float("nan"))` when the judge produced
no statements — a routine LLM outcome, not an error:

```
backend/.venv/.../ragas/metrics/collections/faithfulness/metric.py:121   return MetricResult(value=float("nan"))
                                                              :151,161   return float("nan")
```

`ragas_suite.py:107` does `float(faith.value)`. NaN is not `None`, so it enters `faiths`,
counts toward `cases`, and poisons `_mean` (`ragas_suite.py:122-123`) — **one NaN case turns
the whole metric NaN even when the other cases scored.** Pydantic then serialises NaN as
`null` (verified through a real FastAPI `response_model` round trip: `status 200`,
`{"name":"ragas:faithfulness","value":null,...}`), but `note` is `""` because `faiths` was
truthy.

The panel therefore renders the metric name with **an empty value and no explanation**, while
`cases` claims 2 cases contributed. A reader is told two cases were scored and shown nothing.

**Fix:** `math.isfinite` guard in `_one`, and make the note per-metric:

```python
f = float(faith.value); r = float(rel.value)
return (f if math.isfinite(f) else None), (r if math.isfinite(r) else None)
```

## C-4. ragas's own `0.0` is passed through as a measured score  🟠 HIGH `[SOURCE]`

The doctrine of this phase is *"a zero is a measurement, and not running is not a measurement"*
(`ragas_suite.py:66-68`). The library it now trusts violates it:

```python
# ragas/metrics/collections/answer_relevancy/metric.py:126-127
if not generated_questions:
    return MetricResult(value=0.0)
```

The judge returning no question is a *judge failure*; ragas reports it as **relevancy 0.0**,
and `ragas_suite` records it as a real score that drags the corpus mean down. The adapter
guards its own zero-avoidance carefully and then imports one.

**Fix:** the adapter's `agenerate` already raises `JudgeUnavailableError` for an unparseable
response; extend the same discipline by treating a `0.0` from `AnswerRelevancy` on a case whose
`AnswerRelevanceOutput.question` was empty as not-run. Minimally: document it on the panel.

---

# D — Cost and safety of the route

## D-1. `limit=6` is ~54 model calls and ~$0.044, with no rate limit and no cap  🟠 HIGH `[MEASURED]`

Measured per case: **5 `complete` + 4 `embed` = 9 gateway calls**, ≈ **$0.00735**
(sum of the nine ledger rows above). `SEED_CASES` has 6 entries, so the clamp
`max(1, min(limit, 6))` (`routes.py:3984`) is the whole corpus:

| `limit` | model calls | ≈ cost | measured wall clock |
|---|---|---|---|
| 1 | 9 | $0.007 | 15 s (warm) – 32 s (cold) |
| 2 | 18 | $0.015 | 14 s (warm) – 134 s (loaded) |
| 6 | 54 | $0.044 | not run; ≈ 3× the `limit=2` figure |

There is **no rate limiting of any kind** on the HTTP surface `[SOURCE]` — `main.py:778` adds
only `CORSMiddleware`, and `grep -n "slowapi|RateLimit|rate_limit"` over `main.py` and
`routes.py` returns nothing. There is no per-caller debounce, no idempotency key, no in-flight
guard. Combined with finding A, any `ai_team` or `admin` principal can spend unbounded provider
money in a loop, capped by nothing and recorded nowhere.

**Measured hammering** `[MEASURED]`: three concurrent `POST …?limit=2` all returned `200`
with full scores — 54 model calls, 0 ledger rows, no refusal:

```
$ for i in 1 2 3; do curl -X POST ".../v1/evals/live-run?limit=2" & done
--- 1 {"metrics":[{"name":"ragas:faithfulness","value":1.0,...
--- 2 {"metrics":[{"name":"ragas:faithfulness","value":1.0,...
--- 3 {"metrics":[{"name":"ragas:faithfulness","value":1.0,...
$ psql -c "select count(*) from usage_ledger where id > 69035;"  →  0
```

The only bound in force is `aegis.gateway.limiter`'s fleet-wide concurrency slot, which limits
*rate of spend*, not *total spend*.

**Fix:** bind the governance context (A), which restores the USD cap; and add a coarse
per-principal cooldown or an in-flight guard on this specific route, since it is the only route
in the app whose entire purpose is to spend money on demand.

## D-2. `GET /evals/report` stays cheap — **claim holds**  🟢 `[SOURCE]` + `[MEASURED]`

`routes.py:3997-4015` is a separate handler, memoised in the process-global
`_evals_report_cache`, running `run_regression_gate()` with no LLM. `run_ragas_suite` is
imported only inside `evals_live_run`. Nothing writes live results into the cache and nothing
on the report path can trigger the live path. A dashboard poll of `/evals/report` cannot spend
money. Verified live: `GET /v1/evals/report` → `200`, 0 new ledger rows.

## D-3. `limit` has no OpenAPI constraint  🔵 LOW `[SOURCE]`

`limit: int = 2` (`routes.py:3955`) is an unconstrained query parameter; the 1..6 clamp is
applied silently at `routes.py:3984`. `limit=1000` returns a `limit=6` run without saying so.
Cosmetic, but a documented `Query(2, ge=1, le=6)` costs one line and makes the contract visible
in `openapi.json`.

---

# E — Dependency damage

## E-1. Both suites are green — **no regression**  🟢 `[MEASURED]`

```
backend:  2196 passed, 1 skipped        in 449.04s   (commit claimed 2196 ✓)
aegis:    2406 passed, 14 skipped       in 382.35s
web:      406 pass, 0 fail              (commit claimed 406 ✓)
tsc --noEmit: exit 0
backend/tests/ml + backend/tests/ingestion: 143 passed
aegis/tests/evals/test_isolation.py: 1 passed        (claim 4 holds ✓)
```

Import check of every heavy dependency in the backend venv `[MEASURED]`:

```
OK transformers · OK docling · OK fastembed · OK torch · OK ragas · OK xgboost · OK shap · OK mapie
OK langchain_community.chat_models.vertexai          ← the pin does what it claims
FAIL sentence_transformers (not installed — pre-existing, unrelated to this phase)
```

The `langchain-community<0.4` pin is real and load-bearing: the module ragas imports at load
time resolves.

## E-2. `backend/uv.lock` was never regenerated  🟠 HIGH `[MEASURED]`

`backend/pyproject.toml` gained an `evals-libs` extra in `915252c`. `backend/uv.lock` is dated
**Aug 20** — before the phase — and contains **no `ragas` entry and no `langchain-community`
entry**:

```
ragas                  lock=ABSENT      installed=0.4.3
langchain-community    lock=ABSENT      installed=0.3.31
langchain              lock=ABSENT      installed=1.3.17
instructor             lock=ABSENT      installed=1.15.4
datasets               lock=ABSENT      installed=5.0.1
appdirs / diskcache    lock=ABSENT      installed=1.4.4 / 5.6.3
```

So the working venv is a state no lock file records. `uv sync --locked` / `--frozen` in CI will
fail against the new `pyproject.toml`, and any fresh checkout re-resolves from scratch —
meaning the `<0.4` pin is the *only* thing standing between a new machine and the import
failure the commit message describes. This directly contradicts "*the install resolves
cleanly*" being a settled property.

**Fix:** `uv lock` in `backend/`, commit the result.

## E-3. `rich` was silently downgraded across a major version  🟡 MEDIUM `[MEASURED]`

| package | `uv.lock` (pre-phase) | installed now | |
|---|---|---|---|
| `huggingface-hub` | 1.27.0 | 1.27.0 | ✓ untouched, as claimed |
| `transformers` | 5.8.1 | 5.8.1 | ✓ untouched, as claimed |
| `torch` | 2.13.0 | 2.13.0 | ✓ untouched, as claimed |
| `numpy` | 2.4.6 | 2.4.6 | ✓ untouched, as claimed |
| `tabulate` | 0.10.0 | 0.10.0 | ✓ |
| `langchain-core` | 1.5.4 | **1.6.0** | bumped |
| **`rich`** | **15.0.0** | **14.3.4** | **downgraded a major version** |

The commit message states that only `langchain-community` needed pinning and that the four
heavy packages were untouched. The four heavy packages *were* untouched — that claim is true
and verified. But `rich` went **backwards from 15.0.0 to 14.3.4** to satisfy a ragas
transitive, and `langchain-core` moved up a minor. Neither is recorded, pinned, or mentioned.

Impact is low today — nothing in `backend/src`, `aegis/src`, or `scripts/` imports `rich`
directly `[MEASURED]`, so it is only a transitive of the CLI stack — but an unrecorded major
downgrade in the real venv is exactly the class of thing E was asked to look for.

**Fix:** regenerate the lock (E-2), which makes both movements visible in a diff.

## E-4. `aegis/.venv` cannot run its own test suite  🔵 LOW `[MEASURED]`

```
$ cd aegis && .venv/bin/python -m pytest tests
ImportError while loading conftest: tests/conftest.py:59: from sqlalchemy import make_url, text
ModuleNotFoundError: No module named 'sqlalchemy'
```

The suite must be run from `backend/.venv` (where it passes, 2406). Pre-existing, not caused by
this phase, but worth knowing before anyone reports "the aegis suite is broken".

---

# F — The surviving claims  🔴 **CRITICAL** (F-1) + 🟠 **HIGH**

The plan is unusually explicit about this
(`docs/dev_new_docs_v2/sota/09-evals-ragas-deepeval.md:686-688`):

> *"Shipping the real libraries while the site still reads 'no ragas dependency' is precisely
> the dishonesty this repo forbids. **This part is not optional cleanup; it lands in the same
> commit as the extras.**"*

It then lists **13** user-visible claims. **Two** were changed. Eleven were not `[MEASURED]`.

## F-1. The landing page's claim now points at a file that contradicts it  🔴 CRITICAL

**`web/src/components/landing/stackClaims.ts:108-112`**

```ts
{
  mark: 'Offline eval gate',
  mechanism: 'Real ragas metrics, every judge call metered through our own gateway',
  path: 'aegis/src/aegis/evals/metrics.py',
},
```

Three things are wrong in five lines:

1. **The `mark` still says "Offline eval gate"** while the `mechanism` now describes the *live*
   gate. The landing page has lost its claim for the offline gate and mislabelled the live one.
2. **The `path` still points at `metrics.py`** — the deterministic lexical-proxy module, which
   contains no ragas and never calls a model. A juror who follows the receipt, which is the
   entire design of this grid, finds a file that disproves the sentence above it. `README.md`
   got this right (it added a *new* `Live eval` row pointing at `gateway_adapters.py`);
   `stackClaims.ts` mutated the wrong row.
3. **"every judge call metered through our own gateway"** is the claim finding A measured as
   false.

`web/tests/landing/stackClaims.test.mjs:36-42` asserts only that `claim.path` *exists on disk*.
It passes. Its own docstring says it exists so that a claim cannot "quietly claim something
that moved" — a pointer that resolves to a contradicting file is the same defect with the
assertion satisfied.

**Fix:** split into two rows, as `README.md:210-211` already does — `Offline eval gate` →
`metrics.py`, and a new `Live eval (ragas)` → `aegis/src/aegis/evals/libs/gateway_adapters.py`
— and do not restore the metering half of the sentence until A is fixed.

## F-2. The live `/platform/capabilities` manifest still advertises the imitation  🟠 HIGH

**`backend/src/app/capabilities.py:151`** — `tech="RAGAS-style proxies + LLM judge"`
**`backend/src/app/main.py:161`** — the same line inside the OpenAPI description, therefore in
`backend/openapi.json:13342` and on `/docs`.

The plan flags `capabilities.py:151` specifically: *"same — **this one is served live** at
`GET /platform/capabilities` and `GET /about`"*. Unchanged. The public manifest and the public
API description both still say the platform imitates RAGAS, while the platform imports it.

## F-3. `docs/architecture/eval-strategy.md:11` — the load-bearing false sentence  🟠 HIGH

```
> it says so and says why. `ragas`, `deepeval`, `langfuse`, and `patronus` are **not**
> dependencies of this repo
```

`ragas>=0.4.3,<0.5` is a declared dependency in `backend/pyproject.toml` and is installed in the
backend venv. The plan named this line as "the load-bearing false sentence". It is untouched
(last commit to the file: `9ad9565`, a pre-phase docs prune). Same file, lines 21, 31, 41-42,
90-91, 107-110, 158 carry the same framing.

## F-4. Three more docs still assert non-dependency  🟡 MEDIUM

| File:line | Text |
|---|---|
| `docs/architecture/backend.md:167` | *"**not** the `ragas` library (which is not a dependency…)"* |
| `docs/architecture/backend.md:40` | `RAGAS-style proxies + LLM judge` |
| `docs/architecture/system-architecture.md:196` | `RAGAS-style metrics + an LLM-judge harness` |
| `docs/module/MODULE_REFERENCE.md:125` | mermaid node `aegis.evals<br/>RAGAS-style + LLM-judge` |
| `docs/teaching/evals.md:100-102` | *"not a dependency on the actual RAGAS package"* |

## F-5. DeepEval — **clean**  🟢 `[MEASURED]`

`deepeval` is genuinely **not installed** (`importlib.metadata.version('deepeval')` →
`PackageNotFoundError`), and I found **no claim anywhere in the repo that it is**. Every
surviving mention is either "the DeepEval *pattern*" (accurate — a native pytest-native gate),
or an explicit statement that the package is not a dependency (which remains true for
deepeval). The plan wanted `README.md:93` rewritten to *"ragas 0.4.3 + deepeval 4.2.0"*; not
shipping deepeval and not claiming it is the **right** call, and the README's actual wording
("Deterministic proxies offline, real `ragas` metrics live") is honest. **No finding.**

## F-6. Internal docstrings — acceptable  🔵 LOW

`aegis/src/aegis/evals/__init__.py:3-5`, `metrics.py:3`, `harness.py:133`, `regression.py:1,6`
still say "RAGAS-style … no heavy deps (no `ragas`/`deepeval`)". These describe
`aegis.evals` *core*, which genuinely imports neither — the isolation test proves it. They are
accurate about their subject; they simply no longer tell the whole story. Worth a one-line
"see `aegis.evals.libs` for the real thing" pointer, not a correctness finding.

---

# G — The UI

I could not drive the browser (the Claude-in-Chrome extension is not connected in this
environment), so G was exercised over the same HTTP path the browser uses — the Next dev
rewrite on `:3001` — plus source reading of `EvalsView.tsx`.

## G-1. The Receipt is dishonest  🔴 CRITICAL

**`web/src/components/evals/EvalsView.tsx:502`** — `<Receipt origin={live.source} />`, where
`live.source` is `"ragas, judged through the Aegis gateway (metered in usage_ledger)"`
(`routes.py:3993`).

**`web/src/components/evals/EvalsView.tsx:468`** —
> `needed="Press the button; the run is metered like any other call."`

Both are false as measured in A. The receipt is the strongest honesty device on the screen and
it is certifying the one thing that is not true.

## G-2. The card that "refuses to fake a number" now shows a tautological 1.000  🟠 HIGH

`ragas_suite.py:91-96` sets the answer under test to the retrieved context itself:

```python
answer = " ".join(contexts)[:800]
```

The code comment is candid about this ("this measures whether the METRICS work end to end
against real content, not whether some particular generator is good"). **The UI is not.**
Faithfulness of a context against itself is 1.0 by construction, and it measured 1.0 on every
one of my five runs `[MEASURED]`. The card renders:

```
ragas · answer relevancy
Scored by ragas
  ragas:faithfulness       1.000
  ragas:answer_relevancy   0.543
  [Receipt: ragas, judged through the Aegis gateway (metered in usage_ledger)]
```

with no statement anywhere on the panel that the "answer" is the context. A juror reads
"faithfulness 1.000" as "this platform's answers are perfectly grounded". That is a stronger
claim than the empty cell this card replaced, and it is not supported.

Note the card's own copy at `EvalsView.tsx:465-470` is a well-written argument about *not*
filling a cell with an undefendable number. It then fills it with one.

**Fix:** state it on the panel — *"scored against the retrieved context as the answer; a
generated answer is the next increment"* — or score a real generated answer. The second is the
honest version of the claim the card is making.

## G-3. The button 500s at the dev proxy's 30-second ceiling  🟠 HIGH `[MEASURED]`

`web/next.config.mjs:70-78` proxies `/v1/:path*` to `127.0.0.1:8110`. Next's rewrite proxy caps
at 30 s (`experimental.proxyTimeout`, default 30 000 ms). Two consecutive attempts through the
console's own origin, against a cold backend:

```
$ curl -X POST "http://127.0.0.1:3001/v1/evals/live-run?limit=2"   → HTTP 500  t=30.007s
$ curl -X POST "http://127.0.0.1:3001/v1/evals/live-run?limit=1"   → HTTP 500  t=30.016s
$ curl     "http://127.0.0.1:3001/v1/evals/report"                 → HTTP 200  t= 8.029s   (control)
```

and, once warm:

```
$ curl -X POST "http://127.0.0.1:3001/v1/evals/live-run?limit=1"   → HTTP 200  t=15.035s
```

Measured backend-side durations for the same route span **14 s → 32 s → 134 s** depending on
provider latency and load. So the button is a coin-flip on the first press after a restart —
i.e. the demo scenario — and `limit=6` (~3× the `limit=2` work) would essentially always
exceed the ceiling. When it trips, `runLive` (`EvalsView.tsx:297-308`) shows
`errorSentence(error, 'The live evaluation could not run.')` — while **the backend keeps
running and keeps spending**, unrecorded.

**Fix:** either set `experimental.proxyTimeout` above the route's worst case in
`next.config.mjs`, or — better — make the route return a job id and let the panel poll, which
also gives the progress state G-4 is missing. A money-spending request should not be a
synchronous 30-second hold in the first place.

## G-4. Loading state is a disabled button and nothing else  🟡 MEDIUM `[SOURCE]`

`EvalsView.tsx:491-498`: `disabled={scoring}` and the label flips to `Judging…`. For a
14-134 second wait there is no spinner, no elapsed counter, no per-case progress, and no
statement of what it is going to cost before it is pressed. `web/src/lib/api/client.ts:116`
sets no `AbortSignal`, so a user who navigates away leaves the spend running.

The card copy says "asking costs model calls" but never says *how many* or *how much*. The
route knows both (6 cases × 9 calls). **Fix:** state the cost on the button
("Score 2 cases · ~18 model calls"), and add elapsed feedback.

## G-5. Error handling drops the previous result  🔵 LOW `[SOURCE]`

`EvalsView.tsx:302-304`: on error `setLive(null)`, so a failed *re-*score wipes a successful
earlier one and the card reverts to "One cell left empty" — which now reads as a *policy*
statement ("the platform refuses to fake it") rather than a failure. Keep the last good result
and show the error beside it.

## G-6. `metricGloss` default — **claim holds**  🟢

The plan worried that `EvalsView.tsx:97`'s default gloss (`'Deterministic overlap metric — no
LLM.'`) would be applied to LLM-judged metrics. It is not: the live card renders `m.name` /
`m.value` directly (`:474-487`) and never calls `metricGloss`, which is used only on the
offline report metrics, which are deterministic. No finding.

## G-7. Batch embedding is not implemented — N round trips  🔵 LOW `[MEASURED]`

`gateway_adapters.py:136-141` implements `aembed_text` / `embed_text` but not `aembed_texts`.
ragas's base class falls back to per-text calls (`ragas/embeddings/base.py:92-98`, *"Default
implementation processes texts concurrently"*), so `AnswerRelevancy` makes **4 separate
embedding calls per case** where `aegis.gateway.embed` accepts a list and would do it in one —
4× the round trips, 4× the ledger rows, 4× the limiter slots. Overriding `aembed_texts` to call
`embed(list(texts))` is three lines.

## G-8. `gold_doc_ids` is a `frozenset` — latent non-determinism  🔵 LOW `[SOURCE]`

`ragas_suite.py:88` iterates `case.gold_doc_ids`, a `frozenset[str]`
(`aegis/src/aegis/evals/corpus.py:40`). Iteration order over a string frozenset varies with
`PYTHONHASHSEED`, so the concatenated `answer` — and therefore the score — would differ between
processes for any case with 2+ gold docs. All six seed cases currently have exactly one, so it
is latent. `sorted(case.gold_doc_ids)` closes it.

---

# Summary of findings

| # | Severity | Location | Finding |
|---|---|---|---|
| A | 🔴 CRITICAL | `backend/src/app/api/routes.py:3953-3994` | No governance context bound: 0 ledger rows, no attribution, no budget check. The headline claim is false. |
| B-1 | 🔴 CRITICAL | same | A budget cap cannot refuse this route at all |
| F-1 | 🔴 CRITICAL | `web/src/components/landing/stackClaims.ts:108-112` | Landing claim points at a file that contradicts it; wrong `mark`; asserts the false metering claim |
| G-1 | 🔴 CRITICAL | `EvalsView.tsx:468,502`, `routes.py:3993` | The Receipt certifies "metered in usage_ledger" — measured false |
| B-2 | 🟠 HIGH | `ragas_suite.py:115-116`, `gateway_adapters.py:23-25` | Budget refusal mislabelled "the judge was unavailable"; docstring's "allowed to propagate" is not what happens |
| C-3 | 🟠 HIGH | `ragas_suite.py:107,122` | ragas NaN counted as a case, poisons the mean, renders as a blank cell with no note |
| C-4 | 🟠 HIGH | `ragas_suite.py:107` | ragas's own `0.0`-on-judge-failure passed through as a measured score |
| D-1 | 🟠 HIGH | `routes.py:3953`, `main.py:778` | ~54 calls / ~$0.044 per press, no rate limit, no cap, no record. 3 concurrent presses measured to succeed |
| E-2 | 🟠 HIGH | `backend/uv.lock` | Never regenerated; `ragas`/`langchain-community` absent from the lock |
| F-2 | 🟠 HIGH | `capabilities.py:151`, `main.py:161` | Live `/platform/capabilities` + OpenAPI still advertise "RAGAS-style proxies" |
| F-3 | 🟠 HIGH | `docs/architecture/eval-strategy.md:11` | "`ragas` … **not** dependencies of this repo" — now false |
| G-2 | 🟠 HIGH | `ragas_suite.py:91-96`, `EvalsView.tsx:460-487` | Faithfulness 1.000 is tautological (answer = context); UI states no caveat |
| G-3 | 🟠 HIGH | `next.config.mjs:70-78` | 500 at the 30 s proxy ceiling; measured 2/3 cold attempts fail, spend continues |
| C-1 | 🟡 MEDIUM | `limiter.py:111`, `ragas_suite.py:115` | `SlotUnavailableError` mislabelled as a judge outage |
| C-2 | 🟡 MEDIUM | `gateway_adapters.py:114,141` | `asyncio.run` bridge raises `RuntimeError` in-loop, swallowed as an outage |
| E-3 | 🟡 MEDIUM | backend venv | `rich` 15.0.0 → 14.3.4 (major downgrade), `langchain-core` 1.5.4 → 1.6.0; unrecorded |
| F-4 | 🟡 MEDIUM | 4 docs files | Still assert ragas is not a dependency |
| G-4 | 🟡 MEDIUM | `EvalsView.tsx:491-498` | No progress state and no stated cost for a 14-134 s spend |
| D-3 | 🔵 LOW | `routes.py:3955` | `limit` unconstrained in OpenAPI; silent clamp |
| E-4 | 🔵 LOW | `aegis/.venv` | Missing `sqlalchemy`; the aegis suite must run from `backend/.venv` |
| F-6 | 🔵 LOW | `aegis/src/aegis/evals/*` | Internal docstrings accurate but no longer the whole story |
| G-5 | 🔵 LOW | `EvalsView.tsx:302-304` | A failed re-score wipes a good result |
| G-7 | 🔵 LOW | `gateway_adapters.py:136-141` | `aembed_texts` not overridden — 4× embedding round trips per case |
| G-8 | 🔵 LOW | `ragas_suite.py:88` | `frozenset` iteration order — latent non-determinism |
| — | 🟢 PASS | `test_isolation.py` | Claim 4 holds: `aegis.evals` imports no ragas; importing `evals.libs` does not either |
| — | 🟢 PASS | `routes.py:3997-4015` | Claim D holds: `/evals/report` is separate and memoised; a poll cannot spend |
| — | 🟢 PASS | backend/aegis/web suites | 2196 / 2406 / 406 pass; `tsc` clean; no ML or ingestion regression |
| — | 🟢 PASS | F-5 | DeepEval is not installed and nothing claims it is |

---

## What has to happen before this phase can pass

1. **Bind the governance context in `evals_live_run`** (A / B-1), and add the test that would
   have caught its absence: a live-run must write one ledger row per model call, carrying the
   caller's `tenant_id` and `user_id`.
2. **Until (1) lands, remove the metering sentence** from `routes.py:3993`,
   `EvalsView.tsx:468`, `web/src/lib/api/types.ts:159` and `stackClaims.ts:110`. A claim that is
   measured false must not be on the screen while it is being fixed.
3. **Catch `BudgetExceededError` by name** and give `LiveMetric` a per-metric note (B-2), and
   guard non-finite values (C-3).
4. **Fix the `stackClaims.ts` row** — two rows, correct paths (F-1) — and retire the live
   manifest claim in `capabilities.py:151` / `main.py:161` (F-2) and the false sentence in
   `eval-strategy.md:11` (F-3).
5. **Say on the panel what the "answer" is** (G-2). Faithfulness 1.000 against itself is the
   most misreadable number on the screen.
6. **`uv lock`** (E-2).
