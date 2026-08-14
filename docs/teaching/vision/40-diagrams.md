# Vision — the diagrams

Diagram 2 is the one to know by heart. If you can draw the five stages with their exits
and say what each `None` dependency does, you own this module.

---

## 1. Why the ordering exists — the attack

```mermaid
flowchart LR
    A["attacker renders text<br/>into an image"] --> IMG["a normal-looking screenshot<br/>white-on-white instruction"]

    IMG --> H{"a human reviews it"}
    H --> HSEE["sees a picture of an invoice<br/><i>nothing looks wrong</i>"]

    IMG --> TR{"the TEXT rails"}
    TR --> NEVER["never consulted —<br/>they cannot receive an image"]

    IMG --> VLM["the vision model"]
    VLM --> READ["reads the text perfectly<br/><i>that is the capability you paid for</i>"]
    READ --> OBEY["may simply obey —<br/>projected pixels and text tokens<br/>are the same kind of thing"]
```

**Three reasons this is worse than text injection.** Invisible to human review. Passes
every text rail without touching one — they do not fail, they are never consulted. And
the carrier is innocuous: "users can upload screenshots" is a normal requirement nobody
flags.

---

## 2. The pipeline — the order IS the product

```mermaid
flowchart TB
    P["ImagePayload + question"] --> H{"1 · payload hygiene<br/><i>free, offline</i>"}
    H -->|refused| B1["BLOCKED at hygiene<br/>stages 2-5 marked NOT_RUN"]

    H -->|ok| S{"2 · injection screen<br/><b>before the answering model</b>"}
    S -->|"no completer"| FC["screened=FALSE<br/>FAILED_CLOSED"]
    S -->|"call raised"| FC
    S -->|"reply unparseable"| FC
    S -->|"injection found"| B2["BLOCKED at the screen<br/>screened=true"]
    FC --> B2

    S -->|clear| PII{"3 · image PII<br/><i>opt-in</i>"}
    PII -->|"not enabled"| NR["NOT_RUN + the install command"]
    PII -->|"ImportError"| RAISE["RE-RAISED<br/><i>a deployment fault, not a verdict</i>"]
    PII -->|"other error"| B3["FAILED_CLOSED, BLOCKED"]
    PII -->|"scanned"| RED["redacted payload + boxes"]

    NR --> M
    RED --> M

    M{"4 · the vision model"}
    M -->|"no analyst"| B4["FAILED_CLOSED<br/><i>a refusal, not an empty answer</i>"]
    M -->|"call raised"| B5["FAILED_CLOSED"]
    M -->|answered| OR{"5 · text output rails"}

    OR -->|"none wired"| NR2["NOT_RUN<br/>answer returned, coverage says unscreened"]
    OR -->|"rails raised"| B6["FAILED_CLOSED<br/>answer WITHHELD"]
    OR -->|BLOCK| B7["BLOCKED"]
    OR -->|"REDACT / PASS"| ANS["ANSWERED<br/>with the rails' text, not the raw answer"]
```

**Every arrow into a block also fills in `NOT_RUN` for every later stage**, so a refused
run still lists all five controls with reasons. A reader never infers coverage from a
missing entry.

---

## 3. Proving the ordering

```mermaid
flowchart TB
    subgraph WRONG["screen AFTER the model"]
        W1["call the vision model"] --> W2["get the answer"]
        W2 --> W3["screen the image"]
        W3 --> W4["suppress the answer if flagged"]
    end

    subgraph RIGHT["screen BEFORE the model"]
        R1["screen the image"] --> R2{"flagged?"}
        R2 -->|yes| R3["refuse — the model is never called"]
        R2 -->|no| R4["call the vision model"]
    end

    W4 --> SAME["identical verdict<br/>identical UI<br/>identical outcome assertions"]
    R3 --> SAME

    SAME --> DIFF["but: you PAID,<br/>the image is in provider logs,<br/>any tools already ran,<br/>the injection had its chance"]

    DIFF --> TEST["assert analyst.calls == &#91;&#93;"]
    TEST --> ONLY["the ONLY assertion that separates them"]
```

