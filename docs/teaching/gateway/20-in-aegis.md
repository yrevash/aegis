# The Gateway — our exact implementation

Every path below is real. Line numbers are from the tree at the time of writing; the
function names are the stable part.

The package lives at `aegis/src/aegis/gateway/`:

| File | Lines | What it owns |
|---|---|---|
| `__init__.py` | 64 | The public surface |
| `llm.py` | 1262 | `complete` / `embed` / `transcribe`, the hooks, the tally |
| `routing.py` | 246 | Role→model map, small-model rules, cost + billing units |
| `types.py` | 146 | Result types and `BudgetExceededError` — no heavy deps |
| `stream.py` | 78 | The optional AG-UI `model_call` event wrapper |

---

## How you import it

```python
from aegis.gateway import complete, configure
from aegis.core.models import ModelRole

configure(config=my_config)                       # or rely on GATEWAY_* env vars
result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
```

That usage example is the module docstring at `aegis/src/aegis/gateway/__init__.py:15-21`.

`litellm` is imported **lazily**, inside `_litellm()` at `llm.py:656`, so
`import aegis.gateway` never requires it. `types.py` imports only pydantic and stdlib
(`types.py:1-12`) precisely so a caller that wants the *shapes* does not pull the
LiteLLM dependency chain.

The full export list is `__init__.py:47-64`: `complete`, `configure`, `embed`,
`transcribe`, `record_call`, `usage_tally`, `optimization_config`,
`optimization_summary`, `last_trace_id`, plus the types `LLMResult`, `Usage`,
`CostSource`, `ToolCallResult`, `TranscriptionResult`, `TranscriptionSegment` and
`BudgetExceededError`.

---

## The types (`types.py`)

**`BudgetExceededError`** — `types.py:25`. Not a plain exception: it carries `scope`
(`"tenant"` / `"user"`), `scope_id`, `limit_type` (`"token_cap"` / `"usd_cap"` /
`"rpm"` / `"tpm"`), `limit` and `used` (`types.py:42-61`). The host builds a terminal
"budget exceeded" wire event straight from those fields.

**`CostSource`** — `types.py:74`, a `StrEnum` with three values (`types.py:82-84`):

```python
PROVIDER  = "provider"   # the provider's own cost map priced the call
ESTIMATED = "estimated"  # priced from measured units × the configured rate
UNPRICED  = "unpriced"   # billable units consumed but no rate/unit count known
```

This is the mechanism that stops a `$0` being ambiguous.

**`Usage`** — `types.py:87`. Carries `prompt_tokens`, `completion_tokens`, `cost_usd`,
**`audio_seconds`** (`:99`), **`images`** (`:103`) and `cost_source` (`:105`). Every
field defaults to zero, so a token-only caller is unaffected by the non-token fields
existing.

**`LLMResult`** — `types.py:111`: `content`, `tool_calls`, `usage`, `model`. Note
`model` is the deployment that *actually responded*, which is how a fired fallback is
detectable.

**`TranscriptionResult`** — `types.py:129`: `text`, `language`, `duration_seconds`,
`segments`, `usage`, `model`. The docstring at `:131-135` is explicit that
`segments`/`language`/`duration_seconds` are populated **only when the provider reports
them** — they stay empty rather than being invented.

---

## The three injected hooks

Defined as `Protocol`s in `llm.py`:

**`GatewayConfig`** — `llm.py:105`. Fields: `base_url`, `api_key`, `ssl_verify`,
`max_output_tokens`, `timeout_seconds`, `budget_fail_open`. The standalone default
`_EnvGatewayConfig` (`llm.py:117`) reads `GATEWAY_*` environment variables.

**`GovernanceHook`** — `llm.py:142`. Three methods:

- `get_context()` (`:150`) — returns the governed context or `None`. **`None` means
  ungoverned**: enforcement and ledgering are skipped entirely for that call.
- `enforce(ctx)` (`:158`) — raises `BudgetExceededError` if over any cap. The docstring
  says it plainly: *"Called BEFORE spend."*
- `record(ctx, *, model, prompt_tokens, completion_tokens, cost_usd, trace_id,
  audio_seconds=0.0, images=0)` (`:166`) — one durable ledger row.

**`ObservabilitySink`** — `llm.py:193`: `span(operation, model, *, temperature,
max_tokens)`, `set_usage(span, ...)`, `trace_id()`.

