# Media — in Aegis

Two packages, deliberately split:

| Package | Holds | Depends on |
|---|---|---|
| `aegis.media` | **Facts about bytes** — types, sniffing, hygiene. No policy. | pydantic + stdlib |
| `aegis.guardrails.media` | **Policy** — the rails that act on those facts. | `aegis.core`, `aegis.media` |

The split is the reason `aegis.media` stays as cheap to import as `aegis.core`: it
pulls no codec, no model client and no network library
(`aegis/src/aegis/media/types.py:25-26`).

---

## How you import it

```python
from aegis.media import (
    ImagePayload, AudioPayload, TextPayload,
    MediaSource, Provenance, MediaLimits,
    inspect_payload, sniff_mime, as_payload,
)

payload = ImagePayload(
    data=raw_bytes,
    mime_type="image/png",
    provenance=Provenance(source=MediaSource.USER_UPLOAD, origin="upload.png"),
)
report = inspect_payload(payload)
if not report.ok:
    raise ValueError(report.summary())
```

That is the standalone shape, documented at `aegis/src/aegis/media/__init__.py:1-22`.
In practice you never call it directly — `Guardrails.check_input` routes any non-text
payload through the chain for you.

The full export surface is `aegis/src/aegis/media/__init__.py:47-66`.

---

## 1. The typed payload union — `aegis/src/aegis/media/types.py`

### The two enums

**`MediaKind`** (`:47-52`) — `TEXT` / `IMAGE` / `AUDIO`. It is the **discriminator** of
the union, which is what lets pydantic parse a serialised payload back into the right
concrete class.

**`MediaSource`** (`:55-70`) — `USER_UPLOAD`, `TOOL_OUTPUT`, `RETRIEVAL`,
`MODEL_OUTPUT`, `UNKNOWN`. The docstring at `:58-64` states the trust argument
explicitly: `RETRIEVAL` and `TOOL_OUTPUT` are the *indirect* injection surfaces
(OWASP LLM01).

### `Provenance` (`:73-95`)

Frozen. Two fields: `source` (the coarse trust class) and `origin` (free text —
a filename, a URL, a tool name). `origin` is documented at `:78-81` as **never parsed
for control flow**; it exists so a human reading a blocked verdict knows what was
blocked.

The load-bearing bit is the `untrusted` property (`:88-95`):

```python
return self.source in {
    MediaSource.RETRIEVAL,
    MediaSource.TOOL_OUTPUT,
    MediaSource.UNKNOWN,
}
```

`UNKNOWN` is in that set. **A payload nobody tagged gets the strict path**, not the
lenient one.

### `_BasePayload` (`:98-180`)

Frozen (`:106`), and the docstring at `:99-104` says why: *a payload is evidence*. A
rail that wants to change one returns a **new** payload, so the original bytes that
were screened remain exactly what was screened.

The fields:

| Field | Line | Point |
|---|---|---|
| `data: bytes \| None` | `:108-113` | The bytes, when the process holds them |
| `uri: str \| None` | `:114-118` | A reference to bytes it does **not** hold |
| `mime_type: str` | `:118-121` | The **declared** type. "Attacker-controlled and never trusted" |
| `declared_byte_size` | `:122-126` | Only meaningful for URI payloads — "bytes cannot lie about their length" |
| `provenance` | `:127` | Defaults to `Provenance()`, i.e. `UNKNOWN` |

Four validators/serialisers do real work:

**`_accept_base64`** (`:129-140`) — a `mode="before"` validator that base64-decodes a
`str` input. The comment at `:134-137` explains why it must exist: pydantic's default
`bytes` coercion would **UTF-8-encode** the string, silently corrupting binary content.

**`_dump_base64`** (`:142-145`) — the JSON serialiser, because raw bytes are not valid
UTF-8. Together these two make a payload round-trip over the wire losslessly.

**`_exactly_one_source`** (`:147-152`) — an `after` model validator enforcing exactly
one of `data`/`uri`:

```python
if (self.data is None) == (self.uri is None):
    raise ValueError("a media payload needs exactly one of `data` or `uri`")
```

The docstring calls the ambiguity "a security hole". A payload with both, or neither,
has no defined screening semantics.

