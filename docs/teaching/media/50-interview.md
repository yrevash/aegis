# Media — interview questions and answers

Claim, then reason, then a concrete detail from this system. The full argument behind every
answer is in [`10-guide.md`](10-guide.md).

---

### "You have guardrails on text. What happens when someone uploads an image?"

Before this module, nothing — and that is the honest answer to lead with, because it is
the finding the module exists for.

Every guardrail entry point was `Callable[[str], GuardResult]`. An image sent as an
OpenAI multimodal content block travelled beside the text, in a different field, and
reached the model having passed through **no rail at all**. The pipeline still ran, and
still returned PASS, because the string it was handed was fine.

That is the class of bug worth naming: a control that is structurally unable to see the
input looks identical, from outside, to a control that saw it and approved it. Both
emit green.

The fix was a type change. `Rail` widened from `str` to `MediaPayload` — a discriminated
union of `TextPayload | ImagePayload | AudioPayload` — because **a rail cannot screen
what it cannot receive.**

---

### "Why not just trust the Content-Type header?"

Because it is written by whoever sent the bytes. It is attacker-controlled input,
exactly like the body it describes.

Two attacks fall straight out of trusting it. Declare an image as `text/plain` and your
*router* sends it down the text path — the rails decode it, find nothing objectionable,
and it reaches the model as an image anyway. Or declare something else as `image/png`
and your image handling accepts a format it was never built to parse.

So we keep the declared type and never believe it. `sniff_mime` derives the real type
from magic bytes — `89 50 4E 47 0D 0A 1A 0A` for PNG, `FF D8 FF` for JPEG — and the
hygiene rail compares the two. **A mismatch is itself the signal.** A file whose label
disagrees with its contents is not a mistake to correct; it is a routing decision
someone tried to influence.

Two details I would add. RIFF and ISO-BMFF need the **form type** at offset 8, not just
the first four bytes — `RIFF` alone covers both WEBP and WAV. And when nothing matches,
`sniff_mime` returns `None`, which the caller must treat as *"unidentifiable"* and fail
closed on. **`None` is a refusal to guess, never a pass.**

---

### "Walk me through a decompression bomb."

A few kilobytes of PNG whose header declares 40,000 × 40,000 pixels. That is 1.6 billion
pixels — at four bytes each, 6.4 GB of RAM, allocated the instant any library decodes
it. Your size cap does not fire, because the file is 40 KB. The process dies anyway.

The defence is cheap once you see it: image formats declare their dimensions in the
**header**, before the pixel data. So we read the header, multiply, and refuse —
without ever decoding. The bomb cannot go off while being inspected, because it is
never opened.

Two independent thresholds, not one. An absolute cap at 40 megapixels — chosen
deliberately below Pillow's own 89 MP `DecompressionBombWarning`, so we refuse before
any downstream decoder even warns. And a compression-ratio cap of 500 pixels per byte,
which catches the small-but-absurd file that slips under the absolute cap. A real photo
is 1–10 pixels per byte; a bomb is thousands.

**The detail that shows you have implemented it:** if the dimensions cannot be read —
truncated header, or a format we do not parse — the guard *cannot run*, so the image is
**blocked**. And the accepted-image allowlist defaults to exactly the set of formats we
can read dimensions from, so "accepted" and "bomb-checkable" are the same set by
construction. TIFF sniffs correctly and is still refused, because we cannot read its
dimensions.

---

### "How do you widen a public callback type without breaking everyone's code?"

An adapter that looks at what each rail was *written* to accept and hands it exactly
that.

A rail whose first parameter is annotated with a payload type — or that wears the
`@media_rail` decorator — gets the payload. Anything else is a legacy string rail and
gets `payload.text`, byte for byte the behaviour it had before.

The interesting case is a legacy string rail faced with an image. Three options: crash,
which is hostile; pass it a stringified blob, which is worse because it will always
return "fine" and report coverage it does not have; or **skip it and record the skip**.
We skip, log a warning naming the rail, and append the reason to the verdict's
`rails_skipped` — with an instruction telling the operator how to port it.