Both defaults are explicit no-ops — `_NoOpGovernance` at `llm.py:224` and
`_NoOpObservability` at `llm.py:240`. Read `_NoOpGovernance`'s docstring: *"no
enforcement, no ledger — a clean no-op."* Standalone `aegis.gateway` does **not**
pretend to enforce budgets.

`configure(...)` at `llm.py:269` wires them, plus two optimisation knobs: `fallbacks`
and `baseline_role`. Any argument left `None` keeps the current binding
(`llm.py:299-308`).

---

## Routing (`routing.py`)

**The table** — `_DEFAULT_ROUTING` at `routing.py:34-41`:

```python
CHEAP:      "genailab-maas-gpt-4o-mini"
REASONING:  "genailab-maas-Phi-4-reasoning"
GENERATION: "genailab-maas-gpt-4o"
EMBEDDING:  "genailab-maas-text-embedding-3-large"
VISION:     "genailab-maas-Llama-3.2-90B-Vision-Instruct"
VOICE:      "genailab-maas-whisper"
```

`model_for(role)` (`routing.py:44`) returns the id, with a `MODEL_<ROLE>` env override
per role. `routing_table()` (`:49`) returns the whole effective map for the dashboard.

**Small-model classification** — `is_small_model(model_id)` at `routing.py:100`.
Markers at `:81` are `("mini", "3.5", "3-5", "llama-3.2", "phi-3.5")`, but a parameter
count spelled in the id **vetoes** them: `_SMALL_MODEL_MAX_PARAM_B = 10.0` (`:90`) and
`_PARAM_COUNT_RE` (`:91`) extract e.g. `90B`, and anything ≥ 10B is not small. The
comment at `:85-89` records exactly why (see [`30-deep-dive.md`](30-deep-dive.md)).

**Routable roles** — `_ROUTABLE_ROLES` at `routing.py:118` is
`{CHEAP, GENERATION, REASONING, VISION}`. `is_routable_role(role)` (`:123`) gates the
`small_model_share` denominator. Embeddings and transcriptions have exactly one
deployment each; they were never routed.

**Cost and billing units.** `_COST_PER_1K` at `routing.py:142-149` gives per-role
`(input_rate, output_rate)`. Read the `VOICE` entry: `(0.006, 0.0)` — six-tenths of a
cent per **audio minute**, zero output-token rate, because Whisper produces no billable
output tokens.

