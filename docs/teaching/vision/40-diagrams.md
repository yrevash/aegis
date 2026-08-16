# Vision — the diagrams

Five diagrams. The two worth reproducing from memory are **the pipeline** and **the two
orderings** — between them they carry the whole argument for why this module exists.

Everything else about this module is explained in [`10-guide.md`](10-guide.md); a picture is only
here when it shows something prose cannot.

---

## 1. The pipeline — the order *is* the product

*Look at where the screen sits, and at the fact that every refusal exits sideways rather than
falling through.*

```mermaid
flowchart TB
    P["ImagePayload + question"] --> H{"1 · payload hygiene<br/><i>free, offline</i>"}
    H -->|"bomb · MIME lie ·<br/>bare URI · over cap"| B1["BLOCKED<br/>stages 2–5 marked NOT_RUN"]

    H -->|ok| S{"2 · injection screen<br/><b>before the answering model</b>"}
    S -->|"instructions found"| B2["BLOCKED<br/>screened = true"]
    S -->|"no completer wired"| B3["BLOCKED · FAILED_CLOSED<br/>screened = <b>false</b>"]
    S -->|"call raised ·<br/>reply unparseable"| B4["BLOCKED<br/>screened = true<br/><i>see diagram 3</i>"]

    S -->|clear| PII{"3 · image PII<br/><i>opt-in</i>"}
    PII -->|"not enabled"| NR["NOT_RUN<br/>+ the install command"]
    PII -->|"ImportError"| RAISE["<b>RE-RAISED</b><br/><i>a deployment fault,<br/>not a verdict</i>"]
    PII -->|"any other error"| B5["FAILED_CLOSED · BLOCKED"]
    PII -->|"scanned"| RED["redacted payload + boxes"]

    NR --> M{"4 · the vision model"}
    RED --> M
    M -->|"no analyst · call raised"| B6["FAILED_CLOSED · BLOCKED<br/><i>a refusal, not an empty answer</i>"]
    M -->|answered| OR{"5 · text output rails"}

    OR -->|"none wired"| NR2["NOT_RUN<br/>answer returned,<br/>coverage says unscreened"]
    OR -->|"rails raised"| B7["FAILED_CLOSED<br/>answer <b>WITHHELD</b>"]
    OR -->|BLOCK| B8["BLOCKED"]
    OR -->|"REDACT / PASS"| ANS["ANSWERED<br/>with the rails' text,<br/>not the raw answer"]
```

Every arrow into a block also fills in `NOT_RUN` for every *later* stage, so a run refused at
stage 1 still lists all five controls with reasons.

The one arrow that leaves the diagram is the `ImportError` — the single failure this pipeline
refuses to turn into a tidy verdict.

---

## 2. Two orderings, one observable outcome

*Look at where the two paths converge, and at what has already happened by the time they do.*

```mermaid
flowchart TB
    subgraph WRONG["screen AFTER the model"]
        W1["call the vision model"] --> W2["get the answer"]
        W2 --> W3["screen the image"]
        W3 --> W4["suppress the answer if flagged"]
    end

    subgraph RIGHT["screen BEFORE the model"]
        R1["screen the image"] --> R2{"flagged?"}
        R2 -->|yes| R3["refuse — the model<br/>is never called"]
        R2 -->|no| R4["call the vision model"]
    end

    W4 --> SAME["<b>identical</b> verdict<br/><b>identical</b> UI<br/><b>identical</b> outcome assertions"]
    R3 --> SAME

    W1 -.->|"already true on the left"| COST["you paid for the expensive call ·<br/>the hostile image is in provider logs ·<br/>any tools already ran ·<br/>the injection had its chance"]

    SAME --> TEST["<b>assert analyst.calls == &#91;&#93;</b>"]
    TEST --> ONLY["the only assertion<br/>the left-hand pipeline<br/>cannot satisfy"]
```

When the claim is about ordering or non-occurrence, assert on the thing that should *not* have
happened. Asserting on the outcome tests nothing — both designs produce the same outcome.

---

## 3. Inside the screen, and the two flags that come out

*Look down the refusal column: all three block, but only the top one reports `screened = false`.*

