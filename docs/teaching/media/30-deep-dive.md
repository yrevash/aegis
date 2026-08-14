# Media — deep dive

Failure modes first, then the real bugs and the gaps that are still open. Nothing in
this file is hypothetical unless it says so.

---

## The bug this whole module is

Most modules exist to add a capability. `aegis.media` exists because of a **finding**.

The pre-media state of the codebase, stated in the module's own docstring at
`aegis/src/aegis/media/types.py:9-14`:

> Before it, every guardrail entry point was `str` typed (`Rail = Callable[[str], ...]`)
> and the agent guarded `state["query"]`, a string. An image sent as an OpenAI
> multimodal content block was forwarded to the model verbatim, having passed through
> **no rail at all** — and text rendered inside an image is the standard
> prompt-injection vector against vision models.

Sit with the shape of that. Nothing was broken. No test failed. The guardrail pipeline
ran on every request and returned `PASS`, correctly, because the string it was handed
contained nothing objectionable. The image travelled beside that string, through a
different field, and reached the model unexamined.

**The rails were not bypassed. They were never given the thing.**

That is the class of bug worth learning: a control that is *structurally unable* to see
the input is indistinguishable, from the outside, from a control that saw the input and
approved it. Both emit green.

The fix is a type change, which is why the module leads with a type: `Rail` widens from
`str` to `MediaPayload`, and now a rail can at least *receive* an image. Whether it
judges it well is a second problem; whether it can see it at all was the first.

---

## Failure mode 1 — the declared type as a routing weapon

The obvious attack on a MIME check is "declare a PNG as a JPEG." That is a nuisance.

The real attack is one layer up: **declare a binary as `text/plain`** so the *router*
sends it down the text path. The text rails then receive a `TextPayload` whose bytes
are a PNG, decode them (or fail to), find nothing objectionable, and pass.

`_mime_failures` closes this at `aegis/src/aegis/media/hygiene.py:138-150`. Text gets a
**family** comparison — `sniffed.startswith("text/")` — and the comment explains why
the check is family-level rather than exact:

> Magic bytes cannot tell `text/plain` from `text/markdown` or `application/json`, so
> asserting an exact match here would be a lie. The family is the honest claim: bytes
> that do not decode as clean text are not text, whatever the declaration says (that
> check already happened — `sniffed` would be None).

That parenthetical is the interesting half. `sniff_mime` returns `None` for bytes that
neither match a container signature nor decode as clean UTF-8
(`aegis/src/aegis/media/sniff.py:117-120`), and `None` is handled first at `:131-137`
with an immediate return. So a PNG declared as text hits `MIME_UNKNOWN`… no, it hits
something better: PNG *does* match a signature, so it sniffs as `image/png`, the
`startswith("text/")` test fails, and it is `MIME_MISMATCH` with the detail *"text
payload carries 'image/png' bytes"*.

Both paths refuse. Neither guesses.

---

## Failure mode 2 — the payload nobody can inspect

`inspect_payload` has an asymmetry that looks inconsistent until you read the argument
(`aegis/src/aegis/media/hygiene.py:235-250`):

```python
if not payload.inline:
    if payload.kind is MediaKind.TEXT:
        return HygieneReport(ok=True, ...)      # text by URI: allowed
    return HygieneReport(ok=False, ...)          # image/audio by URI: refused
```

Why is text allowed and an image not?

Because the consequence differs. A URI-only *text* payload is a reference something
upstream will resolve into a string, and that string will then go through the full
text stack. A URI-only *image* is bytes that this process never holds — so it cannot be
sniffed, cannot be sized, cannot be bomb-checked, and the injection screen cannot look
at it either.

And worse: even if you fetched it to screen it, **the model would fetch it again**.
There is no guarantee the server returns the same bytes twice. `screen_image` states
exactly this at `aegis/src/aegis/guardrails/media/injection.py:189-195`:

> Image is a bare URI whose bytes this process never held; it cannot be screened, and
> what a model would fetch later is not what was screened. Blocked.

This is a **time-of-check to time-of-use** (TOCTOU) problem, and it is not solvable by
fetching harder. The only sound answer is to require the bytes.

---

## Failure mode 3 — the verdict that overstates its coverage

This one has a documented history. From
`aegis/src/aegis/guardrails/media/types.py:11-14`:

> The previous audit found `completer=None` silently disabling two rails while the
> verdict text still claimed all four had run.

Read that as a general hazard rather than one bug. Any pipeline with optional stages
will, by default, produce a verdict string written at design time — *"Input passed
schema, PII, injection, and content-safety rails"* — that describes the **intended**
chain, not the chain that ran. Disable a stage by leaving out a dependency and the
sentence keeps claiming it.

The structural fix is that the sentence must be **generated from the record of what
executed**. `MediaGuardResult` carries `rails_run` and `rails_skipped`
(`:39-48`), and `coverage()` (`:50-56`) builds the line from them. A rail that did not
run *cannot* appear in the sentence, because the sentence is a join over a list it is
not in.

You can see the discipline applied at every exit:

- Hygiene block: `rails_skipped=["every downstream media rail (hygiene refused the
  payload first)"]` (`screen.py:167-169`).
