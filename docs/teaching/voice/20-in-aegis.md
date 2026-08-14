# Voice — in Aegis

`aegis.voice` is deliberately thin. It owns **no model, no codec and no decoding
policy** — the gateway already owns the model call. What this module adds is the three
things that sit either side of it (`aegis/src/aegis/voice/transcribe.py:9-24`):

1. media hygiene **before** spend
2. silence-aware chunking
3. the security ordering — transcribe, then the full text rail stack

---

## How you import it

```python
from aegis.media import AudioPayload, MediaSource, Provenance
from aegis.voice import transcribe_and_guard

payload = AudioPayload(
    data=wav_bytes,
    mime_type="audio/wav",
    provenance=Provenance(source=MediaSource.USER_UPLOAD, origin="note.wav"),
)
result = await transcribe_and_guard(payload, text_check=guards.check_input)

result.transcription.text      # evidence for the console
result.transcription.segments  # time-aligned, on the RECORDING's timeline
result.guard.verdict           # the full text stack's verdict
result.agent_input             # None unless the rails cleared it
```

And to wire speech into the media rail chain (audio is **blocked** without this):

```python
from aegis.guardrails.media import MediaScreen
from aegis.voice import make_transcriber

screen = MediaScreen(transcriber=make_transcriber())
```

Both snippets are the module's own documented usage
(`aegis/src/aegis/voice/__init__.py:20-41`). The export surface is `:51-84`.

---

## 1. The result types — `aegis/src/aegis/voice/types.py`

Pydantic + stdlib only (`:3-5`), so an API schema layer can import these without
dragging in `litellm`.

### `VoiceSegment` (`:30-56`)

Frozen. `index`, `start`, `end`, `text`, `confidence`, `chunk`.

Two fields carry design decisions. **`index` is transcript-wide** (`:33-35`) — chunked
audio is renumbered end to end, so it is a transcript index rather than the provider's
per-request one. **`start`/`end` are seconds from the start of the whole recording**
(`:36-40`), with chunk offsets added back.

**`confidence` is always `None` today**, and the module docstring says why at `:9-16`:

> Whisper's `verbose_json` reports `avg_logprob` and `no_speech_prob`, but the
> gateway's segment parser (`aegis.gateway.llm._parse_segments`) keeps only
> `id`/`start`/`end`/`text`, so no confidence signal reaches this module. The field
> exists so a provider that *does* report one can be carried straight through — it is
> never filled in with a derived or invented number, and the console renders "not
> reported" rather than a plausible-looking percentage.

That is verifiable: `aegis/src/aegis/gateway/llm.py:1102-1123` builds
`TranscriptionSegment(id=…, start=…, end=…, text=…)` and nothing else.

### `VoiceTranscription` (`:59-94`)

The **evidence**, before any rail has judged it. The docstring at `:61-64` states the
reason it is a separate type from `VoiceResult`: *"a transcript exists as evidence the
moment the model returns it, but it is not agent input until the text rails have
cleared it."*

Fields: `text`, `language`, `duration_seconds`, `segments`, `model`, `chunk_count`,
`chunking` (one honest line about *why* it was or was not split), `cost_usd`,
`audio_seconds_billed`.

`has_confidence` (`:91-94`) is the honesty flag the UI keys on.

### `VoiceResult` (`:97-145`) — and the property that is the whole module

```python
@property
def agent_input(self) -> str | None:
    if not self.cleared:
        return None
    return self.guard.text
```
`:125-137`

The docstring is the argument, verbatim:

> This is deliberately the rails' `text`, not the raw transcript: when the PII rail
> redacts, the agent must receive the *redacted* string, and when any rail blocks, the
> agent must receive nothing at all. Reading `VoiceTranscription.text` instead would be
> the bypass this whole module exists to prevent.

Three properties make that stick:

- It is **computed**, not stored — there is no field holding an unscreened string that
  someone could read by mistake.
- `cleared` (`:120-123`) is `verdict is not BLOCK`, so `PASS`, `REDACT` and `FLAG` all
  yield text and `BLOCK` yields `None`.
