# Plan 05 — Modularity, the public API, and scale

> **Scope.** Requirements **B1** (plug-and-play module contract), **B2** (public API surface
> and generated documentation) and **B3** (multi-user / multi-agent scaling architecture)
> from [`../01-V2-ADDITIONS.md`](../01-V2-ADDITIONS.md) §B.
>
> **The bar this plan is written against**, from the regional-round retrospective:
>
> > An LLM handed a blind problem statement plus Aegis should produce a correct, fully-traced
> > integration on the **first attempt**.
>
> That is not "make it modular". It is a testable property, and §2 is the evidence about what
> actually moves it — which turns out not to be what intuition suggests.
>
> **Owned by this plan:** the `aegis` package contract (`aegis/src/aegis/core/**`, the
> `configure_*` seams, `pyproject.toml` extras), the domain-adapter Protocol and its
> conformance suite, the HTTP versioning/OpenAPI story, the generated reference docs, and the
> per-process resource architecture.
> **Not owned:** the job/queue substrate, request tracking and the pipeline-health page
> (Plan 04, being written concurrently — see §8 for the two places this plan depends on it);
> concurrency *mechanism* for sub-agents (Plan 02 §2.1 settled it: `asyncio.gather` inside a
> `run_team` node — this plan takes that as given and asks what happens at N users);
> per-tenant vector/graph workspaces (Plan 03 §2.4 owns the design; §7.3 here states the
> scaling consequence).
> **Explicitly not duplicated:** Plan 01 §Phase 7 already specifies a `DomainAdapter`
> Protocol, an `AEGIS_ADAPTER` env setting and an adapter conformance test. This plan
> **adopts that** and extends it in the three places it does not reach: the *runtime* seam
> (§4.1), the executable checker as a shipped artifact (§4.3), and AI-legibility (§4.4–4.6).

---

## 1. Ground truth — measured, not recalled

Everything numbered here was read out of the source or measured on this machine. Where a
document in this repo says otherwise, that is called out, because two of the documents you
would read first are wrong about the code.

### 1.1 What `aegis` is today, in numbers

| Fact | Value | Source |
|---|---|---|
| Importable packages under `aegis.*` | 18 | `aegis/src/aegis/*/` |
| Names in the union of every package `__all__` | **427** | counted across `aegis/src/aegis/*/__init__.py` |
| `aegis.governance` alone | 78 exported names | `governance/__init__.py` |
| Install extras | 16 (+ `all`, `dev`) | `aegis/pyproject.toml` |
| Examples shipped | **1**, 42 lines, guardrails only | `aegis/examples/` |
| `py.typed` markers | **0** | `find aegis/src -name py.typed` |
| Backend HTTP endpoints | ~60, one `APIRouter`, no version prefix | `backend/src/app/api/routes.py` (3 066 lines) |
| Hand-written TS mirrors of the Pydantic schemas | 696 lines mirroring 1 598 | `web/src/lib/api/types.ts` vs `backend/src/app/api/schemas.py` |
| Process-global mutable singletons | **~35** (`global` statements) | `grep -rn "global " aegis/src backend/src` |
| `configure_*` / `set_default_*` / `init_*` entry points | **9** | `aegis/src/aegis/**` |

427 public names with one 42-line example is not a library surface, it is a codebase with
`__init__.py` files. That is the honest starting point.

### 1.2 The Module Contract is enforced in exactly one place

`aegis/tests/core/test_core_is_dep_free.py` spawns a subprocess, imports `aegis.core` and
asserts that none of eleven heavy modules landed in `sys.modules`. That is a real, good test.

It is also the *only* structural enforcement. As `docs/teaching/core/10-guide.md` §"Where the
star still has chords" already admits, in the repo's own words:

> The isolation tests check *heaviness*, not *topology*, which is exactly why topology
> drifted. … A rule with a test is an invariant. A rule with a paragraph is a hope.

The drift is documented in `MODULE_REFERENCE.md` under "Known deviations" (memory→retrieval,
governance→gateway, vision/voice→media+guardrails) and is real. `aegis/guardrails/__init__.py`
imports `aegis.media` at module import time, so `pip install aegis` + `from aegis.guardrails
import check_input` — the README's headline quick-start — drags in a second leaf package.

### 1.3 The composition root is 512 lines the package does not ship

`aegis.agent.deps.AgentDeps` has **eight required fields**, six of them typed
`Callable[..., Awaitable[Any]]`. The docstring is explicit that `AgentDeps.default()` "lives
host-side". It does: `backend/src/app/agent/deps.py`, 512 lines, containing the concrete
`MemoryDeps` implementation, eleven `_default_*` binding functions and the wiring of
gateway / retrieval / guardrails / adapter / answer-cache / tenant-scope / audit / embedder.

So the answer to "what does a consumer import, and what must they implement?" is currently:
**they import 427 names and they must implement 512 lines of composition root that the package
gives them no template for.** That is the fork-not-import failure, unchanged, one level down.

Only three of the eight are typed precisely enough to check: `RetrieveFn` (a real Protocol,
and the comment says why — it is a security boundary), `MemoryDeps` and `AnswerCache`. The
rest are `Callable[..., Awaitable[Any]]`, which is a type that accepts anything.

### 1.4 Configuration is nine global mutations, three of them import-time side effects

| Seam | Configured from | When |
|---|---|---|
| `aegis.gateway.configure(...)` | `app/core/llm.py:216` | **module import** |
| `aegis.governance.security.configure_security(...)` | `app/core/security.py:52` | **module import** |
| `aegis.ops.configure_ops(...)` | `app/ops/__init__.py:49` | **module import** |
| `aegis.governance.configure_governance(...)` | `app/data/governance.py:77` | module import |
| `aegis.memory.set_default_index(...)` | `app/main.py:211` | lifespan startup, `if stores_enabled and not is_dev` |
| `aegis.observability.init_observability(...)` | `app/main.py:154` | lifespan startup |
| `aegis.memory.set_default_spec(...)` | via `app/memory/__init__.py` | module import |
| `aegis.governance.configure_enforcement` / `configure_audit` | folded into `configure_governance` | — |

Two consequences, both load-bearing:

1. **Import order is a hidden dependency.** `import app.ops` has a side effect. An integrator
   who imports in a different order, or who imports `aegis.ops` without the host shim, gets a
   package configured differently — silently, in three of the nine cases.
2. **Missing a call fails in two different ways.** `aegis.governance` raises a named
   `RuntimeError` telling you to call `configure_enforcement` — excellent. But
   `aegis.memory.vector_ops.get_default_index()` **builds an ephemeral in-memory Chroma
   engine on first use** (`vector_ops.py:306`), and
   `aegis.retrieval.memory.MemoryBackend.__init__` does
   `self._vector_store = vector_store or ChromaVectorStore.local()` (`memory.py:222`). Forget
   the wiring and you get a working system with no durable vectors and no error. That is the
   platform's own "no silent fallbacks" principle violated inside the package that defines it.

There is already a good precedent for the fix in-repo: `configure_governance` exists purely to
compose `configure_enforcement` + `configure_audit` behind one call. It is used, it works, and
nobody has generalised it. (`configure_security` is not folded in, for no stated reason.)

### 1.5 The adapter seam has eight pieces; the docs say five, and the code says six

| Source | Claim |
|---|---|
| `adapter/README.md` | "the **five** swappable domain pieces" |
| `adapter/SWAP.md` | a six-step retarget checklist |
| `adapter/memory_spec.py` line 1 | `"""Memory contract — **piece 6 of 6**` |
| The directory | `schema.py`, `generator.py`, `tools.py`, `personas.py`, `prompts.py`, `ml_spec.py`, `memory_spec.py`, `roster.py`, `corpus/`, `skills/` — **eight** pieces plus two data directories |

