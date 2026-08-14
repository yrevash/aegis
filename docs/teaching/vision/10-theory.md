# Vision — the theory

The architecture of a vision-language model, the published attack literature, what OCR
actually does, and the economics that shape the pipeline.

---

## 1. How a VLM is built

Nearly every production VLM follows the same three-part recipe.

**A vision encoder.** Usually a Vision Transformer (ViT), often the image tower of a
**CLIP** model. The image is split into fixed patches (14×14 or 16×16 pixels), each
patch is linearly embedded, position embeddings are added, and a transformer stack
produces one vector per patch. A 336×336 image at 14×14 patches gives 576 patch vectors.

**A projection.** A small learned module — in LLaVA it is literally a two-layer MLP; in
BLIP-2 it is a "Q-Former" that also compresses; in Flamingo it is gated cross-attention
inserted into the frozen LM. Its job is to map vision-encoder vectors into the **same
dimensionality and distribution as the language model's token embeddings**.

**A language model.** Standard decoder-only transformer. It receives a sequence that
interleaves projected image vectors with real text token embeddings, and generates text.

Training is typically two-stage: first align the projection on image-caption pairs with
the encoder and LM frozen, then instruction-tune end to end on visual question-answering
data.

### The security consequence, stated precisely

After the projection, the LM's input is one flat sequence of vectors. There is **no type
tag**, no segment ID, no attention mask distinguishing "this vector came from a pixel
patch" from "this vector came from the system prompt".

So when people ask *"why can't the model just ignore instructions in images?"*, the
answer is not that it is badly trained. It is that **the model has no representation of
the distinction you are asking it to enforce.** You are asking for a privilege boundary
in a system that has no privilege bits.

This is the same root cause as text prompt injection — Simon Willison's original framing
in 2022 was that there is no separation between "instructions" and "data" in a prompt —
and the image case simply widens the channel.

### Two more consequences worth knowing

**Resolution and tiling.** Many VLMs handle high-resolution images by tiling: split into
several crops, encode each, plus a downscaled whole-image view. This is why cost scales
with image size, and why a very large image can consume thousands of tokens. It is also
why a size cap is a *cost* control as well as a memory one.

**OCR ability is emergent and good.** These models were not trained as OCR engines, but
large-scale image-text pretraining makes them strong at reading rendered text —
including small, low-contrast, rotated and stylised text. That capability is exactly
what the attacker uses. You cannot mitigate the attack by hoping the model fails to read
the payload; it reads it better than a human does.

---

## 2. The attack literature

**OWASP Top 10 for LLM Applications, `LLM01: Prompt Injection`.** Separates *direct*
(the user types it) from *indirect* (it arrives in content the model consumes). The
2025 revision names **multimodal injection** as a distinct sub-case: instructions hidden
in an image accompanying benign text, where the cross-modal interaction is the attack
surface. It states plainly that current mitigations are partial.

**`LLM06: Sensitive Information Disclosure`.** The rule that governs sending an
unredacted image to a third party — *including your own screening model*, which is a
third party from the data subject's point of view.

**NIST AI 100-2 (2025), *Adversarial Machine Learning: A Taxonomy and Terminology***.
Classifies prompt injection under *abuse* attacks on generative systems, and makes the
point every design review should absorb: **no current defence has a completeness
guarantee**. Everything is probabilistic. That is the argument for defence in depth, and
for never letting a single screen be the only thing between untrusted content and a
consequential action.

**MITRE ATLAS.** Tracks the same techniques as adversary TTPs, which is the vocabulary
an enterprise security reviewer already has.

### The specific techniques

| Technique | Why it works |
|---|---|
| **Low-contrast text** | White-on-white or near-white grey. Invisible to a human at normal viewing; the encoder sees pixel values, not perceptual contrast. |
| **Very small text** | A few pixels tall in a corner. Human skips it; tiling at high resolution reads it. |
| **Rotated / skewed text** | Defeats naive OCR preprocessing; VLMs handle it fine. |
| **Watermark-style overlay** | Low alpha across the whole image, reads as texture to a human. |
| **In-context text** | A chart label, a UI screenshot, a form field — text that *belongs* there, carrying an extra sentence. |
| **Adversarial perturbation** | A crafted noise pattern that shifts the image embedding toward a target instruction without any human-legible text at all. |

