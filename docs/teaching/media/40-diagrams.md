# Media — the diagrams

Every path a non-text payload can take. If you can draw diagram 2 (hygiene) and
diagram 4 (the image chain) from memory, you can talk about this module for twenty
minutes.

---

## 1. The two packages, and why they are two

```mermaid
flowchart TB
    subgraph FACTS["aegis.media — facts about bytes"]
        T["types.py<br/>payload union, provenance"]
        S["sniff.py<br/>magic bytes, header dimensions"]
        H["hygiene.py<br/>size, MIME truth, bomb guard"]
    end

    subgraph POLICY["aegis.guardrails.media — policy"]
        AD["adapt.py<br/>call_rail, the widened contract"]
        IN["injection.py<br/>the image screen"]
        PI["image_pii.py<br/>redact and return pixels"]
        AU["audio.py<br/>transcribe then guard"]
        SC["screen.py<br/>the ordered chain"]
    end

    T --> S
    S --> H
    H --> SC
    T --> AD
    IN --> SC
    PI --> SC
    AU --> SC

    SC --> G["aegis.guardrails.Guardrails"]

    FACTS -.->|"pydantic + stdlib only"| CHEAP(["import cost = aegis.core"])
```

**The split is the point.** `aegis.media` holds no policy — only facts. That is what
keeps it importable with no codec, no model client and no network library.

---

## 2. Payload hygiene — the cheap, offline gate

```mermaid
flowchart TB
    P["payload arrives"] --> INLINE{"bytes in hand?"}

    INLINE -->|"no, and it is text"| TOK(["ok — upstream resolves it"])
    INLINE -->|"no, and it is image or audio"| NOTINSP["BLOCK<br/>uri_not_inspectable"]
    INLINE -->|yes| EMPTY{"zero bytes?"}

    EMPTY -->|yes| EMP["BLOCK<br/>empty_payload"]
    EMPTY -->|no| SNIFF["sniff_mime — read magic bytes"]

    SNIFF --> DIMS["image_dimensions — read the header<br/>NO pixels decoded"]

    DIMS --> C1["size cap<br/>8 MiB binary / 256 KiB text"]
    DIMS --> C2["MIME truth<br/>declared vs sniffed"]
    DIMS --> C3["bomb guard<br/>pixels and pixels-per-byte"]

    C1 --> AGG["collect ALL failures"]
    C2 --> AGG
    C3 --> AGG

    AGG --> OK{"any failures?"}
    OK -->|no| PASS(["HygieneReport ok=True"])
    OK -->|yes| BLK["HygieneReport ok=False<br/>every code and detail"]
```

**Three things to notice.** The URI branch is *asymmetric* — text may be a reference,
an image may not, because unscreenable pixels are the hole this module closes. Failures
are **collected, not short-circuited**, so an operator debugging a rejected upload sees
the whole picture. And nothing here calls a model, so a hostile payload is refused
before it costs a cent.

---

## 3. MIME truth — why the declared type is only ever a comparand

```mermaid
flowchart TB
    D["declared mime_type<br/>attacker-controlled"] --> CMP{"compare"}
    B["payload bytes"] --> SN["sniff_mime"] --> CMP

    SN --> NONE{"sniffed is None?"}
    NONE -->|yes| UNK["BLOCK — mime_unrecognized<br/>a refusal to guess"]

    CMP --> KIND{"payload kind"}
    KIND -->|text| FAM{"sniffed starts with text/ ?"}
    KIND -->|image or audio| EXACT{"sniffed == declared ?"}

    FAM -->|no| MM1["BLOCK — mime_mismatch"]
    FAM -->|yes| OKT(["text accepted"])

    EXACT -->|no| MM2["BLOCK — mime_mismatch"]
    EXACT -->|yes| FAM2{"right family?"}
    FAM2 -->|no| MM3["BLOCK — mime_mismatch"]
    FAM2 -->|yes| ALLOW{"in the allowlist?"}
    ALLOW -->|no| NA["BLOCK — mime_not_allowed"]
    ALLOW -->|yes| OKB(["binary accepted"])
```

**Text gets a family check, binary gets an exact one.** Magic bytes cannot tell
`text/plain` from `text/markdown`, so asserting an exact match there would be a lie.
For binary they can, so anything less would be a gap.

---

## 4. The image chain — order is the product

```mermaid
flowchart TB
    IMG["ImagePayload"] --> HY{"hygiene ok?"}
    HY -->|no| B1["BLOCK<br/>layer = media_hygiene<br/>every downstream rail marked skipped"]

    HY -->|yes| PII{"image-PII enabled?"}
    PII -->|no| SKIPPII["record skip:<br/>not enabled, install aegis&#91;media&#93;"]
    PII -->|yes| RED["redact_image<br/>OCR, paint boxes, re-encode PNG<br/>returns a NEW payload"]

    SKIPPII --> SCR
    RED --> SCR

    SCR{"vision completer wired?"}
    SCR -->|no| FC["screened=false, injection=true<br/>BLOCK — no offline backstop for pixels"]
    SCR -->|yes| CALL["cheap vision screen<br/>same data URL the model would see"]

    CALL --> PARSE{"reply parses?"}
    PARSE -->|"no"| FCB["injection=true<br/>ambiguity is never a pass"]
    PARSE -->|yes| VERD{"injection?"}

    VERD -->|yes| B2["BLOCK<br/>layer = media_injection"]
    VERD -->|no| CUST["custom rails<br/>via call_rail"]

    FC --> B2
    FCB --> B2

    CUST --> ENT{"PII entities found earlier?"}
    ENT -->|yes| RD["REDACT<br/>media = the redacted payload<br/>forward THAT, not the original"]
    ENT -->|no| OKI["PASS + coverage sentence"]
```

