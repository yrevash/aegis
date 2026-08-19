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

## Before you start: get a green baseline

Run this **first**, before you have changed anything. If it is not green now, you
are about to attribute a pre-existing failure to your own edit and lose an hour.

**Every command in this file is written from the repository root**, and each `cd`
is wrapped in a subshell so a run of them one after another in one terminal works.
Without the parentheses the second command lands in the first one's directory and
fails on `cd: no such file or directory` — which reads like a broken repo and is
not one.

```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest tests/adapter tests/agent -q)
```

At the time of writing this is **127 passed** in well under a minute, with **no**
database, no Neo4j, no Redis, and no API key. That is deliberate: the whole
vertical slice runs on injected fakes, which is what makes this loop fast enough
to run after every single step below.

**Write down whatever number you actually get** — not the one in this sentence,
which will have drifted. That is your regression baseline, and it should only ever
grow as you add tests for your own domain.

---

## The ten pieces

Eight Python modules plus two content directories. Counted from disk, not quoted
from memory — `backend/tests/adapter/test_piece_manifest.py` re-counts them on
every run and fails if this list drifts from the filesystem.

`__init__.py` is **not** one of the ten. It is the registry: the interface the
core imports. You edit the ten; you leave its `__all__` alone.

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

Thirteen checks, no database, no key, well under a second. Each one descends from a
wiring mistake this repository actually shipped — a specialist with no handler node,
a playbook the selector can never name, an ML spec that silently trained on noise —
and each failure prints what is wrong, the edit that fixes it, what happens if you
leave it, and the defect it came from. `pytest --pyargs aegis.conformance` with no
`--aegis-adapter` stops with a usage error naming the flag, not with thirteen skips.

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
- `features_for_request(...)`, `feature_matrix(dataset)`, `training_frame(...)`.
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

**Done when:** `generate_synthetic_sync(config)` returns schema-valid records
whose labels come from `ml_spec`'s latent function, with `complete=None`.

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

**Done when:** every persona id used as a key in `ALLOWLIST` exists in `PERSONAS`,
and `DEFAULT_PERSONA_ID` names a real one.

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

**Done when:** every `role` you declare is `qa` or `memory` (or you have made a
deliberate, reported core edit), and exactly one is `is_default`.

**Verify** — run a query and read the `routing` stream event, or check the build
warning:
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
`memory_spec.py`.

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

# 1. The conformance suite — thirteen checks, no infrastructure, under a second.
#    Every one of them descends from a wiring defect this repo actually shipped,
#    and every one fails with the fix, the consequence and the scar written out.
#    Run it after every step above, not only here: it is the fastest signal in
#    the repo that the adapter is wired and not merely present.
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest \
    --pyargs aegis.conformance --aegis-adapter app.adapter -q)

# 2. The adapter and the whole agent graph, on fakes. No infrastructure.
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

---

## Do not touch

Everything outside `backend/src/app/adapter/`. Concretely: `app.agent`,
`app.core`, `app.retrieval`, `app.memory`, `app.ml`, `app.guardrails`, `app.ops`,
`app.eval`, `app.observability`, `app.data`, `app.mcp`, `app.api`, `app.platform`
— and all of `aegis/`.

If you find yourself adding a business rule to any of those, it belongs in the
adapter. That is the entire design, and it is what makes the retarget an
afternoon rather than a rewrite.

The one sanctioned exception is the `SPECIALIST_NODES` edit in step 8, and it must
be reported rather than done quietly.

## What to report when you are done

New dependencies, new environment variables, any core edit you were forced to
make and why, and the outputs of all six final-gate commands. Do not add
dependencies to `aegis/pyproject.toml` yourself — name them in the report.

## If you need more context

- `backend/src/app/adapter/README.md` — the same ten pieces as a local map.
- `docs/learn/50-run-and-extend.md` §7 — the `AgentDeps` hook table: which engine
  hook calls which adapter function.
- `aegis/PUBLIC.md` — what in the core is promised and what is not.
- `AGENTS.md` — repo layout, commands and boundaries.
