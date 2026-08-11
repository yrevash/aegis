# Aegis Module Contract + Guardrails Pilot — Design Spec

- **Date:** 2026-08-11
- **Branch:** `feat/aegis-module-contract`
- **Status:** Approved design (pending written-spec review)
- **Scope of this spec:** the foundational **Aegis Module Contract** (`aegis.core`) and its
  first proof — a **SOTA, fully-packaged `aegis.guardrails`** module — plus the reusable
  **frontend process-rail** that renders any module's live steps. Maturing the remaining
  components (retrieval, ML, gateway, memory, governance, evals, agent, trace) to the same
  contract is explicitly **future work**, each its own spec → plan → build cycle.

---

## 1. Problem & goal

Aegis won Mumbai but three structural problems made it painful to wield in the hackathon and
block the nationals goal of Aegis being **importable, not forkable**:

1. **Not modular.** Aegis is one monolithic FastAPI backend (`backend/src/app/*`). Integrating a
   piece into a solution meant dragging in the whole world; the result was "slop."
2. **The frontend could not show the agent's work.** The single biggest demo weakness: input
   guardrail → router/sub-agent choice → reasoning → RAG retrieval + citations → ML/SHAP → tool
   calls → approval gate were not clearly, cleanly visible live in the UI.
3. **Dishonest infra.** Code configured for Redis + Postgres+pgvector silently fell back to
   in-process SQLAlchemy + Python dicts and stored the "vector DB" in RAM, with no check that the
   real backends existed. RAM masqueraded as Postgres.

**Goal:** define a single **Module Contract** every Aegis component obeys, then prove it end-to-end
on a **SOTA-complete guardrails module**. Once proven on one, the pattern rolls across the rest.

**Non-negotiable quality bar:** every module built to this contract is a **mature, SOTA, complete
implementation** — never a stub or a toy "15-line package." The 15-line artifact in this spec is
only a *standalone proof script*, not the module. We reach SOTA-complete + fully-packaged first;
enhancement of each module is a later pass.

**Environment constraints (unchanged):** 16 GB Windows machine on the day, no Docker, no GPU, no
local model weights, API-only models, all infra local. Everything portable.

---

## 2. The Module Contract — three pillars

Every Aegis module (`aegis.<component>`) MUST satisfy all three.

### Pillar A — Importable & isolated (packaging)

- **Single installable `aegis` package**, one repo, one `pyproject.toml`, with **optional-dependency
  extras**: `pip install aegis[guardrails,redis]`. (A future multi-wheel split is kept mechanical by
  the boundary rules below, but is out of scope now.)
- **`aegis.core`** holds only: Protocol/ABC interfaces, shared Pydantic types (the event union,
  results, enums), the component **registry**, config, health probes, and the `require()` lazy-import
  helper. **`aegis.core` has ZERO heavy dependencies** — no litellm, torch, xgboost, DB drivers,
  langgraph. Core = interfaces + pydantic + stdlib only.
- **Boundary invariants (enforced from day 1, enable later split):**
  - `aegis.core` imports nothing internal.
  - A leaf module (`aegis.guardrails`, …) imports **only** `aegis.core` + its own third-party libs.
  - **No leaf ↔ leaf imports.** Shared logic goes into `aegis.core`.
- **Optional deps fail loud.** A missing optional dependency raises via
  `require("aegis[nemo]", "nemoguardrails")` with the exact install command. There is **no**
  `except ImportError: pass` silent no-op anywhere.

### Pillar B — Shows its work (observability contract)

- Every module emits an ordered stream of typed events. We **keep and extend the existing
  18-member `StreamEvent` union** (already mirrored in the frontend `types/stream.ts`) as the
  canonical contract — we do **not** rename everything to AG-UI. We extend it with the missing
  "show-your-work" parts and formalize the discipline:
  - **Start/delta/end discipline** for streamed content (reasoning, tool args, text).
  - **New/extended parts:** `data-guardrail` (verdict, rules[], score, rationale, redactions),
    richer `data-retrieval` citations (`{id, content, score, origin: graph|vector, uri}`),
    `data-rerank` (before/after), `data-shap` (`{base, prediction, features[]}`),
    `data-conformal` (`{point, lower, upper, coverage}`), `reasoning` deltas.
  - Each event stamps an **OpenInference span-kind** (`LLM`/`RETRIEVER`/`RERANKER`/`TOOL`/
    `GUARDRAIL`/`AGENT`/`CHAIN`/`EVALUATOR`) + `traceId/spanId/parentSpanId`, so the **same stream**
    (1) renders live in the UI and (2) exports as OTel/OpenInference spans to Phoenix. One contract,
    two consumers.