`roster.py` and `skills/` appear in **no** checklist. `memory_spec.py` appears in one file's
docstring and in neither README nor SWAP.md. And `memory_spec` is not re-exported from
`adapter/__init__.py` at all — `app/api/routes.py:906` and `:1498` and `app/memory/__init__.py:28`
reach past the declared seam with `from app.adapter.memory_spec import ...`.

So on the morning of 30 August, an agent handed `SWAP.md` retargets six of eight pieces and
leaves the memory contract and the agent roster pointing at customer support. It will not
error. It will just be subtly wrong, in the two subsystems the demo is built on.

### 1.6 The package is invisible to a type checker, and its README documents an API that does not exist

- **No `py.typed`.** Under PEP 561 a type checker treats an unmarked installed package as
  untyped and discards every annotation in it. Every Protocol, every `RiskLevel`, every
  `GuardResult` in this package is invisible to pyright/mypy in a consumer's project — which
  is precisely the signal an AI agent's IDE integration reads. This is a one-line fix with
  disproportionate value.
- **`aegis/README.md` documents `aegis.require()`.** Verified against `aegis/.venv`:

  ```
  >>> import aegis; hasattr(aegis, "require")
  False
  ```

  The function is `aegis.core.require`. `aegis/__init__.py` exports exactly one name,
  `__version__`. The first code an integrator copies out of the README raises `AttributeError`.

### 1.7 The HTTP surface has no version, no contract test, and a hand-copied client

- ~60 routes on one unprefixed `APIRouter`. `FastAPI(version="0.1.0")` is metadata only; no
  path or header carries it.
- OpenAPI is generated (and the metadata is unusually rich — `main.py` `_DESCRIPTION` is 20
  lines of honest capability naming). **Nothing consumes it.** No schema snapshot in CI, no
  client generation, no drift test.
- `web/src/lib/api/types.ts` is 696 lines of hand-maintained TypeScript mirroring the Pydantic
  models. The working tree *as I read it* has `backend/src/app/api/schemas.py` and
  `web/src/lib/api/types.ts` both modified in the same uncommitted change — the manual mirror
  being maintained by hand, right now.
- No `CHANGELOG`, no deprecation mechanism, no `DeprecationWarning` anywhere in `aegis/src`.
  Both `pyproject.toml`s say `version = "0.1.0"`.

### 1.8 The runtime is one process, one event loop, three embedded stores

- `scripts/start.ps1` and `start.sh` launch `uvicorn app.main:app --port 8000` — **no
  `--workers`**. One process, one event loop.
- Three in-process background tasks are created in the lifespan: the SLA sweeper, the memory
  consolidation sweeper, the ML warm-up. Each is supervised with a done-callback that logs
  loudly if it dies — that part is done well.
- **SQLAlchemy pools are entirely unconfigured.** `grep -rn "pool_size|max_overflow|pool_pre_ping|pool_recycle"`
  over `aegis/src` and `backend/src` returns nothing. Both engines take the SQLAlchemy
  default: `pool_size=5`, `max_overflow=10` → 15 connections each, then a 30-second block and
  a `TimeoutError`. The live cluster reports `max_connections = 100`, `shared_buffers = 128 MB`.
- **Three separate embedded stores, all in-process, all single-writer:**
  1. **Chroma** — `PersistentClient` under `VECTOR_STORE_PATH`, one collection with a
     `tenant_id` metadata filter (`TENANT_METADATA_KEY = "tenant_id"`), not per-tenant
     collections. The wrapper is genuinely good: `.server(url=...)` and `.local(path=...)` are
     explicit modes and server mode heartbeats at construction and fails loud.
  2. **LightRAG's NanoVectorDB** — `working_dir="rag_storage"`, a **relative** path, so the
     store location depends on the process CWD (there is a stray `aegis/rag_storage/` in the
     tree proving it). Not per-tenant.
  3. **Neo4j** — a server, so it is not a per-process constraint. One shared graph, no tenant
     workspace (Plan 03 §2.4 owns this).
- **A blocking call on the event loop.** `aegis/memory/vector_ops.py` correctly wraps every
  Chroma call in `asyncio.to_thread` (lines 181, 182, 240). `aegis/retrieval/memory.py:509`
  does **not** — `self._vector_store.search(...)` is called directly inside
  `async def _vector_list`. Same engine, two conventions, one of which stalls the loop.
- **Budget enforcement costs 2–4 Postgres aggregates per model call.**
  `aegis/governance/enforcement.py:enforce_governance` opens a session, sets the tenant GUC,
  loads the governing budget rows and runs a `SUM` over `usage_ledger` per row — twice when
  rpm/tpm are set. `UsageLedger` has single-column indexes on `tenant_id`, `user_id` and `ts`
  and **no composite `(tenant_id, ts)`**.

### 1.9 What "multi-agent" means today

Plan 02 §1.1 established it and I re-verified it: `grep -n "asyncio.gather"` over
`aegis/src/aegis/agent/*.py` returns **nothing**. The graph is strictly sequential; the
"supervisor" picks exactly one of two specialists. Everything in §7 about *multi-agent* scale
is therefore about the design Plan 02 §2.1 specifies (`asyncio.gather` inside a `run_team`
node), not about code that exists. That is stated as an assumption, not smuggled in as fact.

---

## 2. What actually makes a package AI-integrable — the evidence

This is the section the requirement asked for evidence on, and the evidence is
counter-intuitive enough that getting it wrong would send the whole plan in the wrong
direction.

### 2.1 The finding that changes the design