That last row deserves emphasis: **it is a real research direction and a screen based on
"read the text" does not catch it.** A perturbation-based attack carries no rendered
glyphs; it manipulates the embedding directly. This is why the honest claim is "we screen
for *rendered instructions*", not "we make images safe."

---

## 3. Screening as a classification problem

The screen is a binary classifier implemented as a model call, and it has the usual
trade-off.

**False negative** — a real injection passes. Cost: the attack proceeds.
**False positive** — a benign image is refused. Cost: a user cannot upload their receipt.

Two design choices push the operating point in the right direction.

**Ask two questions, not one.** `contains_text` and `injection`, separately. A single
"is this bad?" question forces the model to do the whole judgement in one hop. Splitting
it lets the model *report* what it read and then *decide* whether it is addressed to an
AI — and it lets you log the intermediate answer, which is enormously useful when tuning.

More importantly, it prevents the degenerate policy. A screen that refuses any image
containing text refuses every document, chart, screenshot and photograph of signage. It
would have perfect recall and be unusable.

**Name the evasion in the prompt.** Explicitly instructing the model to report *"faint,
low-contrast, very small, rotated, or watermark-style text"* raises recall on exactly the
techniques an attacker uses. The model can read it; you have to ask it to look.

### Cheap screen, expensive answer

The screen is a second model call per image, so it has to be cheap relative to the
answering call. In practice: a smaller/faster vision deployment, a short system prompt,
`temperature=0`, and a strict-JSON response format. The economics only work because the
screen answers a narrow question and returns three fields.

There is a design tension here worth naming. The screen should ideally be a *different,
cheaper* model. But it must see **the same bytes** the answering model will — screening a
downscaled or re-encoded copy is a bypass, because the payload may be legible in one and
not the other. So "cheap" must mean a cheaper *model*, never a cheaper *representation*.

---

## 4. Structured output from a screening model, and the fail direction

Ask for JSON, get JSON — most of the time. The real distribution includes:

- a markdown fence: ```` ```json { … } ``` ````
- a reasoning preamble, especially from reasoning-tuned models
- prose: *"Yes, this image appears to contain an injection attempt."*
- valid JSON with the wrong key names
- an API error, a timeout, a rate limit

A robust parser handles the first three with a tolerant scan. But there is a bright line:
**when the reply cannot be resolved into a boolean, the verdict is a block.**

The reasoning is asymmetric-cost. Reading an unparseable reply as `injection=False` means
a parser bug becomes a fail-open across every image. Reading it as `injection=True` means
a parser bug becomes a visible outage — annoying, and *loud*. Loud beats silent every
time in a control.

The same applies to the exception path. `except Exception` around the completer call,
returning `injection=True`, is the correct shape here even though broad excepts are
usually a smell. A screen that fails must not pass.

---

## 5. Image PII — what OCR-based detection actually does

Presidio's image redactor is three stages:

1. **OCR** (Tesseract via `pytesseract`) returns recognised words *with bounding boxes*
   and per-word confidence.
2. **Text PII analysis** — Presidio's ordinary analyser runs over the recognised text,
   using pattern recognisers (regex + checksum for card numbers, IBANs, national IDs)
   and NER (spaCy) for names, locations, organisations.
3. **Redaction** — draw filled rectangles over the boxes of the matched words.

Every limitation follows from stage 1:

- **OCR misses.** Handwriting, low resolution, unusual fonts, heavy skew, poor contrast.
  What OCR does not read, Presidio cannot analyse.
- **Word-level boxes.** An entity spanning multiple words is several boxes; a partial
  match may under-redact.
- **A face is not text.** OCR-based PII detection finds *rendered* personal data. It has
  nothing to say about a photograph of a person, a signature, or a barcode.
- **Language coverage** follows the OCR model and the NER model, not the image.

So an image-PII rail's honest claim is: *"we scanned the rendered text and redacted the
personal data we recognised in it."* Not *"this image contains no personal data."*

The dependency cost is also real: `pytesseract` needs the `tesseract` **binary**, and
`presidio-image-redactor` pulls OpenCV. That is a heavy, system-level dependency chain,
which is why the rail is opt-in and lazily imported — and why an operator who *declares*
it and does not have it must get a loud `ImportError`, not a silent no-op. Declared and
missing is a deployment fault; treating it as "the rail found nothing" is the failure
this codebase specifically bans.

---

## 6. Why bounding boxes belong on the result

