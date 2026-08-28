---
name: retarget-aegis
description: >-
  Retarget the Aegis platform to a new problem domain: exactly which files to
  write, in what order, what "done" looks like at each step, and the command that
  proves it. Use this when pointing Aegis at a new domain, writing or rewriting
  anything under backend/src/app/adapter/, or when a task says "adapt Aegis to X".
---

# Retargeting Aegis to a new domain

**One rule, and everything else follows from it: only `backend/src/app/adapter/`
changes.** The core reaches the domain exclusively through names re-exported by
`adapter/__init__.py`. Keep those names, replace what is behind them, and the
agent graph, gate, memory, retrieval, governance, tracing and console all keep
working untouched.

This document replaces the retired `adapter/SWAP.md`. It is the only retargeting
procedure; if something else in this repo disagrees with it, this file is right
and the other one is stale.

## Step 0 — install, because there is no `.venv` in a fresh checkout

**Do this first. Every command below runs `backend/.venv/bin/python`, and a fresh
clone has no `.venv` at all** — this file used to open with a command that could
not run, and left you to guess whether that meant a broken repo.

```bash
./scripts/bootstrap.sh          # macOS / Linux
.\scripts\install-windows.ps1   # Windows
```

It is idempotent, needs no Docker, no GPU and no database, and installs the backend
venv with every extra plus the console's npm dependencies. Full detail, including
the native stores the `full` run mode wants, is in `INSTALL.md`. If `bootstrap.sh`
stops on a missing prerequisite, install that and re-run it — do not work around it.

**Every command in this file is written from the repository root**, and each `cd`
is wrapped in a subshell so a run of them one after another in one terminal works.
Without the parentheses the second command lands in the first one's directory and
fails on `cd: no such file or directory` — which reads like a broken repo and is
not one.

## Then: get a green baseline

Run this **before** you have changed anything. If it is not green now, you are about
to attribute a pre-existing failure to your own edit and lose an hour.

```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest tests/adapter tests/agent -q)
```

On 2026-08-28 this is **153 passed in about 90 seconds**, with **no** database, no
Neo4j, no Redis, and no API key. That is deliberate: the whole vertical slice runs on
injected fakes, which is what makes this loop cheap enough to run after every single
step below.

**Write down whatever number you actually get** — not the one in this sentence,
which will have drifted. That is your regression baseline, and it should only ever
grow as you add tests for your own domain.

## Read this before step 1: the whole suite goes red, and stays red

`backend/tests/conftest.py` imports through `app.adapter`. The moment you replace
the entity models in step 1, **every test in the repository fails at import** — a
wall of `ImportError`, hundreds of lines, nothing to do with whether your edit was
right. It stays that way until step 8 is finished and the registry's re-exports all
resolve again.

That is expected, it is not a signal, and it is why the per-step "Verify" commands
below are written against **one file at a time**: those are the tests you have just
rewritten and they are the only ones that can be meaningful mid-flight. Do not chase
the wall, and do not "fix" it by loosening a conftest.

**And the per-step verifies only mean something once you have rewritten the tests
they run.** `backend/tests/adapter/*` is not domain-neutral scaffolding — it carries
between 3 and 26 shipped-domain literals per file (`test_tools.py` 26,
`test_allowlist.py` 19, `test_ml_spec.py` 13, `test_schema.py` 9, `test_generator.py`
7, `test_registry.py` 3). **Rewriting them is part of each step, not a follow-up.**
Two files there are the exception and must be left alone, because they check the
*structure* rather than the domain: `test_piece_manifest.py` and
`test_domain_adapter_protocol.py`. So is `test_conformance_suite.py`, and so is
`broken_adapter/`, which is deliberately self-contained and imports nothing of yours.

The one check that is green from your first edit to your last, and needs no
infrastructure at all, is the conformance suite. Lean on it.

---

## The ten pieces

Eight Python modules plus two content directories. Counted from disk, not quoted
from memory — `backend/tests/adapter/test_piece_manifest.py` re-counts them on
every run and fails if this list drifts from the filesystem.

