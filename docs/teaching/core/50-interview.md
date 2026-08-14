# Core — interview questions and answers

Claim, reason, concrete detail.

---

### "How is this codebase structured?"

One shared core plus independently-installable leaf modules, held together by three rules.

**The core imports nothing internal.** **A leaf imports only the core** (plus its own
third-party libraries). **There is no leaf-to-leaf import** — anything two modules must
agree on goes into the core instead.

Draw that and it is a star, not a mesh. Which means any leaf can be lifted out and
installed on its own, because its only dependency is a package with no dependencies of
its own.

The reason this needs to be a stated rule rather than a naming convention is that **two
modules always end up needing to agree on something** — a verdict enum, an event shape, a
risk level. When that happens the path of least resistance is for one to import it from
whichever module defined it first. That single edge is how a "modular monolith" becomes a
mesh: folders were never a boundary.

---

### "Why does the core have no dependencies?"

Because **the core is imported by everything, so every dependency it carries is a
dependency everything carries.** The blast radius is total.

Concretely: if the core imported SQLAlchemy — reasonable-looking, since several modules
persist things — then the guardrails, which touch no database, would drag in an ORM. Every
install gets bigger, every import gets slower, and a version conflict in SQLAlchemy
becomes a conflict for every module in the system.

So the core is pydantic and the standard library. The package's three base dependencies
are `pydantic`, `pydantic-settings` and the AG-UI protocol; everything else is an extra.

And because "we intend to keep it light" degrades over eighteen months, **it is a test,
not an intention**: a subprocess imports the core and asserts a list of ten banned heavy
modules is absent from `sys.modules`.

Two details in that test worth mentioning. It runs in a **subprocess**, because another
test in the same process may already have imported SQLAlchemy and the guard would pass by
accident. And it sets `PYTHONPATH` to the source tree, so it tests the real import graph
rather than whatever editable-install state the machine happens to be in.

---

### "How do you handle optional dependencies?"

Through one four-line helper that either imports the module or raises with the exact
install command.

```python
def require(extra: str, module: str) -> ModuleType:
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(f"This feature needs '{module}'. Run: pip install {extra}") from exc
```

The pattern it exists to ban is `try: import x / except ImportError: HAS_X = False`. That
looks defensive and it is genuinely dangerous, because it **converts a deployment error
into a silent runtime behaviour change**.

Walk it through with a security control. An operator enables the image-PII rail. The extra
is missing — wrong install command, stale container, dropped requirements line. With a
`try/except`: the flag is false, the rail is a no-op, every image goes to the model
unredacted, the verdict says PASS, nothing logs, the dashboard is green. That runs for
months until an audit finds it.

The deployment error has become a silent security downgrade.

Two details in those four lines. **`from exc`** preserves the chain — so a missing
*transitive* dependency shows the real culprit rather than a confusing message about the
top-level package you already have installed. And the message carries the **command**, not
a category: `pip install aegis[forecast]` is paste-able; "feature unavailable" is a support
ticket.

Placement matters too: every call site is **inside a function body**. At module top level
the import would be mandatory and the extra would be an extra in name only.

I'd flag one gap: `aegis[media]` is named at four `require` sites and is not declared in
the pyproject extras. The failure is still loud and still fails closed — which is the
security-relevant half — but that install command would not resolve today.

---

### "How do the guardrails call a model without depending on a model client?"

They declare the shape of what they need, and the application supplies it.

```python
@runtime_checkable
class ChatCompleter(Protocol):
    async def __call__(self, messages: list[dict], *,
                       response_format: dict | None = None) -> str: ...
```

Anything with that shape satisfies it — a litellm wrapper, an OpenAI wrapper, a local
stub, a three-line test fake. No inheritance, no registration, and at runtime it is an
annotation that costs nothing.

The alternative — an abstract base class the implementer subclasses — creates a dependency
edge in the **wrong direction**: the gateway would have to import guardrails to satisfy
its interface. Hoisting the abstraction into a package both sides already depend on is the
whole move.

What it buys in practice: a test fake for the injection classifier is three lines with no
mocking library and no API key.

And not every seam is a Protocol. A rail is
`Callable[[MediaPayload], GuardResult | None]`, because there is nothing keyword-only to
express. `VisionAnalyst` *is* a Protocol because it must return usage as well as text —
a bare `ChatCompleter` returning a string would throw away the cost the console has to
show. `TranscribeCallable` is one because it takes a file handle, not messages.

---