```mermaid
flowchart TB
    I["ImagePayload<br/><i>on the vision path, hygiene<br/>has already refused bare URIs</i>"] --> C{"vision completer wired?"}

    C -->|no| F2["injection = true<br/><b>screened = false</b><br/><i>no offline backstop for pixels</i>"]

    C -->|yes| CALL["cheap vision call<br/>SAME data URL the model gets<br/>strict-JSON response format"]

    CALL --> ERR{"raised?"}
    ERR -->|yes| F3["injection = true<br/>screened = <b>true</b> (default)<br/><i>a screen that fails must not pass</i>"]

    ERR -->|no| P{"parse the reply"}
    P -->|"strict JSON"| V["read injection + contains_text<br/><i>two questions, not one</i>"]
    P -->|"prose or fenced"| KW["keyword fallback"]
    P -->|"unreadable"| F4["injection = true<br/>screened = <b>true</b> (default)<br/><i>ambiguity is never a pass</i>"]

    KW --> V
    V --> OUT["ImageScreenVerdict"]
    F2 --> OUT
    F3 --> OUT
    F4 --> OUT
```

Every path blocks, so nothing unsafe reaches the model on any of them.

But `screened` is what the console uses to say *"no model looked at all"*, and a screen deployment
that is simply **down** takes the middle path — so it renders as an ordinary red block rather than
as an outage. That is a reporting gap, not a safety one, and it is worth raising.

---

## 4. The three states the console must show

*Look at the order of the two questions — `screened` is asked first, and it has to be.*

```mermaid
flowchart TB
    V["ScreenVerdict"] --> Q1{"screened?"}
    Q1 -->|"false — asked FIRST"| U["<b>could not screen</b><br/>no model looked at all<br/>blocked, fail-closed"]
    Q1 -->|true| Q2{"injection?"}
    Q2 -->|true| B["<b>blocked</b><br/>a model looked and found<br/>instructions aimed at an AI"]
    Q2 -->|false| CL["<b>cleared</b><br/>a model looked<br/>and found none"]

    NULLV["verdict is null"] --> N["<b>not reached</b><br/>hygiene refused first"]

    U -.->|"collapsed into 'blocked'"| OPS["hides that NO screening happened —<br/><i>your deployment is down and<br/>nobody pages anyone</i>"]
    U -.->|"collapsed into 'cleared'"| LIE["a lie"]
```

A fail-closed block carries `injection = true` **and** `screened = false`. Ask `injection` first
and the third state becomes unreachable.

---

## 5. Image PII — verdict, artefact, and evidence

*Look at the two outputs on the right: one goes to the model, the other to the console.*

```mermaid
flowchart TB
    IMG["the image that<br/>cleared the screen"] --> OCR["Tesseract OCR<br/>words + bounding boxes"]
    OCR --> AN["Presidio analyser<br/>over the recognised text"]
    AN --> F{"anything found?"}

    F -->|no| ORIGINAL["return the ORIGINAL object,<br/>unchanged · analysed once"]
    F -->|yes| K["entity <b>KINDS</b><br/><i>never the values —<br/>the values ARE the PII</i>"]
    F -->|yes| BOX["bounding boxes<br/>source pixel space, top-left origin"]

    K --> PAINT["paint opaque black rectangles<br/><i>not blur — blur is reversible<br/>on rendered text</i>"]
    PAINT --> PNG["re-encode as PNG<br/><i>lossless; JPEG would smear<br/>the box edges</i>"]
    PNG --> NEW["a <b>NEW</b> ImagePayload<br/><i>the original is frozen evidence</i>"]

    NEW --> FWD["this is what the model is sent"]
    BOX --> OVERLAY["the console draws the regions<br/>over the image the user uploaded"]

    OVERLAY -.->|"a finding with<br/>incomplete geometry"| NODRAW["report the kind,<br/>draw NOTHING —<br/><i>an invented box<br/>looks authoritative</i>"]
```

A `REDACT` verdict with no redacted image is theatre: the caller is still holding the original
bytes. And a claim with no box is a claim, not evidence.

---

**Next:** [`50-interview.md`](50-interview.md)