`BillingUnit` (`routing.py:152`) is `TOKENS` / `AUDIO_MINUTES` / `IMAGES`.
`_BILLING_UNIT` (`:173`) maps only `VOICE → AUDIO_MINUTES`; everything absent bills per
token. `VISION` deliberately stays on tokens (the comment at `:170-172` explains: the
fleet's vision deployment charges image content as input *tokens*), but the image
**count** still flows end to end and becomes billable the moment `COST_VISION_UNIT=images`
is set.

`billable_input_units(...)` (`routing.py:207`) converts a call into its own unit;
`unit_cost(...)` (`:227`) is `units_in × rate_in + completion_tokens/1000 × rate_out`.
For a token-only call this reduces exactly to the original per-1k formula.

---

## `complete` — the chat path (`llm.py:835`)

Signature: `complete(role, messages, *, tools=None, temperature=0.0,
response_format=None, max_tokens=None) -> LLMResult`.

The order of operations, with line numbers:

1. **`llm.py:871-874` — governance first.** `gov_ctx = _governance.get_context()`, and
   if it is not `None`, `await _governance.enforce(gov_ctx)`. The comment says *"Budget/
   rate check BEFORE spend — refuse the call if over any cap."* Nothing below this line
   runs if the cap is breached.
2. **`llm.py:881` — `images = count_images(messages)`.** `count_images` is at
   `llm.py:815`; it walks list-shaped message content for parts whose `type` is in
   `_IMAGE_PART_TYPES` (`llm.py:812`: `image_url`, `image`, `input_image`). Text-only
   messages always return `0`.
3. **`llm.py:885-887` — output cap.** Explicit `max_tokens` wins, else
   `config.max_output_tokens`, so no generation is unbounded.
4. **`llm.py:905-907` — fallbacks.** `_effective_fallbacks().get(role, [])` mapped
   through `_provider_model`. The default chains are at `llm.py:327-331`:
   `GENERATION → [REASONING, CHEAP]`, `REASONING → [GENERATION, CHEAP]`,
   `CHEAP → [GENERATION]`.
5. **`llm.py:912-916` — the outer ceiling.** `per_call_timeout × (len(fallbacks) + 1)`,
   so each attempt keeps its own budget and the whole await still returns.
   `_bounded_acompletion` (`llm.py:379`) applies it via `asyncio.wait_for`.
6. **`llm.py:918-923` — the span** opens around everything that follows.
7. **`llm.py:925-974` — `_account(response)`**, run per real attempt. It reads token
   counts, calls `_resolve_cost`, calls `record_call`, calls
   `_observability.set_usage`, and awaits `_record_usage` (the ledger write).
8. **`llm.py:983-1001` — the one corrective re-ask.** Fires only when JSON was
   requested (`_wants_json`, `llm.py:360`), the reply is not valid JSON
   (`_is_valid_json`, `:367`), **and** there are no tool calls — a tool-call reply has
   empty content by design and must not trigger it.

Raises `BudgetExceededError` when the injected hook refuses (`llm.py:868-869`).

---

## `embed` — `llm.py:1011`

Same shape: enforce first (`:1023-1025`), span, `asyncio.wait_for` with `timeout + 5.0`
as the hard backstop (`:1037-1040`).

The load-bearing lines are `:1043-1058`. The comments there are a bug postmortem left
in the source:

> *"The configured embedding deployment is a custom gateway id that is NOT in LiteLLM's
> cost map, so `completion_cost` returns 0 for every embedding. Without the same
> estimate fallback `complete` uses, every embedding row ledgered $0.00 — embeddings
> never counted against a USD cap."*

and

> *"...and they were invisible to `usage_tally` because `record_call` was never invoked
> for them. It is now (as a non-routable role, so the small-model-share denominator is
> untouched)."*

---

## `transcribe` — `llm.py:1126`

`transcribe(audio, *, language=None, prompt=None, response_format="verbose_json",
duration_seconds=None) -> TranscriptionResult`.

Differences from `complete`, all forced by the modality:

- LiteLLM's transcription API takes a **file handle**, not `messages` (`llm.py:1179`).
  `_audio_handle` (`llm.py:1080`) accepts either an open handle (left alone — whoever
  opened it owns closing it) or a path (opened and closed here).
- `response_format` defaults to `"verbose_json"` because that is what carries
  `duration` — **the billing unit** (`llm.py:1152-1154`).
- The billable seconds come from the provider's own `duration` when reported, else the
  caller's `duration_seconds` (`llm.py:1197-1199`). If neither exists, a WARNING is
  logged (`:1205-1210`) and `_resolve_cost` is called with `billable_work=True`
  (`:1221`) so the result is tagged `UNPRICED`, not "free".

---

## Cost resolution — `_resolve_cost` at `llm.py:694`

Returns `(cost, CostSource)`. The order:

1. `_safe_cost(litellm, response)` (`llm.py:685`) — the provider's cost map. `> 0` →
   `PROVIDER` (`:719-721`).
2. `_estimate_cost(role, ...)` (`llm.py:398`, delegating to `routing.unit_cost`). `> 0`
   → `ESTIMATED` (`:723-731`).
3. Otherwise: if anything billable happened (`:733-738`), log a WARNING naming the role,
   the model and every measured unit, and return `(0.0, UNPRICED)` (`:739-753`).
4. Only when genuinely nothing billable happened is it an unambiguous zero (`:754-755`).

---

## The tally and the savings metric

`_UsageTally` (`llm.py:441`) is a process-global dataclass holding `total_calls`,
`routable_calls`, `small_calls`, `total_cost_usd`, `baseline_cost_usd`,
`total_audio_seconds`, `total_images` and a per-role `by_role` breakdown
(`_RoleAgg`, `llm.py:429`).

`record_call(...)` (`llm.py:524`) folds one call in. Note `llm.py:556-558`: only a
**routable** role increments `routable_calls` and `small_calls`.

`_baseline_cost(...)` (`llm.py:481`) prices the same work at the frontier baseline role.
For non-token work it returns `max(token_baseline, cost_usd)` (`:502`) — a frontier
*chat* model cannot transcribe audio, so the honest baseline is the call's own cost,
which books a **zero** saving rather than a fabricated negative one.

`call_saving_usd(usage)` (`llm.py:505`) computes one call's saving **from that call's
own `Usage` alone** — see [`30-deep-dive.md`](30-deep-dive.md) for why that matters.

`usage_tally()` (`llm.py:578`) returns the live dict; `small_model_share` is
`small_calls / routable_calls`, or `None` before any routable call (`:594`) — never a
fake zero.

`optimization_summary()` (`llm.py:600`) takes the top-line figures **verbatim** from
`usage_tally()` (`:614`) — one source of truth, never recomputed — and adds the per-role
breakdown and the baseline model. Its docstring (`:609-612`) is explicit that
cache-hit savings are deliberately excluded: a cache hit skips the model entirely and
never reaches this ledger, so counting it here would be double-counting.

`optimization_config()` (`llm.py:635`) returns the live routing table, fallback chains,
timeout, output cap and baseline role for the dashboard.

---

## Streaming — `stream.py:30`

`stream_complete(role, messages, emitter, **kwargs)` wraps `complete` in a
`STEP_STARTED`/`STEP_FINISHED` bracket of span kind `LLM` (`stream.py:48`) and emits one
`MODEL_CALL` custom event (`:64-77`) carrying `model`, `role`, `primary_model`,
`fallback_fired`, token counts, `cost_usd`, `cost_saved_usd` and `small_model`.

`fallback_fired` (`stream.py:70`) is `result.model != model_for(role)` — measured from
the deployment that actually responded, not guessed.

Callers **opt in**. The gateway itself never streams, because `complete`/`embed` are
also called by non-agentic code (the eval harness, for one) — see the module docstring
at `stream.py:8-11`.

---

## How the backend composes it

The composition root is `backend/src/app/core/llm.py` — a **strangler shim**. Its whole
job is at `backend/src/app/core/llm.py:216-220`:

```python
gateway.configure(
    config=_SettingsGatewayConfig(),
    governance=_GovernanceHook(),
    observability=OtelObservabilitySink(),
)
```

run once, at import time, then the public surface is re-exported (`:38-52`) so every
existing call site — `app.agent.deps`, `app.retrieval.gateway`, guardrails, memory,
ops/eval — keeps working unchanged.

**`_SettingsGatewayConfig`** (`core/llm.py:77`) is a class of `@property`s, each reading
`get_settings()` *fresh on every access* (`:80-83`). Not a snapshot — so a test that
mutates the settings singleton is honoured on the next call.

**`_GovernanceHook`** (`core/llm.py:129`):

- `get_context()` (`:132`) returns `_governed(get_governance_context())`. `_governed`
  (`:117`) returns `None` unless a tenant is bound — an unscoped request skips the
  database entirely.
- `enforce()` (`:136`) calls `app.data.governance.enforce_governance`. A real
  `BudgetExceededError` propagates (`:156-157`). Any *other* exception —- a database
  blip — **fails closed** by default (`:165-173`), raising a `BudgetExceededError` with
  `limit_type="enforcement_error"`. `budget_fail_open` opts into the other behaviour
  and logs a warning when it does (`:159-164`).
- `record()` (`:175`) forwards to `app.data.governance.record_usage`, including
  `audio_seconds` and `images`.

**Observability** is the standalone `aegis.observability.OtelObservabilitySink`
(imported at `core/llm.py:53`) — no bespoke adapter of the app's own.

### Where the governance context comes from

`backend/src/app/api/routes.py:454` — `_resolve_governance(auth)` builds a
`GovernanceContext` from the authenticated principal, calling
`effective_limits(tenant_id, user_id)` (`:462`). An unscoped principal yields an empty
context, so the chokepoint enforces nothing.

It is bound inside the streaming task, not around it —
`routes.py:920` `token = set_governance_context(governance)` with
`reset_governance_context(token)` in a `finally` at `:934`. That placement matters:
the SSE generator runs in its own task context, so binding outside it would not be
visible at the chokepoint.

---

## Where the numbers surface

- `GET /savings` → `backend/src/app/platform/savings.py:37` `build_savings()` reads
  `usage_tally()` and reports `baseline`, `actual`, `saved`. Its module docstring
  (`savings.py:10-17`) states the honesty rule: cache savings are shown at `$0` **in
  this figure** with an explanation, rather than invented and double-counted.
- `GET /metrics` → `routes.py:1018`.
- The routing/optimisation view → `routes.py:2499` imports `optimization_config` and
  `optimization_summary` directly.

**Next:** [`30-deep-dive.md`](30-deep-dive.md) — the real bugs, told as stories.
