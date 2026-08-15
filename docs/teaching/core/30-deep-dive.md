# Core — deep dive

The invariant, where it is actually violated, the bug that made every span kind inert,
and the failure modes a shared foundation is uniquely able to cause.

---

## Story 1 — the instrumentation that was stored and never read

From the emitter's own docstring
(`aegis/src/aegis/core/stream.py:40-44`):

> The step's OpenInference `span_kind` is carried on both frames via AG-UI's `raw_event`
> passthrough. **It used to be stored and never read** — inert at every call site, so the
> trace could not tell a RETRIEVER step from a GUARDRAIL one despite every caller
> declaring it.

Sit with the shape of that.

Every module in the system was doing the right thing. `async with emitter.step("retrieve",
SpanKind.RETRIEVER)`. `async with emitter.step("guard_input", SpanKind.GUARDRAIL)`. Every
call site declared its kind, correctly, deliberately.

The emitter accepted it, stored it on `_StepScope`, and **never put it on the wire**.

So:

- Every call site *looks* instrumented. A reviewer reading any module sees a span kind
  being passed and concludes the tracing is done.
- Nothing errors. Nothing warns. There is no missing field to notice, because the field
  is being populated — into an object nobody reads.
- The trace *exists* and is *complete*, with every step present. It just cannot
  distinguish a retrieval step from a guardrail step from a model call.
- The symptom appears at the far end — a trace viewer that renders every span as a
  generic chain — and nobody working in the modules ever sees it.

**Inert instrumentation is worse than no instrumentation**, because no instrumentation is
obvious and inert instrumentation looks finished.

The fix is three lines (`stream.py:63-65`, `:73-79`, `:88-94`): a `_raw()` helper
producing `{"spanKind": …}`, passed as `raw_event` on both the started and finished frames.

**How this class of bug is caught.** Not by unit-testing the emitter, which would happily
assert the field is stored. By an **end-to-end assertion on the wire format** — encode a
step and assert the emitted frame contains the span kind. That is exactly what
`aegis/tests/core/test_stream_emitter.py:141-156` does — and it is labelled
`REGRESSION` in its own docstring:

```python
assert evs[0]["rawEvent"] == {"spanKind": "RETRIEVER"}
assert evs[1]["rawEvent"] == {"spanKind": "RETRIEVER"}
```

It asserts on the **decoded wire frame**, not on the scope object. The test boundary has
to be the thing the consumer actually reads.

The general lesson: **for anything whose consumer is external — a wire format, a log
line, a metric — the test must assert on the serialised output**, not on the in-memory
object. Every "we set the field" test passes while the field goes nowhere.

---

## Story 2 — the invariant, and the two places it does not hold

The Module Contract says: `aegis.core` imports nothing internal; a leaf imports only
`aegis.core` (plus `aegis.data` for the data-backed ones); **there is no leaf-to-leaf
import**.

There are two, both real, both verified in the source, and both documented rather than
smoothed over (`docs/module/00-overview.md:55-71`).

**1. `aegis.memory` → `aegis.retrieval`**

```
aegis/src/aegis/memory/cache.py:41       from aegis.retrieval.vectors import cosine_similarity
aegis/src/aegis/memory/recall.py:40-42   from aegis.retrieval.fusion import ...
                                         from aegis.retrieval.models import Candidate
                                         from aegis.retrieval.types import RetrievalOrigin
aegis/src/aegis/memory/working.py:32     from aegis.retrieval.spotlight import ...
aegis/src/aegis/memory/vector_ops.py:58  from aegis.retrieval.vector_store import ChromaVectorStore
```

This is a **deliberate** repoint, not an accident: memory recall genuinely needs RRF
fusion, cosine similarity and spotlighting, and the alternative was a second
implementation of each.

But look at the cost the overview names, because it is the interesting part:

> Because Python runs a package's `__init__.py` on any submodule import,
> `import aegis.memory` transitively imports **all** of `aegis.retrieval`.

