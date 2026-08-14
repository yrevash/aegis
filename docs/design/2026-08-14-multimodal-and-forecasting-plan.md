# Plan — voice, vision and forecasting as first-class Aegis modules

**Date:** 2026-08-14 · **Status:** proposed, awaiting approval
**Constraint:** every module SOTA, no corner-cutting, each in its own clean module
obeying the existing Module Contract.

Three new capabilities — **voice-to-text**, **vision**, **forecasting** — plus the
foundations they all stand on, plus activating the Colang policy that currently
ships inert.

---

## 0. What the audits found

Two audits ran against the codebase before this plan was written. Their findings
set the sequencing, so they are recorded here rather than summarised away.

### 0.1 LangGraph — correct in one dimension, shallow elsewhere

**Good, and genuinely hard:** durable human-in-the-loop.
`interrupt()` → checkpoint → `Command(resume=...)` → cross-worker rehydration by
`thread_id`, with an optimistic `PENDING→RESUMING` DB lock giving exactly-once
tool execution. The `approval` node correctly emits *nothing* before
`interrupt()` because it re-executes on resume — a trap most implementations
fall into. This is right and should not be touched.

**Shallow everywhere else.** 14 nodes, 6 binary branches, 1 bounded loop, and:

| Gap | Consequence |
|---|---|
| **Zero `Annotated` reducers** (`state.py:15`, 39 keys, all last-write-wins) | Safe today only because no superstep ever runs two nodes. **Adding any parallel branch silently loses updates or raises `InvalidUpdateError`.** This blocks parallel vision/forecast outright. |
| No `Send` / parallelism | `retrieve` → `ml_predict` serialised though independent; `act` runs tool calls in a `for` loop. Free latency on the table. |
| No `RetryPolicy` on any node | One transient gateway blip in `plan`/`generate` kills the whole run. |
| `stream_answer` (`graph.py:887`) chunks an **already-complete** string | It is called streaming and is not. Either do `stream_mode="messages"` or rename it. |
| Router is substring matching over a **2-entry** roster, with the edge hardcoded (`graph.py:951-955`) | Any third specialist an adapter declares is silently routed to `qa`. The "multi-agent supervisor" claim is thin. |
| `graph.get_graph()` never called; frontend hardcodes its own DAG (`orchestration.ts:43-103`) | **The published architecture picture contradicts the code** — it draws the human gate branching out of `ml`, while the code gates on tool risk and states ML never gates. 7 of 14 real nodes cannot light up. Jury-visible. |
| `langgraph>=0.2` unbounded, running **1.2.11** | A framework that went 0.2 → 1.x under an unbounded pin, days before a competition. |

**Ordering defect, unrelated to the above:** `recall_memory` reads
`state["query_vec"]` (`graph.py:480`) but sits *upstream* of `retrieve`, the only
node that sets it (`graph.py:454`, edge at `:959`). **Semantic memory recall has
always run vector-less.**

### 0.2 Seams for the new work

- `ModelRole.VISION` and `ModelRole.VOICE` are **already declared, routed to real
  hosted models and priced** (`routing.py:27-28,87-88`) — and never invoked. The
  Token-opt dashboard already displays two models the platform never calls.
- The fleet hosts `azure/genailab-maas-whisper`,
  `azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct` and
  `azure_ai/genailab-maas-Phi-3.5-vision-instruct`, and the architecture doc
  states **only these models may be used**. So voice and vision are hosted calls
  through the existing gateway — inheriting budget enforcement, the usage ledger,
  routing and OTel for free. No local Whisper, no GPU question.
- `complete()` forwards `messages` verbatim into `litellm.acompletion`
  (`llm.py:657`), so **multimodal content blocks need no gateway signature
  change**. Transcription does — there is no audio path at all.
- **Guardrails are total-text.** `Rail = Callable[[str], ...]` (`pipeline.py:78`);
  every entry point is `str`-typed; the graph guards `state["query"]`, a string.
  An image passed to `complete()` **bypasses every rail**. Text-in-image prompt
  injection is the standard attack and nothing here would see it.
- **Colang ships inert.** `input.co` and `output.co` exist under
  `aegis/src/aegis/guardrails/config/rails/`, but `config.py:102` defaults
  `guardrails_engine` to `"programmatic"`.
- **The usage ledger is token-denominated end to end** (`Usage`, `_estimate_cost`,
  `_RoleAgg`, `GovernanceHook.record`). Whisper bills per audio-minute, so
  transcription would ledger **$0** and under-report the savings story.
- No route anywhere accepts a file; `python-multipart` is not a dependency.

---

## 1. Architecture: three new Aegis modules

