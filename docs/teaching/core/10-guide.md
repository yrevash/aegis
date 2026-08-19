# The core

The package everything else is allowed to depend on.

---

## 1. What it is

Here is an enum. Four values, no logic:

```python
class GuardVerdict(StrEnum):
    PASS = "pass"
    BLOCK = "block"
    REDACT = "redact"
    FLAG = "flag"
```

Three parts of the system need it. The guardrails return it. The API puts it in a response.
The approvals table stores a sibling of it, `RiskLevel`.

In the original backend it lived in the API's schema module. So a guardrail that wanted to
return a verdict wrote `from app.api.schemas import GuardVerdict`. That line looks free. It
is not. Importing that module loads an ORM, a JWT library, a password hasher and OpenSSL
bindings — because the API layer needs all of those. The guardrails need none of them. They
match text against patterns.

Now multiply it. Every module that touches a verdict pays. And a version clash in a database
library the guardrails have no opinion about becomes a clash they cannot install around.

`aegis.core` is where that shared enum should have lived: a package that carries pydantic and
the standard library and nothing else. It holds the things two modules must agree on — types,
interfaces, the event stream, config, health probes — so no module has to reach into another
one to get them.

This is not a mistake somebody made. It happens to every codebase, because two modules always
end up needing to agree on something, and whichever one defines it first owns it. Do that six
or seven times and your folders are a naming convention with a tangle underneath.

---

## 2. How it works in Aegis

The rule set is three lines. The codebase calls it the **Module Contract**.

1. **The core imports nothing internal.** No other Aegis module. Not one.
2. **Every other module imports only the core**, plus its own third-party libraries.
3. **No module imports a sibling.** Anything two modules must agree on moves into the core.

Draw that and it is a star, not a mesh. A cycle becomes impossible rather than something a
reviewer has to spot.

### What is in it

| Part | What it holds |
|---|---|
| `types.py` | The shared enums and models — `GuardVerdict`, `RiskLevel`, `RunStatus`, `GuardResult` |
| `interfaces.py` | Protocols describing what a module needs, like `ChatCompleter` |
| `events.py` | The event types every module emits, and `SpanKind` |
| `stream.py` | `AegisEmitter` — the one object that writes events to the wire |
| `config.py` | `CoreSettings` and the three infra modes |
| `health.py` | Probes that report whether Redis, Postgres and the vector store answer |
| `lazy.py` | `require` — the one sanctioned way to reach an optional dependency |
| `registry.py` | A name-to-class registry for pluggable components |

### The dependency rule does most of the work

Because the core is imported by everything, every dependency the core carries is a dependency
everything carries. So the whole `aegis` package declares three:

```toml
dependencies = ["pydantic>=2.9", "pydantic-settings>=2.6", "ag-ui-protocol~=0.1.19"]
```

Everything heavy — the model gateway, the ML stack, the agent framework, the vector store,
the ORM — sits behind an extra. `pip install aegis[forecast]` gets the forecasting stack and
nothing else.

One refinement shows the rule being applied against a real temptation. Exactly one file in
the core imports something that is not pydantic: `stream.py` imports the AG-UI protocol. So
`stream` is deliberately **not** re-exported from the package. You import it by path, which
means you opt into that dependency:

```python
from aegis.core import GuardResult, RiskLevel     # cheap
from aegis.core.stream import AegisEmitter        # opts into AG-UI
```

A rule nobody tests is folklore, so this one is executable. A test starts a fresh Python
process, imports `aegis.core` and `aegis.core.stream`, and asserts that none of eleven heavy
libraries — sqlalchemy, torch, litellm, fastapi, jwt and the rest — ended up loaded. It runs
in a separate process on purpose: if another test had already imported SQLAlchemy, an
in-process check would pass by accident. Each module has its own version of that test.

### Optional dependencies fail loudly

Some features need libraries that are not always installed. The pattern you see everywhere is
a `try: import x / except ImportError: DISABLED = True`, and later a check that skips the
feature.

That is fine for a nice-to-have and dangerous for a control. An operator turns on the image
PII rail, the extra is missing from that deployment, the rail becomes a no-op, every image
reaches the model unredacted, and the verdict says `PASS`. Nothing logs. A deployment mistake
has become a silent security downgrade.

So there is one way to reach an optional dependency, and it raises:

```python
def require(extra: str, module: str) -> ModuleType:
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(f"This feature needs '{module}'. Run: pip install {extra}") from exc
```

The message carries a command you can paste. `from exc` keeps the original error, which
matters in the case that actually happens: the extra *is* installed but one of its own
dependencies is missing, and without the chain the operator reinstalls a package they already
have. Calls to `require` go inside the function that needs the library — an optional import
at the top of a module is not optional.

Degrading is allowed. Degrading *quietly* is not. The text PII engine really does fall back
to a simpler regex engine when the better one is missing — but it logs a warning every time,
reports which engine is live, and can be pinned so the fallback becomes an error instead.

### Depending on shapes, not packages

The other half of staying dependency-free is not importing implementations at all. The
guardrails never say "give me a LiteLLM client". They say what shape they need:

```python
@runtime_checkable
class ChatCompleter(Protocol):
    async def __call__(self, messages: list[dict], *,
                       response_format: dict | None = None) -> str: ...
```

Anything with that signature satisfies it. No base class to inherit, no registry to join. The
payoff shows up in tests, where the whole model-based guardrail path runs against this:

```python
async def fake(messages, *, response_format=None):
    return '{"injection": false, "unsafe": false}'
```

Two lines, no mocking library, no API key, no network.