Two implementation notes I like. Annotations are compared as **strings**, because
`from __future__ import annotations` makes them strings at runtime and resolving them
would mean importing the caller's namespace during a guardrail check. And callables
with no signature at all — C-level builtins, some callable objects — are treated as
legacy, which is the safe default; `@media_rail` is the escape hatch for a
`functools.partial` whose annotation cannot be introspected.

---

### "Your verdict says PASS. How do I know all the rails actually ran?"

Because the sentence is **generated from a record of what executed**, not written at
design time.

This comes from a real finding. An earlier audit caught `completer=None` silently
disabling two rails while the verdict text still claimed all four had run — the string
described the *intended* chain, not the chain that ran.

So `MediaGuardResult` carries `rails_run` and `rails_skipped`, each skip with its
reason, and `coverage()` joins them into the reason line. A rail that did not run
**cannot appear in the sentence**, because the sentence is a join over a list it is not
in. That is a structural guarantee rather than a discipline someone has to remember to
update.

The same discipline covers deliberate gaps. Every image verdict starts with *"image
content-safety/topical screen (not implemented for pixels in this release)"* already in
`rails_skipped`. We do not screen for unsafe *imagery* — only for instructions aimed at
the model — and the verdict says so rather than letting green imply coverage.

---

### "What about an image supplied as a URL?"

Blocked, and this is one of my favourite bits of the design.

Bytes this process does not hold cannot be sniffed, sized, or bomb-checked. And even if
we fetched them to screen them, the model would fetch them **again** — with no
guarantee the server returns the same bytes twice. What we screened would not be what
got read. That is a time-of-check/time-of-use problem, and it is not solvable by
fetching harder.

So "bytes in hand" versus "a URI" is a security-relevant distinction and it lives in the
type — `data` and `uri`, with a validator requiring exactly one. Hygiene fails closed on
a bare-URI image or audio payload.

Text is the deliberate exception: a URI-only text payload is a reference something
upstream resolves into a string, and that string goes through the full text stack
anyway. The asymmetry is argued, not accidental.

---

### "How do you guard audio?"

Transcribe first, then run the **entire existing text rail stack** over the transcript.

The alternative is a parallel audio policy — audio-specific detectors, audio-specific
classifiers. It would be enormous work, weaker than the text rails, and the two would
drift the first time someone updated one and not the other. Every attack that works
typed works spoken, so transcribe-then-guard means voice inherits signatures, the
injection classifier, content safety, PII, schema, topical *and* every custom rail the
operator wrote — unchanged, on day one.

The ordering **is** the control, and the fail direction matters as much. No transcriber
wired means no transcript; no transcript means the rails have nothing to judge, so it
**blocks**. Same if transcription raises. "We could not check it" and "we checked it and
it was fine" have to be different outcomes.

And the verdict comes back with its layer prefixed `media_audio:` and its reason
prefixed `[transcript]`, so a reader can see the verdict arrived via speech rather than
typed input.

---

### "Why does the image-PII rail return an image?"

Because on a binary, a bare `REDACT` verdict is theatre.

On text, `REDACT` is actionable — the pipeline hands you the masked string and you
forward that. On an image, telling you "we found a passport number" changes nothing:
you are still holding the original bytes with the passport number in them.

So the rail returns a **new** `ImagePayload` carrying the redacted pixels, and the
verdict has a `media` field to put it on, with the reason saying "forward that, not the
original."

Three details. We **paint** an opaque black box rather than blurring, because blurring
is partially reversible — especially on rendered text, where the glyph set is small and
the deblurring problem is heavily constrained. We re-encode as **PNG**, so the box
edges are not smeared by JPEG artefacts and the result is still dimension-checkable by
our own hygiene rail. And the original payload is **frozen** — a payload is evidence, so
the redaction produces a new object and what was screened stays exactly what was
screened.

---

### "Your two code paths order PII and the injection screen differently. Isn't that a bug?"

No, and it is the ordering question I would want to be asked.

The guardrails chain redacts **before** it screens, on the standard argument: sending
unredacted pixels to a screening model is itself a sensitive-information disclosure —
OWASP LLM06 — exactly as it is for text.

The vision pipeline screens **before** it redacts, because on that path the image is
going to the fleet's vision deployment either way. Redacting first buys no privacy at
all, while screening first refuses a hostile image *before* we start an expensive OCR
stack on it.