When the claim is about ordering or non-occurrence, **assert on the thing that should not
have happened.** Asserting on the outcome tests nothing, because both designs produce the
same outcome.

---

## 4. The screen, and its four fail-closed exits

```mermaid
flowchart TB
    I["ImagePayload"] --> INL{"bytes in hand?"}
    INL -->|"no — a bare URI"| F1["injection=true, screened=FALSE<br/><i>what a model fetches later<br/>is not what was screened</i>"]

    INL -->|yes| C{"vision completer wired?"}
    C -->|no| F2["injection=true, screened=FALSE<br/><i>no offline backstop for pixels</i>"]

    C -->|yes| CALL["cheap vision call<br/>SAME data URL the model gets<br/>strict-JSON response format"]

    CALL --> ERR{"raised?"}
    ERR -->|yes| F3["injection=true<br/><i>a screen that fails must not pass</i>"]

    ERR -->|no| P{"parse the reply"}
    P -->|"strict JSON"| V["read injection + contains_text"]
    P -->|"prose / fenced"| KW["keyword fallback"]
    P -->|"unreadable"| F4["injection=true<br/><i>ambiguity is never a pass</i>"]

    KW --> V
    V --> OUT["ImageScreenVerdict"]
    F1 --> OUT
    F2 --> OUT
    F3 --> OUT
    F4 --> OUT
```

**Two questions, not one.** `contains_text` and `injection` are separate fields, because
a photo of a receipt has text and is not an attack. A screen that refuses any image
containing text would refuse every document, chart and screenshot.

---

## 5. The three verdict states the console must show

```mermaid
flowchart TB
    V["ScreenVerdict"] --> Q1{"screened?"}
    Q1 -->|"FALSE — checked first"| U["<b>could not screen</b><br/>no model looked at all<br/>blocked, fail-closed"]
    Q1 -->|true| Q2{"injection?"}
    Q2 -->|true| B["<b>blocked</b><br/>a model looked and found<br/>instructions aimed at an AI"]
    Q2 -->|false| C["<b>cleared</b><br/>a model looked and found none"]

    NULLV["verdict is null"] --> N["<b>not reached</b><br/>hygiene refused first"]

    U -.->|"collapse into blocked"| OPS["hides that NO screening happened<br/><i>your deployment is down and<br/>nobody pages anyone</i>"]
    U -.->|"collapse into cleared"| LIE["a lie"]
```

**`screened` is checked before `injection`.** A fail-closed block carries
`injection=true` *and* `screened=false`; checking `injection` first would make the third
state unreachable.

---

## 6. Why the screen and the model must see the same bytes

```mermaid
flowchart LR
    ORIG["the uploaded image"] --> D1["data URL<br/>base64 of the FULL bytes"]
    D1 --> SCR["the screen"]
    D1 --> MOD["the answering model"]

    ORIG -.->|"the tempting optimisation"| SMALL["downscale to 256x256<br/>for a cheap screen"]
    SMALL -.-> LOST["4-point grey text<br/>disappears"]
    LOST -.-> FALSE["screen truthfully reports<br/>no text found"]
    FALSE -.-> BYPASS["the full-size image reaches the model<br/>where the text is perfectly legible"]
```

Both call sites build the URL through the same `data_url` construction, and a test pins
them **equal**. "Cheap" must mean a cheaper **model**, never a cheaper
**representation**.

---

## 7. The deliberate ordering divergence

```mermaid
flowchart TB
    subgraph GR["MediaScreen — the guardrails chain"]
        G1["hygiene"] --> G2["image PII"] --> G3["injection screen"] --> G4["custom rails"]
    end

    subgraph VI["VisionAnalyser — the vision path"]
        V1["hygiene"] --> V2["injection screen"] --> V3["image PII"] --> V4["the model"] --> V5["output rails"]
    end

    G2 -.->|"premise:<br/>the screening model is an<br/>ADDITIONAL party (OWASP LLM06)"| GWHY["redacting first<br/>genuinely reduces exposure"]

    V2 -.->|"premise does NOT hold:<br/>the image goes to the same<br/>vendor either way"| VWHY["redacting first buys ZERO privacy<br/>while screening first refuses a<br/>hostile image before OCR starts"]
```

