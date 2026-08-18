# Phase 8 — Modularity

**This is the thesis of the product, and on 30 August it gets tested for real — by an AI agent,
under time pressure, on a problem nobody has seen.**

Research: [`plans/05-modularity-scale.md`](plans/05-modularity-scale.md)

The bar is not "make it modular". It is:

> **An LLM handed a blind problem statement plus Aegis should produce a correct, fully-traced
> integration on the first attempt.**

That bar comes from the Mumbai retrospective: building the domain solution *on top of* Aegis
became bulky because Aegis is an application you **fork**, not a framework you **import** — and
when a coding agent was pointed at it, *"the agent did not pick this up, I had to do back and
forth."*

---

## The evidence that redirects the obvious approach

The instinct is "write better documentation". The evidence says that is the third lever, not the
first.

`arXiv 2603.15159` benchmarks exactly our case — an AI integrating an unseen private library.
Its **oracle condition — complete, correct specs for every API the task needs — moves pass@1 by
only +5.0 to +8.3 pp.** The conclusion: **the bottleneck is *invoking*, not *seeing*.**

Its two dominant error classes, and both are present in Aegis by name:

**1. Omitted necessary operations in a required sequence.** Aegis has **nine ordered
`configure_*` calls** writing ~35 process-global singletons. Three fire as **module-import side
effects** (`app/core/llm.py:216`, `app/core/security.py:52`, `app/ops/__init__.py:49`), so
import order is a hidden dependency. **Two of them fail *silently*** into an ephemeral in-memory
vector store when skipped — the integration appears to work and retrieves nothing durable.

**2. Signature misinterpretation.** Six of eight `AgentDeps` fields are typed
`Callable[..., Awaitable[Any]]`, and `aegis` has **no `py.typed`** — so every annotation is
invisible to the integrator's type checker anyway.

**Therefore: delete the sequence, make the contract executable, fail loud at the silent seams.**
Documentation comes after all three.

---

## What is actually wrong

### 1. Configuration is a nine-step ritual with no single entry point

`configure_governance` already proves the pattern — it exists solely to compose two
sub-configures — and nobody generalised it.

### 2. The integrator cannot tell what they must implement

The adapter is eight modules plus `corpus/` and `skills/`, and the docs say five, six and "of 6"
in the same directory (Phase 3 §3.11 fixes the sentences; this phase fixes the *structure*).

### 3. Nothing proves an integration is correct

There is no way for an integrator — human or agent — to run one command and learn whether they
wired it right. They find out when a query returns nothing.

### 4. The TypeScript client is hand-maintained

696 hand-written lines mirror 1,598 lines of Pydantic. `schemas.py` and `types.ts` have already
been modified together in a single uncommitted change during this project — the drift is not
hypothetical.

### 5. The most important interface has no machine-readable description

The `StreamEvent` union is the product's primary interface and exists only as Pydantic classes
plus a hand-written TypeScript mirror.

---

## Tasks

| # | Task | Days |
|---|---|---|
| 8.1 | `py.typed` + narrow the six `Any` callables to real Protocols | 0.5 |
| 8.2 | `DomainAdapter` Protocol — eight members, one thing to implement | 1.0 |
| 8.3 | `aegis.Aegis` runtime object — one way up, replacing nine `configure_*` | 1.5 |
| 8.4 | Fail loud at the two silent seams | 0.25 |
| 8.5 | Executable conformance suite | 1.0 |
| 8.6 | `/v1` prefix + OpenAPI snapshot test | 0.5 |
| 8.7 | Generate the TypeScript client | 0.75 |
| 8.8 | Publish the `StreamEvent` union as a schema | 0.25 |
| 8.9 | `PUBLIC.md`, SemVer, `CHANGELOG.md`, `deprecated()` | 0.5 |
| 8.10 | `pdoc` reference docs, git-ignored | 0.25 |
| 8.11 | `AGENTS.md` + one `SKILL.md` replacing `SWAP.md` | 0.5 |

**Total: 7.0 days.**

### 8.3 — The `Aegis` runtime object

```python
aegis = await Aegis.from_env(adapter="myapp.adapter")
```

One supported way up. The globals stay as an implementation detail — ripping out 35 call sites
buys no behavioural change, and this phase is about the *contract*, not the internals.

This is the task that addresses error class 1. A sequence you cannot get wrong beats a sequence
documented correctly.

### 8.4 — Fail loud at the two silent seams

The two `configure_*` calls that currently degrade to an ephemeral in-memory vector store must
**raise** instead. A silent downgrade is the single worst failure for a first-attempt
integration: everything appears to work, retrieval returns nothing durable, and the integrator
has no signal to follow.

This is four lines and it is arguably the highest-value task in the phase.

### 8.5 — The conformance suite

```
pytest --pyargs aegis.conformance
```

Distributed via the `pytest11` entry point, capped at **10–15 checks**, and **every check
traceable to a defect this repo actually shipped**: an adapter that declares a specialist with
no handler node, a tool with no risk tier, a persona the resolver rejects, a memory spec missing
a required field, a corpus that produces no chunks.

**It is a jury artifact, not just a dev tool.** "Here is the command an integrator runs to prove
they wired it correctly, and here is it passing" is a stronger claim than any architecture
diagram, and it belongs in the demo script.

### 8.11 — `AGENTS.md`, and why not `llms.txt`

**Adopt `AGENTS.md`** — Linux Foundation spec, 60k+ repositories, and read from a **local
checkout**, which is exactly our distribution model.

**Skip `llms.txt`** — 97% of them received zero requests in May 2026, and we have no docs site
for one to point at. Adopting it would be cargo cult.

One `SKILL.md` (`retarget-aegis`) becomes the authoritative retargeting procedure, replacing
`SWAP.md`.

---

## Definition of done

- [ ] `py.typed` ships; `mypy` on a toy integrator project sees real types for every `AgentDeps` member.
- [ ] `Aegis.from_env(adapter=...)` is the only documented way up, and it works from a clean process with no prior imports.
- [ ] Skipping either previously-silent `configure_*` **raises** with the exact fix in the message.
- [ ] `pytest --pyargs aegis.conformance` passes against the reference adapter and **fails** against a deliberately broken one — both tested.
- [ ] Every route is under `/v1`; the OpenAPI snapshot test fails on a removal.
- [ ] `types.ts` is generated; regenerating it produces no diff.
- [ ] `PUBLIC.md` lists ~40 Stable names out of 427 exported.
- [ ] `AGENTS.md` exists and a fresh agent session, given only the repo and a one-line problem statement, produces a working adapter — **actually tried, not assumed**.
- [ ] Full suites green, ruff clean, `next build` green.

## Demo at the end of this phase

Open a fresh session, hand an agent the repo and a one-paragraph problem statement, and watch it
produce a working adapter and run the conformance suite green — without back-and-forth.

That is the Mumbai lesson answered, on camera.

## Risks

**The `Aegis` object can become a god object.** It is a composition root, not a facade. If it
grows behaviour beyond wiring, it has failed.

**The conformance suite can become a second test suite.** Cap it at 15 checks. Every check
answers "did you wire this correctly", never "does this work well".

**Generating `types.ts` may produce worse ergonomics** than the hand-written one. Compare before
committing; a generated client nobody wants to use is not an improvement.

**The final DoD item is the only honest test of the whole phase** and it can fail late. Try it
early with a throwaway problem statement rather than discovering it on the 29th.
