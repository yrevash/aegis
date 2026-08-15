# Core — in Aegis

`aegis.core` is deliberately boring: pydantic models, `typing.Protocol` interfaces, a
tiny registry, stdlib config, health probes, one lazy-import helper, and the AG-UI
emitter.

Its own docstring (`aegis/src/aegis/core/__init__.py:1-7`):

> Holds the shared interfaces, data types, registry, config, health probes and the
> lazy-import helper every Aegis component depends on. **This package imports nothing
> internal and pulls in no heavy dependency**, so any component that depends only on it
> stays cheap to install.

Eleven files, 1,062 lines total.

---

## How you import it

```python
from aegis.core import (
    ChatCompleter, Guardrail,          # structural interfaces
    GuardResult, GuardVerdict, RiskLevel, RunStatus,
    CoreSettings, AegisMode,           # config
    require,                           # fail-loud optional imports
    register, get, available,          # the registry
)
from aegis.core.stream import AegisEmitter
from aegis.core import stream_names
from aegis.core.models import ModelRole
```

The full `__all__` is `aegis/src/aegis/core/__init__.py:34-57`.

Note that `stream`, `stream_names`, `models` and `health` are **not** re-exported at
package level — you import them by path. That is deliberate: `stream.py` imports
`ag_ui`, and pulling it into `__init__` would make the AG-UI dependency mandatory for
anyone importing a type.

---

## 1. The boundary invariant, as a test

`aegis/tests/core/test_core_is_dep_free.py:15-45` is the rule made executable:

```python
code = (
    "import sys; import aegis.core; import aegis.core.stream; "
    "banned = {'litellm','torch','langgraph','xgboost','fastapi','redis',"
    "'nemoguardrails','sqlalchemy','jwt','argon2','opentelemetry'}; "
    "hit = banned & set(sys.modules); assert not hit, hit"
)
proc = subprocess.run([sys.executable, "-c", code], ...,
                      env={**os.environ, "PYTHONPATH": _SRC})
```

Three properties worth noting.

**It runs in a subprocess.** The docstring says why (`:3-4`): *"to ensure `sys.modules`
isn't polluted by other tests."* Another test in the same process may already have
imported `sqlalchemy`, and the guard would pass by accident.

**It sets `PYTHONPATH` to the source tree** (`:26-27`): *"so the guard tests the real
import graph deterministically, independent of editable-install state."*

**It imports `aegis.core.stream` too**, not just `aegis.core` — because `stream.py` is the
one file with a third-party import (`ag_ui`), and it must not drag anything else in.

The banned list is documented per-module (`:19-24`): `sqlalchemy`/`jwt`/`argon2` live in
`aegis.data` / `aegis.governance`; `opentelemetry` lives in `aegis.observability` — *"never
in `aegis.core`, which stays pydantic-only."*

The actual dependency declaration, `aegis/pyproject.toml`:

```toml
dependencies = ["pydantic>=2.9", "pydantic-settings>=2.6", "ag-ui-protocol~=0.1.19"]
```

Three base dependencies for the whole package. Everything else is an extra.

---

## 2. The lazy-dependency mechanism — `aegis/src/aegis/core/lazy.py`

Thirty-two lines, and the single sanctioned way to reach an optional dependency
(`:1-6`):

> A missing module raises an `ImportError` naming the exact `pip install` command — never
> a silent `except ImportError: pass`.

```python
def require(extra: str, module: str) -> ModuleType:
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(
            f"This feature needs '{module}'. Run: pip install {extra}"
        ) from exc
```
`:14-32`

Two details doing real work:

**`from exc`** preserves the chain. If `presidio_image_redactor` is installed but *its*
`pytesseract` is not, the traceback shows the real missing module underneath rather than a
confusing message about the top-level package.

**The message carries the command**, not a category. `pip install aegis[forecast]` is
paste-able; "feature unavailable" is a support ticket.