- The raw transcript is still reachable, under a name (`transcription.text`) that does
  not read like agent input.

`controls_run` / `controls_skipped` (`:117-118`) and `coverage()` (`:139-145`) mirror
`MediaGuardResult` exactly — the reason line is *generated from* the lists.

---

## 2. Chunking — `aegis/src/aegis/voice/chunking.py`

The docstring (`:1-23`) gives all three arguments: why chunk at all, why on silence, and
why pure stdlib.

> Policy for this platform is fleet models only and no new media toolchain — no ffmpeg,
> no torch, no local Whisper. So the splitter works on what Python itself can parse:
> uncompressed PCM RIFF/WAVE, via `wave` and `array`. That is a real limitation and it
> is reported rather than hidden.

**Constants.** `_WINDOW_SECONDS = 0.02` (`:39`) — "the standard speech frame".
`_TYPECODE = {1: "B", 2: "h", 4: "i"}` (`:43`) with the comment that WAV also allows
3-byte samples, "which `array` has no type code for; those are reported unsupported, not
guessed."

**`ChunkPolicy`** (`:46-72`), frozen, with every default justified in the docstring:

| Field | Default | Why (`:50-63`) |
|---|---|---|
| `max_chunk_seconds` | 120.0 | Short enough that a stall is cheap to retry |
| `min_chunk_seconds` | 5.0 | Stops a long silence producing near-empty requests |
| `search_seconds` | 20.0 | How far back to hunt before giving up and cutting on time |
| `silence_ratio` | 0.18 | **Relative**, "because recording levels vary by two orders of magnitude" |
| `min_silence_seconds` | 0.25 | "a sentence boundary rather than a stop consonant" |

**`AudioChunk`** (`:75-99`) — `index`, `start_seconds` (the offset added back to every
segment timestamp), `duration_seconds`, `data` (a **self-contained file**, not a slice),
`mime_type`, and `split_on_silence` (`:87-89`): `False` means the splitter ran out of
search window and cut on time, so a word may straddle the boundary.

**`ChunkPlan`** (`:102-121`) — `chunks`, `duration_seconds`, `note`, `splittable`. The
`note` field's docstring (`:108-111`) is the honesty rule: *"so 'one chunk' is never
mistaken for 'chunking worked'."*

### The algorithm

**`_samples`** (`:142-157`) decodes raw PCM into an `array.array`, byte-swapping on a
big-endian host.

**`_envelope`** (`:160-181`) computes mean-square per window. The docstring at
`:162-164`: *"Mean-square rather than RMS: the square root is monotonic, so it changes
no comparison this module makes and costs a call per window."*

**`_quietest_run`** (`:184-212`) scans a band for the longest sub-threshold run of at
least `min_windows`, returning its **centre** — cut in the middle of the pause, not at
its edge.

**`_boundaries`** (`:226-265`) is the walk:

```python
overall = (sum(envelope) / len(envelope)) if envelope else 0.0
threshold = overall * (policy.silence_ratio**2)      # :245-247
```

The comment at `:246` — *"Squared threshold, because the envelope is mean-square"* — is
the detail that separates a working splitter from one that finds silence everywhere.

Then the loop (`:253-265`): target at `cursor + max_w`, floor at
`max(cursor + min_w, target - search_w)`, and if no qualifying run is found, cut on time
with `silence=False`.

**`plan_chunks`** (`:268-370`) is the entry point and has four early returns, each with
its own honest note:

| Condition | Line | Note |
|---|---|---|
| Empty payload | `:287-288` | "empty payload; nothing to split" |
| Not parseable as PCM WAV | `:290-300` | names the container and the exception, then *"transcribed whole in one request — this module decodes no other container (fleet-only policy: no ffmpeg, no local codec)"* |
| Unsupported sample format | `:302-307` | e.g. 24-bit, reports the actual bit depth |
| Short enough already | `:309-325` | "…is within the 120s single-request ceiling; sent as one request" |

The first three set `splittable=False`; the fourth sets `True` — because in that case
the module *could* have split and did not need to. That distinction is the difference
between "we can't" and "we didn't have to."