A verdict saying "we found an EMAIL_ADDRESS" is a claim. A verdict with a rectangle is
**evidence**.

Presidio's analyzer returns `(entity_type, left, top, width, height, score)` in the
source image's pixel space, origin top-left — which is also the browser's coordinate
system, so a console can overlay them on the uploaded image directly.

Two constraints follow:

- **Carry the entity *kind*, never the recognised value.** The value *is* the PII.
  Putting `"j.smith@acme.com"` in a verdict that travels into traces and logs leaks
  exactly what the rail exists to protect.
- **A finding with incomplete geometry must not become an invented rectangle.** Report
  the entity in the kinds list and draw nothing. Drawing a guessed box is worse than
  drawing none, because it looks authoritative.

---

## 7. Cost and the ordering economics

Rough shape of a vision request:

| Stage | Cost |
|---|---|
| Payload hygiene | Free, offline, microseconds |
| Injection screen | One cheap vision call |
| Image PII | OCR + NER — CPU seconds, no API cost |
| The answering call | One expensive vision call; tokens scale with resolution/tiling |
| Output rails | Text rails over a short answer |

Two orderings fall out.

**Free before cheap before expensive.** Hygiene first is uncontroversial: refusing an
8 KB PNG that claims 40,000 × 40,000 pixels costs nothing, and doing it after a model
call costs a model call.

**Screen before OCR — on this path.** OCR is CPU-expensive and starting it on a hostile
image is wasted work. But note this is the *opposite* of the ordering the guardrails
chain uses, and the difference is a privacy argument, not an efficiency one:

- In the guardrails chain, the screening model is an *extra* party seeing the image, so
  redacting first genuinely reduces exposure (OWASP `LLM06`).
- In the vision pipeline, the image is going to the same vendor's vision deployment
  regardless, so redacting before the screen reduces exposure by **zero** while
  costing OCR on images that are about to be refused.

**The rule is: order by the argument, not by habit.** And when a system contains both
orderings, both sites must state the premise, or the next reader will "fix" the
inconsistency.

---

## 8. What "the model never saw it" is worth

Worth quantifying, because it is the claim the whole ordering exists to support.

If the screen runs *after* the answering call:

- you paid for the expensive call
- the hostile image is in the provider's request logs
- if the answering call had tool access, any tool use already happened
- the injection had its chance to influence a real generation

If it runs *before*:

- one cheap call, then a refusal
- the expensive model has no record of the image
- nothing downstream of the model ran

From the outside these are indistinguishable — same verdict, same UI. Which is exactly
why the ordering must be **tested by observation of the model**, not by observation of
the verdict. A recording fake with `assert analyst.calls == []` is the only assertion
that separates them.

---

## 9. The honest scope of a screen

Finally, the boundary of the claim. A rendered-instruction screen covers:

- Text in an image telling the model to do something
- Fake system/developer turns rendered into pixels
- Exfiltration instructions

It does **not** cover:

- **Adversarial perturbations** that shift the embedding with no legible text
- **Unsafe imagery** — a photograph of something the policy forbids. That is a different
  classifier and a second vision call.
- **Steganographic payloads** that only matter if something downstream decodes them
- **Semantic manipulation** — a legitimate image chosen to mislead

A system that says "we screen images" without qualifying is overclaiming. A system that
lists what it screens for, and names what it does not, is defensible — and per NIST, no
defence here has a completeness guarantee anyway, so precision about scope *is* the
honest position.

---

## What you should now be able to explain

- Encoder → projection → LM, and why the projection removes the privilege boundary
- Why tiling makes cost scale with resolution
- Why emergent OCR ability is the attacker's tool, not a bug to fix
- Where multimodal injection sits in OWASP LLM01, NIST AI 100-2 and MITRE ATLAS
- The six evasion techniques, and why perturbation attacks are out of a text screen's scope
- Why the screen asks two questions, and what a one-question screen degenerates into
- Why "cheap" must mean a cheaper model and never a cheaper representation
- Why an unparseable screen reply must block, on asymmetric-cost grounds
- What Presidio's image redactor actually does, and every limit that follows from OCR
- Why boxes are evidence and kinds-not-values is non-negotiable
- The cost ordering, the privacy ordering, and why they disagree between two code paths
- Why the ordering claim must be tested by observing the model, not the verdict

**Next:** [`20-in-aegis.md`](20-in-aegis.md).
