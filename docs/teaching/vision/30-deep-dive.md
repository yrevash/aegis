# Vision — deep dive

The ordering, how it is proved, the failure modes, and the limits of the claim.

---

## Story 1 — the pipeline that would have looked identical

Start with the counterfactual, because it is the clearest way to see what this module
actually bought.

Build the pipeline the natural way. You have an image and a question. You call the
vision model. You get an answer. Then, being responsible, you screen the image and
suppress the answer if the screen objects.

From the outside that is **indistinguishable** from the correct design:

- The blocked request returns a block.
- The verdict text says the same thing.
- The UI renders the same red panel.
- Every integration test asserting "hostile image ⇒ BLOCKED" passes.

And it is a completely different system:

| | Screen after | Screen before |
|---|---|---|
| Cost of a blocked image | One **expensive** vision call, then a refusal | One **cheap** screen call, then a refusal |
| Provider logs | The hostile image is in them | It is not |
| Tool access | If the answering call had tools, it already used them | Nothing downstream ran |
| The injection | Had its chance to influence a real generation | Never reached a generation |

So *"the screen runs before the model"* is a claim that must be **proved by observing the
model**, not by observing the verdict. That is what
`aegis/tests/vision/test_security.py` does, and the file's own docstring says so
(`:12-14`):

> Both are proved with a recording analyst: the assertion is `analyst.calls == []`,
> which cannot be satisfied by a pipeline that calls the model and then decides.

The assertion carries a message written for whoever breaks it (`:47`):

```python
assert analyst.calls == [], "the vision model was called on an image the screen refused"
```

**The transferable lesson:** when your claim is about *ordering* or *non-occurrence*, the
test must assert on the thing that should not have happened. Asserting on the outcome
tests nothing, because both designs produce the same outcome.

---

## Story 2 — the fail-closed direction, and why text and pixels differ

`screen_image` refuses in three ways before it ever finds an injection
(`aegis/src/aegis/guardrails/media/injection.py:172-207`):

1. **Bare URI** (`:189-195`) — the bytes were never held.
2. **No completer** (`:196-206`) — the control cannot run.
3. **The call raised** (`classify_image`, `:163-168`) — any exception.

Plus a fourth, in the parser (`:141-144`): an unparseable reply is `injection=True`.

The second is the interesting one, because it is where a reasonable engineer goes wrong.

Everywhere else in this codebase, a missing model **degrades**. `Guardrails` with
`completer=None` still runs the deterministic injection signatures and the schema rails —
weaker, but real. The pattern is so consistent that "no completer, run the offline
fallback" is the obvious thing to write.

There is no offline fallback for pixels. **No regex reads an image.** So degrading here
means running *zero* image controls while the pipeline reports a pass. The docstring
states it (`:16-22`):

> With no vision completer wired, the text classifier degrades to its deterministic
> signature backstop and keeps working. There is no such backstop for pixels… so
> degrading would mean *no image control at all* while the pipeline reported a pass.

The general rule worth extracting: **whether degrading is honest depends on whether a
weaker control still exists.** Text can degrade because something remains. Images cannot,
because zero controls is not a degraded mode — it is no mode.

And crucially the verdict distinguishes the two reasons for a block. `screened=False`
(`:71-75`) means *"the control did not run"*, not *"we looked and found something"*. The
test pins it (`test_security.py:77`):

```python
assert result.screen.screened is False, "a fail-closed block must not read as 'we looked'"
```

---

## Story 3 — the three console states, and the fail-open that nearly ships

This is where the design would have leaked into the UI.

A verdict panel wants two states. Green: cleared. Red: blocked. Anyone would build that.

But there are three, and the third is the one that matters
(`web/src/components/vision/ScreenVerdict.tsx:38`):

```ts
const state = !verdict.screened ? 'unscreened' : verdict.injection ? 'blocked' : 'cleared'
```

Note the order of the ternaries: `screened` is checked **first**, before `injection`.
That is not stylistic. A fail-closed block has `injection=True` *and* `screened=False`;
checking `injection` first would classify it as an ordinary block and the third state
would be unreachable.

The component's docstring names both ways of getting it wrong (`:16-21`):

> Collapsing this into "blocked" would hide that no model looked at the image at all;
> collapsing it into "cleared" would be a lie.