`__init__.py` is **not** one of the ten. It is the registry: the interface the
core imports.

**You will edit it, and its `__all__`, and that is correct** — step 1 replaces the
entity models it re-exports and step 2 renames the latent function it re-exports, so
leaving `__all__` untouched is impossible and this file used to ask for it twice in
the same page. What must stay stable is not the list of names, it is **the contract**:
`aegis.adapter.DomainAdapter` and its sub-Protocols. Concretely, keep

* the **nine module members** (`schema`, `ml_spec`, `generator`, `tools`, `personas`,
  `prompts`, `memory_spec`, `roster`, `corpus`) reachable as attributes of the package,
  plus `DOMAIN_ID` and `DOMAIN_DESCRIPTION`;
* the **member names inside each piece** that the sub-Protocols name — `PERSONAS`,
  `DEFAULT_PERSONA_ID`, `PERSONA_BY_ROLE`, `persona_for_role`, `TOOL_REGISTRY`,
  `ALLOWLIST`, `run_tool`, `FEATURE_NAMES`, `TARGET`, `training_frame`,
  `describe_prediction`, `DOMAIN_SERIES_LABEL`, `DOMAIN_SERIES_UNIT`,
  `domain_series_events`, `agent_roster`, `sub_agent_roster`, `load_seed_corpus`,
  and every `memory_spec` member;
* `SyntheticDataset` as the container the generator returns and the ML spine reads.

Everything else in `__all__` is yours to re-voice. `missing_members(app.adapter)` and
the conformance suite tell you when you have dropped something that matters.

Edit them in this order. The order is not arbitrary — each piece consumes the
vocabulary the previous one defined.

**Do not count files to check your work — ask the contract.** `aegis.adapter`
declares the seam as an executable Protocol, so "have I implemented the adapter?"
has a real answer:

```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -c "
import app.adapter
from aegis.adapter import DomainAdapter, missing_members
print('missing:', missing_members(app.adapter))
print('satisfies:', isinstance(app.adapter, DomainAdapter))
")
```

`missing: []` means every member exists. It does **not** mean every member has
the right shape — that is what the per-step checks below are for. Ten pieces map
to **nine members**; `skills/` has none of its own because it is already named by
`memory_spec.SKILLS_DIR`.

**The shape check is one command too**, and it is the one to keep re-running as you
work through the steps below:

```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest \
    --pyargs aegis.conformance --aegis-adapter app.adapter -q)
```

Fourteen checks, no database, no key, well under a second. Each one descends from a
wiring mistake this repository actually shipped — a specialist with no handler node,
a playbook the selector can never name, an ML spec that silently trained on noise —
and each failure prints what is wrong, the edit that fixes it, what happens if you
leave it, and the defect it came from. One of the fourteen does not read your adapter at all: it reads the **core**, and
fails if any module outside `backend/src/app/adapter/` still names the shipped
domain — a persona id, a record type, a feature key, a chart label. That is the
check that makes "only the adapter changes" a fact rather than a promise, and it is
the one that would have caught all four defects a real retarget rehearsal shipped.

`pytest --pyargs aegis.conformance` with no `--aegis-adapter` stops with a usage
error naming the flag, not with fourteen skips.

| # | File | You define |
|---|---|---|
| 1 | `schema.py` | The entities and enums of the new world |
| 2 | `ml_spec.py` | What gets predicted, from which features, and the latent ground truth |
| 3 | `generator.py` | Synthetic records consistent with 1 and 2 |
| 4 | `tools.py` | The real actions, each with a risk tier |
| 5 | `personas.py` | Who is served, and what each may see and call |
| 6 | `prompts.py` | Who the agent is, per persona |
| 7 | `memory_spec.py` | What counts as a durable fact |
| 8 | `roster.py` | Which specialists the supervisor may route to |
| 9 | `corpus/` | Seed knowledge documents (`*.md`) |
| 10 | `skills/` | Procedural how-to-act playbooks (`*.md`) |

---

## Step 1 — `schema.py`: the vocabulary