The test (`aegis/tests/core/test_lazy.py:9-14`) asserts on the message content:

```python
with pytest.raises(ImportError) as ei:
    require("aegis[nemo]", "definitely_not_a_real_module_xyz")
assert "pip install aegis[nemo]" in str(ei.value)
```

### Where it is used, and the placement rule

Every call site is **inside a function body**, so the module stays importable without the
extra:

| Caller | Extra | Line |
|---|---|---|
| `aegis.forecast.engine` | `aegis[forecast]` | `:175`, `:199`, `:237`, `:416`, `:473` |
| `aegis.guardrails.media.image_pii` | `aegis[media]` | `:64`, `:69`, `:74` |
| `aegis.vision.pii` | `aegis[media]` | `:106` |
| `aegis.core.health` | `aegis[redis]`, `aegis[postgres]`, `aegis[retrieval]` | `:65`, `:92`, `:122` |

And the layer above it: `aegis/src/aegis/forecast/__init__.py:112-138` is a wrapper whose
only job is to defer the `engine` import into the function body, so importing
`aegis.forecast` costs nothing.

**One honest gap.** `aegis[media]` is named at four call sites and is **not declared** in
`aegis/pyproject.toml`'s `[project.optional-dependencies]`. The failure is still loud and
still fails closed — which is the security-relevant half — but that install command would
not resolve today.

---

## 3. Typed protocols as seams — `aegis/src/aegis/core/interfaces.py`

Thirty-seven lines. The docstring (`:1-7`):

> The core never imports a concrete implementation; components satisfy these Protocols
> structurally. `ChatCompleter` is how guardrails stays LLM-agnostic: callers inject any
> async completion function (LiteLLM, OpenAI, a local stub) rather than the package
> hard-wiring a provider.

```python
@runtime_checkable
class ChatCompleter(Protocol):
    async def __call__(self, messages: list[dict], *,
                       response_format: dict | None = None) -> str: ...
```
`:16-24`

```python
@runtime_checkable
class Guardrail(Protocol):
    async def check_input(self, text: str) -> GuardResult: ...
    async def check_output(self, text: str) -> GuardResult: ...
```
`:27-37`

Two consequences you can point at in the tree:

**A test fake is three lines.** From `aegis/tests/guardrails/test_custom_rails.py:39-43`:

```python
async def _c(messages, *, response_format=None):
    return '{"injection": false, "unsafe": false}'
```

No mocking library, no API key, no subclassing.

**The same seam pattern repeats in every leaf**, sometimes as a bespoke Protocol when
`ChatCompleter` is the wrong shape:

| Seam | Where | Why not `ChatCompleter` |
|---|---|---|
| `VisionAnalyst` | `aegis/src/aegis/vision/analyst.py:39-51` | Must return **usage** as well as text (`:9-13`) |
| `TranscribeCallable` | `aegis/src/aegis/voice/transcribe.py:79-97` | Takes a **file handle**, not messages |
| `Transcriber` | `aegis/src/aegis/guardrails/media/audio.py:36` | `AudioPayload -> str`, sync or async |
| `Rail` | `aegis/src/aegis/guardrails/pipeline.py:96` | A plain `Callable`; nothing keyword-only |

That last row is the counterexample worth having: not every seam needs a Protocol. A rail
is one function with one positional argument, so `Callable` says everything.

---

## 4. Shared types — `aegis/src/aegis/core/types.py`

The docstring (`:1-8`) records the migration:

> Moved out of the legacy `app.api.schemas` / `app.guardrails.models` so any component
> can import them without pulling in the API layer.

That sentence is the boundary invariant in action. These types started in the host's API
layer, which meant importing a verdict enum meant importing FastAPI.

The enums, each with a note on why it lives here rather than in one consumer:

- **`GuardVerdict`** (`:16-22`) — `PASS`/`BLOCK`/`REDACT`/`FLAG`. `FLAG` is a non-blocking
  advisory (`:5-7`).