- The event union and its schema live in **`aegis.core.events`** so every module and the frontend
  share one source of truth.

### Pillar C — Honest infra (fail-loud policy)

- Typed config (`aegis.core.config`) with an explicit **`AEGIS_MODE`**:
  - `full` (**default**) — probes Redis + Postgres + the **pgvector extension** at boot and
    **refuses to start** (non-zero exit, clear message naming the unreachable host) if any required
    backend is missing.
  - `lite` — deliberately boots on in-memory implementations; **loudly** announced in logs,
    `/readyz`, and a persistent UI banner ("LITE MODE — in-memory, non-durable").
  - `auto` (opt-in) — probe; on failure drop to lite **but stay loud** (never silent).
- Backend selection goes through a **factory** that chooses by explicit mode and **logs the choice**.
  There is **zero** `except → in-memory` path. In-memory impls remain first-class for tests/lite but
  are returned **only** when the mode is `lite`/`auto`. A belt-and-suspenders boot assertion: in
  `full` mode, `isinstance(store, InMemory*)` raises.
- **`/readyz`** reports each dependency's real status (`redis: up/down`, `postgres: up/down`,
  `pgvector: present`, active `mode`, concrete store class). **`/healthz`** (liveness) does no I/O.
  The frontend reads `mode` from `/readyz` and renders it truthfully — never inferred/hardcoded.

---

## 3. The guardrails pilot (concrete)

### 3.1 Migration strategy — strangler, not big-bang

We do **not** rename `backend/src/app/ → aegis/` wholesale. We create a new top-level **`aegis/`
package** and migrate **guardrails only** into it first. The existing `backend/app` continues to
work by importing guardrails from `aegis` through a thin shim (`app.guardrails` re-exports
`aegis.guardrails`). The working system never breaks. Once the pilot is proven, later specs migrate
the remaining components one at a time, deleting each shim as its component moves.

### 3.2 Package layout

```
aegis/
  core/                      # ZERO heavy deps — the shared contract
    interfaces.py            # Protocols: Guardrail (full), + typed stubs for peers
    events.py                # canonical event union (extends StreamEvent) + span-kinds
    types.py                 # GuardrailResult, GuardVerdict, Redaction, enums
    registry.py              # @register("guardrail","pii") + lookup + entry-point discovery
    lazy.py                  # require("aegis[nemo]","nemoguardrails") -> loud ImportError
    config.py                # Settings + AEGIS_MODE (full/lite/auto), fail-fast validation
    health.py                # probe_redis / probe_postgres / probe_pgvector
  guardrails/                # SOTA-complete; depends ONLY on aegis.core + its libs
    __init__.py              # public API: Guardrail, run_guards, check_input, check_output
    pii.py                   # PII detection + redaction (SOTA: regex + presidio-optional)
    injection.py             # jailbreak/override/leak: deterministic signatures + classifier
    output_rails.py          # output validation, leak-marker denylist, spotlighting
    nemo.py                  # NeMo Colang engine (optional extra: aegis[nemo])
    pipeline.py              # composes rails, enforces order, fail-closed
    stream.py                # emits step.started -> data-guardrail -> step.finished
    cache.py                 # injection-classifier cache: Redis(full) | InMemory(lite) via factory
```

`aegis.guardrails` is the **full** current guardrails system (`classifier`, `pii`, `nemo`, `rails`,
`output` denylist, spotlighting, fail-closed injection) refactored behind the `Guardrail` protocol,
registered, streaming its steps, and infra-honest — plus SOTA hardening (e.g. optional Presidio for
PII, a broadened injection signature set). Nothing is dropped in the move.

### 3.3 The three pillars realized on guardrails

- **A:** `pip install aegis[guardrails]` → `from aegis.guardrails import run_guards` works with nothing
  else installed. NeMo is `aegis[nemo]`; absent → `require()` raises the install command, never a
  silent skip. `aegis.guardrails` imports only `aegis.core` + guardrail libs.
- **B:** running a guard emits, in order:
  `step.started{name:"guard_input", spanKind:GUARDRAIL}` →
  `data-guardrail{verdict, rules[], score, rationale, redactions}` → `step.finished`.
  The same events export as a GUARDRAIL OpenInference span.