Replace the entity models and their enums. The shipped domain is a neutral
service-request / case-management world (`Customer`, `SupportAgent`,
`ServiceRequest`, `Document`, `SyntheticDataset`); it is illustrative only.

**Done when:** your entities are Pydantic v2 models, your vocabularies are
`StrEnum`, `SCHEMA_VERSION` is bumped, and `SyntheticDataset` still exists as the
container the generator returns and the ML spine reads.

**Verify:**
```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest tests/adapter/test_schema.py -q)
```

**Trap:** keep the *container* names the registry re-exports even while you
change every field inside them. `adapter/__init__.py`'s `__all__` names
`SyntheticDataset` specifically, and the ML spine and generator both bind to it.

---

## Step 2 — `ml_spec.py`: the predictable signal

This module is the single source of truth for what is predictable. Define:

- `FEATURES` — the typed feature contract (name, dtype, description).
- `TARGET` — what is predicted.
- **the latent signal function** — shipped as `latent_resolution_hours`; rename
  it for your domain. This is the ground truth the generator samples labels
  around.
- `features_for_request(...)`, `feature_matrix(dataset)`, `training_frame(*, num_records, seed)`
  — the keyword is `num_records`, deliberately domain-neutral, because
  `aegis.adapter.MLSpecModule` names it and a core Protocol spelling it `num_requests`
  would force every future domain to call its rows "requests".
- `describe_prediction(resp)` — re-voice it, because its output is injected into
  the plan as evidence and will otherwise name the old target and unit out loud
  in front of a jury.

**Done when:** `feature_matrix(dataset)` yields an `(X, y)` the spine can train
on, and `describe_prediction` says nothing about service requests.

**Verify:**
```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest tests/adapter/test_ml_spec.py -q)
```

**Trap — this one costs a demo:** the generator must sample labels *around your
latent function*. If it does not, the target is noise, the model finds nothing,
and the conformal interval is honestly enormous. Step 3 is where that coupling is
kept; it is written here because this is where the function you must call is
defined.

---

## Step 3 — `generator.py`: data before there is data

Adjust the procedural draws and the LLM fabrication (`_fabricate_request_text`,
`_fabricate_documents`) to the new records.

**Keep the hybrid pattern**: seeded structure + LLM text + a **templated
fallback**. `generate_synthetic(config, *, complete=None)` must return a fully
schema-valid dataset *with no LLM available at all*. On the day, this is what
makes the system demonstrable while the model key is still being sorted out.

**Also in this file: the client-facing demand series `/forecast` charts.** Three
names, and the core reads nothing else about your records over time:

- `DOMAIN_SERIES_LABEL` — the chart's title, in the client's language. It is a
  **sentence a jury reads**; leave it and your deployment charts the shipped domain's
  words over your data forever, silently.
- `DOMAIN_SERIES_UNIT` — what the values are counted in.
- `domain_series_events(*, num_records, seed)` — `(timestamp, value)` arrival events,
  one per record. Prefer arrivals over completions: arrivals are what a client plans
  capacity against, and the series is complete at the recent end.

**Done when:** `generate_synthetic_sync(config)` returns schema-valid records
whose labels come from `ml_spec`'s latent function, with `complete=None`, and
`domain_series_events()` returns events over your own records.

**Verify:**
```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest tests/adapter/test_generator.py -q)
```

---

## Step 4 — `tools.py`: the real actions

One `async def handler(args, ctx) -> ToolActionResult` per action, each:

- **typed** — arguments validated by a Pydantic model,
- **audited** — calls `app.data.record_audit`,
- **registered** in `TOOL_REGISTRY: dict[str, ToolSpec]`, each `ToolSpec` carrying
  a `risk: RiskLevel`,
- **allowlisted** in `ALLOWLIST: dict[str, frozenset[str]]`, persona → tool names.

**The risk tier is the whole human gate.** A tool at or above
`AgentConfig.gate_min_risk` (platform default `HIGH`) pauses for a human. There is
no second signal — not model confidence, not the ML prediction. Mark a
consequential, externally-visible write `HIGH` and the approval gate appears with
no engine change at all. The shipped registry is a worked example:
`add_case_note` LOW, `assign_request` MEDIUM, `update_request_status` HIGH.