**`byte_size`** (`:154-164`) — a `computed_field`: measured when the bytes are held,
declared when they are not, and `None` when nothing is known. The docstring's last
line is the contract: *"Callers must treat `None` as unbounded, never as zero."*

Then `inline` (`:166-169`) — "whether the bytes are in hand (and therefore
screenable)" — and `describe()` (`:171-180`), a **PII-free** identifier for logs and
verdicts. It never includes the bytes or the decoded text, because verdict strings
travel into traces and the UI.

### The three concrete payloads

**`TextPayload`** (`:183-224`). `mime_type` defaults to `text/plain`. Its
`of()` classmethod (`:193-211`) encodes with `errors="surrogatepass"`, and the comment
at `:204-207` is the reason:

> Anything else would either raise or silently swap in U+FFFD — and a guardrail that
> screens a *different* string from the one it was handed is a guardrail with a bypass
> in it.

The `text` property (`:213-224`) decodes symmetrically, and raises if the payload holds
no inline bytes — this module **never fetches anything**.

**`ImagePayload`** (`:227-231`) — default `image/png`. The docstring calls it "the
payload class that motivated this whole module."

**`AudioPayload`** (`:234-238`) — default `audio/wav`.

### The union and its adapter

```python
MediaPayload = Annotated[
    TextPayload | ImagePayload | AudioPayload,
    Field(discriminator="kind"),
]                                                       # :242-245

MEDIA_PAYLOAD_ADAPTER: TypeAdapter[MediaPayload] = TypeAdapter(MediaPayload)   # :248
```

### The two coercion helpers

**`as_payload`** (`:251-271`) — "the compatibility hinge of the widened rail contract".
A bare `str` becomes a `TextPayload`; a payload passes through; anything else raises
`TypeError`. Every public guardrail entry point routes through this.

**`payload_from_context`** (`:274-294`) — rebuilds a payload from a serialised dict (the
Colang/context wire form). `None`/`""`/`{}` return `None` so a policy flow can treat
"no media this turn" as a no-op — but a dict that **fails validation raises**
(`:287-288`): "a malformed payload is an error, never a silent pass."

---

## 2. Magic-byte sniffing — `aegis/src/aegis/media/sniff.py`

The module docstring (`:1-21`) states the whole thesis, including why header parsing
beats decoding for the bomb guard.

**The signature tables.** `_IMAGE_MAGIC` (`:29-37`) covers PNG, JPEG, GIF (both
versions), BMP and TIFF (both endiannesses). `_AUDIO_MAGIC` (`:40-44`) covers ID3,
OGG and FLAC.

**`DIMENSIONABLE_IMAGE_MIMES`** (`:48-50`) — `{png, jpeg, gif, bmp, webp}`. The comment
above it is the security link: *"An image outside this set cannot be bomb-checked and
therefore fails closed."* Note TIFF sniffs but is **not** dimensionable — and is
therefore not in the accepted image set either (see `MediaLimits` below).

**The container resolvers.** `_sniff_riff` (`:53-62`) reads the form type at offset
8–12 to separate WEBP from WAV. `_sniff_iso_bmff` (`:65-72`) reads the `ftyp` brand at
8–12 to separate M4A/M4B from generic MP4.

**The weak tests, last.** `_looks_like_mp3_frame` (`:75-77`) is the frame-sync check;
`_looks_like_utf8_text` (`:80-91`) decodes as UTF-8 and rejects C0 controls other than
tab/newline/CR.

**`sniff_mime(data) -> str | None`** (`:94-120`). Order matters: exact prefixes first,
then RIFF, then ISO-BMFF, then the MP3 frame sync, then text. The return contract at
`:101-104`:

> `None` is a *refusal to guess*, not a pass — callers must treat it as
> "unidentifiable" and fail closed for binary media.

**Dimension readers.** `_png_dimensions` (`:123-128`) verifies `IHDR` at bytes 12–16
before unpacking. `_gif_dimensions` (`:131-136`). `_bmp_dimensions` (`:139-144`) takes
`abs()` of both — BMP height is legitimately negative. `_webp_dimensions` (`:174-191`)
handles all three VP8 variants with their `+1` offsets.