`from aegis.retrieval.fusion import ...` does not import only `fusion`. Python must
initialise the `aegis.retrieval` package first, which runs its `__init__.py`, which
imports everything that file re-exports. So a single narrow import pulls the whole leaf.

The mitigation is that the *heavy* backends inside `aegis.retrieval` (`lightrag`, `neo4j`,
`redis`) are themselves lazy-imported, so the extra cost is bounded by `aegis[data]`. But
the dependency edge is real and the star graph has a chord in it.

**2. `aegis.governance` → `aegis.gateway`**

```
aegis/src/aegis/governance/enforcement.py:33   from aegis.gateway.types import BudgetExceededError
```

One exception class. The correct fix is obvious — move `BudgetExceededError` into
`aegis.core.types`, exactly as `RiskLevel` and `RunStatus` were moved out of the host's
API layer for the same reason. It has not been done.

### Why documenting a violation is the right move

The overview's own justification (`:69-71`):

> Both are noted here rather than smoothed over, because the whole point of "honest infra"
> is not claiming an invariant holds when the code says otherwise.

Three reasons this matters in an interview:

**An architectural claim you cannot verify is marketing.** "No leaf-to-leaf imports" is
checkable in about ten lines of AST walking. If you assert it without checking, you are
guessing.

**A known, documented violation is manageable; an unknown one is not.** These two are
listed with their files and their reasoning, so someone extracting `aegis.memory` knows
exactly what they will have to deal with.

**The honest version is more convincing.** A candidate who says "the invariant holds
except in two places, here they are, here is why, and here is the fix for the second one"
is more credible than one who claims perfection.

**What would make it structural.** The invariant *should* be a test: walk the AST of every
`aegis/<leaf>/**.py`, collect `import aegis.<other>` where `<other>` is neither `core` nor
`data`, and assert the set matches a small, explicit allowlist. Then a new violation fails
CI and an intentional one is a one-line allowlist change with a code-review conversation
attached. Today the rule is documented and per-module isolation tests check *heaviness*,
not *topology*.

---

## Story 3 — why `try/except ImportError` is the pattern this codebase banned

`require` (`lazy.py:14-32`) exists to make one pattern impossible. The docstring calls it
out by name (`:1-6`): *"never a silent `except ImportError: pass`."*

Walk through what the banned pattern actually does when the optional dependency is a
**control**.

An operator enables the image-PII rail. The `presidio-image-redactor` extra is missing
from this deployment — a wrong install command, a stale container, a dropped line in a
requirements file. With a `try/except`:

1. The import fails. The flag is set to false.
2. The rail becomes a no-op.
3. Every image goes to the model **unredacted**.
4. The verdict says `PASS`.
5. Nothing logs. Nothing alerts. The dashboard is green.
6. This continues for months, until an audit.

The deployment error has become a **silent security downgrade**.

`require` makes step 1 raise, with `pip install aegis[media]` in the message. Loud, local,
and fixable in fifteen seconds.

The same rule generalises past imports, and you can see it applied consistently across
the modules:

| Missing thing | Behaviour |
|---|---|
| An optional library | `ImportError` with the install command |
| A vision completer for the image screen | Block every image, `screened=False` |
| A transcriber for audio | Block, listing the skipped control |
| A rail stack for a voice transcript | Block, "speech would have reached the agent unguarded" |
| An infra backend in `full` mode | Refuse to start |

**A control that cannot run must fail closed and say so.** That is one rule with five
implementations, and `require` is the import-shaped one.

### The two details in four lines

**`from exc`** (`lazy.py:32`). Without it, a *transitive* missing dependency —
`presidio_image_redactor` is installed but its `pytesseract` is not — surfaces as
"This feature needs 'presidio_image_redactor'", which is wrong and sends the operator
chasing a package they already have. With the chain, the traceback shows the real missing
module underneath.

