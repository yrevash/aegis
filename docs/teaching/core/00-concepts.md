# Core — the concept, from zero

No code. What a Module Contract is, why a shared foundation must be dependency-free, and
why "importable, not forkable" is an architectural claim with teeth.

---

## The problem: the modular monolith that isn't

You start with one application. It grows. Someone says "let's make it modular", so you
create folders: `guardrails/`, `retrieval/`, `agent/`, `memory/`.

Six months later, nothing is modular. `guardrails` imports a helper from `retrieval`
because that is where someone happened to put it. `memory` imports a type from
`agent`. `agent` imports both. Nobody can extract any single piece, because every piece
transitively depends on every other piece.

The folders were never a boundary. They were a naming convention.

This happens for a reason that is worth stating precisely: **two modules always end up
needing to agree on something.** A verdict enum. An event shape. A "risk level". And when
that need arises, the path of least resistance is for one module to import it from
whichever module defined it first. That single import creates a dependency edge, and the
edges accumulate until the graph is complete.

---

## The fix: a shared foundation with a hard rule

The structural answer is a **core** package plus three rules:

1. **The core imports nothing internal.** No leaf module. Not one.
2. **A leaf module imports only the core** (plus its own third-party libraries).
3. **There is no leaf-to-leaf import.** Ever.

Anything two or more modules must agree on goes into the core. That is the *only* place
shared logic may live.

Draw the graph and it is a star, not a mesh. The core is the hub. Every leaf points at
it and at nothing else. Now any leaf can be lifted out and installed on its own, because
its only dependency is a package with no dependencies of its own.

---

## Why the core must be dependency-free

Rule 1 has a corollary that is easy to miss and does most of the work: **the core must
also carry no heavy third-party dependency.**

Suppose it imports SQLAlchemy — a reasonable-looking choice, since several modules
persist things. Now:

- The guardrails module, which touches no database, drags in a database ORM.
- Every install is bigger and slower.
- Import time grows for everyone.
- And a version conflict in SQLAlchemy becomes a conflict for *every* module.

The core is imported by everything, so **every dependency it carries is a dependency
everything carries.** The blast radius is total.

So the core holds only what is genuinely universal: shared data types, structural
interfaces, a small registry, configuration, health probes, and one helper for reaching
optional dependencies safely. In practice that means **pydantic and the standard
library**, and nothing else.

And because "we intend to keep it light" degrades over eighteen months, this should be a
**test**, not an intention: import the core in a clean subprocess and assert that a list
of banned heavy modules is absent from `sys.modules`. A rule that is not tested is
folklore.

---

## Optional dependencies, and the silent-failure trap

Not every module needs every library. Image PII needs an OCR stack; forecasting needs a
statistics stack; the gateway needs a model client. Those live behind **extras** —
`pip install package[forecast]` — so an install stays proportional to what you use.

Which raises the question: what happens when the code reaches for a library that is not
installed?

The pattern you will see in the wild:

```python
try:
    import fancy_library
    HAS_FANCY = True
except ImportError:
    HAS_FANCY = False
```

and later, `if HAS_FANCY: ... else: <do something weaker>`.

This is one of the most dangerous patterns in production software, and it is worth being
able to explain *why*, because it looks defensive and responsible.

**It converts a deployment error into a behaviour change.** The library is missing because
someone got the install wrong. That is a *deployment* problem with a *deployment* fix. The
`try/except` turns it into a silent runtime behaviour change: the system keeps serving,
with a weaker code path, and nothing anywhere says so.

Now compound it with security. Suppose the missing library is your PII detector. Your
fallback is a simpler regex. Your logs say nothing. Your dashboard is green. And personal
data flows through a control that was never actually installed — for months, until an
audit finds it.

**The correct behaviour is to fail loudly, with the fix in the message.** If code reaches
for an optional dependency and it is absent, raise — and the exception should name the
exact install command. Not "feature unavailable". The command.

The rule generalises past imports: **a control that cannot run must fail closed and say
so.** Missing library, missing model, missing configuration — all the same shape. The
alternative is a system that degrades invisibly, and a system that degrades invisibly is
a system that lies.

There is a corollary about *when* the import happens, too. If the optional import sits at
module top level, importing the module at all requires the dependency — which defeats the
point of the extra. So the import goes **inside the function that needs it**, and the
module stays importable everywhere.

---

## Structural interfaces: seams instead of dependencies

The other half of keeping the core dependency-free is not needing to import
implementations at all.

Suppose the guardrails need to call a language model. The naive approach imports a model
client. Now the guardrails depend on that client, on its version, and on its
configuration — and testing them requires either a real API key or heavy mocking.

The alternative: the guardrails declare **the shape of the thing they need**.

> *"Give me something I can await with a list of messages, that returns a string."*

That is all. Not a class to subclass, not a base to inherit from — a **structural**
description. Anything with that shape satisfies it: a wrapper around one vendor's client,
a wrapper around another's, a local stub, a test fake that returns a fixed string.

In Python this is a `Protocol`; other languages call it structural typing, or duck typing
with a type checker. The properties that matter:

- **No inheritance.** The implementer does not import your package to satisfy your
  interface.
- **No runtime dependency.** The interface is a type annotation; it disappears at runtime.
- **Testable for free.** A three-line async function is a valid implementation.

The general principle: **depend on shapes, not on packages.** The core defines the shapes.
The composition root supplies the implementations.

Which raises the natural question: if leaves do not import each other and the core imports
nothing, who wires it all together?

---

## The composition root

Someone has to. The pattern is that **exactly one place** knows the concrete
implementations, and it sits above every module.