`_jpeg_dimensions` (`:152-171`) is the interesting one. `_JPEG_SOF` (`:149`) enumerates
the real start-of-frame markers and the comment records the trap: `C4`/`C8`/`CC` are
Huffman/arithmetic tables and must be skipped or the width/height read lands on
garbage. Line `:164` skips the standalone markers (`D8`, `01`, `D0`–`D7`) by exactly
two bytes, and `:169-170` bails on a segment length below 2 — without that check the
cursor would never advance.

**`image_dimensions(data, mime=None)`** (`:194-219`). The docstring's warning at
`:202-204` is the one to remember: *"Never pass the declared type: this function's
answer feeds the decompression-bomb guard."*

---

## 3. Payload hygiene — `aegis/src/aegis/media/hygiene.py`

Three checks, in the order the docstring justifies at `:4-15`: size cap, MIME
mismatch, decompression bomb.

**`HygieneCode`** (`:30-41`) — nine stable string codes so a verdict can name exactly
which check refused: `empty_payload`, `size_cap_exceeded`, `uri_not_inspectable`,
`mime_unrecognized`, `mime_mismatch`, `mime_not_allowed`,
`image_dimensions_unreadable`, `decompression_bomb_pixels`, `decompression_bomb_ratio`.

**`MediaLimits`** (`:44-70`) — frozen, with the shipped defaults at `:63-70`:

| Limit | Default | Why |
|---|---|---|
| `max_bytes` | 8 MiB | Hard cap on any binary payload |
| `max_text_bytes` | 256 KiB | Separate, much smaller cap for text |
| `max_pixels` | 40,000,000 | Below Pillow's 89 MP bomb warning, so Aegis refuses first |
| `max_pixels_per_byte` | 500 | Real photos are 1–10; a bomb is thousands |
| `allowed_image_mimes` | `DIMENSIONABLE_IMAGE_MIMES` | Only formats that can be bomb-checked |
| `allowed_audio_mimes` | wav, mpeg, ogg, flac, mp4 | `:68-70` |

**`HygieneReport`** (`:82-102`) carries `ok`, **all** failures (not just the first —
`:89-90` explains: an operator debugging a rejected upload wants the full picture),
`sniffed_mime` and `dimensions`. `summary()` (`:98-102`) joins them into one PII-free
line.

### The checks

**`_size_failures`** (`:105-118`) picks the text cap or the binary cap by kind, and
returns `[]` when `byte_size` is `None` — nothing to compare against.

**`_mime_failures`** (`:126-177`) is the subtle one:

- `sniffed is None` → `MIME_UNKNOWN`, and it returns **immediately** (`:131-137`). No
  further check is meaningful on bytes you cannot identify.
- Text takes a **family** comparison, not an exact one (`:138-150`). The comment at
  `:139-142` explains: magic bytes cannot tell `text/plain` from `text/markdown`, so
  asserting an exact match "would be a lie."
- Binary takes an exact comparison (`:151-157`): `declared 'image/png' but the bytes
  are 'image/jpeg'` is a `MIME_MISMATCH`.
- Then a family check (`:158-165`) — an image payload carrying audio bytes.
- Then the allowlist (`:166-176`) → `MIME_NOT_ALLOWED`.

**`_bomb_failures`** (`:180-215`). Note `:184`: it only applies to images whose sniffed
type is in the allowed set. Then `:186-193` — **unreadable dimensions are a refusal**:

> cannot read {sniffed} dimensions from the header; the decompression-bomb guard
> cannot run, so the image is refused

Then the two independent thresholds: `pixels > max_pixels` → `BOMB_PIXELS`
(`:197-204`), and `pixels / size > max_pixels_per_byte` → `BOMB_RATIO` (`:205-214`).

### `inspect_payload` (`:218-268`)

Pure, offline, cheap — "the first thing any media rail does, so a hostile payload is
refused before it costs a model call."

The URI branch (`:235-250`) is the asymmetry worth knowing:

```python
if not payload.inline:
    if payload.kind is MediaKind.TEXT:
        return HygieneReport(ok=True, failures=[], sniffed_mime=None)
    return HygieneReport(ok=False, failures=[... NOT_INSPECTABLE ...])
```