- Every image path starts with `skipped = [_NO_IMAGE_SAFETY]` (`screen.py:231`) — the
  *deliberate gap*, declared on every single image verdict rather than assumed away.
- Audio with no transcriber: `"transcription + the entire text rail stack (no
  transcriber wired)"` (`screen.py:185`).
- A skipped legacy rail: the exact reason string from `call_rail`
  (`adapt.py:142-146`), collected through `on_skip` and extended into `skipped` at
  `screen.py:247-248`.

The general rule: **make the honest statement a consequence of the data structure, not
a thing a developer has to remember to update.**

---

## Failure mode 4 — the legacy rail with three bad options

Widening `Rail` from `str` to `MediaPayload` is a breaking change to a public callback
type. Aegis ships it as non-breaking, and `adapt.py` is the entire reason.

The design question is narrow: an operator wrote `def no_medical(text: str)` last
month, and today an image arrives. What happens?

| Option | Outcome |
|---|---|
| Crash | Hostile. Punishes the operator for our change. |
| Pass `str(payload)` or base64 | The rail returns "fine" every time. A rail that reports coverage it does not have. |
| **Skip it and record the skip** | The only honest one. |

`call_rail` (`adapt.py:117-150`) implements the third, and the skip message is written
for the operator, not for a log parser:

> custom text rail 'no_medical' skipped: it takes a str and cannot judge a image
> payload (annotate it with MediaPayload or apply @media_rail to have it screen media)

Two implementation details are worth stealing.

**Annotations are compared as strings** (`adapt.py:44-48`, `:91-93`). Because
`from __future__ import annotations` is on, every annotation is *already* a string at
runtime. Resolving it to a real type would mean importing the caller's module
namespace — expensive, and capable of executing arbitrary import side-effects during a
guardrail check. The substring test (`:114`) tolerates `MediaPayload | None`,
`aegis.media.ImagePayload` and bare `ImagePayload` with no resolution at all.

**Some callables have no signature at all** (`adapt.py:80-82`). C-level callables and
some builtins raise on `inspect.signature`. That is caught and treated as "no
annotation" → legacy rail, which is the safe default. This is why `@media_rail` exists:
it is the escape hatch for a `functools.partial` or a callable class whose annotation
is not introspectable.

---

## Failure mode 5 — the bomb that never detonates

The decompression bomb is a nice illustration of a general principle: **do the cheap
check on the metadata before you do the expensive thing the metadata describes.**

`_bomb_failures` (`hygiene.py:180-215`) never decodes a pixel. It reads width and
height from the header (`image_dimensions`, called once at `hygiene.py:260`) and does
two multiplications.

Three details that are easy to get wrong:

**The unreadable case is a block, not a skip** (`:186-193`). If dimensions cannot be
read — truncated header, a format outside `DIMENSIONABLE_IMAGE_MIMES` — then the guard
*cannot run*, and an image that cannot be bomb-checked is refused. This is the same
rule as everywhere else in the module: a control that cannot run fails closed.

Note how the allowlist and the dimensionable set are wired together:
`MediaLimits.allowed_image_mimes` defaults to `frozenset(DIMENSIONABLE_IMAGE_MIMES)`
(`hygiene.py:67`). So the set of images Aegis accepts is *exactly* the set it can
bomb-check. TIFF sniffs correctly (`sniff.py:35-36`) but is not dimensionable and is
therefore not accepted — the two facts are consistent by construction rather than by
coincidence.

**Both thresholds, independently.** `max_pixels` catches the enormous file;
`max_pixels_per_byte` catches the small absurd one. They are separate `if`s (`:197`
and `:206`), both can fire, and both land in the failure list — which is why
`HygieneReport.failures` is a list rather than an `Optional[first_failure]`
(`hygiene.py:89-90`).

**`size > 0` guards the division** (`:206`). `payload.byte_size or 0` would be `0` for
a `None` size, and `pixels / 0` raises. An unknown size cannot be ratio-checked, so it
is not.

---

## Failure mode 6 — mutating the evidence

Every payload class is `frozen=True` (`types.py:106`), and the docstring at `:99-104`
gives the reason in one line: *a payload is evidence.*

Consider the alternative. The image-PII rail finds a passport number and mutates
`payload.data` in place. Now:

- the injection screen's verdict refers to bytes that no longer exist;
- an audit asking "what exactly did the screen look at?" cannot be answered;
- and if the redaction is buggy, you have destroyed the original with no way to
  reproduce the finding.

So `redact_image` constructs a **new** `ImagePayload`
(`image_pii.py:146-150`) and the chain threads it forward as `current`
(`screen.py:216`, `:232`), while `original` stays available for the verdict's
`describe()` (`screen.py:305`). "What was screened is what was screened" is preserved
by the type system rather than by convention.

---

## Concurrency and reentrancy

There is very little shared state here, which is deliberate.

`sniff_mime`, `image_dimensions` and `inspect_payload` are **pure functions over
bytes** — no globals, no caches, no I/O. They are trivially safe to call concurrently
and trivially safe to unit-test.