Each `ToolSpec` also carries two optional booleans the MCP surface publishes as
advisory hints: `destructive` (the call overwrites state a reader would miss) and
`idempotent` (repeating the identical call converges). Assert them per tool — risk
does not imply idempotency, a note-append is LOW risk and not idempotent while a
gated status change is HIGH risk and is. Omit both and the conservative reading is
published instead, which is safe but says less than you know.

**Done when:** every action is registered with an honest risk tier, and `ALLOWLIST`
grants each persona only what it should have.

**Verify:**
```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest tests/adapter/test_tools.py tests/adapter/test_allowlist.py -q)
```

**Trap, and it fails safe:** an *unregistered* tool name resolves to `HIGH`, so
forgetting to register something makes it require approval rather than run
unguarded. Do not rely on that — it means a forgotten registration looks like an
over-cautious gate rather than a bug.

---

## Step 5 — `personas.py`: who is served

Re-voice `PERSONAS`, `DEFAULT_PERSONA_ID`, `get_persona`. A persona carries **its
data scope and its tool allowlist** — it is an authorisation object, not a
personality.

**Also re-point `PERSONA_BY_ROLE`, and this one bites the moment a human signs in.**
Every authenticated principal resolves its persona through `persona_for_role(role)`,
which reads that table: one entry per RBAC role (`admin`, `ai_team`, `devops`,
`client`), each naming a persona id that exists in `PERSONAS`. Re-voice `PERSONAS`
without it and **every login raises `KeyError`** while the adapter suite, the agent
suite and ruff all stay green — none of them go through the login path. The core used
to decide this itself with two persona ids hardcoded in `app/api/routes.py`, which is
exactly how that failure was found.

**Done when:** every persona id used as a key in `ALLOWLIST` exists in `PERSONAS`,
`DEFAULT_PERSONA_ID` names a real one, and `PERSONA_BY_ROLE` maps every role to one.

---

## Step 6 — `prompts.py`: who the agent is

Paired with step 5, one system prompt per persona. `render_system_prompt` folds
the persona's live data scope and tool allowlist into the prompt, so the
instructions the model receives always match what it is actually permitted to do.

`PLATFORM_FLOOR` / `render_platform_floor` is the half **no tenant may edit**.
Leave it alone unless you are deliberately changing the platform's floor.

**Done when:** the rendered prompt names your domain, your personas, and your
tools — and nothing about service requests.

**Verify steps 5 and 6 together:**
```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest tests/adapter/test_registry.py -q)
```

---

## Step 7 — `memory_spec.py`: what is worth remembering

The only memory seam. Nothing in `app/memory/*` or `aegis/memory/*` changes.
Define:

- `FACT_TYPES` and `PROFILE_FIELDS` — what a durable fact is in your world.
- `FACT_EXTRACTION_PROMPT` and `IMPORTANCE_HINTS` — how facts are pulled from a
  conversation.
- `memory_subject_for(user_id, persona_id)` — who memory is scoped to.
- `render_profile(profile)` — how the profile reads back.
- `select_skills(query, persona_id, available)` — which playbooks apply.
- `PROFILE_ALIASES` (optional) — predicate spellings your extractor emits mapped onto
  `PROFILE_FIELDS` entries. Absent means no aliases, which is a legitimate statement;
  this table used to live in `aegis/memory/consolidate.py` naming the shipped domain's
  fields, where it quietly matched nothing after a retarget.
- `SKILLS_DIR` — where step 10 lives.

**Trap:** `memory_spec` is deliberately **not** re-exported through
`adapter/__init__.py`. Its consumer binds to the *module object*:
`backend/src/app/memory/__init__.py:33` calls
`set_default_spec(app.adapter.memory_spec)`. Three other places import it
directly. So the module must keep its **path and its symbol names**, not just its
behaviour.

---

## Step 8 — `roster.py`: who the supervisor may route to

Declare the specialists: each `RosterSpecialist` needs a `role`, `description`,
optional `keywords`, and exactly one entry must be `is_default=True`.

