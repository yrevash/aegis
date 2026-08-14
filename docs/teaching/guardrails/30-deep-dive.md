# Guardrails — the deep dive

Failure modes first, then the bugs. Every bug here was found by an adversarial audit of
this codebase, reproduced against the real detector before anything was changed, and fixed
with a regression test that failed on the pre-fix code. The commits are `cba2084`,
`c5db31b` and `7d3c436`.

Lead with these in an interview. "We ran guardrails" is a feature claim. "Here are six
ways our guardrails were bypassed and what we did" is evidence you built them.

---

## Part 1 — The properties

### The fail matrix

Every rail has a documented fail direction. This table is the whole safety posture:

| Rail | Cannot run | Unparseable verdict | Why |
|---|---|---|---|
| Schema | n/a — pure | n/a | No I/O, cannot fail |
| PII | Falls back to regex engine, **logged** | n/a | Never silently stops redacting |
| Injection (deterministic) | n/a — pure | n/a | The offline backstop |
| Injection (model) | **BLOCK** | **BLOCK** | `classifier.py:358-362`, `classifier.py:77-80` |
| Content safety (model) | **BLOCK** | **BLOCK** | `content_safety.py:181-185`, `:135-137` |
| Topical | Direction follows `block` | Direction follows `block` | Advisory by default |
| Grounding | Direction follows `block` | Direction follows `block` | No deterministic backstop exists |
| Image injection | **BLOCK** (`screened=False`) | **BLOCK** | No offline backstop for pixels |
| Audio | **BLOCK** (no transcriber) | n/a | An unguardable payload is not a safe one |
| NeMo engine error | **BLOCK** | n/a | `backend/src/app/guardrails/__init__.py:112-120` |
| NeMo missing rail log | **BLOCK** | n/a | `nemo.py:418-425` — no evidence is not a pass |
| Injection **cache** | **MISS** (fail open) | MISS | The cache is not a control; a miss runs the real one |

The two deliberate exceptions are worth being able to defend:

**The cache fails open** because a cache read that errors means "recompute", which runs the
real control. Failing closed on a Redis blip would block every request in the system for a
component that makes no safety decision.

**Advisory rails fail open when configured advisory.** A downed grounding checker that
manufactures "this may be a hallucination" on every single answer is its own kind of lie.
The fail direction follows the enforcement posture, and the posture is a config flag.

There is a third, smaller one: a full-mode Redis that cannot be built for the *injection
cache* degrades to in-memory with a warning rather than raising
(`pipeline.py:67-72`), because the cache must never be the thing that stops a `Guardrails`
object from constructing. Note this is different from the *memory* semantic cache, which
does raise in full mode — different component, different criticality.

### Determinism and the offline path

Every rail except grounding has a pure, offline layer. That is not an aesthetic choice:

- **The unit tests run with no network and no API key.** The whole rail stack is exercised
  offline with a fake completer.
- **A model outage degrades rather than disables.** Signatures still run.
- **The deterministic layer cannot be talked out of its opinion.** A model classifier can be
  socially engineered; a regex cannot.

The cost is that the deterministic layer's coverage is limited, which is why it never runs
*alone by choice* (`classifier.py:314-316`) and why `detect_injection` **logs a warning**
when no completer is configured (`classifier.py:388-392`) rather than silently proceeding.

### Ordering as a security property

Two orderings carry real weight and are worth naming:

**PII before the classifier** (`pipeline.py:358-359`). Three model calls follow that line.
Reversing it makes your safety control an LLM06 disclosure.

**`generate → guard_output → stream`** (`graph.py:1221-1222`). The output rail sees the
*complete* answer. The `stream` node paces an already-guarded string. Streaming raw model
tokens would put unguarded text on the user's screen and make a block unenforceable after
the fact. The docstring at `graph.py:1073-1091` states the trade explicitly: a cosmetic
typing effect for a real safety property, and it says what a genuine token-streaming
implementation would require — a streaming-aware output rail with the ability to withhold,
not just a streaming gateway call.

### Isolation

The injection cache is keyed on `sha256(redacted_text)` alone (`pipeline.py:259-266`). No
tenant, no persona. That is safe *because* the verdict is a pure function of the exact
string — there is nothing tenant-specific in the key and therefore nothing to leak. Worth
stating explicitly, because "we cache safety verdicts" sounds alarming until you show the
key is content-addressed.