`MediaScreen` holds only configuration (`screen.py:100-105`) — a completer, limits,
flags, a transcriber. It builds no per-call state on `self`, so one instance serves
concurrent requests. `Guardrails` constructs exactly one
(`pipeline.py:189-196`) and shares it.

The one shared-mutable object in the flow is the `skipped: list[str]` collector created
per call in `_screen_media` (`pipeline.py:419`) and closed over by the `_custom` inner
function (`:421-422`). It is per-call, so no cross-request leakage — but note it *is*
mutated by `call_rail`'s `on_skip` during the custom-rail loop and then read at
`screen.py:247-248`. If custom rails were ever run concurrently rather than in order,
that list would need synchronising. Today `_run_custom` (`pipeline.py:210-219`) runs
them strictly in sequence and short-circuits on the first non-PASS.

**One recursion worth checking.** `_screen_media` passes `text_check=self.check_input`
(`pipeline.py:422`). For audio, `guard_audio` calls that with the transcript
(`audio.py:105`). Could that loop? No: the transcript is a `str`, so `as_payload` wraps
it as a `TextPayload` (`pipeline.py:486`) and the kind check routes it to the *text*
branch. The media chain is entered exactly once.

---

## Ordering, and the deliberate divergence

`MediaScreen._check_image` redacts PII **before** it screens (`screen.py:232`, then
`:234`). `aegis.vision`'s pipeline screens **before** it redacts
(`vision/pipeline.py:191`, then `:218`).

That is not an inconsistency anyone forgot to fix. Both docstrings state the argument:

- `screen.py:15-18` — sending an unredacted image to a *screening* model is itself a
  sensitive-information disclosure (OWASP LLM06), exactly as it was for text.
- `vision/pipeline.py:26-33` — on the vision path the image is going to the fleet's
  vision deployment either way, so redacting first buys **no privacy**, while screening
  first refuses a hostile image before the OCR stack is ever started on it.

The transferable lesson: an ordering rule is downstream of an *argument*. When the
argument's premise changes, the correct ordering changes with it. Copying the rule
without the premise is how you end up with a control that costs money and buys nothing.

`vision/pipeline.py:32-33` says as much: *"The trade is stated here rather than left for
a reader to discover."*

---

## Open gaps — verified, and not papered over

Three things are true of this module today and worth knowing before you claim it in an
interview.

**1. `aegis[media]` is not a declared extra.** `image_pii.py:64`, `:69`, `:74` and
`vision/pii.py:106` all call `require("aegis[media]", ...)`, and every docstring
promises an `ImportError` "naming the install command". But `aegis/pyproject.toml` has
no `media` entry under `[project.optional-dependencies]` — the extras are `redis`,
`nemo`, `pii`, `postgres`, `retrieval`, `gateway`, `data`, `governance`,
`observability`, `phoenix`, `agent`, `ml`, `forecast`, `all`, `dev`. So the error
message is accurate about *why* and points at a target that would not resolve.

The failure is still loud and still fails closed — which is the security-relevant half
— but the remediation instruction is currently wrong. This is exactly the kind of thing
worth saying out loud rather than being caught on.

**2. There is no dedicated test module for `aegis.media`.** `aegis/tests/` has folders
for core, data, forecast, guardrails, memory, ml, vision, voice and the rest — none for
`media`. The sniffing, hygiene and adapter code is exercised **indirectly**, chiefly
through `aegis/tests/voice/test_security.py:102-114`, which builds a payload declaring
`audio/wav` whose bytes are a PNG signature and asserts the transcriber was never
called:

```python
lying = AudioPayload(data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, mime_type="audio/wav")
...
assert fake.calls == [], "hygiene must refuse before the paid call"
```

That is a good test of the *seam*. It is not a test of `_jpeg_dimensions`' marker
walk, of the WEBP `+1` arithmetic, or of `is_media_rail` against a `functools.partial`
— which are precisely the fiddly, high-traffic parsers where an off-by-one is easiest
to introduce and hardest to notice.

**3. Image content-safety over pixels is out of scope**, declared on every image verdict
via `_NO_IMAGE_SAFETY` (`screen.py:60-62`). The screen answers "is this image talking
to the model?" and nothing else. Unsafe *imagery* — a photograph of something the
policy forbids — is not screened, because it would need a second vision call per image.
The system says so rather than letting a green verdict imply coverage it does not have.

---

## What you should now be able to tell as a story

- **The founding bug**: an image reaching the model having passed through no rail,
  with the whole pipeline reporting green — and why a type change was the fix
- **Why `if not payload.inline` is asymmetric** between text and images, and the
  TOCTOU argument behind it
- **The coverage-overstatement bug** and how generating the sentence from a list makes
  it structurally impossible to repeat
- **The three options for a legacy rail** and why "skip and say so" is the only honest
  one
- **Why the accepted image set is exactly the bomb-checkable set**, and why TIFF is
  therefore refused despite sniffing correctly
- **Why frozen payloads make the audit statement true** rather than aspirational
- **The deliberate PII/screen ordering divergence** and the premise that flips it
- **The three open gaps**, stated before someone else finds them

**Next:** [`40-diagrams.md`](40-diagrams.md).