- **`RiskLevel`** (`:25-35`) — `LOW`/`MEDIUM`/`HIGH`. *"the approvals ORM row and the
  agent's human-gate logic both key on it, so it lives in `aegis.core.types`."*
- **`RunStatus`** (`:38-49`) — *"Lives here so the agent core never imports the host's
  `app.api.schemas`."*
- **`GuardStage`** (`:52-56`), **`ApprovalDecision`** (`:59-63`).

The models: `PIIMatch` (`:66-72`), `InjectionVerdict` (`:75-79`), `FormatCheck`
(`:82-86`), and `GuardResult` (`:89-102`) — whose `redactions` field is documented
*"Detector kinds redacted (**kinds only — never raw PII values**)"* (`:99-102`).

That comment is a cross-module invariant enforced at the type: `_entity_names` in
`aegis/src/aegis/guardrails/media/image_pii.py:79-90` implements exactly the same rule for
images.

**`ModelRole`** lives separately in `aegis/src/aegis/core/models.py:17-25` — `CHEAP`,
`REASONING`, `GENERATION`, `EMBEDDING`, `VISION`, `VOICE`. The docstring (`:1-8`) is
explicit about the split:

> Code should request a model by **role**, never by a hard-coded id… This module
> intentionally contains *only* the enum — no litellm, no routing table, no env reads — so
> every light module can depend on it without pulling in anything heavy.

The enum is in core; the routing table (`aegis/src/aegis/gateway/routing.py:40`) is in
the gateway. That is the split that lets `aegis.voice` name `ModelRole.VOICE` in a
docstring without importing the gateway.

---

## 5. The event contract — `aegis/src/aegis/core/events.py`

`SpanKind` (`:17-28`) is the OpenInference vocabulary: `LLM`, `EMBEDDING`, `RETRIEVER`,
`RERANKER`, `TOOL`, `GUARDRAIL`, `AGENT`, `CHAIN`, `EVALUATOR`.