The general point: an ordering rule is downstream of an argument. When the premise
changes, the correct ordering changes with it. Copying the rule without the premise is
how you end up with a control that costs money and buys nothing. Both docstrings state
the trade rather than leaving a reader to discover it.

---

### "What is provenance for?"

Distinguishing an image a human uploaded from one that arrived inside a retrieved
document or a tool result. The second is the *indirect* injection surface — content the
user never chose and an attacker may control.

Five sources: `USER_UPLOAD`, `TOOL_OUTPUT`, `RETRIEVAL`, `MODEL_OUTPUT`, `UNKNOWN`. The
`untrusted` property returns true for retrieval, tool output **and unknown** — the default
that matters, because a payload nobody tagged then gets the strict classification rather than
the lenient one.

I would be precise about what it does today, because it is easy to overclaim. Provenance is
carried on every payload and emitted on the `guardrail_media` event, so it reaches the trace
panel and the audit log — but **no rail currently branches on `untrusted`**. An image tagged
`RETRIEVAL` goes through the same rails as one tagged `USER_UPLOAD`. The distinction is
recorded and the strict default is already right; differential treatment is the part that is
not written yet. That is the shape of the next change, not a claim about the current one.

The free-text `origin` field — a filename, a URL, a tool name — is documented as never
parsed for control flow. It exists so a human reading a blocked verdict knows what was
blocked.

---

### "What would you fix in this module?"

Three things, and I would rather name them than be caught on them.

**`Provenance.untrusted` has no consumer.** The property exists, provenance is emitted on
every media event, and `UNKNOWN` correctly counts as untrusted — but nothing branches on it,
so retrieved and user-uploaded images are screened identically. All the data a differential
rule needs is already carried; the rule is not written.

**There is no dedicated test package for `aegis.media`.** The seams are covered, and covered
well — the voice tests drive `MediaScreen` directly and assert on `rails_run`/`rails_skipped`,
and build an `AudioPayload` declaring `audio/wav` whose bytes are a PNG signature to prove the
transcriber is never called; the vision tests cover the bomb refusal and the URI-only refusal.
What is untested is the fiddly parsing: the JPEG start-of-frame marker walk, the WEBP `+1`
arithmetic, negative BMP height, a truncated header per format, and `is_media_rail` against a
`functools.partial` or a C builtin. Those are exactly where an off-by-one is easiest to
introduce and hardest to notice.

**No content-safety screen over pixels.** Declared on every verdict rather than
implied, but it is a real gap: we screen for instructions aimed at the model, not for
unsafe imagery.

One I would mention because the *shape* of it recurs: the `aegis[media]` extra used to be
named in four fail-closed remedy messages without existing in `pyproject.toml`, so a user who
hit the correct refusal was told to run an install that could not resolve. It is declared now.
A fail-closed path whose error message is right about the cause and wrong about the remedy is
still a bug — and it is the half nobody writes a test for.

---

### "How would you test this?"

Four layers, and the direction of each assertion matters more than its coverage.

**The parsers, directly.** Table-driven over real byte fixtures: every magic number
including the RIFF and ISO-BMFF form types; every dimension reader including negative
BMP height, the JPEG standalone-marker skip, and a JPEG whose first marker is a Huffman
table so a naive walk reads garbage. Plus a truncated header for each format, which
must return `None` and therefore block.

**The hygiene decisions, as failure directions.** A PNG declared `text/plain` must
block. A 40 KB PNG declaring 40,000 × 40,000 must block on *both* codes. An image with
unreadable dimensions must block. A bare-URI image must block; a bare-URI *text* payload
must pass.

**The adapter, against the awkward callables.** An annotated function, a `@media_rail`
lambda, a `functools.partial`, a callable class, and a C builtin with no signature — the
last two must be classified legacy, and legacy plus an image must produce a skip
*record*, not a call and not a crash.

**The seam, end to end.** The assertion style is the one already used in the voice and vision
suites: `assert fake.calls == []`, or `assert screen.calls == [] and analyst.calls == []`. A
pipeline that spends money and *then* decides cannot satisfy that assertion. Testing "the
verdict was BLOCK" would pass either way.