- **C:** the injection-classifier cache uses Redis in `full` (probed at boot) or an explicit
  in-memory store in `lite`, chosen by the factory, logged — never by `except`.

### 3.4 Public API (stable surface)

```python
from aegis.guardrails import Guardrail, run_guards, check_input, check_output
from aegis.core import GuardrailResult, GuardVerdict, AegisMode

result: GuardrailResult = await check_input("ignore previous instructions ...")
# result.verdict in {PASS, BLOCK, REDACT, FLAG}; result.rules, result.score, result.redactions
```

---

## 4. Frontend — the process rail (UX is a scored deliverable)

- A vertical **process step timeline** down the console. Each `step.started` adds a row
  (guard_input → route → reasoning → retrieve → ml → tool → approval → answer), lit live in its
  subsystem color, with latency + token/cost chips. Clean, ordered, legible on a projector.
- Each row **expands to a specialized renderer** via one `event.type → React component` **dispatcher**
  (assistant-ui pattern): guardrail verdict card, citation source-cards (id/score/graph-vs-vector
  badge), SHAP waterfall (Recharts, plain-language labels), conformal band, streamed tool-args,
  approval Approve/Reject card. **Unknown event type → clean JSON fallback**, so nothing the agent
  does is ever invisible.
- A persistent **infra-mode banner** driven by `/readyz.mode` (green "Live infrastructure" chips vs
  yellow "LITE MODE — non-durable").
- The **frontend-design skill** is invoked when building the rail so it is genuinely polished
  (typography, color system, motion), not a templated default.
- For the pilot, the rail must fully render the **guardrail** step end-to-end; the specialized
  renderers for retrieval/SHAP/etc. are built now as registered components but exercised as their
  modules land.

---

## 5. Testing & proof

The pilot is "done" only when all of the following pass:

1. **Standalone-import proof.** `examples/use_guardrails_standalone.py` — a short script that, in a
   **clean venv** with only `pip install aegis[guardrails]`, imports, runs a guard, and prints the
   event stream. If it runs, modularity is real (not claimed).
2. **Isolated-import test** — importing `aegis.guardrails` does not import litellm/torch/langgraph
   (assert those modules are absent from `sys.modules`).
3. **Event-sequence test** — a guard run emits exactly `step.started → data-guardrail → step.finished`
   with the OpenInference `GUARDRAIL` span-kind and a well-formed payload.
4. **Fail-loud infra test** — `AEGIS_MODE=full` with no Redis → boot raises `SystemExit`; a
   grep-enforced test asserts **no** `except ... : return InMemory*()` path exists in the codebase.
5. **Frontend render test** — the process rail renders a guardrail row from a fixture event stream,
   including the expand-to-verdict-card and the unknown-type JSON fallback.
6. **Parity** — existing guardrail behavior (PII redaction, jailbreak block, fail-closed) still
   passes through the `app.guardrails` shim; the full backend suite stays green.

All tests run **offline** — no live infra, no API keys — via the `lite` mode + mocked backends.

---

## 6. Rollout after the pilot (future specs)

Each is its own spec → plan → build, in your groups of 2–3:

1. **Pilot (this spec):** `aegis.core` + SOTA `aegis.guardrails` + process rail.
2. **Rest of Set 1:** token-optimization module, evals module (RAGAS + LLM-judge), to the contract.
3. retrieval/RAG (real pgvector + graph + citations) + ML/SHAP (streamed explanations).
4. gateway/routing + memory + governance.
5. agent orchestration + trace + frontend money-shot polish.

---

## 7. Risks & mitigations

- **Restructure churn breaks the working backend.** → Strangler + shim; full suite green at each step.
- **`aegis.core` accidentally gains a heavy dep.** → CI/test asserts core's import graph is
  dep-free; PR checklist item.
- **Scope creep (trying to migrate everything now).** → This spec is core + guardrails + rail only.
- **PEP-420/namespace pitfalls surface at the future multi-wheel split.** → Deferred; boundary rules
  keep it mechanical, no namespace work needed for the single-package build.

---

## 8. Definition of done (this spec)

`aegis.core` exists (dep-free, contract complete), `aegis.guardrails` is a SOTA-complete importable
module emitting its step stream and honest about infra, the frontend process rail renders the
guardrail step cleanly with the dispatcher + JSON fallback + infra-mode banner, all six proofs in §5
pass, and the existing backend still works through the shim.