Each capability becomes a first-class module under `aegis/src/aegis/`, obeying the
Module Contract: importable and isolated, dependency-light core, optional extras
failing loud via `aegis.core.lazy.require()`, its own AG-UI stream events, its own
tests, an entry in the capabilities manifest, and a console surface.

| Module | Extra | Underlying tech |
|---|---|---|
| `aegis.media` | — (core types) | Typed payloads + the media rail contract |
| `aegis.voice` | `aegis[voice]` | Hosted Whisper via the gateway |
| `aegis.vision` | `aegis[vision]` | Hosted Llama-3.2-90B-Vision / Phi-3.5-vision |
| `aegis.forecast` | `aegis[forecast]` | statsforecast + conformal intervals |

This takes the manifest from twelve modules to fifteen or sixteen.

---

## Phase 0 — Foundations (must precede every feature)

Several of these are correctness or security defects that exist today,
independently of the new work.

| # | Task | Why it is first |
|---|---|---|
| 0.1 | **`Annotated` reducers** on `cost_usd`, `prompt_tokens`, `completion_tokens`, `tool_results`, `messages`; drop `_accrue`'s read-modify-write (`graph.py:1043`) | Hard prerequisite for any parallel node. Without it, parallel vision/forecast corrupts accounting. |
| 0.2 | **Bound the langgraph pin** to `>=1.2,<2` in both pyprojects | Competition-day risk |
| 0.3 | **Fix the `query_vec` ordering defect** — move `recall_memory` after the vector exists, or compute the embedding upstream | Live bug: memory recall runs vector-less |
| 0.4 | **`RetryPolicy`** on `plan`, `generate`, `retrieve`, and the new model-calling nodes | Cheapest resilience win |
| 0.5 | **Generate the console orchestration map from `graph.get_graph()`** | Published picture currently contradicts the code |
| 0.6 | **Real token streaming** via `stream_mode="messages"` | Stop calling a word-chunker streaming |
| 0.7 | **Roster-driven routing** — replace the hardcoded memory/qa edge | Makes the multi-agent claim real |

---

## Phase 1 — `aegis.media`: the modality seam + Colang activation

The security phase. Nothing may reach a model until this exists.

**1.1 Typed payloads.** `aegis.media.types`: `MediaPayload` union of
`TextPayload | ImagePayload | AudioPayload`, each carrying bytes/URI, MIME type,
size and provenance. Pydantic-only, no heavy imports, so `aegis.core` stays clean.

**1.2 Widen the rail contract.** `Rail` becomes
`Callable[[MediaPayload], GuardResult | None | Awaitable[...]]`, with the existing
text rails adapted so a `TextPayload` behaves exactly as today. **Backwards
compatibility is a hard requirement** — the 50+ guardrail tests must pass
unchanged or the change is wrong.

**1.3 Media rails (new, SOTA):**
- **Image-injection screen** — a cheap vision call asking whether the image
  contains instructions/text directed at an AI, run *before* the main vision
  call. This is the control that closes the open channel.
- **Image PII** — `presidio-image-redactor`, which extends the Presidio
  dependency already present, and can return a genuinely redacted image rather
  than the meaningless "redact" verdict text rails would give.
- **Audio** — transcribe first, then run the full existing text rail stack on the
  transcript. Defensible and reuses everything.
- **Payload hygiene** — MIME sniffing, size caps, decompression-bomb limits.

**1.4 Activate Colang.** Wire a real `ChatCompleter` into the NeMo engine, flip
`guardrails_engine` to a genuine dual-engine switch, extend `input.co`/`output.co`
with flows covering the media cases, and add parity tests asserting the
programmatic and Colang engines agree on the same corpus.

**1.5 Gateway extensions.** `transcribe()`; `GenAIOperation.TRANSCRIPTION` and
`.VISION`; `Usage` gains non-token units (`audio_seconds`, `image_count`) and
`_estimate_cost` learns per-minute and per-image rates; `GovernanceHook.record`,
`record_usage` and the `UsageLedger` table follow. Budget enforcement itself needs
no change — `enforce(ctx)` is modality-agnostic, so the new paths simply copy the
four existing lines.

**1.6 Upload path.** Add `python-multipart` and real `UploadFile` routes. Chosen
over base64 deliberately: base64 inflates payloads ~33% and streams badly for
audio.

---

## Phase 2 — `aegis.voice`

**Backend.** `POST /voice/transcribe` (multipart) → `aegis.voice.transcribe()` →
gateway `transcribe()` with `ModelRole.VOICE`. Long audio is chunked with silence
detection so a lecture-length file works. Transcript, segments, language and
duration are returned; duration feeds the ledger.

