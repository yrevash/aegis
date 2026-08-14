# Core — the theory

The software-engineering literature this design comes from: dependency inversion,
structural typing, packaging extras, event-stream discipline, and the observability
conventions that make one stream serve two consumers.

---

## 1. Dependency inversion, and where it actually bites

**The Dependency Inversion Principle** (Martin, the "D" in SOLID):

> High-level modules should not depend on low-level modules. Both should depend on
> abstractions. Abstractions should not depend on details.

The naive reading is "use interfaces." The useful reading is about **which package owns
the abstraction**.

If the guardrails package defines `ChatCompleter` and the gateway package implements it,
the dependency edge runs **from the implementation to the interface** — the gateway
depends on guardrails, not the other way round. If instead the *core* defines it and both
depend on the core, neither leaf depends on the other at all.

That is the whole architectural move: **hoist the abstraction into a package both sides
already depend on.**

The related idea is the **Stable Dependencies Principle**: depend in the direction of
stability. A package that many things depend on must change rarely. A dependency-free
core of types and protocols is about as stable as code gets — there is nothing in it that
a library upgrade can break.

And the **Acyclic Dependencies Principle**: no cycles in the package graph. Enforcing
"core imports nothing internal; leaves import only core" makes cycles structurally
impossible rather than something a reviewer has to spot.

---

## 2. Structural typing in Python

**PEP 544** introduced `typing.Protocol` — static duck typing. A class satisfies a
protocol by having the right methods, with no inheritance and no registration.

```python
class ChatCompleter(Protocol):
    async def __call__(self, messages: list[dict], *,
                       response_format: dict | None = None) -> str: ...
```

Anything callable with that signature satisfies it. Type checkers verify it statically;
at runtime it is an annotation and costs nothing.

Compare the alternatives:

| Approach | Cost |
|---|---|
| **ABC + inheritance** | The implementer must import your package to subclass. That is a dependency edge in the wrong direction. |
| **`isinstance` checks on concrete classes** | You must import the concrete class. Same problem, worse. |
| **Duck typing with no annotation** | Works, but the contract is undocumented and unverifiable. |
| **Protocol** | Documented, statically checked, zero runtime coupling. |

`@runtime_checkable` adds `isinstance` support, but note the limitation honestly: it
checks only **method presence**, not signatures. `isinstance(x, ChatCompleter)` returns
true for anything with a `__call__`. It is useful for a coarse guard and useless as a
contract check.

### Protocols vs a plain `Callable`

Why declare a Protocol rather than `Callable[[list[dict]], Awaitable[str]]`?

Because `Callable` cannot express **keyword-only parameters**, and `response_format` is
keyword-only. It also cannot carry a docstring, which for a public seam is most of the
value — the docstring is where you say "returns the assistant's text" and "this is where
you inject your provider."

Which is also why some seams *should* be plain callables: a rail is
`Callable[[MediaPayload], GuardResult | None]` because there is nothing keyword-only to
express and the shape is genuinely one function.

---

## 3. Optional dependencies: extras, and the anti-pattern

**PEP 508 / PEP 621** define optional dependency groups:

```toml
[project.optional-dependencies]
forecast = ["statsforecast>=2.1,<3", "pandas>=2.2,<2.4", "numpy>=1.26"]
```

`pip install package[forecast]` installs the group. Base install stays small.

### Why `try/except ImportError` is the wrong runtime pattern

Enumerate what it does:

1. **Converts a deployment error into a behaviour change.** The library is missing because
   an install was wrong — a deployment problem with a deployment fix. The `try/except`
   makes it a silent runtime path change.
2. **Produces two code paths with different behaviour and no signal about which ran.**
3. **Is unobservable.** Nothing logs. Nothing alerts. The dashboard is green.
4. **Is security-relevant when the optional thing is a control.** A missing PII engine
   with a "simpler regex" fallback is a downgraded control nobody chose.
5. **Fails at the wrong time.** Import time is startup; the fallback fails later, in
   production, under load.

The alternative is a single helper that imports or raises with the remedy:

```python
def require(extra: str, module: str) -> ModuleType:
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(f"This feature needs '{module}'. Run: pip install {extra}") from exc
```

Two details in that four-line function do real work.

