# Voice — the diagrams

Four diagrams. The two worth reproducing from memory are **the guarded path**, with all four
fail-closed exits and the bypass it exists to prevent, and **the silence splitter**.

Everything else about this module is explained in [`10-guide.md`](10-guide.md); a picture is only
here when it shows something prose cannot.

---

## 1. The guarded path — every exit closes

*Look at the dotted edge off `VoiceTranscription`. That is the one-attribute bypass.*

```mermaid
flowchart TB
    A["AudioPayload"] --> H{"payload hygiene"}
    H -->|refused| X1["BLOCK, layer voice:media_hygiene<br/><i>nothing transcribed, nothing spent</i>"]

    H -->|ok| P["plan_chunks"]
    P --> LOOP["for each chunk, in order:<br/>a gateway call that enforces the budget<br/>before it spends, then rebase the timestamps"]

    LOOP --> E{"raised?"}
    E -->|yes| X2["BLOCK, layer voice:transcription<br/><i>no transcript to judge</i>"]

    E -->|no| T["VoiceTranscription<br/><b>evidence, not agent input</b>"]

    T --> RC{"text_check supplied?"}
    RC -->|no| X3["BLOCK, layer voice:text_rails<br/><i>speech would have reached<br/>the agent unguarded</i>"]

    RC -->|yes| G["guard_audio<br/>the FULL text rail stack,<br/>run over the transcript"]

    G --> V{"verdict"}
    V -->|BLOCK| X4["agent_input = None"]
    V -->|"PASS, REDACT or FLAG"| OK["agent_input = guard.text"]

    X1 --> COV
    X2 --> COV
    X3 --> COV
    X4 --> COV
    OK --> COV
    COV(["the reason line is regenerated from<br/>controls_run and controls_skipped"])

    T -.->|"reading transcription.text instead"| BAD["<b>the bypass</b><br/>discards every redaction<br/>and every block"]
```

**Four exits, one direction.** Hygiene refusal, transcription error, a missing rail stack and a
rail block all end in BLOCK. No path through this function returns agent-usable text the rails
have not judged.

The dotted edge is why `agent_input` is a **computed property** rather than a field: a field could
hold the unscreened string and be read by mistake, and a property derived from `guard.text` never
holds it at all. It returns `None` on a block, not `""` — an empty string flows silently through
concatenation and formatting.

Hygiene runs before the first paid call, which is why the test asserts the transcriber was never
called rather than merely that the verdict was BLOCK.

---

## 2. The silence splitter

*Look at the search direction, and at the two places a "one chunk" plan comes from.*

```mermaid
flowchart TB
    W["WAV bytes"] --> PARSE{"the stdlib wave module<br/>can parse it?"}
    PARSE -->|no| WHOLE1["ONE chunk, splittable=false<br/>the note names the container<br/><i>no ffmpeg, no local codec</i>"]
    PARSE -->|yes| FMT{"1, 2 or 4 bytes per sample?"}
    FMT -->|no| WHOLE2["ONE chunk, splittable=false<br/><i>24-bit has no array type code</i>"]

    FMT -->|yes| DUR{"duration within the<br/>120s ceiling?"}
    DUR -->|yes| WHOLE3["ONE chunk, splittable=<b>TRUE</b><br/><i>we did not NEED to split</i>"]

    DUR -->|no| DEC["decode the samples<br/>8-bit? subtract the 128 midpoint"]
    DEC --> ENV["envelope: mean-square<br/>per 20 ms window"]
    ENV --> THR["threshold = mean of the envelope<br/>x the ratio <b>SQUARED</b>"]
    THR --> TGT["target = cursor + max_chunk"]

    TGT --> SRCH["search <b>BACKWARDS</b> from the target,<br/>as far as max of cursor+min<br/>and target-search"]
    SRCH --> RUN{"a sub-threshold run long<br/>enough to be a pause?"}
    RUN -->|yes| CUT1["cut at its CENTRE<br/>split_on_silence = true"]
    RUN -->|no| CUT2["cut on time<br/>split_on_silence = <b>FALSE</b>"]

    CUT1 --> MORE{"more than max_chunk<br/>of audio left?"}
    CUT2 --> MORE
    MORE -->|yes| TGT
    MORE -->|no| ENC["re-encode each slice<br/>as a standalone WAV"]
```