And they are different kinds of wrong. Collapsing into "cleared" is a security failure.
Collapsing into "blocked" is an **operational** failure: your screen deployment is down,
every image is being refused, and the dashboard shows a wall of ordinary blocks. Nobody
pages anyone, because blocks are normal.

There is even a fourth rendering, for `verdict == null` (`:26-35`): *"The injection screen
was not reached — payload hygiene refused this image first."* Four distinguishable
states, because there are four distinguishable things that can happen.

---

## Story 4 — screening a different representation would be a bypass

Subtle, and easy to introduce while optimising.

The screen should be cheap. The obvious economy: downscale the image before screening.
Thumbnails are much cheaper to process, and the screen only needs to read text, right?

No. **Downscaling destroys exactly the payload the screen is looking for.** Four-point
grey text in a corner survives at full resolution and disappears at 256×256. The screen
returns "no text found", entirely truthfully about the image *it* saw, and the full-size
image goes to the model where the text is perfectly legible.

The same hazard applies to re-encoding, cropping, or format conversion.

So the invariant is: **the screen must see the same bytes the model will.** Both call
sites build their messages through the same `data_url` construction
(`injection.py:78-93` for the screen, `prompts.py:59` for the analysis), and there is a
test that pins the resulting URLs to be **identical**
(`aegis/tests/vision/test_security.py:133-146`):

```python
screened = screen.calls[0][1]["content"][1]["image_url"]["url"]
analysed = analyst.calls[0][1]["content"][1]["image_url"]["url"]
assert screened == analysed
```

That test also protects the *other* end of the pipeline. When the PII rail fires, the
payload is rewritten — and `analysis_messages` is called with `current`, the redacted
payload (`pipeline.py:296`), while the screen ran on the original. The two URLs differ in
that case, by design, because the screen ran first and the redaction happened after. The
test uses a clean image so the two must match; a PII test asserts the model got the
*redacted* one.

**"Cheap" must mean a cheaper model, never a cheaper representation.**

---

## Story 5 — the deliberate ordering divergence

Two code paths in this codebase order PII and the injection screen differently, and this
is the one place a reviewer is most likely to file a bug against correct code.

**`MediaScreen._check_image`** (`aegis/src/aegis/guardrails/media/screen.py:229-234`):
hygiene → **image PII** → **screen** → custom rails.

**`VisionAnalyser.analyse`** (`aegis/src/aegis/vision/pipeline.py:156-273`):
hygiene → **screen** → **image PII** → model → output rails.

Both docstrings carry the argument.

`screen.py:15-18` — the guardrails chain:

> The PII-before-classifier ordering carries over verbatim: sending an unredacted image
> to a screening model is itself a sensitive-information disclosure (OWASP LLM06),
> exactly as it was for text.

`pipeline.py:26-33` — the vision path:

> That reasoning does not transfer: on this path the image is going to the fleet's vision
> deployment either way, so redacting first buys no privacy — while screening first means
> a hostile image is refused before the OCR stack is ever started on it. The trade is
> stated here rather than left for a reader to discover.

Read them together and the structure is clear. The rule *"redact before you send to a
model"* is downstream of a premise: **the screening model is an additional party**. On
the guardrails path that premise holds. On the vision path it does not — the same vendor
sees the same bytes moments later regardless — so the rule that follows from it does not
apply, and the efficiency argument takes over.

Two things make this a good answer rather than a hand-wave:

- The costs are asymmetric and concrete. On the vision path, redacting first means
  running OCR (CPU-seconds, plus a Tesseract binary) on images that are about to be
  refused.
- Both sites **state the premise**. Without that, the next reader unifies the two and
  silently makes one path worse.

---

## Story 6 — `NOT_RUN` and `FAILED_CLOSED` are not the same word

`ControlOutcome` (`aegis/src/aegis/vision/types.py:38-52`) has five values, and the
docstring defends the two that look redundant (`:41-46`):

> "The operator did not enable the image-PII rail" and "the injection screen had no
> completer, so the image was blocked rather than passed" are different statements about
> coverage, and collapsing them into one would be the exact dishonesty this codebase
> bans.

They are different in **who should act**:

- `NOT_RUN` — a **configuration** state. Nobody enabled it. Fix: enable it. No incident.
- `FAILED_CLOSED` — a **failure**. Something that should have run could not. Fix: find
  out why the screen deployment is unreachable. This is an incident.

And note `ControlReport.ran` (`:77-80`):

```python
return self.outcome not in {ControlOutcome.NOT_RUN, ControlOutcome.FAILED_CLOSED}
```

A fail-closed control counts as **not having run**. It blocked, which is the right
outcome, but the coverage sentence must not list it among the controls that provided
coverage. `coverage()` (`:219-230`) partitions on exactly that property.

`_blocked` (`pipeline.py:434-468`) completes the picture: every blocked result gets a
`NOT_RUN` entry for every stage after the one that refused, with the detail *"Not reached
— injection_screen refused first."* A caller never has to infer a missing entry.

---

## Story 7 — the ImportError that is deliberately not caught

`analyse()` wraps the PII rail in a `try` with two `except` clauses, in a specific order
(`pipeline.py:219-248`):

```python
try:
    scan = scan_and_redact(payload, ...)
except ImportError:
    # A rail the operator declared, with its dependency missing, is a
    # deployment fault and must be shouted about — not folded into a
    # verdict a UI would render as an ordinary block.
    raise
except Exception as exc:
    ... FAILED_CLOSED, blocked ...
```

Consider what happens if `ImportError` is caught by the general handler instead. The
operator set `image_pii=True`. The extra is missing in this deployment. Every request
returns a tidy, well-formatted block saying "the image-PII rail is enabled but errored."
That is fail-closed, so it is *safe* — and it is also a **permanent, total outage
rendered as an ordinary policy verdict**, which nobody investigates because blocks look
normal.

Re-raising turns it into a 500 with a stack trace naming the missing package. Loud beats
tidy for a deployment fault.

Everything else — a corrupt image, an OCR crash, a Presidio bug — is a per-request
failure, and those correctly become `FAILED_CLOSED` blocks.

The backend then makes the *whole question* moot by never enabling a rail it cannot run:
`image_pii_available()` (`backend/src/app/vision/__init__.py:46-53`) checks
`find_spec("presidio_image_redactor")` and passes the result into the analyser
(`:155`). If the package is absent the stage reports `NOT_RUN` with the install command,
which the console renders. **Checked, not assumed.**

---

## Story 8 — a blocked run must carry no model text and no invented cost

`_blocked` builds its result with `usage=VisionUsage()` (`pipeline.py:467`) — a fresh
empty usage object — and never sets `answer`.

The test drives it deliberately (`test_security.py:119-130`): the analyst is configured to
return `"this text must never appear"`, and then:

```python
assert result.answer == ""
assert result.usage.cost_usd == 0.0
assert result.usage.model == ""
```

Two reasons this matters more than it looks.

**Leakage.** If a blocked result carried the model's text in a field the UI happened to
render — a debug panel, a tooltip, a JSON dump — the block would be cosmetic. The
`VisionAnalysis.answer` docstring is categorical (`types.py:185-187`): *"**Empty unless**
`outcome` is `ANSWERED` — a blocked run never carries model text, because on a blocked
run there is no model text."*

**Accounting.** A blocked run costs the screen call, not the analysis call. Reporting a
non-zero `cost_usd` on a blocked run would inflate the cost dashboard with spend that
never happened. And `cost_source` (`types.py:139-143`) distinguishes a genuine `$0.00`
from `unpriced` — "billable work nobody could price" is a different claim from "this cost
nothing."

---

## Concurrency, state and reentrancy

**`VisionAnalyser` holds only configuration** (`pipeline.py:122-128`) — three injected
callables, limits, and three PII flags. Nothing accumulates on `self`. All per-run state
(`controls`, `entities`, `regions`, `current`) is local to `analyse()`. One instance
serves concurrent requests safely.

The backend still builds a **fresh instance per call** (`build_analyser`,
`backend/src/app/vision/__init__.py:144-156`), and the docstring gives the real reason:
it is not thread-safety, it is that building it at import time would require a configured
gateway, *"which the offline test suite depends on"* not needing.

**Two sequential model calls per request**, both `ModelRole.VISION`. Sequential is
required, not incidental — the second must not start until the first has cleared.

**The governance context is bound around both** (`routes.py:2842-2855`, with the reasoning
at `:2816-2825`). That is the fix for a real bug; see below.

