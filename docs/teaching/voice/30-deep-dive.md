# Voice — deep dive

The failure modes, the traps that were closed before they shipped, and the honest
limits of what is actually verified here.

---

## Story 1 — the bypass that is one attribute access away

This is the most important thing in the module, and it is not a bug that was fixed. It
is a bug that was **designed out**, which is a better story to be able to tell.

After `transcribe_and_guard` returns, you are holding a `VoiceResult` with two strings
on it:

```python
result.transcription.text   # what Whisper heard
result.guard.text           # what the rails returned
```

Now imagine wiring this into an agent. The obvious line is:

```python
state["query"] = result.transcription.text        # ← the bypass
```

It reads perfectly. It is the field literally named "text". It is what a reviewer
skimming a diff would expect to see. And it silently discards:

- **every redaction** — if the PII rail masked a card number, that masking is gone
- **every block** — a spoken injection sails straight into the agent, because the
  transcript exists regardless of the verdict

The module's answer is `agent_input`
(`aegis/src/aegis/voice/types.py:125-137`), and the three properties that make it work
are worth stating separately:

1. **It is computed, not stored.** There is no field on the object holding an unscreened
   agent-facing string. You cannot read it by mistake because it does not exist.
2. **It returns the rails' text**, so a `REDACT` verdict yields the masked version
   automatically — the agent gets the safe string with no extra step.
3. **It returns `None` on a block.** Not `""`. `None` is awkward to use accidentally;
   an empty string flows through string concatenation and formatting without complaint.

The docstring names the failure directly (`:131-133`): *"Reading
`VoiceTranscription.text` instead would be the bypass this whole module exists to
prevent."*

And the *type split* reinforces it. `VoiceTranscription` (`:59-94`) is a separate class
from `VoiceResult` (`:97-145`) precisely so that the evidence object and the verdict
object are distinguishable. `transcribe_audio` returns the former; the test at
`aegis/tests/voice/test_security.py:149-155` pins it:

```python
transcription = await transcribe_audio(payload(), transcriber=FakeTranscriber([INJECTION]))
assert not hasattr(transcription, "agent_input")
assert not hasattr(transcription, "cleared")
```

You cannot call the unguarded helper and then read `agent_input`, because the unguarded
helper does not return an object that has one.

**The general principle:** when there is a right value and a wrong value sitting next to
each other, do not rely on a comment. Make the wrong one structurally unavailable and
give the right one the name a caller will reach for.

---

## Story 2 — the required argument

Related, and a one-line design decision with real weight.

`transcribe_and_guard(payload, *, text_check, ...)` — `text_check` is **keyword-only
with no default** (`aegis/src/aegis/voice/transcribe.py:357`).

It could have defaulted to `None`. It could have defaulted to some built-in rail set.
Both would be worse:

- A `None` default means forgetting it is silent, and a caller who forgets gets… what?
  Unguarded text, which is the hole.
- A built-in default means the module carries a *second*, weaker policy that competes
  with the operator's configured stack, and the two drift.

So it is required, and there is a test that asserts the **signature itself** rather than
the behaviour (`aegis/tests/voice/test_security.py:140-147`):

```python
sig = inspect.signature(transcribe_and_guard)
param = sig.parameters["text_check"]
assert param.kind is inspect.Parameter.KEYWORD_ONLY
assert param.default is inspect.Parameter.empty
```

That test fails if someone later adds a convenience default — which is exactly the sort
of "helpful" change that would open the hole back up.

And belt-and-braces: even if you pass `text_check=None` explicitly, you get a **BLOCK**
(`transcribe.py:440-448`) reading *"The transcript was never screened — no text rail
stack was supplied, so speech would have reached the agent unguarded."*

---

## Story 3 — the empty transcript that would have sailed through

`make_transcriber` (`transcribe.py:311-351`) builds the callable that
`MediaScreen` wants. Its return-value docstring (`:333-337`) records a decision that is
easy to get backwards:

> It raises rather than returning an empty string on failure, because
> `aegis.guardrails.media.guard_audio` converts a raising transcriber into a
> fail-closed BLOCK — whereas an empty transcript would sail through the text rails as
> "nothing to object to".