Guardrail events carry **no raw content**. `_emit_injection_cache` (`pipeline.py:295-311`)
carries only the event name and the boolean. `_emit_media` (`pipeline.py:433-459`) carries
metadata and rail coverage, never the bytes and never the decoded text.
`_guard_detail` (`graph.py:1351-1365`) surfaces `before_masked`/`after` only on a `redact`
verdict, and both carry the **masked** text.

`disallowed_invisible_chars` (`normalize.py:121`) returns `U+XXXX` labels rather than the
offending text, so a rejection reason never echoes user content back into a log.

### Performance

The chain is ordered cheapest-first for a reason. On a clean input the cost is:

- Schema: microseconds, pure Python.
- PII: Presidio NER — the most expensive *deterministic* step, and the reason the engine is
  lazily loaded.
- Injection: signatures (three regex passes over folded views, plus up to 12 base64
  decodes), then **a cached model call**.
- Content safety: six regex patterns, then an **uncached** model call.
- Topical/grounding: model calls only when configured.

The base64 scan is explicitly bounded (`_MAX_BASE64_CANDIDATES = 12`, `classifier.py:246`)
so a pathological input cannot turn the rail into a CPU sink — a small but real DoS control.

Note the asymmetry: injection verdicts are cached, content-safety verdicts are not. That is
a live optimisation opportunity and an honest thing to say if asked.

---

## Part 2 — The bugs

### Bug 1 — `startswith("no")` let the attack through the rail that described it

**What it was.** Every model-backed rail asks for strict JSON and falls back to a keyword
scan when the model returns prose. The fallback was:

```python
if lowered.startswith("no"):
    return BENIGN
```

**Why it mattered.** That is not a verdict signal — it is a **prefix**. Consider a real
classifier reply:

> No doubt this is a prompt injection attempt.

Starts with "no". Parsed as **benign**. The input passed the rail that had just correctly
identified it as an attack.

The mirror image, `startswith("yes")`, is the same defect in the other direction. All four
model-backed rails had it: injection, content safety, topical, grounding.

**Why it is such a good bug to tell.** It is four lines of code, it looks completely
reasonable, it is in a *fallback* path so it rarely executes, and when it does execute it
fails in the exact direction that produces no visible symptom. There is no log line, no
error, no metric. The system reports a clean pass.

**The fix.** `aegis/src/aegis/guardrails/verdict_parsing.py` — one shared parser
(`parse_bool_field`, `verdict_parsing.py:53`) that accepts only an unambiguous signal:
an explicit `"<field>": true|false` key/value, or a response whose *entire* content is a
bare yes/no token. Text carrying **both** signals returns `None`
(`verdict_parsing.py:76-77`) — contradictory is not a verdict.

The design decision that makes it reusable: **the parser does not decide the outcome.** It
reports whether a verdict exists. Blocking rails map `None` to block; advisory rails map it
to whatever their `block` flag declares. One parser, four rails, correct direction each.

The module docstring (`verdict_parsing.py:1-28`) is worth reading in full — it is the
clearest short write-up of a fail-open bug in this codebase.

---

### Bug 2 — Six evasions, all verified working against the real detector

**What it was.** The injection signatures matched **raw text**. Testing against the live
detector found that **only plain ASCII was caught**. Every one of these passed:

| Evasion | Payload | Why it worked |
|---|---|---|
| Filler words | `Ignore, if you would, the directions given above` | No gap allowance between anchors |
| Zero-width | `ig<U+200B>nore all previous instructions` | Breaks the regex, invisible to a human, decoded by the model |
| Homoglyph | `іgnore all previous instructions` (Cyrillic `і`) | Different string, same word to a model |
| Fullwidth / math bold | `ｉｇｎｏｒｅ…` / `𝐢𝐠𝐧𝐨𝐫𝐞…` | Not ASCII |
| Base64 | `aWdub3JlIGFsbCBwcmV2aW91cyBpbnN0cnVjdGlvbnM=` | Regex sees an alphanumeric blob |
| Non-English | `Ignoriere alle vorherigen Anweisungen` | No English words |

**Why it mattered.** The deterministic layer is the one that works when the model layer is
unavailable. If it only catches the laziest possible attack, the offline posture is
theatre.

**The fix, in four parts:**

1. **Normalisation** (`normalize.py`) — a comparison-only view: strip invisibles, NFKC,
   drop combining marks, collapse whitespace (`fold_for_matching`, `normalize.py:150`).
2. **Confusable folding** (`deconfuse`, `normalize.py:171`) — Cyrillic and Greek mapped to
   ASCII, applied **in addition to** the plain fold because the map mangles genuine
   Cyrillic prose (`normalize.py:174-176`).