**The two edges that matter.** `SCR --> FC`: no completer is not a degraded mode, it is
*no control*, so it blocks — and `screened=false` records that the block was caused by
the control being unavailable rather than by anything found in the image.
`ENT --> RD`: a `REDACT` on a binary must hand back **pixels**, or the caller is still
holding the original with the passport number in it.

---

## 5. Audio — transcribe, then the whole text stack

```mermaid
flowchart TB
    A["AudioPayload"] --> HY{"hygiene ok?"}
    HY -->|no| B1["BLOCK — nothing transcribed, nothing spent"]
    HY -->|yes| TR{"transcriber wired?"}

    TR -->|no| B2["BLOCK<br/>the text rails never saw this payload"]
    TR -->|yes| CALL["transcribe"]

    CALL --> ERR{"raised?"}
    ERR -->|yes| B3["BLOCK<br/>no transcript for the rails to judge"]
    ERR -->|no| TEXT["transcript, a plain str"]

    TEXT --> STACK["the FULL text rail stack<br/>signatures, injection classifier,<br/>content safety, PII, schema, topical,<br/>and every custom rail"]

    STACK --> V["verdict, with layer prefixed media_audio:<br/>and reason prefixed &#91;transcript&#93;"]
```

**Why not a parallel audio policy?** Because it would be weaker than the text rails,
and the two would drift the first time someone updated one and not the other. Every
attack that works typed works spoken, so reuse the mature stack unchanged.

**Why no recursion?** `text_check` is `Guardrails.check_input`, and a transcript is a
`str` — so it takes the text branch. The media chain is entered exactly once.

---

## 6. `call_rail` — how the contract widened without breaking

```mermaid
flowchart TB
    R["a custom rail"] --> M{"marked @media_rail?"}
    M -->|yes| NEW["rail(payload)"]
    M -->|no| ANN{"first param annotated<br/>with a payload type?"}

    ANN -->|yes| NEW
    ANN -->|"no, or no signature at all"| LEG{"is the payload text?"}

    LEG -->|yes| OLD["rail(payload.text)<br/>byte-for-byte the old behaviour"]
    LEG -->|"no — image or audio"| SKIP["do NOT call it<br/>log a warning<br/>on_skip(reason)<br/>return None"]

    SKIP --> REC["the reason lands in<br/>MediaGuardResult.rails_skipped"]
```

**Annotations are compared as strings**, never resolved — `from __future__ import
annotations` makes them strings already, and resolving would mean importing the
caller's namespace during a guardrail check.

**The skip is recorded, not swallowed.** A rail that did not run must never be counted
among the rails that did.

---

## 7. Where the verdict sentence comes from

```mermaid
flowchart LR
    RUN["rails_run<br/>hygiene, image-PII redaction,<br/>image-injection screen"] --> COV["coverage()"]
    SKIP["rails_skipped<br/>image content-safety (not implemented),<br/>custom rail 'x' (takes a str)"] --> COV

    COV --> SENT["Rails run: ... . Not run: ... ."]
    SENT --> REASON["appended to GuardResult.reason"]
    REASON --> UI["trace panel + audit log"]

    HAND["a hand-written sentence<br/>listing the INTENDED chain"] -.->|"the bug this replaces"| WRONG["claims coverage<br/>a disabled stage never provided"]
```

If the sentence is **generated from the lists**, a rail that did not run cannot appear
in it. That is a structural guarantee, not a discipline someone has to remember.

---

## 8. Routing — where a payload enters the guardrails

```mermaid
flowchart TB
    IN["check_input(text)"] --> AS["as_payload()<br/>a bare str becomes TextPayload"]
    AS --> K{"kind"}

    K -->|TEXT| TXT["the unchanged text path<br/>schema, PII, injection,<br/>content safety, topical, custom"]
    K -->|"IMAGE or AUDIO"| MED["_screen_media<br/>MediaScreen.check"]

    MED --> EV["emit CustomEvent(guardrail_media)<br/>kind, mime, size, provenance,<br/>verdict, rails_run, rails_skipped"]
    MED --> RES["MediaGuardResult"]

    TXT --> RES2["GuardResult"]

    EV -.->|"never the bytes,<br/>never the decoded text"| SAFE(["safe to log and render"])
```

A `str` caller is handed **straight** to the text path with no encode/decode round-trip
— so the rails screen the exact string given, not a re-derived copy of it.

---

**Next:** [`50-interview.md`](50-interview.md).