Not every seam needs a Protocol. A rail is a plain `Callable` — one argument, one return,
nothing keyword-only to describe. Use a Protocol when the signature has keyword-only
arguments or the return shape needs explaining.

Somebody still has to supply the real implementations. That happens in exactly one place
above every module — the composition root, which here is `backend/src/app/`:

```python
gateway.configure(config=..., governance=..., observability=OtelObservabilitySink())
```

The library says "I need something completer-shaped". The application says "here is ours,
wired to our credentials". Note what the application does *not* get a vote on: the order the
guardrails run in. That stays in the library, the same for every host.

### One emitter owns the wire

Every module produces events a user should be able to watch: a guardrail fired, retrieval
ran, a tool executed. If each module built its own event objects, fourteen modules would
frame them fourteen ways and the names would drift.

So there is one `AegisEmitter` and one registry of event names. Modules call small helpers;
the emitter owns the rules — the framing, the field naming, and the ordering. Two of those
rules are enforced rather than documented. A step is a context manager, so its finish is
emitted even if the body raises — with manual start/finish calls, one exception leaves the
browser spinning forever. And an event name that is not in the registry raises, because a
typo would otherwise become an event the frontend silently drops.

Every step also carries a `SpanKind` — `LLM`, `RETRIEVER`, `GUARDRAIL`, `TOOL` and so on. The
same enum drives the live UI stream and the exported trace, so the two can never disagree
about what kind of step something was.

### Configuration says what it is doing

Three ways to behave when a system that needs Redis, Postgres and a vector store finds one
missing. `AEGIS_MODE` picks:

| Mode | Behaviour | When |
|---|---|---|
| `full` (default) | Refuse to start without real backends | Production |
| `lite` | Use in-memory backends, announced | Local dev, CI, demos |
| `auto` | Probe the backends and pick, logging which and why | Mixed environments |

The one that is missing is the common one: quietly fall back to RAM while still calling
itself durable. Every test passes, staging passes, then a production restart loses data
everyone believed was saved.

Two details are worth copying. Error messages name `AEGIS_REDIS_URL`, the thing an operator
actually sets, not the field name. And probing is I/O, so resolving is async — which creates
a trap the module docstring flags: `settings.mode` is the **declared** mode and stays
`"auto"` forever. You must `await settings.resolve_mode()` and use what it returns.

### Health probes

`/readyz` should report measured reachability, not a config value, so each probe does the
cheapest real round-trip — a Redis `ping`, a `SELECT 1`, listing the vector store's
collections. Two habits generalise. A probe **reports** failure and never raises; a health
endpoint that 500s has confused "I am unhealthy" with "I cannot answer". And it closes only
the client it opened — `/readyz` is polled, so a probe that leaks a connection per call
exhausts the pool it is meant to be reporting on.

### Where the star still has chords

Being straight about it: rule 3 is not fully true today. A handful of leaf-to-leaf edges
exist — memory reaches into retrieval for rank fusion, vision and voice import guardrails to
order rails they do not own. Some are deliberate; at least one is a hoist into `core.types`
nobody has done. The isolation tests check *heaviness*, not *topology*, which is exactly why
topology drifted.

> A rule with a test is an invariant. A rule with a paragraph is a hope.

---

## 3. How you use it in code

```python
from aegis.core import (
    GuardResult, GuardVerdict, RiskLevel, RunStatus,   # shared types
    ChatCompleter, Guardrail,                          # protocols
    SpanKind, AegisEvent,                              # events
    CoreSettings, AegisMode,                           # config
    require,                                           # optional imports
)
```

Reaching an optional dependency, inside the function that needs it:

```python
def _analyzer():
    presidio = require("aegis[pii]", "presidio_analyzer")
    return presidio.AnalyzerEngine()
```

Resolving config at startup — note the `await`:

```python
settings = CoreSettings()          # reads AEGIS_* environment variables
mode = await settings.resolve_mode()
if mode is AegisMode.lite:
    ...
```

Emitting events, if you are writing a module that streams:

```python
from aegis.core.stream import AegisEmitter

emitter = AegisEmitter(thread_id=session_id, run_id=run_id, sink=sink)
await emitter.run_started()
async with emitter.step("retrieve", SpanKind.RETRIEVER):
    ...
await emitter.run_finished()
```

### Settings worth knowing

| Variable | Default | What it does |
|---|---|---|
| `AEGIS_MODE` | `full` | `full`, `lite` or `auto` — see above |
| `AEGIS_REDIS_URL` | unset | Required in `full` |
| `AEGIS_DATABASE_URL` | unset | Required in `full` |
| `AEGIS_VECTOR_STORE_URL` | unset | The Qdrant node (also read from `QDRANT_URL`). Required in `full` |
| `AEGIS_VECTOR_STORE_PATH` | unset | LightRAG's local working directory. Not required |

---

## 4. Why it helps us

**Aegis is something you install, not something you fork.** A team can `pip install
aegis[guardrails]` and get the guardrails, without an agent framework, a database driver or
an ML stack coming with them.

**Nothing pulls in what it does not use.** Installs stay small, test collection stays fast,
and a version conflict in one library stays that library's problem.

**Every module is testable offline.** Because modules depend on shapes, a two-line fake
replaces a model, a database or a rail. No API key, no network.

**Failures are visible.** A missing optional library raises with the install command. A
degraded mode says so. A misconfigured deployment refuses to boot rather than serving
quietly.

**The console and the trace agree.** One event vocabulary, one emitter, one span-kind enum.

Without the core, the three rules have nothing to point at — every module would import
whichever sibling defined the thing it needed first, and within months there would be no way
to lift any single piece out.

**Next:** [`40-diagrams.md`](40-diagrams.md)