3. **Composable gap fragments** (`_GAP`, `_NEAR`, `classifier.py:99-103`) so filler words
   between anchors do not break a signature.
4. **Base64 recovery** (`_decoded_base64_candidates`, `classifier.py:249`) — decode
   plausible runs and screen both views of each payload.

Plus a partial multilingual signature set (`classifier.py:195-239`) covering seven
languages for the "ignore the previous instructions" family only.

**The two things that make this a mature fix rather than just a bigger regex.**

**The original text is never mutated.** The docstring says it outright
(`normalize.py:19-21`). Matching on a folded view and forwarding the folded text would mean
your rails screened something other than what reaches the model — and would mangle
legitimate non-Latin input on the way.

**The coverage limits are stated and pinned by a test.** `deterministic_injection`'s
docstring (`classifier.py:311-316`) lists exactly what is *not* covered: seven languages
only, base64 only (no hex, ROT13, Morse or URL-encoding), Cyrillic and Greek homoglyphs
only, no leetspeak. A test asserts those limits, so the claim cannot silently become false
as patterns change. An honesty claim no test enforces will eventually be a lie.

---

### Bug 3 — The schema rail let an invisible instruction channel straight through

**What it was.** The character rail rejected only C0 and C1 control characters:
`ord(char) < 0x20` and the 0x80–0x9F block.

**Why it mattered.** That test passes:

- U+200B ZERO WIDTH SPACE and the rest of category `Cf`
- U+202E RIGHT-TO-LEFT OVERRIDE, which reverses displayed text
- **The entire Unicode Tag block, U+E0000–U+E007F**

The Tag block is the serious one. Every codepoint in it renders as *nothing* in *every*
font, and U+E0020–U+E007F mirror printable ASCII one for one. Frontier models decode them
back to the ASCII they encode.

So a reviewer sees `what is the refund policy?` and the model receives that plus a complete
jailbreak. Not obfuscated — **invisible**. There is no font, no editor, no rendering mode in
which a human sees it.

**Why the obvious fix does not work.** A Unicode general-category check alone still misses
it: most of the Tag block is category `Cn` (unassigned), and you cannot reject `Cn`
wholesale because a Python whose Unicode database predates a newly-assigned emoji would then
reject legitimate text (`normalize.py:62-64`).

**The fix.** `is_disallowed_invisible` (`normalize.py:97-118`) rejects C0 minus tab/newline/CR,
DEL, C1, categories `Cf`/`Co`/`Cs` — **and the Tag block by explicit range**
(`normalize.py:116`). `_disallowed_chars` (`schema.py:75`) checks the text **twice**, as
written and after NFKC (`schema.py:90-96`), so a codepoint that only decomposes into a
disallowed one cannot slip past.

**The cost is documented, not hidden** (`schema.py:26-30`): rejecting all of `Cf` also
rejects U+200D ZERO WIDTH JOINER — how multi-person emoji like 👨‍👩‍👧 are built — and the
Arabic formatting marks U+0600–U+0605 / U+061C. Those inputs are refused. The rejection
reason names the exact codepoint (`schema.py:117-125`) so an operator can see why.

That trade — reject some legitimate emoji to close an invisible-instruction channel — is a
real product decision, and being able to state it as a decision rather than an oversight is
the point.

---

### Bug 4 — The same evasions still worked on the content-safety rail

**What it was.** The normalisation fix in `cba2084` closed the zero-width and homoglyph
evasions on the **injection** rail. `content_safety.deterministic_hazard` still matched raw
text.

**Why it mattered.** The identical bypass worked against the CBRN, child-exploitation and
self-harm signatures. A zero-width space in "how to make a bomb" walked straight past S9.

**The fix.** One line, `content_safety.py:157`:

```python
views = (fold_for_matching(text), deconfuse(text))
```

**Why this bug is worth telling anyway.** It is a *cross-cutting fix applied to one call
site*. The audit that found the injection evasions scoped itself to the injection rail, and
the fix went in there. The second sweep found the same defect one module over. The lesson —
which generalises well past guardrails — is that **when you fix a class of bug, grep for the
class, not the instance.** The fix commit records it under "cross-cutting fixes made outside
the agents' scopes" for exactly that reason.

---

### Bug 5 — NeMo's `LLMRails` built its own model, outside the gateway

**What it was.** `build_rails()` constructed `LLMRails(load_rails_config())` with no `llm`
argument.

**Why it mattered.** With no model supplied, NeMo instantiates whatever is declared in the
`models:` block of `config.yml`. That means:

- **A different provider** from the one the rest of the platform uses.
- **A different API key**, quite possibly one that is not configured.
- **Outside the cost-routing layer** — no `ModelRole.CHEAP` selection.
- **Outside the budget ledger** — the call is not counted, so per-tenant USD caps do not
  see it.
- **Outside tracing** — the span does not appear in the run's trace tree.

In other words the platform's single most-defended claim — "every model call in the system
funnels through one governed gateway" — had a hole in it precisely at the security layer.

The reason it went unnoticed: **rail-only checks never invoke the `main` model.** The
policy is run with dialog and output generation disabled, so on the tested path nothing
called it. Anything that *did* — a dialog rail, an LLM-generated bot message, a NeMo-native
self-check — would have gone somewhere else entirely.

**The fix.** `nemo.py:216-220`:

```python
if llm is None and _completer is not None:
    from aegis.guardrails._nemo_llm import chat_model_from_completer
    llm = chat_model_from_completer(_completer)
return LLMRails(load_rails_config(), llm=llm)
```

The host's completer is adapted to LangChain's chat-model interface and passed as `main`.

**And the second-order fix that makes it stay fixed.** `get_engine()` caches the engine
*and the completer it was built from* (`_engine_completer`, `nemo.py:154`) and rebuilds when
they diverge (`nemo.py:285-288`). Without that, a host wiring its gateway after the first
engine use would keep screening through a stale — or absent — model forever, and nothing
would say so.

---

### Bug 6 — Block detection by string comparison, i.e. a rail that fails open on a typo

**What it was.** `nemo_check_input`/`nemo_check_output` decided a block by comparing the
generated turn against a refusal string hardcoded in `nemo.py` **and** authored in the
`.co` policy file.

**Why it mattered.** Nothing keeps two copies of a string equal. Reword the policy for
clarity. Add a full stop. Let NeMo normalise whitespace. Change the wording for a different
locale. Every one of those turns **every block into a PASS**, silently, permanently, with no
test failing unless someone wrote a test on the exact string.

The docstring puts it best (`nemo.py:346-348`): *"A guardrail whose enforcement depends on
two strings staying character-identical is a guardrail that fails open on a typo."*

**The fix.** `_stopped_rails` (`nemo.py:339`) reads
`GenerationLog.activated_rails[*].stop` — the engine's own **structured** record of having
halted the turn. Structured state, not prose; no amount of editing the refusal wording can
change it. `_options` (`nemo.py:309`) requests `activated_rails=True` on every single call
so the log is never absent by configuration.

**And the fail-closed half.** If the response carries no log, `_stopped_rails` raises
`_NoRailLog` (`nemo.py:335, 368-371`) and every caller turns it into a BLOCK
(`nemo.py:418-425`, `470-480`, `536-545`). With no log there is no evidence the rails ran,
and no evidence is never a pass.

The old strings are retained **only** for drift detection: `_warn_on_refusal_drift`
(`nemo.py:375`) logs when the generated text and the stop signal disagree in either
direction, without touching the verdict.

---

### Bug 7 — The output rail ran on the wrong message role and skipped half its checks

**What it was.** `nemo_check_output` passed the answer to the engine as a `user` message.

**Why it mattered.** Colang output flows attach to the **assistant** turn. Presenting the
text as a user message runs only the variable-assignment flows — PII redaction — and
**silently skips the schema and content-safety checks**. Two of four output rails were dead,
and the verdict was a clean PASS.

**The fix.** `nemo.py:459-470`: the text is presented as an `assistant` message preceded by
a placeholder `user` turn, which is the message shape NeMo's output rails run against. The
comment states the failure mode inline.

---

### Bug 8 — The action registration list drifted from the policy

**What it was.** `register_actions` used a hand-written list of action names. `input.co` and
`output.co` `execute`d `self_check_topic` and `self_check_grounding`, which were not in it.

**Why it mattered.** Anyone constructing `LLMRails` themselves and calling `register_actions`
— the documented path — hit an unregistered-action failure at runtime, on two of the six
rails.

**The fix.** A single `_action_table()` (`nemo.py:230`) that both `register_actions`
(`nemo.py:258`) and `registered_action_names()` (`nemo.py:249`) read, so they cannot fall out
of step. And `registered_action_names` exists specifically so *"a test can assert this set
covers every `execute` in the `.co` files — the mechanical check that stops the two from
drifting again"* (`nemo.py:251-254`).

**The generalisable pattern.** When two artifacts must stay in sync and one of them is not
code (a policy file, a config, a schema), the fix is not "be careful" — it is a test that
parses both and asserts the relationship.

