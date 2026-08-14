# Guardrails — the implementation in Aegis

Every claim here is checkable against source. Paths are relative to the repo root.

The module lives at **`aegis/src/aegis/guardrails/`** — standalone, LLM-agnostic, with the
model seam injected. The backend at **`backend/src/app/guardrails/`** is a strangler shim
that wires the platform's cost-routed gateway and selects the enforcement engine.

---

## How you import it

The convenience surface, for a one-off check:

```python
from aegis.guardrails import check_input, check_output
result = await check_input("... user text ...", completer=my_completer)
```

The real surface, for a configured pipeline:

```python
from aegis.guardrails import Guardrails
from aegis.core.types import GuardResult, GuardVerdict

guard = Guardrails(
    completer=my_completer,           # text rails' model layer
    vision_completer=my_vision_model, # image screen; None ⇒ images fail closed
    transcriber=my_asr,               # audio; None ⇒ audio blocked
    input_rails=[...], output_rails=[...],
    allowed_topics="...", topical_block=False,
    ground_answers=True, grounding_block=False,
    image_pii=False,
)
verdict = await guard.check_input(text_or_payload)
```

`aegis/src/aegis/guardrails/__init__.py` also re-exports the submodules (`content_safety`,
`grounding`, `pii`, `schema`, `topical`), the media surface (`MediaScreen`,
`MediaGuardResult`, `media_rail`, `screen_image`), the rail type aliases (`Rail`,
`LegacyTextRail`, `AnyRail`), and the taxonomy constant `HAZARD_CATEGORIES`.

---

## The verdict type

**`aegis/src/aegis/core/types.py`**:

| Type | Line | Notes |
|---|---|---|
| `GuardVerdict` | `types.py:16` | `pass` / `flag` / `redact` / `block` |
| `GuardStage` | `types.py:52` | `input` / `output` |
| `GuardResult` | `types.py:89` | `verdict`, `reason`, `text`, `layer`, `redactions` |
| `InjectionVerdict` | `types.py:75` | `injection: bool`, `reason` |
| `FormatCheck` | `types.py:82` | `ok: bool`, `reason` |
| `PIIMatch` | `types.py:66` | `kind`, `start`, `end` |

`layer` is the rail that fired — `"schema"`, `"pii"`, `"injection"`, `"content_safety"`,
`"topical"`, `"grounding"`, `"media_hygiene"`, `"media_injection"`, `"media_pii"`,
`"nemo-input"`, `"nemo-output"`, `"nemo-media"`. It is what the console renders and what
the trace-eval reads.

`text` may be the *redacted* form. `GuardResult.text` on a `REDACT` verdict is what the
caller must forward.

---

## Rail 1 — schema

**`aegis/src/aegis/guardrails/schema.py`.** Pure functions, no I/O.

| Function | Line | Rejects |
|---|---|---|
| `validate_input_format` | `schema.py:99` | Empty/whitespace; over `MAX_INPUT_CHARS` (8000, `schema.py:42`); disallowed invisible characters |
| `validate_output_format` | `schema.py:129` | Over `MAX_OUTPUT_CHARS` (20000, `schema.py:45`); disallowed invisibles. Empty output is **allowed** — a model may legitimately produce nothing |
| `content_filter` | `schema.py:157` | Output containing a `_OUTPUT_DENYLIST` marker (`schema.py:53-72`) |

`_disallowed_chars` (`schema.py:75`) checks the text **twice** — as written and after NFKC
(`schema.py:90-96`) — so a codepoint that only *decomposes* into a disallowed one cannot
slip past.