### "You mentioned no leaf-to-leaf imports. Is that actually true?"

**No — there are two, and they are documented rather than smoothed over.**

`aegis.memory` imports from `aegis.retrieval` in five places — cosine similarity, RRF
fusion, spotlighting, the Qdrant vector store. That is a deliberate repoint: memory recall
genuinely needs those, and the alternative was a second implementation of each.

There is a cost worth naming, though. Because Python runs a package's `__init__.py` on
*any* submodule import, `from aegis.retrieval.fusion import ...` initialises the whole
`aegis.retrieval` package. So a narrow import pulls the entire leaf. It is mitigated —
retrieval's heavy backends are themselves lazy — but the edge is real.

The second is one line: `aegis.governance.enforcement` imports `BudgetExceededError` from
`aegis.gateway.types`. That one has an obvious fix — move the exception into
`aegis.core.types`, exactly as `RiskLevel` and `RunStatus` were moved out of the host's
API layer for the same reason. It has not been done.

I'd say two more things. **An architectural claim you cannot verify is marketing** — this
one is checkable in about ten lines of AST walking, and today it is documented rather than
enforced. And what would make it structural: a test that walks each leaf's imports and
asserts the set matches a small explicit allowlist, so a new violation fails CI and an
intentional one is a one-line change with a code-review conversation attached.

The per-module isolation tests we do have check *heaviness*, not *topology* — which is
exactly why both violations were found by reading the code while writing docs.

---

### "Why one emitter instead of each module building its own events?"

Because fourteen implementations of a wire format produce fourteen chances to get it
wrong, and the failures are all invisible.

The emitter owns the framing, the field-naming convention, the start/content/end
discipline, and the "run started must come first" rule. Modules call ergonomic helpers.
No module constructs a raw event.

Without that, you get: one module gets the framing wrong; the ordering discipline lives in
fourteen developers' heads; event names drift — `guardrail_verdict` in one place and
`guardrailVerdict` in another — and the frontend has no single source of truth for what it
might receive.

Two design details I like.

**Steps bracket with an async context manager.** `__aexit__` runs even if the body raises,
so an unclosed step is structurally impossible. Manual start/finish calls mean an
exception skips the finish and the client shows a spinner forever.

**Unknown event names raise.** `custom()` validates against a registry and raises
`ValueError` naming the constants module. An unregistered name would otherwise reach the
frontend and be silently dropped — nothing happens, nothing complains, which is the
hardest class of bug to find.

The bigger payoff is that every event carries a **span kind**, so the same stream renders
live in a console *and* exports as OpenInference spans. One event contract, two consumers,
no second instrumentation pass — and they cannot disagree, because there is only one
source.

---

### "Tell me about a bug in the core."

The span kind was stored and never read.

Every module was doing the right thing: `async with emitter.step("retrieve",
SpanKind.RETRIEVER)`. Every call site declared the OpenInference kind it was acting as,
correctly and deliberately. The emitter accepted it, stored it on the scope object, and
**never put it on the wire**.

Consider the shape. Nothing errors. Nothing warns. There is no missing field to notice,
because the field is being populated — into an object nobody reads. The trace exists and
is complete, with every step present. It simply cannot distinguish a retrieval step from a
guardrail step from a model call. And the symptom appears at the far end, in a trace
viewer, where nobody working in the modules ever looks.

**Inert instrumentation is worse than no instrumentation**, because no instrumentation is
obvious and inert instrumentation looks finished.

The fix is three lines — a helper producing `{"spanKind": …}` passed as `raw_event` on
both frames. But the interesting part is how it is *caught*: a regression test that
asserts on the **decoded wire frame**, `evs[0]["rawEvent"] == {"spanKind": "RETRIEVER"}`,
not on the scope object.

The generalisation: **for anything whose consumer is external — a wire format, a log line,
a metric — the test must assert on the serialised output.** Every "we set the field" test
passes while the field goes nowhere.

---

### "How do you handle infrastructure that might not be there?"

Three explicit modes, and never a silent fallback.

**full** — real Redis, Postgres and Qdrant required; refuse to start without them.
**lite** — in-memory, deliberately chosen, loudly announced. **auto** — actually *probe*
the backends and pick, logging which one failed and why.

The posture this bans is the middle one done silently: configured for durable storage,
quietly using RAM, and **calling it durable**. It works in every test environment and
loses data on the first production restart. That is the failure mode the whole design is
written against.

Two details worth having.