**`from exc`** preserves the exception chain (PEP 3134). Without it, a missing *transitive*
dependency — the package is installed but one of *its* dependencies is not — surfaces as a
confusing message about the top-level package. With it, the traceback shows the real
missing module underneath.

**The message contains the command.** Not "feature unavailable", not "please install the
extra". The literal string a user can paste. That single choice converts a support ticket
into a fifteen-second fix.

### The placement rule

The optional import must sit **inside the function that needs it**. At module top level it
becomes mandatory, defeating the extra entirely. Inside the function:

- the module imports everywhere
- the cost is paid on first use
- the failure lands at the call site with a message about *this* feature

Ruff's `PLC0415` flags function-level imports as a style issue, which is why these sites
carry an explicit `noqa` with a reason. The lint rule is right in general and wrong here,
and the comment records that.

---

## 4. Event streams and protocol discipline

Any system streaming structured progress to a UI faces the same problems, and they have
standard answers.

**Server-Sent Events (SSE)** is the usual transport: a long-lived HTTP response, frames
of `data: <payload>\n\n`. Unidirectional, works through proxies, auto-reconnects, and far
simpler than WebSockets when you only need server→client.

**Start/content/end discipline.** Streaming text arrives in deltas, so the protocol needs
a start, N content frames and an end, all correlated by an id. Two failure modes:

- A delta for a message that was never started — the client has nowhere to put it.
- A message started and never ended — the client shows a spinner forever.

Enforce this **in the emitter**, by tracking open ids and raising on a delta or end for an
unopened id. A protocol violation is a programming error and should behave like one.

**Bracketing with a context manager.** A step's finish must be emitted even if the body
raises. `async with` guarantees it via `__aexit__` — a discipline the type system enforces
rather than one a developer must remember.

**A closed set of event names.** Domain payloads ride on a generic "custom event"
envelope with a name. Without a registry, names drift (`guardrail_verdict` vs
`guardrailVerdict`), and an unknown name at the client is a **silent no-op** — the hardest
bug class, because nothing happens and nothing complains. A constants module plus
validation at emit time turns that into an immediate, local error.

**Mirroring on the client.** The frontend needs the same list. Two copies of a constant
list will drift; the mitigation is either generation from one source or a test that
compares them.

---

## 5. OpenTelemetry, OpenInference, and the two-consumer trick

**OpenTelemetry** is the vendor-neutral standard for traces, metrics and logs. A **span**
is one timed operation with attributes; spans nest into a tree.

**OpenInference** and the OTel **GenAI semantic conventions** add LLM-specific vocabulary
— what a span *kind* is (`LLM`, `RETRIEVER`, `RERANKER`, `TOOL`, `GUARDRAIL`, `AGENT`,
`CHAIN`, `EVALUATOR`), and attribute names like `gen_ai.request.model`. Standard names
mean an off-the-shelf backend can interpret your traces without bespoke parsing.

Here is the leverage. A UI event stream and a trace are **the same information at
different granularities**. Both are "this operation started, here is what happened, it
finished."

So if every streamed step carries its span kind, the same emissions can drive a live
console *and* be exported as spans. One event contract, two consumers, no second
instrumentation pass — which also means the two can never disagree, because there is only
one source.

The failure mode to know: carrying the span kind on the event object and **never reading
it**. Every call site dutifully declares `SpanKind.RETRIEVER`, the field is stored, and
nothing puts it on the wire. Inert instrumentation is worse than none, because it looks
done.

---

## 6. Registries and entry points

A `(kind, name) -> class` registry solves discoverability: swappable implementations, and
a way to list what is available.

Two mechanisms:

**Decorator registration.** `@register("guardrail", "default")` on the class. Simple, and
requires the module to be imported for the side effect to happen — which is worth
remembering, because a registration in an unimported module is invisible.

**Entry points** (`importlib.metadata`). A third-party package declares
`[project.entry-points."package.guardrail"] mine = "mypkg:MyGuardrail"` in its
`pyproject.toml`, and the host discovers it by group name **without editing the host**.
This is the plugin mechanism `pytest` and `flake8` use.

The trade: entry points can execute arbitrary code from any installed distribution
matching the group, so discovery is a supply-chain surface. Calling it explicitly rather
than at import time is the safe shape.

---

## 7. Fail-fast configuration

**Twelve-Factor** says configuration lives in the environment. Typed settings libraries
(`pydantic-settings`) add validation, so a malformed value fails at construction rather
than at first use.