**This is the step with the real exception to "only the adapter changes", so read
it before you write it.** The graph dispatches on a fixed map in
`aegis/src/aegis/agent/graph.py`:

```python
SPECIALIST_NODES: dict[str, str] = {
    "qa":     "recall_memory",   # the full retrieve -> plan -> gate -> act pipeline
    "memory": "answer_memory",   # answers from long-term memory, skipping RAG/tools
    "team":   "plan_team",       # router-written fan-out; NOT a roster role
}
```

A roster role that is not a key in that map **is not routable**. It falls back to
the `qa` pipeline and logs a warning — it does not raise. So:

- **Re-voice `qa` and `memory` for your domain**: change the descriptions and
  keywords freely, keep the two role strings. This needs no core edit and is what
  you should do under time pressure.
- **Adding a genuinely new specialist requires a core edit** — a handler node in
  the graph plus a `SPECIALIST_NODES` entry. That is outside the adapter. Do it
  only if the domain truly needs a third path, and say so in your report.
- Never declare `team` in a roster. The router writes it when the depth
  classifier chooses fan-out.

**And the half of this file the checklist used to omit entirely: `sub_agent_roster()`.**
It returns the fan-out team the `team` path dispatches, and each `SubAgentSpec` carries
a **`tool_allowlist` of literal tool names**. The shipped `data` lane allowlists two
tools that step 4 deletes. Nothing raises: the allowlist is intersected with the
registry, so a stale name is silently dropped and the sub-agent runs with fewer tools
than you think — or none. Re-point every `tool_allowlist` to names that exist in your
`TOOL_REGISTRY`, and re-voice each spec's `label` and `system_prompt` while you are
there; they are read by the model and shown on screen.

**Done when:** every `role` you declare is `qa` or `memory` (or you have made a
deliberate, reported core edit), exactly one is `is_default`, and every name in every
`sub_agent_roster()` `tool_allowlist` is a key of `TOOL_REGISTRY`.