The denylist has three deliberate groups (`schema.py:53-72`): chat-template control tokens
(`<|im_start|>`, `[/INST]`, `<<SYS>>`), explicit system-prompt framing ("begin system
prompt", "my system prompt"), and verbatim echoes of this platform's own preamble ("you are
a guarded enterprise assistant"). It is a targeted backstop layered *after* PII redaction,
not a moderation model, and the docstring says so.

---

## Normalisation — the comparison-only view

**`aegis/src/aegis/guardrails/normalize.py`.** Three public functions and a constant.

`is_disallowed_invisible(char)` (`normalize.py:97`) rejects:

- C0 controls other than `\t\n\r` (`_ALLOWED_CONTROL`, `normalize.py:48`), and DEL
- The C1 block (0x80–0x9F)
- **The whole Unicode Tag block** U+E0000–U+E007F, by explicit range
  (`normalize.py:56-57`, tested at `normalize.py:116`)
- Categories `Cf`, `Co`, `Cs` (`_REJECTED_CATEGORIES`, `normalize.py:65`)

`Cn` (unassigned) is deliberately **not** in the category set (`normalize.py:62-64`): a
Python whose Unicode database predates a new emoji would otherwise reject legitimate text.
The Tag block is mostly `Cn`, which is exactly why it needs its own range test.

`disallowed_invisible_chars(text)` (`normalize.py:121`) returns distinct `U+XXXX` labels,
capped at eight — the labels, never the surrounding text, so a rejection reason never echoes
user content.

`fold_for_matching(text)` (`normalize.py:150`) is the pipeline: strip invisibles → NFKC →
NFD and drop combining marks → collapse whitespace. **Case is preserved** (`normalize.py:157`)
so a rail can still match case-sensitively.

`deconfuse(text)` (`normalize.py:171`) is `fold_for_matching` plus the `CONFUSABLES` map
(`normalize.py:71-92`): Cyrillic and Greek upper and lower case, plus a handful of Latin
lookalikes NFKC leaves alone (`ı`, `ȷ`, `ɡ`, `ø`, `ł`, `đ`).

**The module docstring states the invariant** (`normalize.py:19-21`): *"The folded text is
never propagated. Rails match on it and then hand the original string downstream, so
normalisation can never itself become a mutation vector."*

And it states its own coverage limits (`normalize.py:23-31`): not the full Unicode
confusables table, and no leetspeak folding. Those fall through to the model classifier.

---

## Rail 2 — PII

**`aegis/src/aegis/guardrails/pii.py`** is a stable facade over two engines:

- `_pii_presidio.py` — Microsoft Presidio (`presidio-analyzer` + spaCy)
- `_pii_regex.py` — the pure-code fallback

Public surface: `scan(text) -> list[PIIMatch]`, `redact(text) -> (masked, kinds)`,
`contains_pii(text) -> bool`, `active_engine()`.

Engine selection is **lazy and self-healing** (`pii.py:14-19`): heavy dependencies are
imported on first use, and any Presidio/spaCy failure falls back to regex with a log line
naming the live engine. It never crashes and never *silently* stops redacting. Pinnable via
`AEGIS_PII_ENGINE` (`pii.py:21`), which is the seam tests use to exercise the fallback.

`_luhn_valid` is re-exported (`pii.py:33`) for the card-heuristic tests.

Redaction produces `[REDACTED_<KIND>]` tokens and a **sorted** kinds list, so the reason
string is deterministic.

---

## Rail 3 — injection

**`aegis/src/aegis/guardrails/classifier.py`.** Two layers.

### The deterministic backstop

`deterministic_injection(text)` (`classifier.py:291`) — pure, offline, no network.

It matches against **three comparison views** (`classifier.py:325-329`):

1. `fold_for_matching(text)`
2. `deconfuse(text)` — in *addition*, not instead, because the map mangles genuine
   Cyrillic prose
3. Both views of any UTF-8 text recovered from a base64 run

Base64 recovery is `_decoded_base64_candidates` (`classifier.py:249`): scans for runs of
≥16 base64 characters (`_BASE64_RUN`, `classifier.py:242`), both as written and with all
whitespace removed so a blob broken across lines still decodes, pads, decodes with
`validate=True`, and keeps results that are ≥8 characters and `isprintable()`. Capped at
12 candidates (`_MAX_BASE64_CANDIDATES`, `classifier.py:246`) so a pathological input
cannot turn the rail into a CPU sink.

The English signatures (`classifier.py:125-187`) are built from composable fragments rather
than written out longhand:

| Fragment | Line | What it is |
|---|---|---|
| `_GAP` | `classifier.py:99` | Up to three filler words *with* a separator — this is what makes "ignore **the above** directions" match the same signature as "ignore all previous instructions" |
| `_NEAR` | `classifier.py:103` | Up to two filler words, separator optional — the adjective→noun hop |
| `_AUTHORITY` | `classifier.py:106` | Words naming the standing instructions |
| `_SECRET_AUTHORITY` | `classifier.py:114` | The *confidential* subset, used only by the exfiltration signature |
| `_INSTRUCTION_NOUN` | `classifier.py:120` | Nouns for the instruction set |

The `_AUTHORITY` / `_SECRET_AUTHORITY` split is a false-positive control: the broad set
would block an ordinary *"show me all instructions in the employee handbook"*. Similar
narrowing appears throughout — the "message" pattern (`classifier.py:149-154`) requires a
tighter qualifier because "repeat the previous message" is an ordinary chat request while
"repeat the system message" is exfiltration, and `\bDAN\b` (`classifier.py:186`) is matched
**case-sensitively** so the given name "Dan" is not a hard block.

`_MULTILINGUAL_SIGNATURES` (`classifier.py:195-239`) covers German, Spanish, French,
Italian, Portuguese, Dutch and Russian — and *only* the "ignore/forget the previous
instructions" family within them. The docstring says "explicitly partial"
(`classifier.py:189-194`). The patterns are written without diacritics because they match
the folded view; the Russian patterns use stems only, because folding strips the combining
mark from `й`.

### The model layer

`classify_injection(text, *, completer)` (`classifier.py:341`) makes one call with
`response_format={"type": "json_object"}` and **fails closed** on any completer exception
(`classifier.py:358-362`).

`_parse_verdict` (`classifier.py:44`) prefers real JSON, falls back to
`parse_bool_field(text, "injection")`, and maps "no verdict" to `injection=True` with the
reason *"Classifier response was unparseable; blocked as a precaution"* (`classifier.py:77-80`).

`detect_injection(text, *, completer)` (`classifier.py:366`) chains them. With
`completer=None` the model layer is **explicitly disabled and logged**
(`classifier.py:388-395`), never silently skipped.

---

## Rail 4 — content safety

**`aegis/src/aegis/guardrails/content_safety.py`.** Same two-layer shape.

`HAZARD_CATEGORIES` (`content_safety.py:39-53`) is the MLCommons/Llama Guard S1–S13 map.
`ContentSafetyVerdict` (`content_safety.py:56-69`) carries `unsafe`, `categories` (validated
against the taxonomy by `_valid_codes`, `content_safety.py:101`) and a `label()` helper.

`_HAZARD_SIGNATURES` (`content_safety.py:77-87`) is **deliberately narrow** — six patterns
covering only CBRN synthesis (S9), child exploitation (S4) and concrete self-harm methods
(S11). Nuance is the model layer's job.

`deterministic_hazard` (`content_safety.py:140`) matches over
`(fold_for_matching(text), deconfuse(text))` (`content_safety.py:157`), for exactly the same
reason the injection rail does. This is one of the fixed bugs — see `30-deep-dive.md`.

`screen_content` (`content_safety.py:189`) runs signatures then the self-check. No
completer ⇒ model layer explicitly disabled and logged (`content_safety.py:202-209`).

---

## Rail 5 — topical (advisory)

**`aegis/src/aegis/guardrails/topical.py`.** `screen_topic` (`topical.py:108`) returns a
`TopicVerdict` (`topical.py:37`). `describe_topics` (`topical.py:47`) normalises a string or
a list into a prompt fragment. Off entirely when `allowed_topics` is `None`/empty.

`_parse_verdict(raw, *, fail_closed)` (`topical.py:73`) takes the fail direction as a
parameter — the same parser, both directions, chosen by the caller's `block` flag.

---

## Rail 6 — grounding (advisory, output only)

**`aegis/src/aegis/guardrails/grounding.py`.** `check_grounding` (`grounding.py:101`) judges
whether every factual claim in an answer is entailed by the retrieved contexts.

There is **no deterministic backstop** — groundedness is a semantic entailment judgement
(`grounding.py:12-13`). So the fail direction is explicit and documented
(`grounding.py:15-21`): advisory mode fails **open** (a downed checker never manufactures a
spurious advisory), `block=True` fails **closed**. `_parse_verdict(raw, *, fail_closed)`
(`grounding.py:66`) takes the direction as a parameter, exactly like the topical rail.

With no `contexts` the rail is a no-op PASS — there is nothing to ground against.

---

## The verdict parser

**`aegis/src/aegis/guardrails/verdict_parsing.py`** — 84 lines, one public function, and
the module docstring is the best short explanation of a fail-open bug in the codebase
(`verdict_parsing.py:1-28`).

`parse_bool_field(raw, field)` (`verdict_parsing.py:53`) returns `True`, `False`, or
**`None`** meaning *no verdict*. It accepts only:

1. `"<field>": true|false` matched by `_field_pattern` (`verdict_parsing.py:44`) — quotes
   optional, `:` or `=`, whitespace tolerated, so it survives `injection : true` or unquoted
   keys.
2. A response whose *entire* content, after trimming `_TRIM` punctuation
   (`verdict_parsing.py:41`), is a bare token in `_BARE_AFFIRMATIVE` / `_BARE_NEGATIVE`
   (`verdict_parsing.py:37-38`).

If the text carries **both** `true` and `false` it returns `None` (`verdict_parsing.py:76-77`)
— contradictory is not a verdict.

Every caller maps `None` to its own fail direction. All four model-backed rails use it.

---

## The pipeline

**`aegis/src/aegis/guardrails/pipeline.py`.** `Guardrails` (`pipeline.py:109`), registered
in the core capability registry as `@register("guardrail", "default")` (`pipeline.py:108`).

Constructor (`pipeline.py:118-196`) takes twelve keyword arguments, documented individually
at `pipeline.py:138-177`. It builds a `MediaScreen` (`pipeline.py:189-196`) and resolves the
injection cache (`pipeline.py:186-188`).

### `check_input`

**`pipeline.py:461`.** Accepts `str | MediaPayload`. A non-text payload routes to
`_screen_media` (`pipeline.py:487-488`) — *"the one path that used to reach the model with
no rail in front of it"* (`pipeline.py:470`). A `str` caller is handed to the text path
directly with **no encode/decode round-trip** (`pipeline.py:489-491`), so the rails screen
the exact string given.

`_screen_input` (`pipeline.py:338`) is the chain, and it returns
`(primary, advisories)` — the primary is never a FLAG; advisories collect non-blocking
flags so they can stream without stopping the request:

| Order | Line | Layer | On failure |
|---|---|---|---|
| 1 | `pipeline.py:350` | `schema.validate_input_format` | BLOCK, `layer="schema"` |
| 2 | `pipeline.py:358` | `pii.redact` | **Rewrites the text** for everything downstream |
| 3 | `pipeline.py:359` | `_detect_injection_cached` | BLOCK, `layer="injection"` |
| 4 | `pipeline.py:370` | `screen_content` | BLOCK, `layer="content_safety"` |
| 5 | `pipeline.py:383` | `_screen_topical` | BLOCK if `topical_block`, else FLAG into advisories |
| 6 | `pipeline.py:388` | custom input rails | First non-PASS short-circuits |
| 7 | `pipeline.py:391` | PII kinds found? | REDACT with the kinds list |
| — | `pipeline.py:402` | none fired | PASS |

**Line 358 is the LLM06 control.** Everything after it — three model calls and every custom
rail — sees the redacted text, not the user's PII.

At `pipeline.py:493-494`, if the primary is PASS but an advisory fired, the advisory is
returned as the result, so the single-result path (the agent graph) still shows it. A BLOCK
always takes precedence.

### `check_output`

**`pipeline.py:523`.** The chain is deliberately different:

| Order | Line | Layer |
|---|---|---|
| 1 | `pipeline.py:548` | `schema.validate_output_format` |
| 2 | `pipeline.py:553` | `schema.content_filter` (the denylist) |
| 3 | `pipeline.py:558` | `screen_content` |
| 4 | `pipeline.py:566` | custom output rails |
| 5 | `pipeline.py:569` | `_screen_grounding` — BLOCK only if `grounding_block` |
| 6 | `pipeline.py:572` | `pii.redact` → REDACT |
| 7 | `pipeline.py:581` | grounding FLAG surfaced on the clean path |

PII is **last** here, because there are no downstream model calls to protect and the
redaction is the final transform.

### The injection cache

`_detect_injection_cached` (`pipeline.py:219`) preserves the layer order exactly: the
deterministic signatures run first and a hit returns immediately, **never cached around**
(`pipeline.py:239-241`) — caching a free, deterministic decision buys nothing. Only the
model verdict is cached.

Key: `"inj:" + sha256(text)` (`_injection_cache_key`, `pipeline.py:259-266`). Keyed on the
text alone, so there is no tenant or persona to leak and no cross-tenant reuse risk — the
docstring says so explicitly.

`_cache_get` / `_cache_set` (`pipeline.py:268-285`) both **fail open as a miss**, logged.
A broken cache means recompute, which runs the real control. `_parse_cached_verdict`
(`pipeline.py:287`) returns `None` on corruption → recompute.

Backend selection: `_default_injection_cache` (`pipeline.py:51`) uses Redis in `full` mode
with a URL, in-memory otherwise — and a full-mode Redis that cannot be built **degrades to
in-memory with a warning rather than raising** (`pipeline.py:67-72`), because the cache is
an optimisation and must never be the thing that stops a `Guardrails` from constructing.
Note this is a *deliberate exception* to fail-closed, justified by the cache not being a
control.

`_emit_injection_cache` (`pipeline.py:295`) emits `guardrail_cache` carrying only the event
and the boolean verdict — never the text, redacted or otherwise.

### Streaming

`stream_check_input` (`pipeline.py:589`) yields `StepStarted` → primary `GuardrailEvent` →
one per advisory → `StepFinished`. An advisory FLAG **never sets `ok=False`**
(`pipeline.py:638`) — only a BLOCK stops the request.

`stream_check_input_agui` (`pipeline.py:641`) emits the richer AG-UI payload including
`redaction_spans` — character offsets from `pii.scan` — but only for `str` input
(`pipeline.py:665-670`), because a binary payload has no character offsets.

---

## The rail contract, and how it widened

**`aegis/src/aegis/guardrails/pipeline.py:77-105`** defines three aliases:

```python
Rail = Callable[[MediaPayload], GuardResult | None | Awaitable[...]]
LegacyTextRail = Callable[[str], ...]      # deprecated but supported
AnyRail = Rail | LegacyTextRail            # what the pipeline accepts
```

The widening was not a breaking change, and **`aegis/src/aegis/guardrails/media/adapt.py`**
is the reason.

`call_rail(rail, payload, *, on_skip)` (`adapt.py:117`) inspects what the rail was written
to accept:

- `is_media_rail(rail)` (`adapt.py:97`) → hand it the payload.
- Otherwise, a `TextPayload` → hand it `payload.text`, byte-for-byte the old behaviour.
- Otherwise (a string rail facing an image or audio) → **skip it**, log, and call `on_skip`
  with a human-readable reason (`adapt.py:141-149`).

Detection: the `@media_rail` decorator marker (`adapt.py:55`, attribute `MEDIA_RAIL_ATTR`)
or a first-parameter annotation containing one of `MediaPayload` / `TextPayload` /
`ImagePayload` / `AudioPayload` (`_PAYLOAD_ANNOTATIONS`, `adapt.py:46-48`). Annotations are
compared **as strings** (`adapt.py:43-45`) because `from __future__ import annotations`
makes every annotation a string at runtime and resolving them would import the caller's
namespace.

`_first_annotation` (`adapt.py:72`) handles plain functions, methods, and callable objects
via `__call__`, and returns `None` for C-level callables with no signature — which is what
`@media_rail` exists for.

**The skip is recorded, not swallowed.** `_run_custom` (`pipeline.py:199`) takes a
`skipped` list, and `MediaGuardResult.rails_skipped` carries it into the verdict. A rail
that did not run is never counted among the rails that did.

---

## The media chain

**`aegis/src/aegis/guardrails/media/screen.py`.** `MediaScreen` (`screen.py:67`) mirrors the
text pipeline layer for layer — the mapping is a table in the module docstring
(`screen.py:5-13`).

Layer labels: `HYGIENE_LAYER` / `INJECTION_LAYER` / `PII_LAYER` (`screen.py:54-56`).

`check(payload, *, text_check, custom, skipped_custom)` (`screen.py:119`) dispatches by
type and **raises `ValueError` on a text payload** (`screen.py:144-147`) — routing text here
would silently skip the text rails.

### Images — `_check_image` (`screen.py:218`)

```
hygiene → image-PII redaction → injection screen → custom rails
```

`_hygiene` (`screen.py:156`) calls `aegis.media.inspect_payload` — size cap, MIME truth
(magic bytes vs the declared type), decompression-bomb guard. A failure blocks and lists
*"every downstream media rail"* as skipped (`screen.py:167-169`).

`_run_image_pii` (`screen.py:198`) runs `presidio-image-redactor` when enabled and returns
the **redacted payload** to screen from here on. PII-before-classifier carries over
verbatim from the text pipeline: sending an unredacted image to a screening model is itself
an LLM06 disclosure (`screen.py:15-18`).

`screen_image` (`media/injection.py:172`) is the injection screen, and it is the cleanest
fail-closed in the codebase:

- Payload is a bare URI, not inline bytes → **blocked** (`injection.py:189-195`), with the
  reason that *"what a model would fetch later is not what was screened"*.
- No vision completer → **blocked** with `screened=False` (`injection.py:196-206`).
- Otherwise `classify_image` (`injection.py:147`), which fails closed on any completer
  error and parses via `parse_bool_field` (`injection.py:134`).

`ImageScreenVerdict` (`injection.py:63-76`) carries `injection`, `contains_text`, `reason`,
and **`screened`** — the flag distinguishing "we looked and found an attack" from "we could
not look". `_injection_block` (`screen.py:286`) uses it to pick the reason prefix
(`screen.py:296-300`), so the verdict never collapses those two cases.

`IMAGE_SCREEN_SYSTEM_PROMPT` (`injection.py:45-58`) asks **two** questions, not one: does
the image carry text, and is that text an instruction *directed at an AI system*. The split
matters — a receipt has text and is not an attack. It also names the hiding tricks
explicitly ("faint, low-contrast, very small, rotated, or watermark-style").

`vision_messages` (`injection.py:96`) builds the OpenAI multimodal content-block shape — the
image is screened in the exact form the model will see it.

### Audio — `_check_audio` (`screen.py:172`)

Hygiene, then `guard_audio` (`media/audio.py:62`): transcribe and run the **whole text rail
stack** over the transcript. There is no parallel audio chain, so every rail an operator
configured — including their custom ones — applies to speech unchanged
(`screen.py:20-22`).

No transcriber wired ⇒ audio blocked, and the skip list says *"transcription + the entire
text rail stack (no transcriber wired)"* (`screen.py:185`).

### Honest coverage

`_NO_IMAGE_SAFETY` (`screen.py:60-62`) is seeded into `rails_skipped` on **every** image
verdict (`screen.py:231`): there is no content-safety or topical screen over raw pixels in
this release. `MediaGuardResult` (`media/types.py:28`) extends `GuardResult` with
`rails_run`, `rails_skipped`, `media` (a rewritten payload when a rail produced one), and a
`coverage()` summary that is appended to the reason (`screen.py:283, 311`).

The design rule: *"the verdict's `rails_skipped` says so rather than letting a reader assume
coverage"* (`screen.py:25-28`).

---

## The NeMo / Colang engine

**`aegis/src/aegis/guardrails/nemo.py`** and **`aegis/src/aegis/guardrails/config/`**.

The policy directory holds `config.yml`, `prompts.yml`, and `rails/input.co` +
`rails/output.co`. The input policy reads as a numbered layer list matching the
programmatic pipeline exactly — schema, PII, injection, content safety, topical, then the
media flows.

`config/actions.py` holds `@action(is_system_action=True)` functions that delegate straight
back to `schema`, `pii`, `classifier`, `content_safety`, `topical`, `grounding` — *"keeping
the logic in one place means the declarative Colang policy and the fast programmatic API
can never drift apart"* (`actions.py:6-8`). The completer is read lazily per call from
`nemo.get_completer()` (`actions.py:30-39`).

`nemoguardrails` is imported lazily via `aegis.core.lazy.require` everywhere, so importing
`aegis.guardrails.nemo` — and running the unit tests — never requires the package.

### Wiring seams

| Setter | Line | Wires |
|---|---|---|
| `set_completer` | `nemo.py:51` | The text rails' model layer |
| `set_allowed_topics` | `nemo.py:78` | The topical rail's domain |
| `set_vision_completer` | `nemo.py:109` | The image screen |
| `set_transcriber` | `nemo.py:125` | The audio rail |

Each has a matching getter and each defaults to `None` — the offline, deterministic-only
posture.

### `build_rails` and the model

**`nemo.py:188`.** The critical lines:

```python
# nemo.py:216-220
if llm is None and _completer is not None:
    from aegis.guardrails._nemo_llm import chat_model_from_completer
    llm = chat_model_from_completer(_completer)
return LLMRails(load_rails_config(), llm=llm)
```

The engine's `main` model is the **host's** completer, adapted to LangChain's interface.
See `30-deep-dive.md` for what happened before this existed.

`get_engine` (`nemo.py:271`) caches the engine **and the completer it was built from**
(`_engine_completer`, `nemo.py:154`), rebuilding when they diverge (`nemo.py:285-288`) — so a
host that wires its gateway after first use never keeps screening through a stale model.

### The block signal

`_stopped_rails(response)` (`nemo.py:339`) reads
`GenerationLog.activated_rails[*].stop` — the engine's own structured record of having
halted the turn. If the response carries no log it raises `_NoRailLog` (`nemo.py:335`), and
every caller turns that into a **fail-closed BLOCK**
(`nemo.py:418-425`, `470-480`, `536-545`): with no log there is no evidence the rails ran,
and "no evidence" is never a pass.

`_options` (`nemo.py:309`) requests `activated_rails=True` on **every** call — never
optional, because it is what makes the block signal drift-proof.

The refusal strings (`nemo.py:146-147`) are retained **only** to detect drift.
`_warn_on_refusal_drift` (`nemo.py:375`) logs when the generated refusal and the stop signal
disagree — in either direction — without changing the verdict.

### The action table

`_action_table()` (`nemo.py:230`) is the single source for all eleven actions.
`registered_action_names()` (`nemo.py:249`) exposes the set *"so a test can assert this set
covers every `execute` in the `.co` files — the mechanical check that stops the two from
drifting again"* (`nemo.py:251-254`).

### Message shape matters

`nemo_check_output` (`nemo.py:450`) presents the text as an **assistant** message preceded
by a placeholder user turn (`nemo.py:459-470`). Passing it as a `user` message runs only the
variable-assignment flows (PII redaction) and silently skips the schema and content-safety
checks.

`nemo_check_media_input` (`nemo.py:513`) sends the payload as a `{"role": "context"}`
message (`MEDIA_CONTEXT_KEY`, `nemo.py:510`) — the only sanctioned way to hand a Colang flow
structured data that is not the message text.

---

## The backend composition root

**`backend/src/app/guardrails/__init__.py`.**

`_gateway_completer` (`__init__.py:49`) adapts `app.core.llm.complete` to the
`ChatCompleter` protocol — `ModelRole.CHEAP`, `temperature=0.0`. It is *"the **only** place
under `app.guardrails` that references the platform's LLM gateway"* (`__init__.py:53-55`).
Imports are deferred so importing the package never requires the gateway.

The process-wide pipeline (`__init__.py:82-86`):

```python
_guard = Guardrails(
    completer=_gateway_completer,
    ground_answers=True,
    grounding_block=get_settings().grounding_block,
)
```

`_use_nemo_engine()` (`__init__.py:89`) returns `True` only when
`settings.guardrails_engine == "nemo"` **and** `nemoguardrails` is importable; an
unavailable package logs and falls back to the programmatic pipeline, so the live path never
loses its rails. It also calls `nemo.set_completer(_gateway_completer)` on the way through —
idempotent, safe every request.

`check_input` (`__init__.py:123`) / `check_output` (`__init__.py:148`) route to the selected
engine, and a NeMo engine error goes through `_fail_closed` (`__init__.py:112`) → BLOCK,
never a silent pass.

### How the agent graph calls it

**`aegis/src/aegis/agent/graph.py`**:

- `guard_input` (`graph.py:373`) — runs `deps.check_input`, stamps the GUARDRAIL span
  (`_stamp_guardrail`, `graph.py:1337`), emits the `guardrail` event, and on BLOCK returns
  `{"blocked": True, "status": "blocked", "answer": result.reason}`.
- A blocked input short-circuits **straight to END** (`graph.py:1174-1178`) — the router
  never runs, nothing downstream executes.
- `guard_output` (`graph.py:1013`) — runs `deps.check_output(answer, contexts=[context])`
  against the **same** retrieved context the answer was generated from (`graph.py:1024-1030`),
  not a re-fetch.
- `_guard_detail` (`graph.py:1351`) maps the result into the event, surfacing **only masked
  text** — `before_masked` and `after` are populated only on a `redact` verdict and both
  carry the masked form. Raw PII never reaches the wire.

**The ordering property.** `generate → guard_output → stream` (`graph.py:1221-1222`). The
`stream` node paces an *already-guarded* string (`graph.py:1073`); the gateway call is
non-streaming on purpose. Streaming raw tokens would put unguarded text on screen and make
a block unenforceable after the fact — *"you cannot unsay a leaked secret"*
(`graph.py:1085-1087`).

---

## The red-team battery

**`aegis/src/aegis/redteam/`** — `battery.py` (the attack cases), `runner.py` (execution),
`__main__.py` (CLI). Cases carry an id and the layer expected to catch them, so a case
that *should* be caught deterministically but is only caught by the model layer registers
as a regression.

**Next:** [`30-deep-dive.md`](30-deep-dive.md) — the failure modes and the bugs.