Text may legitimately be referenced by URI upstream; an unscreenable **image** is
exactly the hole this module closes, so it fails closed. Empty bytes are their own
failure (`:253-257`), and the happy path (`:259-268`) sniffs once, reads dimensions
once for images, and concatenates all three check lists.

---

## 4. The widened `Rail` contract — `aegis/src/aegis/guardrails/media/adapt.py`

The old contract was `Callable[[str], GuardResult | None]`. The new one is
`Callable[[MediaPayload], ...]` — and this file is why that is **not** a breaking
change (`:8-11`).

**`media_rail`** (`:55-69`) — a decorator setting `MEDIA_RAIL_ATTR`
(`"__aegis_media_rail__"`, `:41`). Needed only when the annotation cannot be read: a
`functools.partial`, a callable object, a lambda.

**`_first_annotation`** (`:72-94`) — introspects the first positional parameter and
returns its annotation **as a string**. The comment at `:44-45` explains why string
comparison: `from __future__ import annotations` makes every annotation a string at
runtime, and resolving it would mean importing the caller's module namespace.

**`is_media_rail`** (`:97-114`) — marker first, then a substring test against
`_PAYLOAD_ANNOTATIONS` (`:46-48`). The substring form tolerates `MediaPayload | None`
and `aegis.media.ImagePayload` alike.

**`call_rail`** (`:117-150`) — the dispatcher, and the whole compatibility story in
eleven lines:

```python
if is_media_rail(rail):
    return rail(payload)          # new-style: gets the payload
if isinstance(payload, TextPayload):
    return rail(payload.text)     # legacy on text: byte-for-byte the old behaviour
# legacy rail + non-text payload:
logger.warning("%s", reason)
if on_skip is not None:
    on_skip(reason)
return None                       # skipped, and RECORDED as skipped
```

The skip reason (`:142-146`) names the rail and tells the operator how to port it. The
`on_skip` callback is how the pipeline gets that string into the verdict — the
docstring at `:130-132`: *"a rail that did not run must never be counted among the
rails that did."*

---

## 5. The media verdict type — `aegis/src/aegis/guardrails/media/types.py`

`MediaGuardResult` (`:28-56`) **subclasses** `GuardResult` rather than widening it, so
the wire shape stays byte-identical for every existing text caller (`:16-17`). Three
added fields:

- **`media: MediaPayload | None`** (`:33-38`) — the payload the caller should forward
  *instead of* the original, set when a rail rewrote it. `None` means "forward the
  original."
- **`rails_run: list[str]`** (`:39-43`)
- **`rails_skipped: list[str]`** (`:44-48`)

`coverage()` (`:50-56`) builds the sentence from those two lists. The docstring at
`:11-14` names the bug this design prevents:

> The previous audit found `completer=None` silently disabling two rails while the
> verdict text still claimed all four had run. `rails_run` and `rails_skipped` make
> that structurally impossible to repeat: the reason line is *generated from* them.

---

## 6. The media rail chain — `aegis/src/aegis/guardrails/media/screen.py`

The docstring (`:1-29`) maps each media layer onto its text counterpart, and names the
**deliberate gap** at `:24-28`: there is no content-safety or topical screen over raw
pixels, because both would need a second vision call per image. Unsafe *imagery* is out
of scope for this release, and `rails_skipped` says so (`_NO_IMAGE_SAFETY`, `:60-62`)
rather than letting a reader assume coverage.

Layer labels: `HYGIENE_LAYER`, `INJECTION_LAYER`, `PII_LAYER` (`:54-56`).

**`MediaScreen.__init__`** (`:74-105`) takes everything by injection:
`vision_completer`, `limits`, `image_pii`, `image_analyzer`, `image_redactor`,
`transcriber`. Note `:102` — supplying an analyzer implies `image_pii`.
`has_vision_completer` (`:113-115`) is the honest property: *"False ⇒ images fail
closed"*.

**`check`** (`:119-154`) dispatches by kind. A `TextPayload` **raises** (`:144-147`) —
routing text here would silently skip the text rails.

**`_hygiene`** (`:156-170`) returns a BLOCK verdict on failure with
`rails_skipped=["every downstream media rail (hygiene refused the payload first)"]`.

