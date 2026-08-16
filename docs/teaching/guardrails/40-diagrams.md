# Guardrails — the diagrams

Five diagrams. The two worth reproducing from memory are **the input chain** and **the media
chain** — between them they carry most of a long conversation about this module.

Everything else is explained in [`10-guide.md`](10-guide.md); a picture is only here when it
shows something prose cannot. Detector lists, fail directions and layer comparisons are
tables in the guide, not diagrams.

---

## 1. Where the rails sit in a request

*Look at the dotted edge — that is the attack the input rail cannot see.*

```mermaid
flowchart LR
    U["user input<br/><i>text · image · audio</i>"] --> GI["<b>input rails</b><br/>6 layers"]
    GI -->|block| END1([refused<br/><i>nothing downstream runs</i>])
    GI -->|"pass / redact / flag"| AGENT["retrieve → plan → act → generate"]
    AGENT --> GO["<b>output rails</b><br/>6 layers"]
    GO -->|block| WITHHELD([answer withheld])
    GO -->|"pass / redact / flag"| STREAM["stream to client<br/><i>already guarded</i>"]

    RET[["retrieved documents"]] -.->|"indirect injection<br/>enters HERE, not at the door"| AGENT
    RET -.->|"defended structurally,<br/>by spotlighting"| AGENT
```

A blocked input goes **straight to END** — the router never runs and nothing downstream
executes.

The dotted edge is why the input rail is not the whole story: content that entered through
retrieval never passed the front door. Its defence is spotlighting, not a rail.

---

## 2. The input chain

*Look at the position of layer 2 — everything after it is a model call.*

```mermaid
flowchart TB
    IN["inbound text"] --> SCH{"<b>1. schema</b><br/>empty? over 8000 chars?<br/>invisible characters?"}
    SCH -->|fail| B1([BLOCK<br/>layer=schema])
    SCH -->|ok| PII["<b>2. PII redact</b><br/>Presidio, regex fallback"]

    PII --> INJ{"<b>3. injection</b>"}
    INJ -->|injection| B2([BLOCK<br/>layer=injection])
    INJ -->|clean| CS{"<b>4. content safety</b><br/>MLCommons S1–S13"}
    CS -->|unsafe| B3([BLOCK<br/>layer=content_safety])
    CS -->|safe| TOP{"<b>5. topical</b><br/>configured?"}

    TOP -->|"off topic + block mode"| B4([BLOCK<br/>layer=topical])
    TOP -->|"off topic + advisory"| ADV["collect FLAG"]
    TOP -->|"on topic / off"| CUS
    ADV --> CUS{"<b>6. custom rails</b>"}

    CUS -->|non-PASS| B5([that rail's verdict])
    CUS -->|clean| K{"PII kinds found?"}
    K -->|yes| R([REDACT<br/>text = masked])
    K -->|no| P{"advisory<br/>collected?"}
    P -->|yes| F([FLAG<br/>request proceeds])
    P -->|no| PASS([PASS])
```

Layers 3, 4 and 5 are three requests to a third-party model. Swapping 2 and 3 would send the
user's credit-card number to the classifier — the disclosure the rail exists to prevent.

Only BLOCK stops the request. A FLAG is emitted and the run continues.

---

## 3. Inside the injection rail

*Look at the three edges ending in "fail closed" — those are the fixed bug.*

```mermaid
flowchart TB
    T["text — already PII-redacted"] --> V["build the comparison views"]

    V --> V1["<b>fold</b><br/>strip invisibles → NFKC<br/>→ drop marks → collapse ws"]
    V --> V2["<b>deconfuse</b><br/>the fold + Cyrillic/Greek → ASCII"]
    V --> V3["<b>base64 payloads</b><br/>decode runs, fold each<br/><i>max 12</i>"]

    V1 --> M{"any signature match?"}
    V2 --> M
    V3 --> M

    M -->|yes| HIT(["BLOCK — deterministic<br/><i>free, offline, never cached</i>"])
    M -->|no| C{"completer<br/>configured?"}

    C -->|no| OFF([PASS<br/><i>model layer explicitly<br/>disabled + logged</i>])
    C -->|yes| CACHE{"cache hit on<br/>sha256(text)?"}

    CACHE -->|hit| CV([cached verdict])
    CACHE -->|"miss / read error"| LLM["cheap model call<br/>JSON mode"]

    LLM -->|"call raised"| FC([BLOCK — fail closed])
    LLM --> PARSE{"parse the verdict"}
    PARSE -->|"clear true"| BLK([BLOCK])
    PARSE -->|"clear false"| OK([PASS + cache])
    PARSE -->|"ambiguous or<br/>contradictory"| FC2([BLOCK — fail closed])
```