Think about what a defensive programmer's instinct produces here. Transcription failed?
Return `""` and let the caller decide. It feels safe — no exception, no crash.

But `""` goes to `text_check("")`, every rail finds nothing objectionable in an empty
string, and the verdict is **PASS**. The audio was never transcribed, never screened,
and the system reports a clean pass.

The exception is the safe option, because `guard_audio` (`audio.py:93-103`) catches it
and converts it into a BLOCK with a reason naming the control that failed.

**Generalisation:** in a fail-closed system, "return a benign empty value on error" is
frequently a fail-open. Ask what the empty value does to the *next* stage before
choosing it.

---

## Story 4 — the per-minute call that walked past the USD cap

This one is a live-consequence bug of the kind that never shows up in a test suite.

Every budget cap in the platform is denominated in **USD**, computed by summing ledger
rows. Chat calls ledger `prompt_tokens`, `completion_tokens` and a cost derived from
them. Fine.

Transcription sends up essentially no tokens. Its cost is per **audio-minute**. So a
naive integration ledgers `prompt_tokens=0, completion_tokens=0` → **$0.00**, and:

- the tenant's spend never moves
- the USD cap never binds
- and nothing anywhere looks broken, because $0.00 is a perfectly valid number

The gateway's `transcribe` docstring
(`aegis/src/aegis/gateway/llm.py:1136-1145`) states it plainly:

> Billing is per minute of audio, not per token, so a transcription would otherwise
> ledger `prompt_tokens=0` → `$0.00` and slip past a USD cap entirely.

The fix has three parts, and all three matter:

1. **The billing unit is in the routing table.**
   `aegis/src/aegis/gateway/routing.py:174` declares
   `ModelRole.VOICE: BillingUnit.AUDIO_MINUTES`. It is data, not an assumption baked
   into the pricing function.
2. **The duration is sourced from the provider when it reports one** — which is why
   `response_format="verbose_json"` is the default (`transcribe.py:263`,
   `llm.py:1131`). The plain `text` format does not carry `duration`, so choosing it
   would silently remove the billing unit.
3. **When neither the provider nor the caller supplies a duration, the call is tagged
   `CostSource.UNPRICED` and logged** (`llm.py:1199-1215`). That is a *different
   statement* from a genuine $0.00 — "billable work nobody could price" versus "this
   cost nothing" — and collapsing the two is exactly the dishonesty this codebase bans.

The lesson generalises well beyond voice: **when you add a modality, check whether its
billing unit is the one your budget system counts.** Images-per-call, audio-seconds,
characters — each one is a way for spend to become invisible.

---

## Story 5 — the silence detector that finds silence everywhere

Two arithmetic traps in the chunker, both closed, both the kind of thing that produces
a plausible-looking wrong answer rather than a crash.

**The squared threshold.** `_envelope` (`chunking.py:160-181`) computes **mean
square**, not RMS — deliberately, because the square root is monotonic and changes no
comparison the module makes. But then the threshold has to live in the same space:

```python
threshold = overall * (policy.silence_ratio**2)      # :247
```

`silence_ratio = 0.18` is an *amplitude* ratio — 18% of average loudness. Comparing it
against an energy envelope without squaring gives a threshold ~5.5× too generous, so
ordinary speech falls below it and the splitter "finds" pauses in the middle of words.
No error, no crash — just cuts in the wrong places and a transcript with mangled
boundaries.

**8-bit unsigned WAV.** (`chunking.py:328-331`)

```python
if params.sampwidth == 1:
    # 8-bit WAV is unsigned with a 128 midpoint; centre it or every window
    # looks equally loud and no pause is ever found.
    samples = array.array("h", (s - 128 for s in samples))
```

8-bit WAV stores samples as unsigned bytes centred on 128. Silence is a run of `128`s,
not a run of `0`s. Square 128 and you get 16,384 per sample — so *silence is loud* and
the mean square never dips. Every window looks the same, no pause is ever found, and
every cut is forced on time.

Again: no crash. Just a splitter that quietly stops working on one input format.