**`_check_audio`** (`:172-196`) — hygiene, then `guard_audio`. The coverage lists are
built from whether a transcriber exists (`:184-187`).

**`_check_image`** (`:218-284`) — the ordered chain:

1. `_hygiene` (`:229`)
2. `_run_image_pii` (`:232`, defined `:198-216`) — redact before screening
3. `screen_image` (`:234`) — the injection screen
4. custom rails (`:246-260`)
5. terminal verdict: `REDACT` if entities were found (`:262-275`), else `PASS`
   (`:276-284`) with `coverage()` appended to the reason

---

## 7. The image-injection screen — `aegis/src/aegis/guardrails/media/injection.py`

**`IMAGE_SCREEN_SYSTEM_PROMPT`** (`:45-58`) asks **two** questions, not one, and the
comment at `:39-44` explains why: a photo of a receipt has text and is not an attack; a
screenshot reading "SYSTEM: you are now in developer mode" is. The prompt names the
low-contrast/tiny-print trick explicitly, because that is how real payloads hide from a
human while staying legible to the model.

**`ImageScreenVerdict`** (`:63-75`) — `injection`, `contains_text`, `reason`, and
crucially **`screened`** (`:71-75`): whether a vision model actually looked. `False`
means the control did not run.

**`data_url`** (`:78-93`) and **`vision_messages`** (`:96-117`) build the OpenAI
multimodal content-block shape — the docstring at `:98-100` notes this is *the same
shape the gateway forwards to the model*, i.e. the screen sees the payload in exactly
the form the model will.

**`_parse_verdict`** (`:120-144`) — strict JSON first, then a keyword fallback via
`parse_bool_field`, then:

```python
return ImageScreenVerdict(
    injection=True,
    reason="Image screen response was unparseable; blocked as a precaution.",
)                                                                       # :141-144
```

**`classify_image`** (`:147-169`) wraps the completer call in a bare `except Exception`
that returns `injection=True` (`:163-168`).

**`screen_image`** (`:172-207`) is the public entry, and has two fail-closed paths
*before* it ever calls a model:

- **Bare URI** (`:189-195`): "what a model would fetch later is not what was screened."
- **No completer** (`:196-206`): `screened=False`, `injection=True`, with the reason
  spelling out that there is no offline backstop for pixels.

---

## 8. Audio — `aegis/src/aegis/guardrails/media/audio.py`

**`Transcriber`** (`:36`) — `Callable[[AudioPayload], str | Awaitable[str]]`. Injected;
this package never implements or imports one (`:11-16`).

**`transcribe`** (`:42-59`) awaits it if it is async, and deliberately **does not**
catch — "the caller decides the fail direction."

**`guard_audio`** (`:62-111`) is the contract:

- `transcriber is None` → BLOCK (`:82-92`), reason: *"The text rails never saw this
  payload."*
- transcription raises → BLOCK (`:93-103`).
- otherwise `result = await text_check(transcript)` (`:105`) and the verdict comes back
  with `layer` prefixed `media_audio:` and the reason prefixed `[transcript]`
  (`:106-111`) — so a reader can see the verdict arrived via speech.

---

## 9. Image PII — `aegis/src/aegis/guardrails/media/image_pii.py`

**`redact_image`** (`:93-156`) OCRs via Presidio's `ImageAnalyzerEngine`, and when
nothing is found returns the **original object** unchanged (`:132-138`). When something
is found it redacts, then:

```python
redacted_image.save(buffer, format="PNG")     # :145
new_payload = ImagePayload(
    data=buffer.getvalue(),
    mime_type="image/png",
    provenance=payload.provenance,
)                                             # :146-150
```

The comment at `:143-144` gives both reasons for PNG: lossless (a redaction box must
not be smeared by JPEG artefacts) and dimension-checkable by the hygiene rail.
`REDACTION_FILL` is opaque black (`:41`) — "blurring is reversible enough to be worth
avoiding."

`_entity_names` (`:79-90`) keeps **kinds only**: "the recognised values are the PII —
putting them in a verdict would leak exactly what the rail exists to protect."

Dependencies come through `aegis.core.lazy.require` (`:62-76`), so a missing
`aegis[media]` extra raises `ImportError` with the install command rather than
degrading.