**Placement inside the function.** Every call site is in a function body. At module top
level the import would be mandatory, and the extra would be an extra in name only. Ruff's
`PLC0415` flags function-level imports; the sites carry an explicit `noqa` with the
reason, which is the right way to disagree with a lint rule — in writing, at the site.

### The gap

`aegis[media]` is named at four `require` sites (`guardrails/media/image_pii.py:64`,
`:69`, `:74` and `vision/pii.py:106`) and is **not declared** in
`aegis/pyproject.toml`'s `[project.optional-dependencies]`.

So the mechanism works — loud, fail-closed, with a clear message — and the *remedy string
is wrong*. `pip install aegis[media]` would not resolve today.

Worth noticing what kind of gap that is: the security-relevant behaviour is correct, and
the operator experience is broken. Those are different severities, and saying which is
which is the point.

---

## Story 4 — the failure modes a shared core is uniquely able to cause

A dependency-free core is safe from most problems. The ones it can still cause are worth
enumerating, because they are all *amplified* by the fan-in.

**Blast radius on dependencies.** Anything the core imports, everything imports. If the
core took SQLAlchemy, the guardrails would carry an ORM. This is why the banned list in
`test_core_is_dep_free.py:34-35` names ten specific modules and why the test runs on
`aegis.core.stream` as well — that file has the one third-party import (`ag_ui`) and must
not drag anything with it.

**Blast radius on changes.** A change to `GuardResult` touches every module. Which is why
`MediaGuardResult` **subclasses** rather than widening it
(`guardrails/media/types.py:16-17`): *"keeps the existing wire shape byte-identical for
every text caller that exists today."* Extending a shared type is a compatible change;
widening one is not.

**The import-side-effect registry.** `@register("guardrail", "default")`
(`guardrails/pipeline.py:108`) only takes effect if the module is imported. A component
registered in a module nobody imports is invisible, and `get()` raises `KeyError` with no
hint that the class exists. This is a known ergonomic trap of decorator registries;
`discover()` via entry points is the alternative that does not depend on import order.

**Entry-point discovery is a supply-chain surface.** `discover(kind)`
(`registry.py:85-99`) calls `ep.load()` on every entry point in the `aegis.<kind>` group —
which executes code from any installed distribution that declares one. That is the same
model as `pytest` plugins, and it is why discovery is an explicit call rather than
something that happens at import.

**Two copies of the event-name list.** `stream_names.py:3-5` says the console mirrors
these in `web/src/lib/streamNames.ts`. Two hand-maintained lists **will** drift. The
server side is protected — `custom()` raises on an unknown name (`stream.py:309-311`) — but
the *client* side is not: a name the server knows and the client does not is a silently
dropped event. The robust fix is generation from one source or a test comparing the two.

---

## Story 5 — declared mode versus resolved mode

`resolve_mode` (`config.py:90-130`) is `async`, and the module docstring flags the
consequence (`:10-12`):

> A host must therefore `await settings.resolve_mode()` at startup and use the returned
> mode; the raw `settings.mode` is the **declared** mode, not the resolved one.

That is a genuine footgun and worth being able to explain.

In `auto`, `settings.mode` is *literally the string* `"auto"` forever. It never changes.
The **resolved** mode — `full` or `lite`, determined by probing — is the return value, and
it exists only where someone kept it.

So `if settings.mode is AegisMode.full:` is wrong in `auto`. It is false, always, even on
a machine where every backend is up. Any code guarded by it silently takes the
lite branch on a fully-provisioned box.

Why not just mutate `self.mode` after probing? Because then you lose the declared value,
and *"the operator asked for auto and we resolved to lite"* is a different, more useful
statement than *"the mode is lite"*. Keeping both is the honest design; the cost is that
callers must know which one they hold.

Notice the asymmetry in `require_full_infra` (`:63-88`) too. In `full`, a missing URL
**raises**. In `auto`, it **logs a warning and continues** (`:78-84`) — because dropping
to lite is the point of `auto`. But it still says so, because *"'auto quietly became lite'
is exactly the silent degradation this module exists to prevent."*