**Both are the same species of bug.** Numerical code fails by producing wrong numbers,
not by raising. The test at `aegis/tests/voice/test_chunking.py:24-35` is the kind that
catches them — it builds a synthetic WAV with a known 1.5-second pause every 10 seconds
and asserts every cut lands in the silent tail:

```python
assert all(c.split_on_silence for c in plan.chunks[:-1]), plan.note
for chunk in plan.chunks[1:]:
    assert (chunk.start_seconds % 10.0) > 8.0
```

That is testing *where* the cut landed, not merely that a cut happened. A "chunk count
is greater than one" assertion would pass with both bugs present.

---

## Story 6 — "one chunk" means two different things

`plan_chunks` has four early returns that each produce a single-chunk plan
(`chunking.py:287-325`), and they are **not the same outcome**:

| Reason | `splittable` | What it means |
|---|---|---|
| Empty payload | `False` | Nothing there |
| Not parseable as PCM WAV | `False` | We *cannot* split this |
| Unsupported sample format (24-bit) | `False` | We *cannot* split this |
| Duration within the ceiling | `True` | We *did not need to* split this |

A caller looking only at `len(plan.chunks) == 1` cannot tell "chunking worked, the file
was short" from "chunking is not available for this container and the whole 40-minute
recording went up as one request." The `note` field's docstring makes the requirement
explicit (`:108-111`): *"so 'one chunk' is never mistaken for 'chunking worked'."*

And the note is not a category label — it names the container, quotes the exception, and
states the policy:

> container is not uncompressed PCM WAV (audio/mpeg; unknown format: 85); transcribed
> whole in one request — this module decodes no other container (fleet-only policy: no
> ffmpeg, no local codec)

That string travels into `VoiceTranscription.chunking` (`types.py:74`) and onto the
console. An operator seeing a 40-minute MP3 time out gets the actual reason, not a
shrug.

---

## Story 7 — the confidence nobody reports

`VoiceSegment.confidence` is `float | None` and is **always `None`** on this
deployment.

The chain is verifiable end to end:

1. Whisper's `verbose_json` reports `avg_logprob` and `no_speech_prob` per segment.
2. The gateway's `_parse_segments` (`llm.py:1102-1123`) constructs
   `TranscriptionSegment(id=…, start=…, end=…, text=…)` and drops everything else.
3. `_merge_segments` (`transcribe.py:172-198`) sets `confidence=None` explicitly, with
   the comment: *"Never derived: the gateway's segment parser carries no confidence
   signal, so this stays None and the console says 'not reported'."*

The tempting alternative is `exp(avg_logprob) * 100` rendered as "confidence: 73%". It
would look great. It would also be an overclaim twice over: `avg_logprob` is a
model-internal token likelihood, not a calibrated probability that the transcription is
*correct*, and a fluent hallucination scores well on it.

So the field exists — so a provider that genuinely reports one can be carried straight
through — and the wire payload carries a `hasConfidence` boolean
(`stream.py:62`, `:49-51`) that the console keys on to render "not reported" rather
than a number.

**Same shape as the forecast module's coverage story:** the honest answer is "we did not
measure this", and the dishonest one is a plausible-looking figure derived from
something adjacent.

---

## Story 8 — declaring what the module does not do

`NO_DIARISATION` (`transcribe.py:73-76`) is a *constant* whose only job is to appear in
`controls_skipped` on every single call (`:397`):

> speaker diarisation (the fleet's hosted Whisper deployment reports no speaker labels
> and policy forbids a local model, so no speaker attribution is produced)

Why bother? Because a two-speaker recording transcribed as one continuous block of text
*looks* like a complete transcript. Nothing about the output says "we have no idea who
said which half." A user reading a customer-service transcript would reasonably assume
attribution was attempted and found nothing to report.

Naming the absent control in the same list as the present ones removes the ambiguity.
It is the same discipline as `_NO_IMAGE_SAFETY` in the media chain, and the same as
`ControlOutcome.NOT_RUN` in vision.

---

## Concurrency and the shape of the work

**Chunks are transcribed sequentially** (`transcribe.py:255-290`), not in parallel.