**Graph.** New `transcribe` node, `START → transcribe → guard_input`
(replacing `graph.py:940`), so transcription is a visible glass-box step with its
own OTel span and stream event — not hidden in the API layer. `AgentState` gains
`audio_ref`; `query` remains the transcript sink. `AgentDeps` gains a
`transcribe` callable, wired host-side.

**Console — new AI-team section.** Live recorder (`MediaRecorder`), waveform,
transcript with per-segment confidence, rail verdicts on the transcript, and a
"send to the agent" action that runs the full pipeline from speech.

**SOTA notes:** word-level timestamps, language auto-detect, and a diarisation
pass if the hosted model exposes it; otherwise stated as not-supported rather than
faked.

---

## Phase 3 — `aegis.vision`

**Backend.** `POST /vision/analyse` (multipart) → payload hygiene → **injection
screen** → image PII → hosted vision call via `ModelRole.VISION` with OpenAI-style
content blocks → output rails on the returned text.

**Graph.** `vision` node modelled on `ml_predict` (best-effort, writes
`vision_summary`, concatenated into the planner prompt exactly as `ml_summary` is
at `graph.py:594-596`). Parallel with `retrieve` **only after Phase 0.1 lands**;
serial until then.

**Console.** Upload/drag-drop, the image with detected-PII regions overlaid, the
injection-screen verdict shown prominently (this is the differentiator), the
model's analysis, and the cost of the call.

---

## Phase 4 — `aegis.forecast`

**Chosen engine: statsforecast** (AutoARIMA / AutoETS / Theta), per direction.

**Resolver risk to settle first.** `[tool.uv] constraint-dependencies` pins
`numba==0.67.0` (held for shap) and caps `numpy<2.5` / `pandas<2.4` (held for
presidio and nemoguardrails). statsforecast is numba-based. **Task 4.0 is a
dependency spike** — resolve statsforecast against the existing lock and report
before any code is written. If it cannot resolve, the fallback is lag-feature
reduction into the existing `TrustworthyModel`, and that decision is escalated,
not taken silently.

**Correctness — the trap.** The existing conformal calibration uses a random
`train_test_split` (`model.py:273`). On time series that leaks the future into
calibration and the coverage guarantee becomes **statistically invalid**. Forecast
intervals must use a chronological split or a time-series conformal method
(EnbPI / adaptive conformal). Shipping the current split under a forecast label
would be a false claim of calibration, which is exactly the kind of thing this
platform refuses to do elsewhere.

**Type work.** `TaskType` widened beyond its closed 2-value Literal — and
`_coerce_task` (`spec.py:82-95`) currently maps anything unknown to
`"regression"`, so a forecast spec would silently train a tabular regressor.
`ForecastResult` is a new horizon-indexed type (`[(t, ŷ, lo, hi)]`); the scalar
`MLExplainResponse` cannot represent it.

**Two use cases, both on real data:**
- **Team/platform:** per-tenant spend and call-volume forecasting off
  `usage_ledger`, which already carries indexed `ts`, `tenant_id`, `user_id` and
  cost. Feeds a budget-burn-down projection.
- **Client:** domain series through the adapter seam, so it retargets with
  everything else.

**Console.** Forecast chart reusing the existing `ConformalBand` component,
horizon selector, backtest accuracy (MAPE/sMAPE) shown honestly, and the
budget-burn projection.

---

## Sequencing and gates

```
Phase 0  foundations ....... blocks everything
Phase 1  aegis.media ....... blocks vision; security-critical
Phase 2  aegis.voice ....... first user-visible feature
Phase 3  aegis.vision ...... needs Phase 1
Phase 4  aegis.forecast .... independent; start 4.0 spike early, in parallel
```

`4.0` (the dependency spike) runs during Phase 0 because a negative result changes
the Phase 4 design.

## Definition of done, per module

1. Module Contract: importable, isolated, optional extra failing loud, no `app.*`
   imports under `aegis/`.
2. AG-UI stream events registered in `aegis.core.stream_names` **and** mirrored in
   `web/src/agui/streamNames.ts`.
3. Entry in `capabilities.py` with a real, import-checked `module_path`.
4. Tests: unit + an isolation test + a security test for every new rail.
5. A console surface, light theme, no fabricated numbers, honest empty states.
6. `tsc`, `next lint`, `next build`, and both test suites green.
7. Docs: a `docs/module/aegis-<name>.md` matching the existing eleven.

## Explicitly out of scope

Local model hosting of any kind (policy: only the listed fleet models); video;
real-time bidirectional voice conversation; fine-tuning.