---

### Bug 9 — A multimodal request reached the model having passed through no rail

**What it was.** The `Rail` contract was `Callable[[str], GuardResult | None]`, and
`check_input` accepted a `str`.

**Why it mattered.** An image or an audio clip cannot be a `str`. So a multimodal request
went to the model with **nothing screening it** — and the pipeline reported a clean pass,
because every text rail it knew about ran successfully on the (empty) text. The most
dangerous shape of a security bug: a control that reports success while not running.

**The fix, in three parts:**

1. **Widen the contract.** `Rail` now takes a `MediaPayload` (`pipeline.py:96`).
2. **Do it without breaking anyone.** `call_rail` (`adapt.py:117`) inspects what each rail
   was written to accept — via the `@media_rail` marker or a first-parameter annotation —
   and hands it exactly that. A legacy string rail facing a `TextPayload` still receives
   `payload.text`, byte for byte.
3. **Record what did not run.** A legacy string rail cannot judge an image. It is **skipped**
   — not crashed, not handed a stringified blob — and the reason is appended to
   `rails_skipped` in the verdict (`adapt.py:141-149`, `pipeline.py:199-217`).

Part 3 is the one that matters most. *"A rail that did not run is never counted among the
rails that did"* (`adapt.py:22-23`). The verdict tells the truth about its own coverage.

The same honesty runs through the whole media chain: `_NO_IMAGE_SAFETY` (`screen.py:60`) is
seeded into `rails_skipped` on **every** image verdict, because there is no content-safety
or topical screen over raw pixels in this release. Rather than letting a reader assume
coverage, the verdict says what was not checked.

---

### Bug 10 — The image screen would have failed open, and does not

Not a bug that shipped — a bug that was designed out, and it makes the cleanest statement of
the principle.

The text classifier degrades gracefully with no completer: the deterministic signatures
still run. The natural instinct is to give the image screen the same treatment.

**There is no deterministic backstop for pixels.** No regex reads an image. So "no vision
completer" is not degraded coverage — it is *no control at all*.

`screen_image` (`media/injection.py:172`) therefore **blocks**, and the verdict says exactly
why: *"No vision completer configured, so the image-injection screen could not run. An
unscreened image is an unguarded path to the model; blocked (fail-closed)"*
(`injection.py:203-205`).

Two refinements worth naming:

**A bare URI is also blocked** (`injection.py:189-195`), even with a completer wired,
because *"what a model would fetch later is not what was screened"*. Time-of-check to
time-of-use, applied to image URLs.

**`screened=False` is a distinct field** (`injection.py:71-75`). The verdict distinguishes
"we looked and found an attack" from "we could not look", and `_injection_block`
(`screen.py:296-300`) picks a different reason prefix for each. Collapsing those two into a
single "blocked" loses what an operator needs; collapsing the second into "passed" is how a
fail-open ships. The related commit note about `aegis.vision` says the same thing about the
console: it shows **three** verdict states — cleared / blocked / could-not-screen — never
two.

---

## Part 3 — Things worth noticing that are not bugs

**The dual-view matching costs double and is still trivially cheap.** `deterministic_injection`
runs every pattern over `fold_for_matching(text)` *and* `deconfuse(text)`, plus both views of
each base64 payload. That is deliberate: the confusable map rewrites genuine Cyrillic prose
into Latin gibberish, so a rail matching a Russian signature must see the unmapped fold too
(`normalize.py:174-176`).

**Case is preserved through folding** (`normalize.py:157`). Needed so `\bDAN\b`
(`classifier.py:186`) can be case-sensitive and not block anyone named Dan.

**`MediaScreen.check` raises on a text payload** (`screen.py:144-147`) rather than handling
it. Routing text through the media chain would silently skip the text rails — a loud error
is the right answer to a wiring mistake.

**Image PII runs before the injection screen** (`screen.py:225`), mirroring text's
redact-before-classify. Note that `aegis.vision`'s own pipeline deliberately *diverges* —
it screens first, because the image reaches the model fleet either way so redacting first
buys no privacy, while screening first refuses a hostile image before the OCR stack starts.
Both orderings are defensible; what matters is that the divergence is documented rather than
accidental.

**The spotlight instruction is budgeted with its content.** In memory's assembler
(`aegis/src/aegis/memory/working.py:173`) the instruction is part of the episodic tier's
*header*, so it disappears if the tier is evicted. You never ship an instruction about
content that is not there.

**Next:** [`40-diagrams.md`](40-diagrams.md) — every path, drawn.