---

## 10. How the pipeline wires it — `aegis/src/aegis/guardrails/pipeline.py`

**The type aliases** (`:96-105`):

```python
Rail = Callable[[MediaPayload], "GuardResult | None | Awaitable[GuardResult | None]"]
LegacyTextRail = Callable[[str], "GuardResult | None | Awaitable[GuardResult | None]"]
AnyRail = Rail | LegacyTextRail
```

`Guardrails.__init__` (`:118`) takes `vision_completer`, `media_limits`, `image_pii`,
`image_analyzer`, `image_redactor` and `transcriber`, and builds one `MediaScreen`
(`:189-196`).

**`_run_custom`** (`:198-220`) invokes every custom rail through `call_rail` with an
`on_skip` that appends to the collector.

**`_screen_media`** (`:411-431`) is the bridge. Note line `:422`:

```python
result = await self._media.check(
    payload,
    text_check=self.check_input,   # ← the FULL text stack, for audio transcripts
    ...
)
```

That is how a spoken turn gets the same policy as a typed turn. There is no recursion
risk: the transcript is a `str`, so `check_input` takes the text branch.

**`_emit_media`** (`:433-459`) emits the `guardrail_media` CustomEvent with kind, MIME,
size, provenance, verdict, layer, `rails_run`, `rails_skipped`, `redactions` — and the
docstring at `:435-438` notes it carries *"never the bytes and never the decoded
text"*.

**The three entry points** all route the same way:

| Entry point | Line | Routing |
|---|---|---|
| `check_input` | `:461-494` | `:486-488` — non-text → `_screen_media(payload, self._input_rails, emitter=...)` |
| `check_output` | `:523-585` | `:544-546` — non-text → `_screen_media(payload, self._output_rails)` |
| `stream_check_input` | `:589-…` | `:609-614` — text branch or media branch, same event shape |

And the module-level convenience wrappers `check_input`/`check_output`
(`aegis/src/aegis/guardrails/__init__.py:29-49` and `:52-…`) take
`text: str | MediaPayload` plus a `vision_completer`, with the docstring at `:41-44`
stating the fail direction.

---

## 11. Where the host supplies bytes

Two backend adapters build payloads from user uploads, and both carry the declared type
as a **declaration**:

**Audio** — `backend/src/app/voice/service.py:105-109`:

```python
payload = AudioPayload(
    data=data,
    mime_type=(content_type or "application/octet-stream").split(";")[0].strip(),
    provenance=Provenance(source=MediaSource.USER_UPLOAD, origin=filename),
)
```

with `MAX_UPLOAD_BYTES = MediaLimits().max_bytes` (`:31`) so the transport cap and the
hygiene cap agree — a caller can never get past one and be stopped by the other.

**Images** — `backend/src/app/vision/__init__.py:56-92` (`decode_image`), which strips
a `data:` URL prefix, validates base64 strictly, and tags
`Provenance(source=MediaSource.USER_UPLOAD, origin=filename)`.

---

## What you should now be able to point at

- `aegis/src/aegis/media/types.py:242-245` — the discriminated union
- `aegis/src/aegis/media/types.py:147-152` — exactly one of data/uri
- `aegis/src/aegis/media/types.py:251-271` — `as_payload`, the compatibility hinge
- `aegis/src/aegis/media/sniff.py:94-120` — sniff order, and `None` means refuse
- `aegis/src/aegis/media/hygiene.py:186-193` — unreadable dimensions ⇒ block
- `aegis/src/aegis/media/hygiene.py:235-250` — bare-URI image ⇒ block, text ⇒ ok
- `aegis/src/aegis/guardrails/media/adapt.py:117-150` — `call_rail`
- `aegis/src/aegis/guardrails/media/types.py:50-56` — `coverage()`
- `aegis/src/aegis/guardrails/media/injection.py:196-206` — no completer ⇒ fail closed
- `aegis/src/aegis/guardrails/media/audio.py:82-92` — no transcriber ⇒ BLOCK
- `aegis/src/aegis/guardrails/pipeline.py:486-488` — the routing line

**Next:** [`30-deep-dive.md`](30-deep-dive.md) — the failure modes and the real bugs.