*To See is Not to Master: Teaching LLMs to Use Private Libraries for Code Generation*
([arXiv 2603.15159](https://arxiv.org/html/2603.15159v4)) benchmarks exactly our situation: a
library the model has never seen, and a task that requires using it. Their **oracle**
condition hands the model complete specifications of every API it needs — the theoretical
ceiling of any documentation, RAG or context-stuffing strategy.

| Model | pass@1 without specs | pass@1 with **complete** specs | Gain |
|---|---|---|---|
| DeepSeek-Coder-6.7B | 10.59% | 17.97% | **+7.4 pp** |
| LLaMA-3.1-8B | 8.13% | 13.10% | **+5.0 pp** |
| LLaMA-3.1-70B | 35.88% | 44.20% | **+8.3 pp** |

Their conclusion, quoted: *"the key bottleneck is enabling LLMs to **invoke** private-library
APIs effectively, rather than merely **seeing** required API knowledge."*

Their error taxonomy has two dominant classes:

1. **Omitted necessary operations** — the model skipped a required call in a required
   sequence (their example: calling `multiply` without `asarray` first).
2. **Signature misinterpretation** — right function, wrong arguments.

### 2.2 Both failure modes are already present in Aegis, by name

- *Omitted necessary operations* is **§1.4 exactly**: nine `configure_*` calls in a required
  order, three of which happen as import side effects, two of which fail silently into a
  degraded in-memory store when skipped. And it is **§1.5 exactly**: eight adapter pieces, six
  of them documented.
- *Signature misinterpretation* is **§1.3 exactly**: six of `AgentDeps`' eight required fields
  typed `Callable[..., Awaitable[Any]]`, in a package with no `py.typed` so the type checker
  would not have helped anyway.

### 2.3 Therefore: the fix is a smaller, more prescriptive surface plus an executable checker — not more documentation

This is the load-bearing judgement of the plan, and it cuts against the instinct to answer
"make it AI-legible" with "write a great AGENTS.md and generate beautiful reference docs".
Documentation buys 5–8 pp. What buys more:

- **Delete the sequence.** If nine ordered calls become one, "omitted necessary operations"
  has nothing to omit. This is the single highest-leverage change in the plan.
- **Make the contract executable.** A checker the integrator *runs* converts a silent
  misinterpretation into a named failure with a remedy — the same trick
  `aegis.core.lazy.require()` already plays for missing dependencies, generalised from
  dependencies to contracts. An agent that can run a command and read an error message
  iterates to correct; an agent reading prose guesses.
- **Fail loud at the two silent seams**, so a skipped step cannot produce a working-looking
  system.

Documentation is still worth doing — it is cheap and 5–8 pp is not nothing — but it is the
*third* lever, not the first, and the plan sequences it that way.

### 2.4 Which docs format, from the 2026 evidence

| Artifact | Verdict | Why |
|---|---|---|
| **`AGENTS.md`** at repo root | **Adopt** | Formalised as an open spec in Aug 2025, donated to the Linux Foundation's Agentic AI Foundation in Dec 2025; 60 000+ repos and 30+ agents read it, including Claude Code, Codex, Copilot, Cursor, Gemini CLI, Aider, Zed ([spec guide](https://asdlc.io/practices/agents-md-spec/), [field guide 2026](https://www.iuriio.com/blog/posts/2026/05/agents-md-field-guide-2026)). It is read from a **local checkout**, which is exactly our distribution model. |
| **`llms.txt`** | **Skip** | It is a *hosted-docs-site* convention and we have no docs site. Ahrefs' 2026 log study across 137 000 domains found **97% of `llms.txt` files received zero requests in May 2026** ([Fern](https://buildwithfern.com/post/optimizing-api-docs-ai-agents-llms-txt-guide), [llmtxt.info](https://llmtxt.info/what-is-llms-txt/)). IDE agents do fetch it *when pointed at a docs site* — so revisit if and only if we publish one. Adding it now is cargo cult. |
| **Agent Skills (`SKILL.md`)** | **Adopt, narrowly — one skill** | Open spec at `agentskills.io` since Dec 2025, ~40 compatible products by Jun 2026 ([spec](https://deepwiki.com/anthropics/skills/6.1-agent-skills-specification), [ecosystem report](https://agentman.ai/blog/agent-skills-ecosystem-report-2026)). Its progressive-disclosure model — name + description always loaded, body loaded on demand — is the right shape for the one procedure that matters: *retarget Aegis to a new domain*. See §4.5. Note the repo **already** has a filesystem-markdown skills mechanism (`adapter/skills/`, Plan 02 §1.6) — this aligns the two rather than adding a third. |

**Sequencing note.** `AGENTS.md` describes how to *work in this repo*. The adapter skill
describes how to *retarget the platform*. They are different documents with different
audiences and both are cheap; the mistake would be writing one 400-line file that is neither.

---

## 3. The three decisions everything else follows from

### Decision A — One runtime object replaces nine global mutations

**Recommendation: introduce `aegis.Aegis` (a runtime handle), make the nine `configure_*`
functions a thin compatibility layer over it, and make one call the only supported way to
stand the package up.**

```python
from aegis import Aegis

rt = await Aegis.from_env(adapter="myapp.adapter")   # one call, fails loud, returns a handle
deps = rt.agent_deps()                               # the 512-line composition root, shipped
app  = rt.fastapi()                                  # optional: the mounted HTTP surface
```

What it must do, and what breaks without each:

| Responsibility | What breaks without it |
|---|---|
| Resolve and validate `AegisSettings` from env (already exists as `CoreSettings`) | today's `AEGIS_MODE` is defined in `aegis.core.config` and **not adopted by the backend boot path at all** (`MODULE_REFERENCE.md` lists this under "Scaffolding not yet wired") |
| Own the engine / sessionmaker and hand it to governance, ops, memory | three separate `configure_*` calls the integrator must remember |
| Own the vector store and inject it — never let a leaf default to `ChromaVectorStore.local()` | §1.4's silent ephemeral store |
| Import and validate the domain adapter named by `AEGIS_ADAPTER` | Plan 01 §Phase 7's env-switchable adapter has nowhere to be resolved |
| Build `AgentDeps` from the above | §1.3's 512 lines of host glue |
| Expose `await rt.aclose()` | engines, Chroma clients and Neo4j drivers currently leak on shutdown |

**Why one object and not nine tidier functions.** Calibration rule 2 — one mechanism used
well. Nine `configure_*` calls is three specialised mechanisms wearing a coat: import-time
side effects, explicit startup calls, and lazy-default singletons. Collapsing them removes the
ordering hazard (§2.2) rather than documenting it.

**Why this is not over-engineering.** It is a *composition* object, not a framework. It adds
no indirection at call time: `rt.agent_deps()` returns the same `AgentDeps` dataclass that
exists today, and every existing `configure_*` function keeps working (implemented as
`rt._apply()` under the hood) so nothing in `backend/` has to change on day one.

**The concession I am making explicitly:** the module-level globals stay, because ripping them
out means touching 35 call sites for no behavioural gain. `Aegis` becomes the *sanctioned*
way to set them, and the globals become an implementation detail. Two `Aegis` instances in one
process is therefore **not** supported, and the constructor should say so out loud (raise on a
second construction unless `allow_reconfigure=True`). Honest limitation, loudly stated, beats a
per-instance refactor nobody asked for.

### Decision B — The integrator implements exactly one thing: `DomainAdapter`

Plan 01 §Phase 7 already specifies this Protocol and the `AEGIS_ADAPTER` setting. This plan
adds the two things that make it *checkable* rather than nominal.

**B.1 — The Protocol lists all eight pieces, and the count is asserted.** `roster` and
`skills_dir` join the six in `SWAP.md`; `memory_spec` gets promoted into
`adapter/__init__.__all__` and the three modules that reach past the seam
(`app/api/routes.py:906`, `:1498`, `app/memory/__init__.py:28`) are re-pointed at it.

```python
# aegis/src/aegis/adapter/protocol.py  (illustrative shape, not final)
@runtime_checkable
class DomainAdapter(Protocol):
    DOMAIN_ID: str
    DOMAIN_DESCRIPTION: str
    schema: ModuleType          # entities + enums
    tools: ToolContract         # TOOL_REGISTRY, ALLOWLIST, run_tool, tool_definitions_for
    personas: PersonaContract   # PERSONAS, get_persona, DEFAULT_PERSONA_ID
    prompts: PromptContract     # SYSTEM_PROMPTS, render_system_prompt
    ml_spec: MLSpecContract     # FEATURES, TARGET, feature_matrix, describe_prediction
    memory_spec: MemoryContract # FACT_TYPES, PROFILE_FIELDS, memory_subject_for, SKILLS_DIR
    roster: RosterContract      # agent_roster()
    corpus: CorpusContract      # load_seed_corpus()
    generator: GeneratorContract# generate_synthetic, generate_synthetic_sync
```

**B.2 — Every member is a *narrow* Protocol, not `Any`.** This is where §2.2's "signature
misinterpretation" is actually paid down. `RetrieveFn` in `aegis/agent/deps.py` is already the
model to copy — a keyword-only, no-default `scope` parameter, with a comment saying it is
spelled out because it is a security boundary. Apply the same treatment to the six loose
`Callable[..., Awaitable[Any]]` fields on `AgentDeps`. The runtime cost is zero; the payoff is
that a wrong signature is a red squiggle before it is a runtime failure.

**B.3 — `py.typed` in `aegis/src/aegis/`.** Without it, B.2 is invisible to every consumer.
One empty file; add it to `[tool.hatch.build.targets.wheel]`.

### Decision C — The contract ships as an executable conformance suite

**Recommendation: ship `aegis.conformance` as a pytest plugin, published under the `pytest11`
entry point, that an integrator runs against *their* adapter and *their* runtime.**

```bash
pip install "aegis[dev]"
pytest --aegis-adapter=myapp.adapter --pyargs aegis.conformance
```

```
aegis conformance — myapp.adapter
  adapter/structure ......... 8/8 pieces present
  adapter/tools ............. FAIL
      escalate_case is in TOOL_REGISTRY but not in ALLOWLIST for any persona.
      Fix: add "escalate_case" to ALLOWLIST["supervisor"] in myapp/adapter/tools.py:214.
  adapter/personas .......... 2 personas, every allowlist non-empty
  adapter/ml_spec ........... feature_matrix -> (120, 9) float, y dtype float64
  adapter/memory_spec ....... 4 fact types, 8 profile fields, skills dir readable
  adapter/roster ............ 2 specialists, exactly one is_default
  runtime/wiring ............ vector store = chroma(local:/data/vec) [durable]
  runtime/tenancy ........... FAIL
      RLS policy missing on 1 of 14 tenant-scoped tables: myapp_cases.
  10 checks, 2 failed
```

Why this and not "write good docs":

- It is the **only** artifact in this plan that turns a wrong integration into a *named error
  with a remedy*, which is what an agent can act on. It is the contract-level analogue of
  `require()`'s `"Run: pip install aegis[nemo]"`.
- The failures it must catch are the ones §1 found by hand: a tool registered but not
  allowlisted, an adapter piece missing, a persona with an empty allowlist, a tenant-scoped
  table with no RLS policy, a vector store that resolved to `:memory:`, `feature_matrix`
  returning something untrainable. Each of those is a real defect class this repo has shipped.
- Distribution via `pytest11` is the established mechanism for third-party implementers to run
  an upstream suite against their own code ([pytest plugin docs](https://docs.pytest.org/en/stable/how-to/writing_plugins.html)).
- It is also the **demo**: "here is a second, unrelated domain adapter, and here is the suite
  proving it is wired correctly" is Plan 01 §Phase 7's live-switch demo with evidence attached.

**Scope discipline.** This is a conformance suite, not a test framework. Ten to fifteen checks,
each one corresponding to a defect this codebase has actually had. If a check does not map to
a real past failure, it does not go in.

---

## 4. B1 — the module contract, in full

### 4.1 What a consumer imports

Exactly three things, and this is the whole public story:

```python
from aegis import Aegis                       # the runtime (Decision A)
from aegis.adapter import DomainAdapter       # the one Protocol they implement (Decision B)
from aegis.core import GuardResult, RiskLevel, SpanKind, require   # shared vocabulary
```

Everything else — `aegis.retrieval`, `aegis.memory`, `aegis.governance`, … — remains
importable for the à-la-carte case the README advertises (`pip install aegis[guardrails]`,
`check_input(...)`, standalone), which genuinely works today and must not be broken. But it
stops being the *documented path to a platform*. One obvious way.

### 4.2 What a consumer implements

One package satisfying `DomainAdapter`, eight members. Nothing else. Every capability that is
currently host-side and could have been packaged moves into `Aegis`:

| Moves from `backend/` into `aegis` | Lines today |
|---|---|
| `AgentDeps.default()` and its eleven `_default_*` bindings | ~250 of `app/agent/deps.py` |
| The DB-backed `MemoryDeps` implementation | ~180 of `app/agent/deps.py` |
| Engine / sessionmaker / bootstrap ownership | `app/data/session.py` (458) — *the RLS split logic is already in `aegis.governance.rls`; only the engine plumbing is host-side* |
| The lifespan sweepers | `app/main.py` 93–126, 217–248 — **but see §8: Plan 04 may own these as jobs** |

What legitimately stays in `backend/`: the FastAPI route bodies, CORS, the CLI scripts, and
`app/platform/**` (stack/risk-map/savings — genuinely this product's own surfaces). That is a
composition root. 512 lines of dependency wiring is not.

### 4.3 Where the seam still leaks, ranked by how likely it is to bite on 30 August

| # | Leak | Consequence | Fix |
|---|---|---|---|
| 1 | `memory_spec`, `roster`, `skills/` absent from `SWAP.md` (§1.5) | retarget day: memory + routing silently stay on customer support | conformance check `adapter/structure`; rewrite `SWAP.md` from the Protocol |
| 2 | `app.adapter.memory_spec` imported directly in 3 places | the declared seam is not the real seam | re-export; conformance check forbids deep imports |
| 3 | Silent `ChromaVectorStore.local()` / `get_default_index()` defaults (§1.4) | a "working" system with ephemeral vectors | require injection; raise unless `AEGIS_MODE=lite` |
| 4 | `working_dir="rag_storage"` is CWD-relative (§1.8) | run from a different directory → empty graph | absolute path from settings; conformance check reports the resolved path |
| 5 | Six `AgentDeps` fields typed `Any` (§1.3) | signature misinterpretation, silently | narrow Protocols (Decision B.2) |
| 6 | No `py.typed` (§1.6) | none of the above is visible to a checker | one file |
| 7 | `guardrails` imports `media` at import time (§1.2) | `pip install aegis` alone breaks the README quick-start if `media` gains a hard dep | hoist the payload types into `aegis.core.types` — already the stated fix in `MODULE_REFERENCE.md` |
| 8 | `README.md` documents `aegis.require()` (§1.6) | the first copied line raises | fix the README |

### 4.4 `AGENTS.md` — what goes in it

At the repo root, ~120 lines, and it is a *map plus commands*, not an essay. The 2026 field
guides converge on: build/test commands, boundaries, and conventions — not architecture prose.

```
# AGENTS.md
## What this repo is           (3 lines + the fork-vs-import thesis)
## Layout                      aegis/ = library · backend/ = composition root · web/ = console
## Commands                    install · test · lint · run · conformance
## The one thing you will be asked to do
    Retargeting to a new domain -> see .claude/skills/retarget-aegis/SKILL.md
## Boundaries
    - Never import app.* from aegis.*
    - Never import a leaf aegis module from another leaf; hoist to aegis.core
    - Every new tenant-scoped table registers in aegis.governance.rls
    - No silent fallbacks: a control that cannot run fails closed and says so
## Verify before you claim done
    pytest --pyargs aegis.conformance ; ruff check ; pytest
```

The four boundary rules are the invariants §1.2 says are currently hopes. Two of them
(`no app.* in aegis.*`, `no leaf-to-leaf`) should get a **topology test** alongside the
existing heaviness test — the teaching guide already names this as the missing piece, and it
is ~30 lines of `ast` walking over `aegis/src`.

### 4.5 One skill: `retarget-aegis`

A `SKILL.md` whose description is *"Retarget the Aegis platform to a new problem domain: which
files to write, in what order, and how to prove it worked."* Body = the eight-piece checklist,
the scaffold command, the conformance command, and the three invariants that must hold after.

Progressive disclosure is exactly right here: on 30 August the agent's context is full of the
*problem statement*, and it should pull the retarget procedure on demand rather than carrying
it. This replaces `SWAP.md` as the authoritative procedure (`SWAP.md` becomes a pointer) so
there is one document, not two that will drift.

### 4.6 `python -m aegis.scaffold`

```bash
python -m aegis.scaffold adapter myapp.adapter --domain "grid outage triage"
```

Writes eight files with every required symbol present, typed, docstringed, and **failing** —
each `NotImplementedError` naming what the domain author must decide. Then prints the
conformance command.

Why a scaffold rather than "copy the existing adapter": copying `service_request_management`
is what produces a half-retargeted domain, because the copy *works* and therefore nothing
forces the untouched pieces to be noticed. A scaffold that fails until filled in inverts that.
This is the same fail-closed principle the platform already applies to infrastructure, applied
to the domain seam.

### 4.7 What I am deliberately **not** recommending

| Not doing | Why |
|---|---|
| Splitting `aegis` into multiple wheels | One repo, one wheel, extras. The leaf-to-leaf debt (§1.2) would have to be paid first, and the *only* benefit is a distribution property nobody is asking for. `MODULE_REFERENCE.md` already frames the debt correctly as "debt against a future multi-wheel split" — leave it there. |
| An MCP server exposing the library to agents | Plan 02 §Phase 4 owns MCP for *tools*. An MCP server whose purpose is "let an agent read our API" is a docs site with extra steps, and the §2.1 evidence says the docs are not the bottleneck. |
| A plugin/entry-point system for adapters | `aegis.core.registry.discover()` already exists, is exported from nowhere, and is called by nothing. `AEGIS_ADAPTER=<module path>` is simpler and sufficient. Either wire `discover()` into the adapter resolution or delete it — a registry with one registration (`@register("guardrail","default")`) and zero lookups is not earning its place. |
| Abstract base classes instead of Protocols | Protocols are correct here: the implementations are host-owned and often plain modules, which cannot inherit. Keep `@runtime_checkable` so the conformance suite can assert structurally at runtime. |
| Semantic-versioning the *internal* module boundaries | 427 names cannot all be stable. §5 narrows the surface first; versioning an unnarrowed surface just makes every change a major. |

### 4.8 Change list, in dependency order

| # | Change | Depends on |
|---|---|---|
| 1 | `py.typed`; fix `README.md`; fix `SWAP.md`/`README.md` to name eight pieces | — |
| 2 | Promote `memory_spec` into `adapter/__init__.__all__`; re-point the 3 deep imports | — |
| 3 | Narrow the six `AgentDeps` `Any` callables to Protocols | 1 |
| 4 | `aegis.adapter.DomainAdapter` Protocol (all eight members) | 3 · Plan 01 §Phase 7 |
| 5 | `aegis.Aegis` runtime; move `AgentDeps.default()` + `MemoryDeps` into the package | 4 |
| 6 | Remove the two silent store defaults; fail closed outside `lite` | 5 |
| 7 | `aegis.conformance` pytest plugin (10–15 checks) | 4 · 5 |
| 8 | `python -m aegis.scaffold adapter` | 4 · 7 |
| 9 | Topology test (no `app.*` in `aegis.*`; no leaf-to-leaf) | — |
| 10 | `AGENTS.md` + `retarget-aegis` SKILL.md | 7 · 8 |

Items 1–2 are hours and remove two live documentation lies; do them regardless of whether the
rest of the plan is approved.

---

## 5. B2 — the public API surface, versioning, and generated docs

### 5.1 Public vs internal — the rule, and how it is enforced

**Rule.** A name is public if and only if it is in the `__all__` of a package `__init__.py`
*and* listed in that package's `PUBLIC.md` table. Everything else — including every
submodule — is internal, regardless of leading underscore.

**Why a second list rather than just `__all__`:** `__all__` today is a re-export convenience
that has accumulated to 427 names. It cannot be both "everything importable" and "everything
supported". The `PUBLIC.md` table is the supported set; `__all__` stays as-is so nothing
breaks.

**Enforcement, in one test.** Snapshot every `__all__` to `aegis/tests/api_surface.json`.
The test diffs live against snapshot and fails on any *removal or rename* with the message
"public surface changed — update `PUBLIC.md` and `CHANGELOG.md`, or mark deprecated".
Additions pass silently. This is ~40 lines and it is the difference between a versioning
policy and a versioning intention.

**The narrowing target.** Of 427 names, the ones an integrator plausibly touches are ~40
(`Aegis`, `DomainAdapter`, the `aegis.core` vocabulary, the eight adapter contracts, and the
result types they receive). `governance`'s 78 and `memory`'s 46 are almost entirely internal
plumbing that happens to be re-exported. **Do not delete them** — mark them. `PUBLIC.md` has
three tiers: **Stable** (~40), **Provisional** (may change in a minor), **Internal** (exported
for the host shims; no compatibility promise).

### 5.2 Versioning and deprecation

- **`aegis` follows SemVer from `0.2.0`** — the version bump is the signal that a contract now
  exists. Pre-1.0 semantics: breaking changes in a minor, and say so in `CHANGELOG.md`.
- **Deprecation policy, stated in three lines and mechanised in one helper:**
  `aegis.core.deprecated(since, removed_in, use)` emits a `DeprecationWarning` naming the
  replacement. A Stable name is warned for one minor before removal. No other mechanism —
  there is currently zero deprecation machinery in the package, so this is net-new and must
  stay one function.
- **`CHANGELOG.md`, Keep-a-Changelog format**, at `aegis/CHANGELOG.md`. It doubles as the
  answer to a jury question about release discipline, which today has no answer.

### 5.3 The HTTP layer

**What FastAPI already gives us, honestly assessed.** A complete OpenAPI 3.1 document with
per-tag descriptions, response models on ~50 of ~60 routes, and unusually good prose. That is
genuinely most of the job and it is generated from the code, so it cannot lie about shapes.

**What is missing, in order of value:**

1. **A version prefix.** Mount the existing router at `/v1` and keep the unprefixed paths as
   a deprecated alias for one release. Without it there is no way to change a response shape
   without breaking the console — which is why the console's types are hand-mirrored and
   drifting. *This is the single change that makes the HTTP surface a contract.*
2. **An OpenAPI snapshot test.** `backend/tests/api/test_openapi_contract.py`: dump
   `app.openapi()`, diff against `backend/openapi.json`, fail on any **removed path, removed
   field, or narrowed type**; pass on additions. Regenerating is one command. This is the
   HTTP twin of §5.1 and it is what stops the schema/console drift visible in the working tree
   right now.
3. **Generate the TypeScript client.** `openapi-typescript` over the committed
   `backend/openapi.json`, emitting `web/src/lib/api/generated.ts`, with the 696 hand-written
   lines reduced to the SSE decoder and the thin fetch wrapper. Two consequences worth naming:
   the console can no longer disagree with the backend, and `web/src/lib/streamNames.ts`'s
   value-for-value mirror of `aegis.core.stream_names` gets the same treatment (emit the enum
   from Python at build time). Both mirrors currently rely on a human noticing.
4. **The SSE event union in the schema.** The streaming contract — `StreamEvent`, the thing
   the whole console is built on — is not in OpenAPI at all, because SSE bodies are not typed
   by FastAPI. Publish it explicitly: a `StreamEvent` discriminated union in
   `components/schemas` plus a documented note on `/v1/query`. Without this, the most important
   contract in the product is the one with no machine-readable description.
5. **Error shape.** Adopt RFC 9457 `application/problem+json` for the ~60 routes, or document
   the current shape. Right now an integrator learns error shapes by triggering them.

**Not recommended:** API-key auth for programmatic consumers, rate limiting at the HTTP edge,
or a separate public/internal API split. The gateway already enforces per-tenant rpm/tpm where
the cost is; a second limiter at the edge is a second thing to keep truthful.

### 5.4 Reference documentation — plan the gap, not a rewrite

`docs/teaching/` is 16 modules of guide + diagrams + interview per package, and it is
current and accurate — §1.2's most useful finding came out of it admitting its own gap. It is
a *course*. `docs/module/MODULE_REFERENCE.md` is the *contract*. Neither is a *reference*, and
that is the gap: there is nowhere to look up a signature.

**Recommendation: generate the reference from the source; do not write it.**

- **`pdoc` over `aegis/src/aegis`**, emitting to `docs/reference/`. Chosen over Sphinx and
  MkDocs-material because it is zero-config, reads the Google-style docstrings the repo
  already enforces via `ruff` `D` + `pydocstyle convention = "google"`, and adds one dev
  dependency rather than a toolchain. The docstring quality in this codebase is genuinely
  high — this is the cheapest large documentation win available.
- **`docs/reference/` is generated, git-ignored, and rebuilt by a script** next to the existing
  `scripts/build-teaching-html.mjs`. Committing generated docs is how they become stale lies.
- **`PUBLIC.md` per package is hand-written** (10–30 lines: the tier table plus one worked
  example). This is the only new prose, and it is the part generation cannot do.
- **Keep-it-true test:** the surface snapshot (§5.1) already fails when a public name moves,
  which is the same condition that would invalidate `PUBLIC.md`.

**Explicitly not doing:** a docs *site*, versioned docs, or a docs deploy. There is one
engineer and no consumers yet. Revisit `llms.txt` (§2.4) only if a site ever ships.

---

## 6. B3 — multi-user and multi-agent scale

### 6.1 The honest target, stated first

This must **credibly** scale and be defensible to a technical jury. It does not need to serve
production load, and pretending otherwise is the failure mode `01-V2-ADDITIONS.md` warns about
in both directions. The useful question is therefore not "how many users" but:

> **Which limit binds first, at what number, and is removing it a deployment change or a
> rewrite?**

A limit that is removed by a config change is a *design that scales*. A limit that requires
touching code is the one to fix now. On that test, exactly one thing in Aegis fails.

### 6.2 Which limit binds first — measured and ranked

Assumptions stated up front: the Plan 02 §2.1 design (a 4-agent `asyncio.gather` fan-out) is
built; one uvicorn process as `scripts/start.ps1` launches it; the pricing table in
`aegis/gateway/routing.py` (`GENERATION` $0.0025/1k in, $0.01/1k out; `CHEAP` $0.00015/$0.0006).

| Rank | Limit | Binds at | Removable by | Verdict |
|---|---|---|---|---|
| **1** | **Model-gateway spend** | **~650 fan-out queries, total, forever** | nothing — it is a $100 balance | **Binds first, by an order of magnitude. Not an architecture problem.** |
| 2 | Model-provider rate limits / p95 latency | a handful of concurrent fan-outs (12 calls each) | provider tier | Config |
| 3 | Single event loop, blocked by `retrieval/memory.py:509` and NanoVectorDB matmuls | ~15–25 concurrent in-flight queries | `--workers N` … except **#4 forbids it** | **The real one** |
| 4 | **Embedded stores are single-process** | **`--workers 2`** | *nothing, today* | **Fix now — see §6.4** |
| 5 | LightRAG NanoVectorDB RAM + loop-blocking | ~50–100k chunks on 16 GB | per-tenant workspaces + a real ANN store | Plan 03 §2.4 |
| 6 | Postgres pool (15) / server (100) | ~900 concurrent queries | `pool_size=` | Config — but set it anyway (§6.3) |
| 7 | `usage_ledger` aggregate per model call | ~1M ledger rows (latency, not connections) | one composite index | Config |

**#1, worked.** Per single-agent query today: ~8–11 model calls, of which 2 are `GENERATION`
(plan ≈ 3k in / 300 out = $0.0105; generate ≈ 6k in / 600 out = $0.021) plus ~5 `CHEAP` calls
(≈ $0.0014) and embeddings ≈ $0 → **~$0.035/query**. A 4-agent fan-out adds ~2 `GENERATION`
calls per sub-agent plus a synthesis → **~$0.13–0.20/query**.

$100 ÷ $0.15 ≈ **650 fan-out queries — for all remaining development, all rehearsal, and the
day itself.** Five concurrent users at one query per minute is $0.75/min → **the entire credit
balance is gone in about two hours of demo load.** This is why `00-MASTER-PLAN.md` leads
Phase 4 with a classifier and why the answer cache matters more than it looks. It is also the
honest answer to a jury asking about scale: *the model bill binds, everything else is cheap,
and here is the ledger that proves we measure it.*

**#3, measured.** On this machine, a brute-force cosine over 3072-dim float32 (the dimension
`retrieval/pipeline.py` and `lightrag_backend.py` both use):

| Vectors | Matrix RAM | Query |
|---|---|---|
| 5 000 | 61 MB | 1.2 ms |
| 20 000 | 246 MB | 5.1 ms |
| 50 000 | 614 MB | 13.5 ms |

LightRAG keeps **three** such stores (chunks, entities, relationships), and entity/relation
counts typically exceed chunk count. At 50k chunks that is ~2–3 GB resident and ~40 ms of
**event-loop-blocking** work per graph query, on a 16 GB box — before Chroma, Postgres, the ML
artifact and Python itself. (Measured on macOS/ARM; a Windows x86 laptop will be the same order,
likely slower.)

Chroma is not in this table because it is doing the right thing: real on-disk HNSW, `to_thread`
in the memory path. The one place it is called synchronously
(`aegis/retrieval/memory.py:509`) should be wrapped to match `memory/vector_ops.py` — a
two-line fix that removes an inconsistency, not a bottleneck.

### 6.3 What is needed now

| # | Change | Why | Size |
|---|---|---|---|
| 1 | **`pool_size` / `max_overflow` / `pool_pre_ping` / `pool_recycle` on both engines**, from settings | zero configuration today; the default 15 is invisible and the failure is a 30-second stall then `TimeoutError`, which reads as "the platform hung" | ~10 lines |
| 2 | **Composite index `(tenant_id, ts)` and `(user_id, ts)` on `usage_ledger`** | the aggregate runs 2–4× per model call and the table only grows | 2 lines |
| 3 | **`asyncio.to_thread` around `retrieval/memory.py:509`** | one convention for one engine | 2 lines |
| 4 | **`working_dir` becomes an absolute path from settings** | today the graph store follows the CWD (§1.8) | 3 lines |
| 5 | **A per-tenant concurrency semaphore around the fan-out** | one tenant issuing five fan-outs must not starve every other tenant's event loop; the budget check is per-*call*, not per-*run*, so nothing bounds in-flight work today | ~20 lines |
| 6 | **A load-shape test**, not a load test: 10 concurrent scripted queries against the real stack, asserting no pool timeout, no unbounded memory growth, and p95 within 2× of p50 | "credibly scales" needs one number that was measured | ~80 lines |

Items 1–4 total under 20 lines and remove four of the seven ranked limits from the "needs
code" column. That ratio is the argument for doing them now.

### 6.4 What must not be foreclosed — and the one thing that currently is

**The test for "scaling later is a deployment change":** can `--workers 4` be set without
touching code? Today, **no**, and this is the only genuine architectural defect in this
section.

Uvicorn uses **spawn**, not fork, so `--workers` does work on Windows
([Uvicorn deployment](https://www.uvicorn.org/deployment/), [FastAPI server workers](https://fastapi.tiangolo.com/deployment/server-workers/)).
The blocker is the stores:

- **Chroma `PersistentClient` is not multi-process safe.** It is SQLite-backed; SQLite takes a
  file-level write lock, and Chroma clients detect the `mtime` change and reload the entire
  HNSW index — so N writers means N full index reloads per write. The documented answer is
  client-server mode ([Open WebUI multi-replica guide](https://docs.openwebui.com/troubleshooting/multi-replica/),
  [chroma-core#666](https://github.com/chroma-core/chroma/issues/666)).
- **LightRAG's NanoVectorDB is a JSON file rewritten whole**, with an in-RAM matrix and an O(N)
  linear scan on upsert. Two processes is corruption, not contention.
- Process-global singletons (`_DEFAULT_INDEX`, `_shared_store`, `_pg_checkpointer`, the ops
  prompt cache, the approval/parked registries) are mostly fine — the parked-run path already
  resumes across workers via the durable Postgres checkpointer, which is exactly right. But
  `app/agent/deps.py:_shared_store` is a process-local `InMemoryRecordStore` seeded from the
  synthetic generator, so under `--workers` two users' tool calls mutate different worlds.

**Recommendation — three changes, none of them "scale out now":**

1. **`VECTOR_STORE_MODE = embedded | server`**, resolved by `Aegis` (Decision A) and reported
   at boot. `ChromaVectorStore.server(url=...)` already exists and already heartbeats. This
   makes horizontal scale a **config change and a `chroma run`**, which is the whole claim.
2. **Refuse to boot with `--workers > 1` while `VECTOR_STORE_MODE=embedded`.** A named startup
   error naming the fix. This is the platform's own fail-closed principle applied to the one
   deployment mistake that would silently corrupt data.
3. **Move `_shared_store` behind the durable store**, or gate it on a single-worker assertion.
   It is a demo scaffold that becomes a correctness bug the moment anyone scales out.

Also **not foreclosed, and already right** (worth saying, because it is the defensible part of
the story to a jury): tenant scope is a parameter carried in `RetrievalScope` and enforced in
the Chroma `where` filter *and* by Postgres RLS on 13 tables; the vector store's null-sentinel
handling means an unscoped filter matches null-tenant rows only rather than everything; the
gateway is a single chokepoint with per-tenant rpm/tpm/USD caps; and the two engines are split
by privilege. The architecture is sound. It is the *packaging* of the vector tier that is not.

### 6.5 Per-tenant isolation of the vector and graph stores

Today: **one** Chroma collection filtered by `tenant_id` metadata, **one** LightRAG working
directory, **one** Neo4j graph. Plan 03 §2.4 owns the per-tenant-workspace design. The scaling
consequences to feed into it:

- A metadata filter on one collection means one HNSW index whose size is the sum of all
  tenants, and recall/latency for a small tenant degrade as large tenants grow. Per-tenant
  collections fix that and also make "delete a tenant" a `drop_collection` instead of a scan.
- The counter-argument is real: N collections means N HNSW indexes resident. At the demo's
  tenant count (single digits) per-tenant collections are strictly better; at hundreds they are
  not. **Default: per-tenant collections, with the tenant→collection mapping owned by
  `Aegis`** so the policy can change without touching call sites.
- LightRAG's `working_dir` is the harder one, because NanoVectorDB's whole-file model means
  per-tenant directories multiply the RAM problem in §6.2 rather than dividing it. This is the
  strongest argument for Plan 03's per-tenant workspaces to be paired with moving LightRAG's
  vector tier off NanoVectorDB entirely.

### 6.6 What is premature

Named so nobody starts one.

| Premature | Why |
|---|---|
| Horizontal scale-out (multiple app hosts, a load balancer) | One engineer, one laptop, no Docker. §6.4 keeps the door open; walking through it now buys nothing and costs an operational story nobody can run. |
| A distributed queue (Celery/RQ/Redis Streams) | `01-V2-ADDITIONS.md` already made the call — one Postgres job substrate. Plan 04 owns it. |
| Read replicas, connection poolers (PgBouncer), partitioning `usage_ledger` | Limit #6 binds at ~900 concurrent queries. The composite index in §6.3 is the whole job for now; partitioning is already in `backlog-post-hackathon.md`. |
| Caching layers beyond what exists | There are already three cache implementations of uneven quality (Plan 03 §1.6) and one is unwired (`answer_cache.py`). Making the existing ones real is Plan 03's job; adding a fourth is the over-complex failure mode by name. |
| Per-tenant process/container isolation | The isolation story is RLS + scope-typed retrieval, and it is proven. Process isolation would be a *second* isolation mechanism to keep truthful. |
| Autoscaling, HPA, k8s anything | No Docker. |
| A real load test (Locust/k6) | §6.3 item 6's 10-query shape test answers the jury question. A load-test harness is a tool we would run once. |

---

## 7. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | The `Aegis` runtime becomes a god object that everything imports, recreating the monolith one level up | Medium | High | Hard rule: `Aegis` may import leaves; **no leaf may import `Aegis`**. Add it to the §4.8 #9 topology test on day one, before the object exists. |
| 2 | Moving `AgentDeps.default()` into the package breaks the live console during the move | Medium | High | It is a pure move: `backend`'s `AgentDeps` subclasses the package one *today*. Move the bindings, keep `app.agent.deps.AgentDeps` as a re-export, run the existing suite. No behaviour change is intended and any diff in the SSE stream is a bug. |
| 3 | The conformance suite becomes a second test suite to maintain | Medium | Medium | Cap it at 15 checks, each traceable to a defect this repo actually shipped. If it needs fixtures beyond an adapter module and a runtime, it is out of scope. |
| 4 | Narrowing `AgentDeps`' callables breaks test fakes across both suites | High | Low | The Protocols are structural; fakes that already match keep working. Expect a day of signature churn in `backend/tests` and `aegis/tests`, no runtime change. |
| 5 | Per-tenant Chroma collections regress recall vs the single filtered collection | Low | Medium | Measure on the gold corpus before switching. Plan 03 §15 already owns the measurement harness. |
| 6 | `/v1` prefix + generated TS client lands mid-console-rebuild and collides with Plan 02 Phase 2 | High | Medium | Sequence the prefix **before** the console rebuild, or **after** it — never during. Recommend before: it is a two-line router mount and the console is being rewritten anyway. |
| 7 | The `--workers > 1` boot refusal (§6.4) fires on a machine we actually want to scale | Low | Low | It is gated on `VECTOR_STORE_MODE=embedded` and names the fix. That is the intended behaviour, not a risk. |
| 8 | Time spent on packaging is time not spent on the demo | Medium | High | Items §4.8 #1–2 and §6.3 #1–4 are hours and pay for themselves. Everything else in this plan is genuinely optional relative to 30 August and should be cut in that order if the schedule bites. |

---

## 8. Dependencies on other plans

| Needs | From | Assumption if it does not land |
|---|---|---|
| The job substrate | **Plan 04** (concurrent) | §4.2's "the lifespan sweepers move into `aegis`" assumes they become jobs. If Plan 04 keeps them as asyncio tasks, they stay host-side and `Aegis` exposes `start_background()` / `stop_background()` instead. Either way `Aegis` owns the lifecycle. |
| Request tracking / the `run_events` table | **Plan 04** + Plan 02 §2.2 | The conformance suite's `runtime/observability` check asserts a run produces a durable trace. If `run_events` does not exist, that check is dropped rather than faked. |
| `DomainAdapter` Protocol + `AEGIS_ADAPTER` | **Plan 01 §Phase 7** | This plan *extends* it. If Plan 01 Phase 7 is cut, items §4.8 #4–8 absorb it wholesale and the effort moves here. |
| Per-tenant vector/graph workspaces | **Plan 03 §2.4** | §6.5 states the scaling input; the design decision is Plan 03's. |
| The `asyncio.gather` fan-out | **Plan 02 §2.1** | §6.2's cost model assumes it. Without it, limit #1 binds at ~2 800 single-agent queries instead of ~650, and limit #3 moves further out. Nothing else changes. |
| Console rebuild timing | **Plan 02 Phase 2** | Risk #6. |

---

## 9. Decisions I cannot make alone — with my defaults

| # | Question | My default | Why it needs you |
|---|---|---|---|
| 1 | Does the `aegis` package ever get published to PyPI, or is "importable" satisfied by a path/git install? | **Not published.** `pip install git+…` and the editable path install we have. | Publishing forces a name reservation, a license decision and a release process. It changes §5.2's versioning from "discipline" to "obligation". |
| 2 | Do we ship a **second** domain adapter for the live-switch demo (Plan 01 §Phase 7's stage moment)? | **Yes** — it is the only proof the seam works, and the conformance suite makes it cheap. | It is a real day of work on a non-demo artifact. |
| 3 | Is the 512-line composition-root move worth doing before 30 August, or is it post-hackathon? | **Before** — it is the B1 requirement's whole substance, and it is a mechanical move of code that already exists and is tested. | It touches the agent path, which is the demo path. |
| 4 | `/v1` prefix now or after the console rebuild? | **Now**, before Plan 02 Phase 2 starts. | Sequencing against a plan being executed by someone else. |
| 5 | Generated TS client — replace the hand-written types, or generate alongside and migrate gradually? | **Replace.** Gradual migration means both exist and the hand-written one stays authoritative. | ~20 console files import from `types.ts`. |
| 6 | Per-tenant Chroma collections, or keep the metadata filter? | **Per-tenant collections**, mapping owned by `Aegis`. | It interacts with Plan 03's design and should be decided once, there. |

---

## 10. What this area was missing from the requirement

Six things the B1–B3 framing does not name, in the order I would act on them.

1. **The two documentation lies (§1.5, §1.6) are the single biggest first-attempt risk, and
   they are hours of work.** An agent handed `SWAP.md` retargets six of eight pieces; an agent
   handed `README.md` writes `aegis.require()` and gets an `AttributeError` on line one. Every
   sophisticated thing in this plan is worth less than fixing those two files. The brief warned
   that audits here repeatedly find docs asserting the opposite of the code; this is that,
   twice, in the two files an integrator reads first.

2. **`py.typed` is a one-line change that unlocks every other typing decision.** The plan
   proposes narrowing six `Any` callables to Protocols; without the marker, a consumer's type
   checker discards all of it. This is the highest value-per-character item in the document.

3. **The conformance suite is also the *jury* artifact.** B1 frames it as an integrator tool.
   It is better than that: `pytest --pyargs aegis.conformance` on stage, against a second
   adapter, is a *measured* answer to "is this really domain-agnostic?" — which is precisely
   the claim the regional round taught us to be able to prove rather than assert. It belongs in
   the demo script, not just in the developer docs.

4. **Nothing bounds concurrent work per tenant.** The budget system enforces caps per model
   *call*. There is no limit on in-flight *runs*. One tenant issuing five 12-call fan-outs
   consumes the entire event loop and most of the credit balance while every other tenant
   waits. §6.3 item 5 is twenty lines and it is the difference between "multi-tenant" as a data
   property and as a *resource* property — which is what a technical jury will actually probe.

5. **The stream contract is the most important interface in the product and the only one with
   no machine-readable description.** OpenAPI covers ~60 request/response pairs; the SSE event
   union that the entire console renders is described only by `app/api/schemas.py` and mirrored
   by hand in TypeScript, plus a second, parallel vocabulary in `aegis.core.stream_names`
   mirrored again in `web/src/lib/streamNames.ts`. Two hand-maintained mirrors of the thing the
   demo is made of. §5.3 items 3–4 fix both by generating them.

6. **`aegis.core.registry` is an abstraction that is not earning its keep, and the plan should
   say so plainly.** 100 lines, one registration (`@register("guardrail", "default")`), zero
   `get()` calls in production code, and `discover()` — the entry-point mechanism that would
   make third-party components real — is called by nothing and is not even exported from
   `aegis.core.__init__`. Either wire `discover()` into the adapter resolution in Decision B
   and make it the plug-in story, or delete the module. A registry nobody reads from is a
   promise the package cannot keep, and it is exactly the kind of thing that makes an AI reader
   infer a plug-in architecture that does not exist.

---

## 11. Sources

**Primary — this repository.** `aegis/pyproject.toml` · `aegis/README.md` ·
`aegis/src/aegis/{__init__,core/{__init__,interfaces,registry,lazy,config}}.py` ·
`aegis/src/aegis/agent/deps.py` · `aegis/src/aegis/retrieval/{vector_store,memory,lightrag_backend,types}.py` ·
`aegis/src/aegis/memory/vector_ops.py` · `aegis/src/aegis/governance/{enforcement,models,rls}.py` ·
`aegis/src/aegis/gateway/routing.py` · `aegis/tests/core/test_core_is_dep_free.py` ·
`backend/pyproject.toml` · `backend/src/app/{main,config}.py` ·
`backend/src/app/{agent/deps,api/routes,api/schemas,data/session}.py` ·
`backend/src/app/adapter/{__init__,README.md,SWAP.md,memory_spec,roster}.py` ·
`web/src/lib/api/types.ts` · `web/package.json` · `scripts/start.{ps1,sh}` ·
`docs/module/MODULE_REFERENCE.md` · `docs/teaching/core/10-guide.md` ·
`docs/dev_new_docs_v2/plans/{01,02,03}` · `docs/dev_new_docs_v2/{00-MASTER-PLAN,01-V2-ADDITIONS,backlog-post-hackathon}.md`

**Measured on this machine.** Brute-force cosine at 3072 dims over 5k/20k/50k vectors
(numpy, `backend/.venv`) · `psql -U yrevash -d taif`: `max_connections=100`,
`shared_buffers=128MB`, `work_mem=4MB`, `pg_database_size=11 MB`, 27 public tables ·
`aegis/.venv`: `hasattr(aegis, "require") == False` ·
`nano_vectordb/dbs.py` 0.0.4.3 read from `backend/.venv` (brute-force float32 matrix,
whole-file JSON+base64 persistence, O(N) upsert scan).

**External.**
- *To See is Not to Master: Teaching LLMs to Use Private Libraries for Code Generation* — [arXiv 2603.15159v4](https://arxiv.org/html/2603.15159v4) (the oracle-spec experiment and the error taxonomy in §2.1)
- AGENTS.md specification and 2026 adoption — [ASDLC spec summary](https://asdlc.io/practices/agents-md-spec/), [The AGENTS.md Field Guide, 2026 edition](https://www.iuriio.com/blog/posts/2026/05/agents-md-field-guide-2026), [Harness: the agent-native repo](https://www.harness.io/blog/the-agent-native-repo-why-agents-md-is-the-new-standard)
- llms.txt status and the Ahrefs log study — [Fern: API docs for AI agents](https://buildwithfern.com/post/optimizing-api-docs-ai-agents-llms-txt-guide), [llmtxt.info: 2026 status](https://llmtxt.info/what-is-llms-txt/)
- Agent Skills open specification — [spec](https://deepwiki.com/anthropics/skills/6.1-agent-skills-specification), [2026 ecosystem report](https://agentman.ai/blog/agent-skills-ecosystem-report-2026)
- pytest plugin distribution via `pytest11` — [pytest: writing plugins](https://docs.pytest.org/en/stable/how-to/writing_plugins.html)
- Chroma `PersistentClient` multi-process behaviour — [Open WebUI multi-replica troubleshooting](https://docs.openwebui.com/troubleshooting/multi-replica/), [chroma-core/chroma#666](https://github.com/chroma-core/chroma/issues/666)
- Uvicorn uses spawn (so `--workers` is available on Windows) — [Uvicorn deployment](https://www.uvicorn.org/deployment/), [FastAPI: server workers](https://fastapi.tiangolo.com/deployment/server-workers/)
- Protocol vs ABC for host-owned implementations — [Real Python: Python Protocols](https://realpython.com/python-protocol/)