**Three details make it work.** The threshold is *squared*, because the envelope is mean-square
while the ratio is an amplitude ratio — forget it and the threshold is 5.6x too generous, so
ordinary quiet speech reads as silence and cuts land mid-word. The threshold is *relative to this
clip*, because levels vary by orders of magnitude between a headset and a laptop mic. And the
search runs *backwards*, so no chunk can exceed the ceiling.

Both single-chunk outcomes produce `len(chunks) == 1`, and they carry very different risk: a
30-second note correctly sent as one request, or a 40-minute MP3 that could not be split at all.
That is why there is a `splittable` flag *and* a prose note.

---

## 3. Chunk timelines are rebased onto the recording

*Look at the two offsets. Drop them and every timestamp after chunk 0 is wrong.*

Offsets below are illustrative — where the cuts land depends on where the pauses are.

```mermaid
flowchart TB
    subgraph REC["the recording"]
        C0["chunk 0<br/>0-118s"]
        C1["chunk 1<br/>118-241s"]
        C2["chunk 2<br/>241-300s"]
    end

    C0 --> P0["provider segments<br/>start 0.0, 4.2, 9.8 ..."]
    C1 --> P1["provider segments<br/>start 0.0, 3.1 ...<br/><i>the provider only saw this chunk</i>"]
    C2 --> P2["provider segments<br/>start 0.0, 5.5 ..."]

    P0 --> M0["+0s, indices 0..n"]
    P1 --> M1["<b>+118s</b>, indices n+1.."]
    P2 --> M2["<b>+241s</b>, indices ..."]

    M0 --> T
    M1 --> T
    M2 --> T
    T(["one transcript, on the<br/><b>RECORDING's</b> timeline"])
```

Each chunk is a separate request, so the provider restarts its clock at zero every time.
Concatenating without adding the offset back leaves click-to-seek landing further and further
from the right moment as the recording goes on.

Segment indices are renumbered transcript-wide for the same reason — the provider's are
per-request. A test asserts both: the indices form an unbroken range, and segment starts only ever
increase.

---

## 4. End to end, browser to agent

*Look at the last line. Two strings come back, and only one may be forwarded.*

```mermaid
sequenceDiagram
    participant B as Browser
    participant R as POST /voice/transcribe
    participant V as aegis.voice
    participant G as aegis.gateway
    participant RL as the text rails

    B->>B: MediaRecorder produces WebM/Opus
    B->>B: decodeAudioData, then re-encode<br/>as 16-bit PCM WAV
    B->>R: multipart upload

    R->>R: read_upload — streamed, abandoned at the cap
    R->>R: bind the governance context: tenant, user, caps
    R->>V: transcribe_and_guard with text_check=check_input

    V->>V: inspect_payload — sniff and size, before any spend
    V->>V: plan_chunks

    loop per chunk, in order
        V->>G: transcribe, verbose_json, duration
        G->>G: enforce the budget BEFORE the spend
        G->>G: OTel span, ledger row, audio-seconds
        G-->>V: text, segments, language, usage
    end

    V->>RL: the whole transcript
    RL-->>V: verdict, possibly redacted text
    V-->>R: VoiceResult — transcript, verdict, coverage
    R-->>B: transcript AND agent_input, as separate fields
```

**Why the browser re-encodes.** Chrome's `MediaRecorder` emits WebM/Opus, which payload hygiene
would correctly refuse, and the server's chunker is built on the stdlib WAV parser — so a long
recording only chunks at all if it arrives as PCM WAV.

**Why the chunks are sequential.** Chunk *k+1* is checked against a balance that already includes
chunk *k*'s spend. Concurrent calls would each see the pre-spend balance and could collectively
blow past the cap.

**Why the response separates the two fields.** They are different things: one is evidence, one is
what an agent may consume. A client that forwards `transcript` has bypassed the rails.

**Next:** [`50-interview.md`](50-interview.md).