**Lazy imports everywhere.** `PIL` and `presidio_image_redactor` are reached through
`aegis.core.lazy.require` inside function bodies (`vision/pii.py:106`,
`guardrails/media/image_pii.py:62-76`), and the isolation test asserts that importing
`aegis.vision` pulls **neither** — nor `torch`, `transformers` or `timm`.

---

## Story 9 — two paid calls with no governance context

From the audit-sweep commit (`7d3c436`), and it is the sharpest live bug in this module's
history:

> **`/vision/analyse` made two paid model calls with no governance context (SECURITY).**
> `set_governance_context` was bound only on the query and voice routes, and both
> `enforce` and `record` are gated on it — so the injection-screen call and the analyst
> call skipped budget enforcement and wrote no ledger row. Uncapped, unattributed,
> invisible spend.

Look at the shape. The gateway's budget enforcement and ledger write are both
conditional on a bound context — which is correct, because there are legitimate
unattributed callers (a startup probe, an offline eval). But that means **the control is
opt-in at the call site**, and a new route that forgets to opt in silently gets no
control at all.

Nothing errors. The image is analysed. The answer is correct. The verdict panel is green.
The only symptom is a cost dashboard that does not add up — and only if someone is
checking.

Worse for vision than for chat: vision is the most expensive call per byte on the
platform, and an authenticated caller could loop images indefinitely.

The route's docstring now spells it out (`routes.py:2816-2825`) and the fix binds and
resets in a `finally` (`:2842-2855`) *"so the context can never leak onto the next request
served by this worker."* The commit notes the tests drive **the real route through the
real governance hook to the real ledger** — testing the wiring, not a mock of it.

**The generalisable lesson:** a control that is enabled per-call-site is a control that
the *next* call site will forget. If you cannot make it structural, you need a test that
asserts the ledger row exists — because nothing else will notice.

---

## The honest limits of the claim

Four, and I would rather state them than be caught on them.

**1. Nothing here is verified against the live fleet.** The conftest says so
(`aegis/tests/vision/conftest.py:4-7`): the gateway credential is a placeholder, so the
suite *"proves the ordering and the verdicts, not that the hosted
Llama-3.2-90B-Vision deployment returns good analyses."* The screen's real-world
precision and recall — its false-negative rate on a genuinely subtle payload — is
**unmeasured here**.

**2. The screen covers rendered instructions, not "unsafe images".** There is no
content-safety classifier over pixels. The guardrails chain declares that gap on every
image verdict (`screen.py:60-62`); the vision pipeline's stage list simply does not
contain such a stage. A photograph of something the policy forbids is not screened.

**3. Adversarial perturbation attacks are out of scope.** A crafted noise pattern that
shifts the image embedding toward a target instruction carries no legible text, so a
"read the text and judge it" screen cannot see it. This is an active research area with
no complete defence — which is also NIST AI 100-2's general position on prompt injection.

**4. Image PII is OCR-based, with everything that implies.** It finds *rendered* personal
data that Tesseract read and Presidio recognised. It has nothing to say about a face, a
signature, a barcode, handwriting it could not read, or a language outside the OCR/NER
coverage. The honest claim is *"we scanned the rendered text and redacted what we
recognised"*, not *"this image contains no personal data."* And in this deployment
`presidio-image-redactor` is not installed, so the stage reports `NOT_RUN` — which the
console shows.

---

## What you should now be able to tell as a story

- **The counterfactual pipeline** that looks identical from outside, and the four things
  it costs
- **Why `assert analyst.calls == []`** is the only assertion that separates them
- **Why images fail closed where text degrades**, and the rule that generalises it
- **The three console states**, why `screened` is checked first, and how each collapse
  fails differently
- **Why screening a downscaled copy is a bypass**, and the test that pins both URLs equal
- **The deliberate PII/screen divergence** and the premise that flips it
- **`NOT_RUN` vs `FAILED_CLOSED`** as a question of who should act
- **The `ImportError` that is deliberately re-raised**, and the tidy outage it prevents
- **The two paid calls with no governance context** — uncapped, unattributed, invisible
- **The four limits of the claim**, stated before someone else finds them

**Next:** [`40-diagrams.md`](40-diagrams.md).