The 8-bit fix is at `:328-331`:

```python
if params.sampwidth == 1:
    # 8-bit WAV is unsigned with a 128 midpoint; centre it or every window
    # looks equally loud and no pause is ever found.
    samples = array.array("h", (s - 128 for s in samples))
```

And the closing note (`:363-369`) reports how many cuts landed in a real pause and how
many were forced:

> split into 5 chunks — 3 cut in a detected pause, 2 cut on time (no pause found within
> 20s of the boundary)

---

## 3. Transcription and the guard — `aegis/src/aegis/voice/transcribe.py`

**Control labels** (`:65-68`): `HYGIENE_CONTROL`, `TRANSCRIPTION_CONTROL`
(`"hosted transcription (ModelRole.VOICE)"`), `TEXT_RAILS_CONTROL`, `LAYER = "voice"`.

**`NO_DIARISATION`** (`:73-76`) is a control this module does *not* provide, named so the
coverage line cannot imply it:

> speaker diarisation (the fleet's hosted Whisper deployment reports no speaker labels
> and policy forbids a local model, so no speaker attribution is produced)

It is inserted into `controls_skipped` on **every** path (`:397`).

**`TranscribeCallable`** (`:79-97`) is a Protocol matching `aegis.gateway.transcribe`
exactly, "so a test — or a host with its own chokepoint — can inject a fake, the same
way the text rails take an injected `ChatCompleter`."

**`AudioRejected`** (`:100-114`) — hygiene refused; nothing was transcribed.

**`_NamedBytesIO`** (`:117-134`) — a `BytesIO` carrying a `name`. The docstring at
`:119-124` explains: OpenAI-compatible upload clients read `file.name` to derive the
multipart filename and therefore the format; a bare `BytesIO` has none and cannot be
given one.

**`_default_transcriber`** (`:137-158`) imports `aegis.gateway.transcribe` **inside the
function** — `:139-142`: *"so importing `aegis.voice` stays as cheap as importing
`aegis.media` — the Module Contract's isolation rule."*

**`_merge_segments`** (`:172-198`) is the timeline rebase:

```python
offset = chunk.start_seconds
return [
    VoiceSegment(
        index=next_index + i,
        start=None if seg.start is None else seg.start + offset,
        end=None if seg.end is None else seg.end + offset,
        text=seg.text,
        confidence=None,   # :192-194 — "Never derived"
        chunk=chunk.index,
    )
    ...
]
```

### `transcribe_audio` (`:201-308`)

Hygiene **first** (`:239-241`) — `raise AudioRejected` before a paid request is made.
Then `plan_chunks` (`:244`), then the loop (`:255-290`):

- wrap the chunk in `_NamedBytesIO` with a name like `chunk-003.wav` (`:256-258`)
- call the gateway with `response_format="verbose_json"` and the chunk's own duration
  (`:259-265`)
- accumulate text, rebased segments, cost, billed seconds
- invoke `on_chunk` if supplied (`:276-290`) — **the streaming seam**

Duration resolution (`:292-296`) prefers the container's own value, falls back to the
sum of provider-reported durations, and otherwise stays `None`:

> If neither exists it stays None — an unknown duration is never rendered as 0.

The docstring at `:217-218` is explicit: *"This function returns **evidence, not agent
input**."*

### `make_transcriber` (`:311-351`)

The one-liner that wires speech into `MediaScreen`. The return-value docstring
(`:333-337`) contains a subtle and important point:

> It raises rather than returning an empty string on failure, because
> `aegis.guardrails.media.guard_audio` converts a raising transcriber into a
> fail-closed BLOCK — whereas an empty transcript would sail through the text rails as
> "nothing to object to".

A silent empty string is a fail-open dressed as a result.

### `transcribe_and_guard` (`:354-470`)

The guarded entry point. The docstring at `:367-372` states the control:

> **The ordering is the security control.** Audio is transcribed first because a rail
> cannot judge what it cannot read; the transcript is then handed to `text_check` — the
> caller's *entire* input rail stack — and only what that returns is exposed as
> `agent_input`.

Three fail-closed branches:

| Branch | Line | Verdict layer |
|---|---|---|
| `AudioRejected` from hygiene | `:409-421` | `voice:media_hygiene` |
| Any other transcription exception | `:422-436` | `voice:transcription` |
| `text_check is None` | `:440-448` | `voice:text_rails` |

The third is worth reading in full (`:444-446`):

> The transcript was never screened — no text rail stack was supplied, so speech would
> have reached the agent unguarded. Blocked (fail-closed).

The happy path (`:449-459`) does **not** re-implement the guard. It calls the media
package's own contract:

```python
verdict = await guard_audio(
    payload,
    transcriber=lambda _payload: transcription.text,
    text_check=text_check,
)
```

with the comment at `:450-453` explaining why: `guard_audio` owns the fail-closed
semantics and the `[transcript]` verdict prefix. The lambda is there because the
transcript already exists — this is reuse of the *contract*, not a second transcription.

Finally (`:467-470`) the reason line is regenerated from the coverage lists, so it
cannot overstate which controls ran.

---

## 4. Streaming — `aegis/src/aegis/voice/stream.py`

Three events over the shared `AegisEmitter` (`:6-13`):

- **`voice_chunk`** — once per chunk, carrying the running transcript
- **`voice_transcript`** — the finished transcription
- **`guardrail_media`** — the rail verdict

The docstring at `:15-18` explains why the verdict reuses the **media** event name
rather than inventing a voice one: *"because it is a media verdict: the console's
existing verdict renderer already understands it, and a second name would let the two
drift."*

**`transcript_event`** (`:39-74`) — note `hasConfidence` (`:49-51`): "when it is false
the console shows 'not reported' for every segment instead of a fabricated score."

**`verdict_event`** (`:77-97`) carries `railsRun`, `railsSkipped` and
`agentReady: result.agent_input is not None`.

**`stream_transcribe_and_guard`** (`:100-160`) brackets everything in
`STEP_STARTED/FINISHED("voice_transcribe", SpanKind.CHAIN)` and emits the verdict on
**every** path (`:114-116`), "so a blocked recording is *visible* in the stream rather
than being a stream that simply stops."

---

## 5. The gateway call — `aegis/src/aegis/gateway/llm.py:1126-1257`

This is where the hosted-model argument pays off. `transcribe()` does, in order:

**Budget check before spend** (`:1165-1170`):

```python
gov_ctx = _governance.get_context()
if gov_ctx is not None:
    # Budget/rate check BEFORE spend — identical, modality-agnostic gate to
    # the one ``complete`` applies; a voice call is spend like any other.
    await _governance.enforce(gov_ctx)
```

**Routing by role** (`:1172`) — `model_for(ModelRole.VOICE)`, which
`aegis/src/aegis/gateway/routing.py:40` maps to `genailab-maas-whisper`.

**An OTel span** (`:1175`) — `GenAIOperation.TRANSCRIPTION`.

**A file handle, not messages** (`:1176-1189`) — the comment at `:1179` flags it:
*"LiteLLM's transcription API takes a FILE HANDLE, not `messages`."* `_audio_handle`
(`:1079-1092`) accepts an open handle (left alone — whoever opened it owns closing it)
or a path (opened and closed here).

**A bounded call** (`:1191-1193`) — the `timeout` kwarg caps upstream and an outer
`asyncio.wait_for(..., timeout + 5.0)` is a hard backstop.

**Billing per audio-second** (`:1136-1145` in the docstring, `:1199-1242` in code):

> Billing is per minute of audio, not per token, so a transcription would otherwise
> ledger `prompt_tokens=0` → `$0.00` and slip past a USD cap entirely. The audio
> duration is therefore the billable unit: taken from the provider's own `duration`
> when it reports one (`verbose_json`), else from the caller's `duration_seconds`. If
> neither is available the cost is tagged `CostSource.UNPRICED` and logged — visible,
> never a silent zero.

`routing.py:174` declares `ModelRole.VOICE: BillingUnit.AUDIO_MINUTES`, and
`routing.py:148` carries the rate.

