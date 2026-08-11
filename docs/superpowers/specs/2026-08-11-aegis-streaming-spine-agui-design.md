# Aegis Common Streaming Spine (AG-UI) — Design Spec

- **Date:** 2026-08-11
- **Branch:** continues on `feat/aegis-module-contract` (or a new `feat/aegis-streaming-spine`)
- **Status:** Design for review
- **Depends on:** the Module Contract pilot (`2026-08-11-aegis-module-contract-design.md`) — `aegis.core` exists.

## 1. Problem & goal

The guardrails pilot proved the Module Contract, but its event stream is thin
(`StepStarted → GuardrailEvent → StepFinished`, 3 fields) and bespoke. We need:

1. **A common streaming primitive** every module imports and emits through — defined once,
   not reinvented per module.
2. **Rich, readable detail** — for guardrails (which rail fired, matched span, per-rail timing,
   exact redactions) and, critically, **live agent-thinking / reasoning streaming**.
3. **A recognized standard wire format** (user decision): adopt the **AG-UI protocol** so the
   frontend can use ecosystem renderers (CopilotKit, assistant-ui) and we can say "we speak the
   agent-UI standard."

**This is the shared spine every later module extraction builds on** — get it right first, then
modularize the platform breadth-first onto it (see §7 roadmap).

## 2. Decisions (from research — `docs/superpowers/…` AG-UI research)

- **D1 — Import the official SDK, don't hand-roll.** Depend on `ag-ui-protocol` (PyPI, pin `~=0.1.19`,
  Pydantic v2 models + `ag_ui.encoder.EventEncoder`). It is pydantic-only (no heavy deps) → allowed in
  `aegis.core`. Frontend: `@ag-ui/core` (types + zod) + `@ag-ui/client` (`HttpAgent`).