Same code, three postures, each argued.

---

## Story 6 — the health probe that exhausts its own pool

`health.py:72-75`:

> Only close what this probe opened — an injected client belongs to the caller. `/readyz`
> is polled, so a probe that leaks a connection per call exhausts the pool it is supposed
> to be reporting on.

Two bugs avoided in one comment.

**Leaking per call.** A readiness endpoint is polled every few seconds by an orchestrator.
A probe that opens a client and does not close it leaks one connection per poll. Within an
hour the pool is exhausted, the application starts failing to acquire connections, and the
probe reports `down` — for an outage the probe itself caused. Monitoring that causes the
incident it monitors.

**Closing someone else's client.** The probes accept an injected client for testing
(`:51`, `:78`, `:103`). If the probe closed *that*, a caller who passed a shared client
would find it dead after a health check. The `owned` variable (`:61`, `:118`) tracks
exactly which one this call constructed, and only that one is closed.

And `_aclose` (`:21-40`) handles a genuine ecosystem mess: modern redis-py has `aclose`,
chromadb and older redis have `close`, and some return awaitables. It tries both
names, awaits if needed, and swallows everything (`:38-39`) — *"teardown must never mask
the probe result."*

That last line is a good instinct in general: **cleanup failures must not overwrite the
result you were computing.**

---

## Concurrency and state

There is very little mutable state in the core, which is deliberate.

**`_REGISTRY`** (`registry.py:14`) is a module-level dict, populated at import time by
decorators and read afterwards. Import-time writes are effectively single-threaded (the
import lock), and reads are safe. `discover()` writes later, but is called at startup.

**`AegisEmitter` is per-run** (`stream.py:100-113`) — one thread id, one run id, one sink,
and two open-id sets. It is **not** safe to share across concurrent runs, and it is not
meant to be: the id sets track that run's open messages.

That does raise a question worth noticing. Within one run, concurrent `text_delta` calls
on different message ids would race on `_open_text` — a Python `set`, so individual `add`
and `discard` are atomic under the GIL, but a check-then-act sequence is not. In practice
the emitter is driven from one task per run, which is why this has not mattered.

**`CoreSettings`** is constructed per use and read-only after construction.

**The probes hold nothing.** Each call builds and closes its own client.

**Purity where it counts.** `require`, `is_known`, and every type in `types.py` and
`events.py` are pure or immutable.

---

## The limits of the contract

Three, stated plainly.

**The invariant is documented, not enforced.** There is no test walking the import graph
and asserting no leaf-to-leaf edges. The two known violations were found by *reading the
code while writing documentation* — which is how they should have been found, and also
means a third could exist unnoticed. The per-module isolation tests check that a leaf
imports no heavy dependency and no `app.*`; they do not check that it imports no sibling
leaf.

**`aegis[media]` does not exist as an extra.** Four `require` sites name it.

**Two hand-maintained copies of the event-name list**, one in Python and one in
TypeScript, with drift protected on the server side only.

None of these is a security hole. All three are the kind of thing worth naming before
someone else does.

---

## What you should now be able to tell as a story

- **The span kind stored and never read** — every call site correct, the wire missing it,
  and why the test boundary has to be the serialised output
- **The two leaf-to-leaf imports**, why one is deliberate and one is a two-minute fix, and
  why `__init__.py` makes a narrow import pull a whole package
- **Why documenting a violated invariant beats claiming it holds**
- **The five specific harms of `try/except ImportError`**, and what happens when the
  optional dependency is a control
- **`from exc`**, and the transitive-dependency message it saves you from
- **The four failure modes a shared core amplifies** — dependency blast radius, change
  blast radius, import-side-effect registration, and the mirrored constant list
- **Declared versus resolved mode**, and why `settings.mode` is `"auto"` forever
- **The probe that exhausts the pool it monitors**, and close-only-what-you-opened
- **The three limits**, stated before someone else finds them

**Next:** [`40-diagrams.md`](40-diagrams.md).