`_BaseEvent` (`:31-38`) carries `module_id`, `step_id` (*"Correlates start/data/finish for
one step"*), `span_kind`, `trace_id`, `parent_span_id`.

Then `StepStarted` (`:41-45`), `StepFinished` (`:48-54`), `GuardrailEvent` (`:57-66`), and
the union `AegisEvent` (`:69`).

The module docstring (`:1-7`) states the two-consumer design:

> each stamped with an OpenInference `SpanKind` so the same stream renders live in the UI
> **and** exports as OTel/OpenInference spans. This file is the single source of truth the
> frontend mirrors.

---

## 6. The streaming spine — `aegis/src/aegis/core/stream.py`

The docstring (`:1-7`) is the "one way to emit" argument:

> Modules call ergonomic à la carte helpers; **the emitter owns the wire rules** (camelCase
> via the encoder, `data: …\n\n` framing, START→CONTENT→END bracketing, RUN_STARTED-first
> ordering). **No module constructs raw AG-UI events**, and no module is required to use
> every helper.

### `_StepScope` (`:37-94`) — bracketing that cannot be forgotten

```python
async def __aenter__(self):
    await self._em._emit(StepStartedEvent(..., raw_event=self._raw()))
    return self

async def __aexit__(self, *exc):
    await self._em._emit(StepFinishedEvent(..., raw_event=self._raw()))
```

`__aexit__` runs even if the body raises, so an unclosed step is structurally impossible.

And note the docstring at `:40-44` — **a real bug, recorded**:

> The step's OpenInference `span_kind` is carried on both frames via AG-UI's `raw_event`
> passthrough. **It used to be stored and never read** — inert at every call site, so the
> trace could not tell a RETRIEVER step from a GUARDRAIL one despite every caller
> declaring it.

### `AegisEmitter` (`:97-312`)

Bound to one run (`thread_id`, `run_id`, `sink`), with two open-id sets: `_open_text` and
`_open_tool` (`:112-113`).

| Method | Line | Note |
|---|---|---|
| `run_started` | `:123-131` | *"must be the first event of the run"* |
| `run_finished` / `run_error` | `:133-155` | Terminal |
| `step(name, span_kind)` | `:157-167` | Returns the async context manager |
| `reasoning` | `:169-182` | Live thinking deltas |
| `text_start` / `text_delta` / `text_end` | `:184-230` | Bracketed |
| `tool_start` / `tool_args` / `tool_end` / `tool_result` | `:232-297` | Bracketed |
| `custom(name, value)` | `:299-312` | Domain payloads |

**Protocol discipline is enforced, not documented.** `text_delta` (`:208-209`),
`text_end` (`:225-226`), `tool_args` (`:258-259`) and `tool_end` (`:277-278`) each raise
`RuntimeError` for an id that was never started:

```python
if message_id not in self._open_text:
    raise RuntimeError(f"text_delta for message {message_id!r} not started")
```

**Unknown event names raise** (`:309-311`):

```python
if not stream_names.is_known(name):
    msg = f"unknown CustomEvent name {name!r}; add it to aegis.core.stream_names"
    raise ValueError(msg)
```

An unregistered name would otherwise reach the frontend and be silently dropped — the
hardest bug class, because nothing happens and nothing complains.

### `stream_names.py` (`:1-68`)

The single source of truth. Twenty-two names, each with a docstring comment explaining
what it carries and — often — the honesty property it exists to preserve.

Two examples:

`GUARDRAIL_MEDIA` (`:13-16`): *"payload metadata plus the itemised list of which media
rails ran and which did not, so the console can never imply coverage a control did not
actually provide."*

`VISION_SCREEN` (`:24-27`): *"emitted the moment it decides and **BEFORE** the analysis
call — carrying `screened` so a fail-closed block is never rendered as 'we looked and it
was clean'."*

`ALL` (`:56-63`) and `is_known` (`:66-68`).

And `:3-5` records the client mirror: *"the console mirrors them in
`web/src/lib/streamNames.ts`."*

---

## 7. Fail-fast configuration — `aegis/src/aegis/core/config.py`

`AegisMode` (`:30-35`) — `full` / `lite` / `auto`. The docstring (`:1-8`):

> `full` (default) requires real Redis + Postgres + a usable on-disk vector store and
> **refuses to boot without them**; `lite` opts into in-memory/embedded implementations
> **loudly**; `auto` **actually probes** the configured backends and drops to lite only on
> a real, logged failure. **There is no silent fallback** — degradation is always a named,
> surfaced choice.

`CoreSettings` (`:38-130`) reads `AEGIS_`-prefixed env vars (`ENV_PREFIX`, `:27`).

`_missing_urls` (`:51-61`) returns the unset variables **named as an operator must set
them** — `AEGIS_REDIS_URL`, not `redis_url`. The comment at `:24-26` says why: error
messages should name the variable a human actually sets.

`require_full_infra` (`:63-88`):

- `lite` → return (`:73-74`)
- `auto` with missing URLs → **log at WARNING and continue** (`:78-84`), because *"'auto
  quietly became lite' is exactly the silent degradation this module exists to prevent"*
- `full` with missing URLs → **raise** (`:85-88`), naming the variables and the escape
  hatch

`resolve_mode` (`:90-130`) is `async` because probing is I/O, and the docstring at
`:10-12` names the trap:

> A host must therefore `await settings.resolve_mode()` at startup and use the returned
> mode; the raw `settings.mode` is the **declared** mode, not the resolved one.

The `auto` path (`:107-129`) probes all three backends and, on any failure, logs *which*
one and why before returning `lite`.

---

## 8. Health probes — `aegis/src/aegis/core/health.py`

`DependencyStatus` (`:43-48`) — `name`, `status: "up" | "down"`, `detail`.

Three probes: `probe_redis` (`:51-75`), `probe_postgres` (`:78-100`), `probe_vector_store`
(`:103-130`). Each accepts an **injected** client (tests pass a fake) or builds a real one
through `require`.

Every probe has `except Exception` returning `down` with the detail, and each carries the
comment *"a probe reports failure, never raises"* (`:69`, `:99`, `:126`). A health
endpoint that 500s because a dependency is down has confused "I am unhealthy" with "I
cannot answer."

Two ownership details:

**`_aclose`** (`:21-40`) handles the driver disagreement — `aclose` on modern redis-py,
`close` on chromadb and older redis, sometimes awaitable — and never raises
(`:38-39`): *"teardown must never mask the probe result."*

**Only what the probe opened is closed** (`:72-75`, `:129-130`):

> Only close what this probe opened — an injected client belongs to the caller. `/readyz`
> is polled, so **a probe that leaks a connection per call exhausts the pool it is
> supposed to be reporting on.**

---

## 9. The registry — `aegis/src/aegis/core/registry.py`

`register(kind, name)` (`:18-40`) is a decorator returning the class unchanged.
`get` (`:43-63`) raises `KeyError`. `available(kind)` (`:66-82`) lists sorted names.

`discover(kind)` (`:85-99`) loads third-party components from the `aegis.<kind>` entry
point group — *"This allows third-party packages to register components without modifying
Aegis core."*

Live use: `@register("guardrail", "default")` on `Guardrails`
(`aegis/src/aegis/guardrails/pipeline.py:108`).

---

## 10. What imports the core, and what it proves

Every leaf's isolation test is the boundary invariant applied per module:

| Test | Asserts |
|---|---|
| `aegis/tests/core/test_core_is_dep_free.py` | core pulls none of 10 banned modules |
| `aegis/tests/voice/test_isolation.py` | `aegis.voice` pulls no litellm/torch/numpy/pandas, and no `app.*` |
| `aegis/tests/vision/test_isolation.py` | plus **no torch/transformers/timm** — *"a policy that is not tested is folklore"* — and **no PIL** |
| `aegis/tests/forecast/test_isolation.py` | `types`/`series` import with no statsforecast |

The voice test goes further (`:57-79`): it runs the **whole guarded path** in a
subprocess with only the base install, using fakes — proving the module is not merely
importable but *usable* without the extras.

And the negative form of the rule is tested too: `aegis.vision` must import no `app.*`
module (`test_isolation.py:56-67`). A leaf that reaches back into the host is a leaf that
cannot be installed alone.

---

## Where to look

| Claim | File:line |
|---|---|
| Core is dependency-free, tested in a subprocess | `aegis/tests/core/test_core_is_dep_free.py:15-45` |
| Fail-loud optional import | `aegis/src/aegis/core/lazy.py:14-32` |
| Structural seams, no inheritance | `aegis/src/aegis/core/interfaces.py:16-37` |
| Types moved out of the API layer | `aegis/src/aegis/core/types.py:1-8` |
| Role enum without the routing table | `aegis/src/aegis/core/models.py:1-8` |
| Bracketing that cannot be forgotten | `aegis/src/aegis/core/stream.py:67-94` |
| The span kind that was stored and never read | `aegis/src/aegis/core/stream.py:40-44` |
| Unknown event name raises | `aegis/src/aegis/core/stream.py:309-311` |
| Start/delta/end enforced | `aegis/src/aegis/core/stream.py:208-209`, `:225-226` |
| No silent infra fallback | `aegis/src/aegis/core/config.py:1-8` |
| Declared vs resolved mode | `aegis/src/aegis/core/config.py:10-12` |
| Close only what you opened | `aegis/src/aegis/core/health.py:72-75` |
| Entry-point discovery | `aegis/src/aegis/core/registry.py:85-99` |

**Next:** [`30-deep-dive.md`](30-deep-dive.md).