That is a real trade and worth being able to defend. Parallel would be faster —
N chunks in the time of the slowest, rather than the sum. It gives up two things:

- **Budget ordering.** Every chunk is a gateway call, and every gateway call runs
  `_governance.enforce` before spending (`llm.py:1165-1170`). Sequential means chunk
  *k+1* is checked against a budget that already includes chunk *k*'s spend. Concurrent
  calls can each see the pre-spend balance and collectively blow past the cap.
- **Ordered streaming.** `on_chunk` (`transcribe.py:276-290`) fires after each chunk
  with the *running* transcript, so the console renders speech arriving in order. Out of
  order, the UI would have to buffer and reassemble.

**No shared mutable state.** `plan_chunks`, `_envelope`, `_quietest_run` and
`_boundaries` are pure functions. `transcribe_and_guard` accumulates into locals. There
is nothing on `self` because there is no `self` — the module is functions, not a class.

**One caveat on the file handle.** `_NamedBytesIO` is constructed fresh per chunk
(`:256-258`) rather than rewound and reused. That matters: an upload client reads the
handle to exhaustion, so a shared handle would hand the second request zero bytes.
`_audio_handle` in the gateway (`llm.py:1079-1092`) reinforces the ownership rule — a
handle the caller opened is left alone, because whoever opened it owns closing it.

---

## Honest limits — what is not verified

The module docstring says this before anything else
(`aegis/src/aegis/voice/__init__.py:43-46`):

> **Not verified against the live fleet.** The gateway credential in this repo is a
> placeholder, so every behaviour here is proven against injected fakes. What the live
> `azure/genailab-maas-whisper` deployment returns for segments, language detection and
> duration is asserted nowhere in this package.

The test conftest repeats it (`aegis/tests/voice/conftest.py:1-7`). Every test drives
`FakeTranscriber`, which matches the `aegis.gateway.transcribe` signature exactly and
records what it was handed.

That is the right way to test *this* module — the security ordering, the fail
directions, the chunk arithmetic and the timeline rebase are all properties of code that
lives here, and a fake exercises them deterministically and for free. But be precise
about the claim in an interview: **the contract is tested; the provider's behaviour is
not.**

The specific things a live run would have to confirm:

- that `duration` comes back on `verbose_json` from this deployment (the billing unit
  depends on it)
- that segment timings are what the timeline rebase assumes
- that language auto-detection populates the field
- how the deployment behaves on a chunk that is mostly silence, given Whisper's known
  hallucination-on-silence failure mode

Three other limits worth stating:

**Only PCM WAV chunks.** Everything else is transcribed whole. That is a policy
consequence (no ffmpeg), it is reported rather than hidden, and the browser side works
around it by re-encoding — but a *server-side* caller uploading a 40-minute MP3 gets one
enormous request.

**Guarding the transcription, not the audio.** Anything ASR drops is invisible to the
rails: tone, a whispered aside the model missed, an overlapping second speaker. That is
the accepted cost of reusing the mature text stack, and it is much smaller than the cost
of maintaining a second, weaker policy.

**Whisper hallucinates on silence.** A chunk that is genuinely quiet may come back with
invented text. The rails will screen it — so it is not a security hole — but it is a
quality one, and nothing in this module currently filters on `no_speech_prob` because
the gateway does not carry it.

---

## What you should now be able to tell as a story

- **The one-attribute bypass**, and the three properties of `agent_input` that close it
- **Why `text_check` is required**, and the test that asserts the signature
- **Why returning `""` on failure is a fail-open**, and why raising is safer
- **The per-minute call that walked past a USD cap**, and the three-part fix
- **Two silence-detector arithmetic traps** that produce wrong answers rather than crashes
- **Why "one chunk" needs a `splittable` flag** and a prose note
- **Why confidence is `None` rather than `exp(avg_logprob)`**
- **Why an absent control gets named** in the same list as the present ones
- **Why chunks are sequential**, and what that buys
- **What is fake-tested versus fleet-verified**, stated before someone asks

**Next:** [`40-diagrams.md`](40-diagrams.md).
