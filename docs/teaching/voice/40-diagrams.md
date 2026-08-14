# Voice — the diagrams

If you can draw diagram 2 (the guarded path with all four fail-closed exits) and
diagram 4 (the silence splitter) on a whiteboard, you can hold a long conversation
about this module.

---

## 1. What this module owns, and what it borrows

```mermaid
flowchart TB
    subgraph VOICE["aegis.voice — thin by design"]
        CH["chunking.py<br/>silence splitter, pure stdlib"]
        TR["transcribe.py<br/>hygiene, chunk loop, the ordering"]
        TY["types.py<br/>VoiceResult, agent_input"]
        ST["stream.py<br/>AG-UI events"]
    end

    subgraph BORROWED["borrowed, not rebuilt"]
        MED["aegis.media<br/>payload hygiene"]
        GM["aegis.guardrails.media<br/>guard_audio contract"]
        GW["aegis.gateway.transcribe<br/>budget, ledger, tracing, routing"]
        RAILS["the host's full text rail stack"]
    end

    TR --> MED
    TR --> GM
    TR --> GW
    GM --> RAILS

    GW --> W["hosted Whisper<br/>ModelRole.VOICE"]

    VOICE -.->|"no torch, no ffmpeg,<br/>no local model"| POL(["fleet-only policy"])
```

**The point of the borrowing.** Routing speech through the gateway means transcription
is budget-enforced, ledgered per audio-second and traced — with no new code. A local
model would sit outside all of it.

---

## 2. The guarded path — every exit closes

```mermaid
flowchart TB
    A["AudioPayload"] --> H{"payload hygiene"}
    H -->|"refused"| X1["BLOCK<br/>layer voice:media_hygiene<br/><i>nothing transcribed, nothing spent</i>"]

    H -->|ok| P["plan_chunks"]
    P --> LOOP["for each chunk:<br/>budget check, transcribe, rebase timestamps"]

    LOOP --> E{"raised?"}
    E -->|yes| X2["BLOCK<br/>layer voice:transcription<br/><i>no transcript to judge</i>"]

    E -->|no| T["VoiceTranscription<br/><b>evidence, not agent input</b>"]

    T --> RC{"text_check supplied?"}
    RC -->|no| X3["BLOCK<br/>layer voice:text_rails<br/><i>speech would have reached<br/>the agent unguarded</i>"]

    RC -->|yes| G["guard_audio<br/>runs the FULL text rail stack<br/>over the transcript"]

    G --> V{"verdict"}
    V -->|BLOCK| X4["agent_input = None"]
    V -->|"PASS / REDACT / FLAG"| OK["agent_input = guard.text"]

    X1 --> COV
    X2 --> COV
    X3 --> COV
    X4 --> COV
    OK --> COV
    COV["reason regenerated from<br/>controls_run + controls_skipped"]
```

**Four exits, one direction.** Hygiene refusal, transcription error, missing rail stack
and a rail block all end in BLOCK. There is no path through this function that returns
agent-usable text the rails have not judged.

---

## 3. The two texts, and why only one may reach an agent

```mermaid
flowchart LR
    ASR["hosted Whisper"] --> RAW["transcription.text<br/><b>EVIDENCE</b><br/>the operator console,<br/>the audit trail"]

    RAW --> RAILS["the full text rail stack"]
    RAILS --> GT["guard.text<br/><b>AGENT INPUT</b><br/>redacted if the PII rail fired"]

    GT --> AI["agent_input<br/><i>computed property</i>"]
    RAILS -->|BLOCK| NONE["agent_input = None"]

    RAW -.->|"the bypass this<br/>module exists to prevent"| BAD["reading the raw transcript<br/>discards every redaction<br/>and every block"]
```

**Why a computed property and not a field.** A field could hold the unscreened string
and be read by mistake. A property that derives from `guard.text` never holds it at all
— so the wrong value is not merely discouraged, it is **absent**.

And `None`, not `""` — an empty string flows silently through concatenation and
formatting; `None` does not.

---

## 4. The silence splitter

```mermaid
flowchart TB
    W["WAV bytes"] --> PARSE{"stdlib wave can parse it?"}
    PARSE -->|no| WHOLE1["ONE chunk, splittable=false<br/>note names the container<br/><i>no ffmpeg, no local codec</i>"]
    PARSE -->|yes| FMT{"1, 2 or 4 bytes per sample?"}
    FMT -->|no| WHOLE2["ONE chunk, splittable=false<br/><i>24-bit has no array type code</i>"]

    FMT -->|yes| DUR{"duration <= ceiling?"}
    DUR -->|yes| WHOLE3["ONE chunk, splittable=TRUE<br/><i>did not NEED to split</i>"]

    DUR -->|no| DEC["decode samples<br/>8-bit? subtract the 128 midpoint"]
    DEC --> ENV["envelope: mean-square<br/>per 20 ms window"]
    ENV --> THR["threshold = mean(E) x ratio-SQUARED<br/><i>squared, and relative to this clip</i>"]
    THR --> WALK["walk the recording"]

    WALK --> TGT["target = cursor + max_chunk"]
    TGT --> SRCH["search BACKWARDS to<br/>max(cursor+min, target-search)"]
    SRCH --> RUN{"a sub-threshold run<br/>long enough to be a pause?"}
    RUN -->|yes| CUT1["cut at its CENTRE<br/>split_on_silence = true"]
    RUN -->|no| CUT2["cut on time<br/>split_on_silence = FALSE"]

    CUT1 --> MORE{"more than one chunk left?"}
    CUT2 --> MORE
    MORE -->|yes| TGT
    MORE -->|no| ENC["re-encode each slice<br/>as a standalone WAV"]
```