The deterministic hit returns before the cache is ever consulted: caching a free, offline
decision buys nothing and adds somewhere for a stale answer to live.

The `ambiguous → BLOCK` edge used to be `startswith("no") → PASS`, which read *"No doubt this
is a prompt injection attempt"* as benign.

---

## 4. The output chain

*Look at where PII sits — last here, second on the way in.*

```mermaid
flowchart TB
    A["generated answer<br/><i>complete, not streaming</i>"] --> S{"<b>1. schema</b><br/>over 20000 chars?<br/>invisible characters?"}
    S -->|fail| B1([BLOCK])
    S -->|ok| CF{"<b>2. content filter</b><br/>denylist markers"}
    CF -->|hit| B2([BLOCK<br/>system-prompt leak])
    CF -->|clean| CS{"<b>3. content safety</b>"}
    CS -->|unsafe| B3([BLOCK])
    CS -->|safe| CU{"<b>4. custom rails</b>"}
    CU -->|non-PASS| B4([that verdict])
    CU -->|clean| G{"<b>5. grounding</b><br/>contexts supplied?"}

    G -->|"ungrounded + block mode"| B5([BLOCK])
    G -->|"ungrounded + advisory"| KEEP["hold the FLAG"]
    G -->|"grounded / off"| PIIR
    KEEP --> PIIR["<b>6. PII redact</b>"]

    PIIR --> KIND{"kinds found?"}
    KIND -->|yes| RED([REDACT<br/><i>delivered, masked</i>])
    KIND -->|no| HELD{"FLAG held?"}
    HELD -->|yes| FL([FLAG])
    HELD -->|no| PASS([PASS])
```

On the input path you redact early to protect the user's data from your own model calls. Here
there are no downstream model calls, so redaction is the last transform — applied to text
that has already survived every block decision.

The verdict is REDACT, not BLOCK: the answer is delivered with the address masked, not
withheld.

---

## 5. The media chain

*Look at the two different BLOCK reasons for an image — they are not the same fact.*

```mermaid
flowchart TB
    P["payload"] --> K{"kind?"}
    K -->|text| TEXTPATH["the text pipeline<br/><i>the media chain RAISES on text</i>"]
    K -->|audio| AU
    K -->|image| IM

    AU["<b>audio</b>"] --> AH{"hygiene"}
    AH -->|fail| AB([BLOCK])
    AH -->|ok| TR{"transcriber<br/>wired?"}
    TR -->|no| ABC([BLOCK — fail closed])
    TR -->|yes| TT["transcribe"]
    TT --> FULL[["the FULL text rail stack<br/>runs on the transcript"]]

    IM["<b>image</b>"] --> IH{"hygiene<br/>size · MIME truth · bomb guard"}
    IH -->|fail| IB([BLOCK])
    IH -->|ok| IP["image-PII redaction<br/><i>if enabled</i>"]
    IP --> SC{"inline bytes?<br/>vision completer?"}
    SC -->|"bare URI"| UB([BLOCK<br/>screened=false<br/><i>bytes never held</i>])
    SC -->|"no completer"| SB([BLOCK<br/>screened=false<br/><i>no control at all</i>])
    SC -->|yes| VS["cheap vision screen"]
    VS -->|injection| VB([BLOCK<br/>screened=true])
    VS -->|clean| CR["custom rails"]
    CR --> OUT([verdict + rails_run + rails_skipped])
```

`screened=false` means the control could not run; `screened=true` means it ran and found
something. Collapsing them into one "blocked" loses what an operator needs to act on;
collapsing the second into "passed" is how a fail-open ships.

Audio has no chain of its own. It becomes text and runs the whole stack, so every rail an
operator configured — including their custom ones — applies to speech unchanged.

Every image verdict leaves the chain carrying `rails_skipped`, which always names the missing
pixel-level content-safety screen. A rail that did not run is never counted among the rails
that did.

**Next:** [`50-interview.md`](50-interview.md) — the questions you'll be asked.