**Error messages name the variable an operator sets.** `AEGIS_REDIS_URL`, not
`redis_url`. Small thing; it is the difference between a fix and a hunt.

**There is a declared mode and a resolved mode, and they are different values.** Probing
is I/O, so resolution is async, and in `auto` `settings.mode` is the string `"auto"`
*forever*. The resolved mode is the return value. So `if settings.mode is AegisMode.full`
is false on a fully-provisioned box running `auto`, and anything guarded by it silently
takes the lite branch.

Why not just overwrite the field after probing? Because *"the operator asked for auto and
we resolved to lite"* is a more useful statement than *"the mode is lite"*. Keeping both is
honest; the cost is that callers must know which one they hold.

---

### "Anything subtle in the health probes?"

Two things, both in one comment.

**A probe must close only what it opened.** The probes accept an injected client for
testing and build a real one otherwise. If they closed the injected one, a caller who
passed a shared client would find it dead after a health check.

**And it must close what it opened.** `/readyz` is polled every few seconds by an
orchestrator. A probe that leaks one connection per call exhausts the connection pool it
is supposed to be reporting on — monitoring that causes the outage it monitors.

A third: **a probe reports failure, it never raises.** Every one has a broad `except`
returning `down` with the detail. A health endpoint that 500s because a dependency is down
has confused "I am unhealthy" with "I cannot answer."

And there is a small piece of ecosystem archaeology in the close helper: modern redis-py
uses `aclose`, qdrant-client and older redis use `close`, and some return awaitables. It
tries both, awaits if needed, and swallows everything — because *teardown must never mask
the result you were computing*.

---

### "What does 'importable, not forked' actually buy you?"

The difference between "we could rebuild this for another domain in a few weeks" and "we
can retarget it by implementing one interface."

Forking gives you everything immediately and leaves you maintaining a divergent copy:
upstream fixes never arrive, your changes never go back, and after six months they are
different systems.

Importing requires three things to be true, and all three are consequences of the same
discipline:

**No domain logic in the package.** The moment the guardrails know about invoices, they
work for invoices. **Independent installability** — if installing the guardrails pulls an
agent framework and a database driver, nobody installs them. **Injection, not import** —
if the package imports one vendor's model client, it works only for that vendor's users.

The concrete payoff: the application holds all the domain knowledge and composes generic
modules. The guardrails do not know what they are guarding. The forecaster does not know
what it is forecasting. So pointing the platform at a new problem means writing one
adapter and changing nothing else.

That is a capability, not a refactoring benefit.

---

### "What would you change about the core?"

Three things, and none is a security hole.

**Enforce the boundary invariant with a test.** Walk each leaf's AST, collect
`import aegis.<other>` where `<other>` is not `core` or `data`, and assert against a small
allowlist. Today the two known violations were found by reading code. That is the right
way to *find* them and the wrong way to *keep* the invariant.

**Declare the `aegis[media]` extra**, so the four `require` sites naming it point at
something that resolves.

**Generate the client-side event-name list from the server-side one.** There are two
hand-maintained copies — Python and TypeScript — and two hand-maintained lists will drift.
The server side is protected, because emitting an unknown name raises. The **client** side
is not: a name the server knows and the client does not is a silently dropped event.

If pushed for a fourth: `BudgetExceededError` should move from `aegis.gateway.types` into
`aegis.core.types`, which removes one of the two leaf-to-leaf edges in about two minutes.

---

### "How would you test a package like this?"

Four kinds, and the first two are the ones people skip.

**Import guards, in subprocesses.** Assert the core pulls none of a banned list. Assert
each leaf pulls no heavy dependency and no `app.*` module. Subprocess is mandatory — in
the same process another test may already have imported the banned module and the guard
passes by accident. The vision one goes further and bans `torch`, `transformers` and
`timm` specifically, with the comment *"a policy that is not tested is folklore"*.

**Usability on the base install.** The voice isolation test runs the **whole guarded path**
in a subprocess with only pydantic installed, using fakes. That proves the module is not
merely importable without the extras but *usable*.

**Wire-format assertions for anything with an external consumer.** Encode an event and
assert on the decoded frame — the span-kind regression test is exactly this, and it is the
only shape that would have caught the inert-instrumentation bug.

**Message-content assertions on failure paths.** The `require` test asserts
`"pip install aegis[nemo]" in str(exc)`. Testing that it raises is not enough: the whole
value of the mechanism is the remedy string, so the remedy string is what the test pins.