Then `record_call` (`:1223-1229`), `set_usage` on the span (`:1231-1236`) and
`_record_usage` into the governance ledger (`:1237-1244`) with `audio_seconds` carried
through.

**That whole list is the "hosted inherits the controls" argument, made concrete.** None
of it is code `aegis.voice` wrote.

---

## 6. Backend wiring — `backend/src/app/voice/`

The composition root. `backend/src/app/voice/__init__.py:1-21` states its job and, more
usefully, what it deliberately does *not* do:

> Nothing about the security ordering lives here. Transcribe-then-guard, and the
> fail-closed direction of every failure, are properties of `aegis.voice`; this package
> would have to go out of its way to break them.

**`read_upload`** (`backend/src/app/voice/service.py:50-77`) streams the multipart part
in 64 KiB blocks and raises `AudioTooLarge` the moment the running total passes the cap
— *"the point of a cap is to stop before the memory is spent"*. `MAX_UPLOAD_BYTES`
(`:31`) is `MediaLimits().max_bytes` so the transport cap and the hygiene cap agree.

**`transcribe_upload`** (`:80-113`) builds the payload with the declared content type
carried "**as a declaration, not as a fact**" (`:89-92`) and calls:

```python
return await transcribe_and_guard(payload, text_check=check_input, language=language)
```

with the comment at `:110-112`: `check_input` is *the same* stack the agent graph runs
on typed input, "which is what makes a spoken turn and a typed turn subject to one
policy rather than two that can drift."

**The route** — `backend/src/app/api/routes.py:2683-2786`. Multipart rather than base64,
with the reasoning at `:2691-2697`. And critically, `:2729-2740`:

```python
governance = await _resolve_governance(auth)
token = set_governance_context(governance)
try:
    result = await transcribe_upload(...)
finally:
    reset_governance_context(token)
```

That binding is what makes the gateway's budget check and ledger write actually fire.
The response separates `transcript` from `agent_input`, and carries `controls_run` /
`controls_skipped` verbatim.

---

## 7. The browser side — `web/src/components/voice/wav.ts`

`toWav` (`:98-120`) decodes the recording with `AudioContext.decodeAudioData`, downmixes
to mono (`:33-42`), and re-encodes as 16-bit PCM WAV (`:45-69`). The module docstring
(`:4-17`) gives both reasons:

1. `aegis.media` accepts wav/mp3/ogg/flac/m4a; Chrome's `MediaRecorder` produces
   **WebM/Opus**, which payload hygiene would correctly refuse.
2. The server chunks with the stdlib WAV parser, so *"a long recording only chunks if it
   arrives as PCM WAV."*

And the failure direction: `toWav` returns `null` when the browser cannot decode its own
recording, so the caller says so — *"it never silently uploads a container the server
will reject and calls that a transcription failure."*

---

## Where to look

| Claim | File:line |
|---|---|
| `agent_input` returns the rails' text, `None` on block | `aegis/src/aegis/voice/types.py:125-137` |
| Confidence never derived | `aegis/src/aegis/voice/types.py:9-16`, `transcribe.py:192-194` |
| Squared silence threshold | `aegis/src/aegis/voice/chunking.py:245-247` |
| 8-bit unsigned centring | `aegis/src/aegis/voice/chunking.py:328-331` |
| Unparseable container ⇒ whole, and says so | `aegis/src/aegis/voice/chunking.py:290-300` |
| Hygiene before spend | `aegis/src/aegis/voice/transcribe.py:239-241` |
| Three fail-closed branches | `aegis/src/aegis/voice/transcribe.py:409-448` |
| `make_transcriber` raises rather than returning "" | `aegis/src/aegis/voice/transcribe.py:333-337` |
| Budget enforced before spend | `aegis/src/aegis/gateway/llm.py:1165-1170` |
| Audio-seconds billing | `aegis/src/aegis/gateway/llm.py:1199-1244` |
| One policy for typed and spoken | `backend/src/app/voice/service.py:110-113` |
| Browser re-encodes to PCM WAV | `web/src/components/voice/wav.ts:4-17` |

**Next:** [`30-deep-dive.md`](30-deep-dive.md).