**Verify** — the conformance suite checks both halves (the roster's roles against the
graph's nodes, and every sub-agent allowlist against the registry). Then run a query
and read the `routing` stream event, or check the build warning:
```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest tests/agent/test_router.py -q)
```

---

## Step 9 — `corpus/`: the seed knowledge

Drop your seed `*.md` documents into `backend/src/app/adapter/corpus/`, using the
**same frontmatter keys** as the shipped three. `load_seed_corpus()` reads them
and they back retrieval before any real document is ingested.

**Done when:** `load_seed_corpus()` returns your documents and the shipped
service-request ones are gone.

---

## Step 10 — `skills/`: how to act

Rewrite the procedural playbooks in `backend/src/app/adapter/skills/*.md`. They
are discovered from `memory_spec.SKILLS_DIR` and chosen per query by
`memory_spec.select_skills`.

**Trap:** they are selected **by filename**, through a literal keyword→filename
`hints` dict inside `select_skills` (shipped: `"close" → "closing_requests"`,
`"angry" → "de_escalation"`, and so on — names without the `.md`). Adding or
renaming a playbook without updating that dict means it is never chosen, and
nothing warns you: `select_skills` just returns `None`, the core injects no skill,
and the agent acts without procedural guidance. You will read that as a prompt
problem and spend an hour in the wrong file.

So step 10 is really two edits — the `*.md` files, and the `hints` dict back in
`memory_spec.py`. The conformance check reads that table whether you keep it inside
`select_skills` or hoist it to a module constant, and if it can see no table at all
it probes the selector behaviourally and fails if nothing it is given can ever reach
a playbook.

---

## The final gate

```bash
# 0. The structural check — seconds, and it catches a whole piece you forgot.
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -c "
import app.adapter
from aegis.adapter import DomainAdapter, missing_members
assert not missing_members(app.adapter), missing_members(app.adapter)
assert isinstance(app.adapter, DomainAdapter)
print('adapter contract: satisfied')
")

# 1. The conformance suite — fourteen checks, no infrastructure, under a second.
#    Every one of them descends from a wiring defect this repo actually shipped,
#    and every one fails with the fix, the consequence and the scar written out.
#    Run it after every step above, not only here: it is the fastest signal in
#    the repo that the adapter is wired and not merely present.
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest \
    --pyargs aegis.conformance --aegis-adapter app.adapter -q)

# 2. The adapter and the whole agent graph, on fakes. No infrastructure. This is the
#    first command that can pass again once step 8 is finished — and only once you
#    have rewritten tests/adapter/* for your own domain, which is part of the steps
#    above, not a follow-up.
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest tests/adapter tests/agent -q)

# 3. The full backend suite.
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest -q)

# 4. The core package, untouched by your edits — so it must be exactly as green
#    as it was before you started.
(cd aegis && PYTHONPATH=src ../backend/.venv/bin/python -m pytest -q)

# 5. Lint.
backend/.venv/bin/python -m ruff check aegis backend
```

`tests/adapter/test_piece_manifest.py` is the tripwire for the structure itself:
add a ninth module and it fails until this file, `adapter/README.md` and every
`piece N of M` docstring are updated together. That is intentional. A piece that
is missing from the checklist is a piece nobody swaps.

Then train the ML spine on your new spec:

```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m app.ml)
```

Read its last line. It prints the model's prediction for the lowest- and
highest-labelled rows of **your** training frame, in **your** target's unit, and
`distinct=False` there means the spine learned nothing — check step 3's trap, not the
prompt. (It builds those two rows from your spec; it used to spell out the shipped
domain's feature keys, so after any correct retarget it cried wolf every time.)

---

## Do not touch

Everything outside `backend/src/app/adapter/`. Concretely: `app.agent`,
`app.core`, `app.retrieval`, `app.memory`, `app.ml`, `app.guardrails`, `app.ops`,
`app.eval`, `app.observability`, `app.data`, `app.mcp`, `app.api`, `app.platform`,
`app.forecast`, `app.seed` — and all of `aegis/`.

If you find yourself adding a business rule to any of those, it belongs in the
adapter. That is the entire design, and it is what makes the retarget an
afternoon rather than a rewrite.

**You do not have to take that on trust, and you should not.** The conformance
suite's core check scans every module outside the adapter for the shipped domain's
vocabulary and fails naming the file and the line. If it passes, no core module knows
this domain — which also means that if you *think* you must edit a core file to
finish, the check is the fastest way to find out whether the leak is real and where.

The one sanctioned exception is the `SPECIALIST_NODES` edit in step 8, and it must
be reported rather than done quietly.

## The console is not covered by any of the above — and it names this domain

`web/` is outside the adapter and outside the conformance check, which scans Python.
Four console files carry shipped-domain literals today and **will show the old
domain's words on screen after an otherwise perfect retarget**:

| File | What it names |
|---|---|
| `web/src/config/personas.ts` | the two persona ids, and the three tool names in its prose |
| `web/src/components/ops/opsShared.ts` | `PROMPT_KEY`, and the tool names in two prompt strings |
| `web/src/components/sim/SimulationView.tsx` | the persona id it drives the scripted demo with |
| `web/src/components/ml/MLOpsView.tsx` | a literal ML feature row — the same defect as the trainer's old sanity probe |

Re-voice those four by hand as part of your retarget, and say in your report that you
did. The real fix is for the API to serve the persona list and the feature spec so the
console reads them like everything else; there is no such endpoint yet, and inventing
one mid-retarget is not the moment.

## What to report when you are done

New dependencies, new environment variables, any core edit you were forced to
make and why, and the outputs of all six final-gate commands. Do not add
dependencies to `aegis/pyproject.toml` yourself — name them in the report.

## If you need more context

- `backend/src/app/adapter/README.md` — the same ten pieces as a local map.
- `docs/module/MODULE_REFERENCE.md` and
  `docs/architecture/system-architecture.md` — the Module Contract, the streaming
  spine, and which engine hook calls which adapter function. (These replace the
  `docs/learn/` path this file used to name; that directory no longer exists.)
- `aegis/PUBLIC.md` — what in the core is promised and what is not.
- `AGENTS.md` — repo layout, commands and boundaries.