- **D2 — Reasoning via CustomEvent for now.** Native `REASONING_*` events are DRAFT / not in the stable
  release. Stream agent thinking as `CustomEvent(name="reasoning", value={messageId, delta})`; the
  frontend renders it as a live collapsible "thinking" lane. Swap to native reasoning events when they
  graduate (isolated behind the emitter, so it's a one-file change).
- **D3 — Domain payloads via CustomEvent + a shared name registry.** SHAP, conformal, guardrail detail,
  and retrieval citations ride `CustomEvent` with canonical `name`s owned by `aegis.core.stream.names`
  (one source of truth imported by backend and mirrored in the frontend).
- **D4 — The emitter enforces the AG-UI contract.** camelCase on the wire (`by_alias=True`), no `event:`
  SSE line (type in-band), `\n\n` frame boundary, and the ordering/bracketing rules (RUN_STARTED first →
  RUN_FINISHED/RUN_ERROR last; message & tool events bracketed by a shared id; `TOOL_CALL_ARGS.delta` is
  partial JSON to concatenate). Modules never touch these rules — they call ergonomic helpers.

## 3. The spine — `aegis.core.stream`

New module `aegis/src/aegis/core/stream.py` (+ `aegis/src/aegis/core/stream_names.py`):

```python
from ag_ui.core import (RunStartedEvent, RunFinishedEvent, RunErrorEvent, StepStartedEvent,
                        StepFinishedEvent, TextMessageStartEvent, TextMessageContentEvent,
                        TextMessageEndEvent, ToolCallStartEvent, ToolCallArgsEvent,
                        ToolCallEndEvent, ToolCallResultEvent, CustomEvent, EventType)
from ag_ui.encoder import EventEncoder

class AegisEmitter:
    """The one streaming primitive every Aegis module emits through.

    Wraps the AG-UI EventEncoder, stamps run/message/tool ids, enforces the AG-UI
    ordering + bracketing contract, and exposes ergonomic helpers so modules never
    construct raw events or worry about the wire rules.
    """
    def __init__(self, *, thread_id: str, run_id: str, sink: Callable[[str], Awaitable[None]] | None = None): ...

    # lifecycle
    async def run_started(self) -> None: ...
    async def run_finished(self, result: dict | None = None) -> None: ...
    async def run_error(self, message: str, code: str | None = None) -> None: ...

    # steps (context manager -> STEP_STARTED / STEP_FINISHED, with timing)
    def step(self, name: str, span_kind: "SpanKind") -> "StepScope": ...

    # agent thinking (D2): CustomEvent name="reasoning"
    async def reasoning(self, delta: str, *, message_id: str | None = None) -> None: ...

    # assistant text answer (bracketed TEXT_MESSAGE_*)
    async def text_start(self, message_id: str, role: str = "assistant") -> None: ...
    async def text_delta(self, message_id: str, delta: str) -> None: ...
    async def text_end(self, message_id: str) -> None: ...

    # tool calls (bracketed TOOL_CALL_*; args delta is partial JSON)
    async def tool_start(self, tool_call_id: str, name: str) -> None: ...
    async def tool_args(self, tool_call_id: str, delta: str) -> None: ...
    async def tool_end(self, tool_call_id: str) -> None: ...
    async def tool_result(self, tool_call_id: str, message_id: str, content: str) -> None: ...

    # domain payloads (D3): CustomEvent name from the registry
    async def custom(self, name: str, value: dict) -> None: ...
```

`aegis/src/aegis/core/stream_names.py` — the canonical registry:

```python
REASONING = "reasoning"
GUARDRAIL_VERDICT = "guardrail_verdict"
SHAP_EXPLANATION = "shap_explanation"
CONFORMAL_INTERVAL = "conformal_interval"
RETRIEVAL_CITATIONS = "retrieval_citations"
ROUTING = "routing"
MEMORY_RECALL = "memory_recall"
ALL: frozenset[str] = frozenset({...})   # the emitter validates custom() names against this
```

Every event carries the OpenInference `span_kind` inside its payload (for guardrail/retrieval/etc.
CustomEvents) or as a step attribute, so the same stream still exports as Phoenix/OTel traces.

`aegis.core.stream` is dependency-light: `ag-ui-protocol` + pydantic + stdlib only. It stays within the
"no heavy deps in core" invariant (ag-ui-protocol is pydantic-only). A `test_core_is_dep_free` update
adds `ag_ui` to the allowed set but keeps litellm/torch/langgraph/etc. banned.

## 4. Guardrails retrofit

`aegis.guardrails.pipeline.Guardrails` gains a streaming method that emits through an `AegisEmitter`:

```python
async def stream_check_input(self, text: str, emitter: AegisEmitter) -> GuardResult:
    async with emitter.step("guard_input", SpanKind.GUARDRAIL):
        result = await self.check_input(text)
        await emitter.custom(names.GUARDRAIL_VERDICT, {
            "verdict": result.verdict.value,
            "rules": [result.layer] if result.layer else [],
            "matched_span": ...,          # the offending span, when applicable
            "per_rail_timing_ms": {...},  # schema / pii / injection timings
            "redactions": result.redactions,
            "rationale": result.reason,
            "spanKind": "GUARDRAIL",
        })
    return result
```

The existing non-streaming `check_input`/`check_output` and the current `stream_check_input` (our-own
event union) are kept for back-compat during the transition; the AG-UI method is additive. The legacy
`GuardrailEvent`/`StepStarted`/`StepFinished` in `aegis.core.events` are retained until the frontend and
all modules move to AG-UI, then removed in a cleanup task.

## 5. Backend SSE integration (thin, in this spec)

Add one demonstrator endpoint (or adapt the existing `/query` path in a follow-on) that runs a guardrail
check and streams **real AG-UI SSE** via the emitter + `EventEncoder`: `Content-Type: text/event-stream`,
one `data: <json>\n\n` frame per event, no `event:` line. This proves the wire format end-to-end. The
full `/query` orchestrator migration to AG-UI is a follow-on (§7), not this spec.

## 6. Frontend (thin, in this spec)

- Add `@ag-ui/core` (types + zod schemas) to the frontend deps; add a canonical CustomEvent-name constants
  file mirroring `stream_names.py`.
- A minimal decoder path (either `@ag-ui/client` `HttpAgent` or a thin `fetch`+SSE reader validating with
  `@ag-ui/core` schemas) that consumes the demonstrator stream and logs/render-tests: text deltas by
  `messageId`, tool-arg concatenation, and `switch(event.name)` for custom payloads (guardrail verdict,
  reasoning).
- The **full console** (reasoning lane, process rail, citation cards, SHAP, CopilotKit/assistant-ui
  renderers) is the follow-on frontend spec — this spec only proves the frontend can consume the AG-UI
  stream and render a guardrail verdict + a reasoning delta.

## 7. Roadmap (breadth-first, per user direction)

1. **This spec:** the streaming spine (`aegis.core.stream` on AG-UI) + guardrails retrofit + end-to-end proof.
2. **Module extraction waves** — each becomes an importable `aegis.<module>` emitting through the emitter:
   - `aegis.agent` (**reasoning.delta live thinking**, routing, tool calls) · `aegis.retrieval`
     (candidates → reranked → citations) · `aegis.ml` (prediction, SHAP, conformal) · `aegis.gateway`
     (model calls, tokens, cost, cache) · `aegis.memory` · `aegis.governance` · `aegis.evals` ·
     `aegis.observability`.
   - Goal: whole platform is `import aegis.<x>`, all speaking one AG-UI stream.
3. **Full frontend console** — reasoning lane + process rail + citation/SHAP renderers over the AG-UI stream.
4. **Depth pass** — mature each module to SOTA functions.

## 8. Testing & proof

1. **Emitter contract tests (offline):** run a scripted sequence through `AegisEmitter` capturing the encoded
   SSE; assert exact AG-UI frames (camelCase keys, no `event:` line, `\n\n` boundary), correct ordering
   (RUN_STARTED first, RUN_FINISHED last), bracketed message/tool ids, and that `custom()` rejects a name
   not in the registry.
2. **Round-trip:** feed the captured frames back through the AG-UI decoder (`ag_ui` models / a JSON parse)
   and assert they validate — proving we emit spec-valid events.
3. **Guardrails-over-AG-UI:** a block/redact/pass run emits `STEP_STARTED` → `CUSTOM(guardrail_verdict, rich)`
   → `STEP_FINISHED`; assert the rich payload fields.
4. **Reasoning stream:** emitting N `reasoning(delta)` calls produces N `CUSTOM(name="reasoning")` frames with
   the deltas in order.
5. **Dep-free guard updated:** `aegis.core` import graph adds only `ag_ui` (+ pydantic); heavy deps still banned.
6. **Frontend:** a decode/render test over a fixture AG-UI stream (guardrail verdict + reasoning delta).
7. All backend/aegis suites stay green.

## 9. Definition of done

`aegis.core.stream.AegisEmitter` emits spec-valid AG-UI SSE (proven by round-trip decode), guardrails stream
their verdict as a rich `CustomEvent`, agent-thinking has a first-class `reasoning` channel ready for modules,
a demonstrator endpoint produces a real AG-UI stream the frontend decodes and render-tests, the CustomEvent name
registry is shared, and all suites stay green. The spine is ready for breadth-first module extraction.