The interesting part is what happens when a *backend* is unreachable rather than a value
being malformed.

Three postures:

| Posture | Behaviour | When it is right |
|---|---|---|
| **Fail fast** | Refuse to start | Production. A misconfigured deploy should not serve. |
| **Silent fallback** | Quietly use RAM | **Never.** |
| **Explicit degraded mode** | Boot in-memory, loudly | Local dev, CI, demos |

The middle row is the one that causes incidents, and the shape is always the same: the
system is *configured* for durable storage, uses RAM instead, and **calls it durable**.
It works in every test environment. It loses data on the first restart in production.

A probing "auto" mode is legitimate **if** it genuinely probes, and **if** dropping to
degraded is logged with which backend failed and why.

One structural subtlety: probing is I/O, so resolution must be `async`, which means there
is a **declared** mode (the config value) and a **resolved** mode (the probe result).
They are different values. Code that reads the declared one when it means the resolved one
is wrong precisely when it matters.

### Health probes

`/readyz` should report **measured** reachability, not a config value. Two engineering
details that bite:

**Close only what you opened.** If a probe accepts an injected client for testing and
also builds its own in production, it must close only the one it built. Closing an
injected client is a surprise for the caller.

**Do not leak a connection per poll.** `/readyz` is polled every few seconds by an
orchestrator. A probe that leaks one connection per call will exhaust the pool it is
supposed to be reporting on — a monitoring endpoint causing the outage it monitors.

And a probe must **never raise**. It reports `down` with a detail. A health endpoint that
500s because a dependency is down has confused "I am unhealthy" with "I cannot answer."

---

## 8. Packaging for isolation

**`src/` layout.** Source under `src/package/` rather than at the repo root means tests
run against the *installed* package, not the working directory. Without it, an import that
only works because you happen to be in the repo root passes CI and fails for a user.

**Version pinning with upper bounds.** An unbounded `>=` is a bet that no upstream will
ever break you. A concrete example from this codebase: LangGraph went `0.2 → 1.x` while a
pin said `>=0.2`, so a fresh resolve could silently pull a major version with different
interrupt and state semantics. `>=1.2,<2` is the fix.

**Constraint files for cross-extra conflicts.** When two extras both pin a shared
transitive dependency (`pandas`, `numpy`), the resolver may find no solution — or find a
surprising one. A constraints block pins the shared ones once, centrally.

**Import guards as tests.** Assert in a **subprocess** — with the source tree on
`PYTHONPATH` so the guard tests the real import graph — that importing the core adds none
of a banned list to `sys.modules`. A subprocess is required: another test in the same
process may already have imported the banned module, and the guard would pass by
accident.

---

## 9. What "importable, not forkable" requires

Three conditions, and they are all consequences of the same discipline:

1. **No domain logic in the package.** The moment the guardrails know about invoices,
   they work for invoices.
2. **Independent installability.** If installing the guardrails pulls an agent framework
   and a database driver, nobody installs it.
3. **Injection, not import.** If the package imports one vendor's model client, it works
   only for that vendor's users.

The payoff is measurable rather than aesthetic: if the application holds all the domain
knowledge and composes generic modules, retargeting the platform at a **new problem
domain** is writing one adapter. Not a rewrite, not a fork — an implementation of an
existing interface.

That is the difference between "we could rebuild this for another domain" and "we can
retarget it."

---

## What you should now be able to explain

- Dependency inversion as a question of *which package owns the abstraction*
- Stable Dependencies and Acyclic Dependencies, and how the star graph enforces both
- PEP 544 protocols, and why `@runtime_checkable` checks less than you think
- Why some seams are Protocols and some are plain `Callable`
- The five specific harms of `try/except ImportError`
- Why `from exc` matters for transitive missing dependencies
- Why the optional import belongs inside the function, and what lint rule that fights
- SSE, start/content/end discipline, and why an unknown event name must raise
- How a span kind makes one stream serve a console and a tracer
- The inert-instrumentation failure mode
- Decorator registration vs entry points, and the supply-chain trade
- The three infra postures, and why "declared" and "resolved" mode differ
- Close-only-what-you-opened, and the probe that exhausts its own pool
- `src/` layout, upper bounds, constraint files, and subprocess import guards

**Next:** [`20-in-aegis.md`](20-in-aegis.md).