The library says *"I need something completer-shaped."* The application says *"here is
our completer, wired to our credentials, our budget hook and our tracer."*

That inversion is what keeps the modules importable in isolation, and it is why the
security-relevant orderings live in the library rather than in the application. The
application supplies dependencies; it does not get a vote on the order the controls run
in.

---

## Streaming: why one emitter beats fourteen builders

A second problem the core solves.

An agentic system does work the user should be able to watch: retrieval ran, a guardrail
fired, the model was called, a tool executed. That is a stream of typed events.

The tempting implementation is for each module to build its own event objects. Guardrails
constructs a verdict event; retrieval constructs a citations event; the agent constructs
a step event.

It fails predictably, and in more ways than you would guess:

- **Fourteen implementations of the wire format.** One of them gets the field naming
  convention wrong, or the framing, or the encoding.
- **Ordering rules are unenforceable.** Streaming protocols have discipline: a run must
  start before anything else; a text message must be started before deltas can be
  appended and ended after. If every module emits raw events, that discipline lives in
  fourteen developers' heads.
- **Event names drift.** One module emits `guardrail_verdict`, another emits
  `guardrailVerdict`, and the frontend has a switch statement with both.
- **The frontend has no single source of truth** for what it might receive.

The fix is one **emitter** that owns the wire rules, and one **registry of event names**
that is the single source of truth.

Two design details worth internalising:

**Bracketing as a context manager.** A step has a start and an end, and the end must
happen even if the body raises. That is exactly what a scoped block gives you — enter
emits the start, exit emits the finish, and there is no way to forget.

**Validating event names at emit time.** If a module emits an unregistered name, that is a
programming error, and the emitter should raise. The alternative is an event that
silently vanishes at the frontend — the hardest class of bug to find, because *nothing*
happens and nothing complains.

The payoff is bigger than consistency. If every event carries a **span kind** — a label
saying whether this step is a retrieval, a model call, a guardrail — then the *same
stream* can be rendered live in a console **and** exported as distributed-tracing spans.
One event contract, two consumers, no second instrumentation pass.

---

## Configuration, and the honest-degradation rule

The last core concern is knowing what infrastructure you are actually running on.

A system that needs Redis, a database and a vector store has three ways to behave when
one is missing:

1. **Crash at startup.** Safe, obvious, and sometimes exactly right.
2. **Fall back to an in-memory version.** Convenient. **Dangerous.**
3. **Ask, and be told which.**

Option 2 is the one that ruins people, and the failure has a specific shape: the system
is *configured* for durable storage, silently uses RAM instead, and **calls it durable**.
Everything works in testing. Everything works in staging. Then a restart loses data that
everyone believed was persisted.

The honest design makes the mode **explicit**:

- **full** — real infrastructure required; refuse to start without it.
- **lite** — in-memory, deliberately chosen, loudly announced.
- **auto** — actually **probe** the backends and pick, logging which and why.

The critical property of `auto` is that it must genuinely probe, not assume. And when it
drops to lite it must say **which backend was unreachable and why**. A degraded process
must never be a quiet one.

There is a second-order point here. In `auto` mode there is a *declared* mode (what the
config says) and a *resolved* mode (what probing determined). Those are different values,
and code that reads the declared one when it means the resolved one will be wrong exactly
when it matters most.

---

## Imported, not forked

Now put it together, because this is the claim that makes all of it worth the discipline.

Two ways to reuse a system:

**Fork it.** Copy the repository, delete what you do not need, change what you do. You
get everything immediately. You are also now maintaining a divergent copy: upstream fixes
do not reach you, your changes do not reach upstream, and after six months the two are
different systems.

**Import it.** `pip install package[guardrails]`, call it from your own application. You
get one component with a stable interface. Upgrades are a version bump. Your application
code stays yours.

Importing only works if three things are true:

1. **The package has no domain logic.** The moment it knows about *your* invoices, it
   only works for invoices.
2. **Each module installs independently.** If installing the guardrails pulls in the
   agent framework, a database driver and an ML stack, nobody will install it.
3. **Dependencies are injected, not imported.** If the package imports a specific model
   client, it works only for users of that client.

All three are consequences of the same discipline: the core is dependency-free, leaves
depend only on the core, and everything concrete arrives by injection.

**What it buys, concretely.** If the application composes importable modules and holds all
the domain knowledge itself, then pointing the whole platform at a *new problem* means
writing a new adapter — and changing nothing else. The guardrails do not know what they
are guarding. The forecaster does not know what it is forecasting. The retriever does not
know what it is retrieving.

That is not a refactoring benefit. It is a capability: it is the difference between "we
could rebuild this for another domain in a few weeks" and "we can retarget it by
implementing one interface."

---

## What you should now be able to explain

- Why folders are not a boundary, and how the dependency graph becomes a mesh
- The three rules of a Module Contract, and why the graph becomes a star
- Why the core's dependencies are everyone's dependencies
- Why `try/except ImportError` converts a deployment error into a silent behaviour change
- Why "fail loud with the install command" is the correct alternative
- Why the optional import belongs inside the function, not at module top level
- What structural typing buys, and why "depend on shapes, not packages" matters
- What a composition root is, and why the library keeps the ordering
- Why one emitter beats fourteen builders — wire format, ordering, name drift
- Why unregistered event names must raise rather than vanish
- How a span kind lets one stream serve a console and a tracer
- Why silent in-memory fallback is the worst of the three infra behaviours
- Declared mode versus resolved mode
- What "imported, not forked" requires, and what it buys

**Next:** [`10-theory.md`](10-theory.md).