**Three details that make it work.** The threshold is **squared**, because the envelope
is mean-square and the ratio is an amplitude ratio — forget it and everything looks like
silence. The threshold is **relative to this clip**, because levels vary by orders of
magnitude between a headset and a laptop mic. And the search runs **backwards** from the
target, so no chunk can exceed the ceiling.

---

## 5. "One chunk" is two different outcomes

```mermaid
flowchart LR
    ONE["len(chunks) == 1"] --> Q{"splittable?"}
    Q -->|true| GOOD["short recording<br/><i>we did not need to split</i>"]
    Q -->|false| BAD["unparseable container,<br/>or an unsupported sample format<br/><i>we could NOT split</i>"]

    BAD --> RISK["a 40-minute MP3 went up<br/>as ONE request"]
    GOOD --> FINE["a 30-second note<br/>went up as one request"]

    ONE --> NOTE["ChunkPlan.note carries the prose,<br/>into VoiceTranscription.chunking,<br/>onto the console"]
```

A caller counting chunks cannot tell these apart. That is why the flag and the prose
note both exist.

---

## 6. Chunk timelines are rebased onto the recording

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

    P0 --> M0["+0s, index 0..n"]
    P1 --> M1["<b>+118s</b>, index n+1.."]
    P2 --> M2["<b>+241s</b>, index ..."]

    M0 --> T["one transcript,<br/>on the RECORDING's timeline"]
    M1 --> T
    M2 --> T

    P1 -.->|"forget the offset"| WRONG["every timestamp after chunk 0<br/>is wrong; click-to-seek<br/>lands in the wrong place"]
```

---

## 7. Billing — why the unit has to be in the routing table

```mermaid
flowchart TB
    CALL["a transcription call"] --> TOK{"count tokens?"}
    TOK -->|"the naive path"| ZERO["prompt_tokens = 0<br/>cost = $0.00"]
    ZERO --> CAP["USD cap never binds<br/><i>and nothing looks broken</i>"]

    CALL --> UNIT["BillingUnit.AUDIO_MINUTES<br/><i>declared in the routing table</i>"]
    UNIT --> DUR{"duration known?"}
    DUR -->|"provider reported it<br/>(verbose_json)"| BILL["cost = minutes x rate<br/>ledgered, cap binds"]
    DUR -->|"caller supplied it"| BILL
    DUR -->|neither| UNP["CostSource.UNPRICED<br/>+ a log line<br/><i>billable work nobody could price</i>"]

    UNP -.->|"NOT the same claim as"| FREE["a genuine $0.00"]
```

**The generalisable check:** when you add a modality, ask whether its billing unit is
the one your budget system counts. Images-per-call and audio-seconds are both ways for
spend to become invisible.

---

## 8. The streaming view

```mermaid
flowchart TB
    S["STEP_STARTED(voice_transcribe, CHAIN)"] --> L["for each chunk"]
    L --> CE["CUSTOM(voice_chunk)<br/>index, offset, splitOnSilence,<br/>the RUNNING transcript"]
    CE --> L
    L --> TE["CUSTOM(voice_transcript)<br/>segments, language, duration,<br/>chunking note, cost, hasConfidence"]
    TE --> VE["CUSTOM(guardrail_media)<br/>verdict, layer, redactions,<br/>railsRun, railsSkipped, agentReady"]
    VE --> F["STEP_FINISHED(voice_transcribe)"]

    VE -.->|"emitted on EVERY path,<br/>including the blocked ones"| VIS(["a blocked recording is visible,<br/>not a stream that simply stops"])
```

The verdict reuses the **media** event name rather than inventing a voice one — it *is*
a media verdict, the console's renderer already understands it, and a second name would
let the two drift.

---

## 9. End to end, browser to agent

```mermaid
sequenceDiagram
    participant B as Browser
    participant R as POST /voice/transcribe
    participant V as aegis.voice
    participant G as aegis.gateway
    participant RL as the text rails

    B->>B: MediaRecorder produces WebM/Opus
    B->>B: decodeAudioData then re-encode<br/>as 16-bit PCM WAV
    B->>R: multipart upload

    R->>R: read_upload — streamed, abandoned at the cap
    R->>R: bind governance context (tenant, user, caps)
    R->>V: transcribe_and_guard(payload, text_check=check_input)

    V->>V: inspect_payload — sniff, size, before any spend
    V->>V: plan_chunks

    loop per chunk
        V->>G: transcribe(handle, verbose_json, duration)
        G->>G: enforce budget BEFORE spend
        G->>G: OTel span, ledger row, audio-seconds
        G-->>V: text, segments, language, usage
    end

    V->>RL: the whole transcript
    RL-->>V: verdict, possibly redacted text
    V-->>R: VoiceResult (transcript + verdict + coverage)
    R-->>B: transcript AND agent_input, separately
```

**Why the browser re-encodes.** Chrome's `MediaRecorder` emits WebM/Opus, which payload
hygiene would correctly refuse — and the server's chunker is built on the stdlib WAV
parser, so a long recording only chunks if it arrives as PCM WAV.

**Why the response separates `transcript` from `agent_input`.** They are different
things. One is evidence; one is what an agent may consume.

---

**Next:** [`50-interview.md`](50-interview.md).