**An ordering rule is downstream of an argument.** When the premise changes, the correct
ordering changes with it. Both sites state the premise — otherwise the next reader
unifies them and silently makes one path worse.

---

## 8. `NOT_RUN` versus `FAILED_CLOSED`

```mermaid
flowchart LR
    N["NOT_RUN"] --> NM["nobody enabled it"]
    NM --> NA["fix: enable it<br/><i>a configuration state</i>"]

    F["FAILED_CLOSED"] --> FM["something that should have run<br/>could not"]
    FM --> FA["fix: find out why<br/><i>an incident</i>"]

    N --> RAN["ControlReport.ran == false"]
    F --> RAN
    RAN --> COV["neither is listed among the<br/>controls that provided coverage"]

    N -.->|"collapse them"| BAD["one word for two<br/>different call-to-actions"]
    F -.-> BAD
```

A fail-closed control **blocked**, which is the right outcome — but it did not provide
coverage, so `coverage()` must not list it as if it did.

---

## 9. Image PII — verdict plus artefact plus evidence

```mermaid
flowchart TB
    IMG["the image"] --> OCR["Tesseract OCR<br/>words + bounding boxes"]
    OCR --> AN["Presidio analyser<br/>over the recognised text"]
    AN --> F{"anything found?"}

    F -->|no| ORIGINAL["return the ORIGINAL object<br/>unchanged"]
    F -->|yes| K["entity KINDS<br/><i>never the values —<br/>the values ARE the PII</i>"]
    F --> BOX["bounding boxes<br/>source pixel space, top-left origin"]

    K --> PAINT["paint opaque black rectangles<br/><i>not blur — blur is reversible<br/>on rendered text</i>"]
    PAINT --> PNG["re-encode as PNG<br/><i>lossless; JPEG would smear the edges</i>"]
    PNG --> NEW["a NEW ImagePayload<br/><i>the original is frozen evidence</i>"]

    NEW --> FWD["this is what the model is sent"]
    BOX --> OVERLAY["the console draws the regions<br/>over the image the user uploaded"]

    OVERLAY -.->|"a finding with<br/>incomplete geometry"| NODRAW["report the kind,<br/>draw NOTHING —<br/>an invented box looks authoritative"]
```

A `REDACT` verdict with no redacted image is theatre: the caller is still holding the
original bytes. And a claim with no box is a claim, not evidence.

---

## 10. Backend wiring — where the leaf gets its dependencies

```mermaid
flowchart TB
    subgraph LEAF["aegis.vision — a leaf, owns no provider"]
        PIPE["VisionAnalyser<br/>ordering + policy"]
    end

    subgraph HOST["backend/src/app/vision — the composition root"]
        SC["_vision_completer<br/>ModelRole.VISION"]
        AN["_analyst<br/>ModelRole.VISION + usage mapping"]
        OR["_output_rails<br/>app.guardrails.check_output"]
        AV["image_pii_available()<br/>find_spec, CHECKED not assumed"]
    end

    SC --> PIPE
    AN --> PIPE
    OR --> PIPE
    AV --> PIPE

    subgraph ROUTE["POST /vision/analyse"]
        G["bind governance context<br/>tenant + user + caps"]
        R["reset in a finally"]
    end

    G --> PIPE
    PIPE --> R

    G -.->|"the bug that was fixed"| BUG["without the binding, BOTH paid calls<br/>skipped budget enforcement and<br/>wrote no ledger row —<br/>uncapped, unattributed, invisible spend"]
```

The leaf decides *what must clear before pixels reach a model*, and nothing else. The
host supplies the calls. That is what keeps `aegis.vision` importable with no gateway,
no `app.*`, and no local model.

---

**Next:** [`50-interview.md`](50-interview.md).
