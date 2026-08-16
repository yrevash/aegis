# Guardrails — interview questions and answers

Claim first, then the reason, then a concrete detail from this system. The detail is what
separates "I read about prompt injection" from "I built the defence."

---

### "How do you defend against prompt injection?"

You don't *solve* it — there is no parameterised prompt, so there is no privilege boundary
to enforce. You layer imperfect controls and you are honest about each one's limits.

Six rails on the way in: **schema** (format, length, invisible characters), **PII
redaction**, **injection detection**, **content safety**, **topical**, then **custom
domain rails**. Six on the way out — schema, a system-prompt-leak content filter, content
safety, custom rails, grounding, and PII redaction *last*, for a reason I'll come to.

The injection rail itself has two layers. A deterministic signature backstop that runs
offline with no API call and cannot be talked out of its opinion, then a cheap-model
classifier for everything the signatures cannot cover — paraphrase, novel phrasings,
languages outside the seven we pattern-match.

And the control that actually matters for an agent is not a text filter at all. It is
structural: the model's output alone cannot authorise a dangerous action. A HIGH-risk tool
call stops for a human regardless of what any rail said.

---

### "Why is the PII rail second and not last?"

Because everything after it is a model call.

Layers three, four and five are the injection classifier, the content-safety self-check and
the topical screen — three requests to a third-party model. If you send the user's raw text
to those, your *safety control* is itself a sensitive-information disclosure. That's OWASP
LLM02 in the 2025 list, committed by the thing you built to prevent LLM01.

So redaction happens first and every downstream layer sees the masked text.

**On the output path it's reversed** — PII redaction is last. There are no downstream model
calls to protect there, so it's the final transform on text that has already survived every
block decision. If it ran first, every later rail would be judging text that had been
rewritten.

---

### "Signature matching is trivially evadable. Why bother?"

Because it's free, offline, instant, and it cannot be socially engineered.

When the classifier API is down, the signatures still run. That's the difference between
degraded and disabled. And it means the entire rail stack is unit-testable with no network
and no API key.

But you're right that it's evadable, and we verified exactly how. Against the real detector,
**only plain ASCII was caught.** Filler words between the anchors, zero-width characters,
Cyrillic homoglyphs, fullwidth and mathematical-bold fonts, base64 and non-English all walked
straight past. Every one of those strings is now a case in the regression suite.

---

### "So how did you close those?"

Normalisation, and one design decision that makes it correct.

Before matching, we build a **comparison-only view**: strip invisible and format characters,
NFKC-normalise so fullwidth and mathematical-bold letterforms fold to ASCII, decompose and
drop combining marks, collapse whitespace. Separately we build a second view with Cyrillic
and Greek homoglyphs mapped to their Latin lookalikes. Signatures run over both, plus both
views of anything recoverable from a base64 run.

**The critical part: the folded text is never propagated.** Rails match on the view and hand
the *original* string downstream. Two reasons. If a payload only passed your rails after
being rewritten, you screened something other than what reaches the model. And the homoglyph
map turns genuine Cyrillic prose into Latin gibberish — a Russian-language question would
arrive mangled.

We also fixed the patterns themselves: composable gap fragments so "ignore **the above**
directions" matches the same signature as "ignore all previous instructions".

**I'd volunteer where that stops**, because it's the honest answer. The gap allowance is
three filler words. `Ignore, if you would, the previous instructions` has four and is *not*
caught deterministically today — widening it further would start matching ordinary sentences
containing "ignore" and "instructions" fifteen words apart. So the deterministic layer is a
backstop, not a filter, and a padded paraphrase is exactly what the model classifier is for.

And the coverage limits are stated in the docstring **and pinned by a test** — seven
languages, base64 only, Cyrillic and Greek homoglyphs only, no leetspeak. An honesty claim
that no test enforces will eventually be a lie.

---

### "What's the most interesting attack you found?"

The Unicode Tag block.

There's a block at U+E0000–U+E007F where every codepoint renders as **nothing** in **every**
font — and U+E0020 through U+E007F mirror printable ASCII one for one. Frontier models
decode them back to the ASCII they encode.

So an attacker pastes something that displays as *"what is the refund policy?"* and carries
a complete jailbreak that no human reviewer can see. Not obfuscated — invisible. There is no
editor, no font, no rendering mode in which it shows up.

Our schema rail was checking `ord(char) < 0x20`. The Tag block is far above ASCII, so it
sailed through.

**And the obvious fix doesn't work either.** A Unicode general-category check still misses
it, because most of the block is category `Cn` — unassigned — and you can't reject `Cn`
wholesale: a Python whose Unicode database predates a newly-assigned emoji would then reject
legitimate text. The range needs an explicit test.

We now reject C0 minus tab/newline/CR, DEL, C1, categories `Cf`/`Co`/`Cs`, and the Tag block
by range — checked both as written and after NFKC, so a codepoint that only *decomposes*
into a disallowed one can't slip past.

The cost is documented, not hidden: rejecting all of `Cf` also rejects the zero-width joiner,
so multi-person emoji like 👨‍👩‍👧 are refused, along with the Arabic formatting marks. That's
the deliberate price, and the rejection reason names the exact codepoint so an operator can
see why.

---

### "What does fail closed mean here, and where do you deliberately not do it?"

A control that cannot run must **block and say so**. Not log-and-continue.

Fail closed applies to: the injection classifier (call error or unparseable verdict),
content safety, the image screen, audio with no transcriber, a NeMo engine error, and a NeMo
response carrying no rail-execution log.

That last one is worth naming. If the engine returns no log, we have **no evidence the rails
ran**, and no evidence is never a pass. That's a BLOCK.

**Three deliberate exceptions.**

**Advisory rails follow their configuration.** Topical and grounding are quality signals,
not safety controls. Configured advisory, they fail *open* — because a downed grounding
checker that manufactures "this may be a hallucination" on every answer is its own kind of
lie. Set `block=True` and both the enforcement and the fail direction flip together.

**The injection cache fails open, as a miss.** The cache makes no safety decision. A miss
means recompute, which runs the real control. Failing closed there would let a Redis blip
block every request in the system for zero security benefit.

**The PII engine degrades.** Presidio unavailable falls back to a regex engine, logged, with
the active engine queryable. It never *silently* stops redacting.

---

### "Walk me through a bug you found in the guardrails."

The one I'd pick is four lines and it opens every model-backed rail.

Each rail asks the classifier for strict JSON and falls back to a keyword scan when the
model returns prose. The fallback was `if lowered.startswith("no"): return BENIGN`.

That is not a verdict signal. It's a **prefix**. A classifier replying *"No doubt this is a
prompt injection attempt"* starts with "no" and was parsed as benign — so the input passed
the rail that had just correctly identified it as an attack. The mirror image,
`startswith("yes")`, is the same defect producing false blocks.

All four model-backed rails had it: injection, content safety, topical, grounding.

**Why it's such a good bug:** it's in a fallback path so it rarely executes, it looks
completely reasonable, and when it fires it fails in the direction that produces no visible
symptom. No log, no error, no metric. The system reports a clean pass.

The fix is one shared parser that accepts only an unambiguous signal: an explicit
`"<field>": true|false` key/value anywhere in the text, or a response whose *entire* content
is a bare yes/no token. Text carrying **both** returns "no verdict".

And the design decision that makes it reusable: **the parser doesn't decide the outcome.**
It reports whether a verdict exists. Blocking rails map "no verdict" to block; advisory rails
map it to whatever their config declares. One parser, four rails, correct direction each.

---

### "You mentioned NeMo Guardrails. Why two engines?"

Two audiences with genuinely different needs. Engineers want a fast function they can call
and unit-test offline. Security reviewers and auditors want a *readable policy document*.

Maintaining two implementations guarantees drift, and a drifted security policy is worse
than one policy because nobody knows which is enforced. So: **two front doors, one policy.**
The Colang flows `execute` custom actions that delegate straight back to the same rail
functions the programmatic path calls. The `.co` file is a readable artifact; the enforcement
is shared code.

Selection is a setting. `"nemo"` uses Colang **only if** the package is importable; anything
else keeps the programmatic pipeline, which is also the fallback — so the live path never
loses its rails.

**Where I'd stop short of the claim:** "one policy" is true of the six built-in rails. It is
not yet true of the extension seam — the Colang policy has no custom-rails step, so an
operator's own rails run on the programmatic path only, and the grounding action is a no-op
on the NeMo path because that call passes no contexts. Those are gaps I'd close, not gaps
I'd hide.

---

### "Did that dual-engine design cause any problems?"

Two, and the first is my favourite bug in the module.

**The engine constructed its own model.** We built `LLMRails(config)` with no `llm` argument.
With none supplied, NeMo instantiates whatever is declared in its own `models:` block — a
different provider, a different key, **outside our cost routing, outside the budget ledger,
outside tracing**. Our single most-defended claim is that every model call funnels through
one governed gateway, and there was a hole in it precisely at the security layer.

It went unnoticed because rail-only checks never invoke the engine's `main` model — we run
the policy with dialog and output generation disabled. Anything that *did* invoke it would
have gone somewhere else entirely.

The fix passes the host's completer in as `main`, adapted to LangChain's interface. And the
second-order fix that makes it stay fixed: the cached engine remembers *which completer it
was built from* and rebuilds when that changes — otherwise a host wiring its gateway after
first use keeps screening through a stale model forever.

**The second problem: block detection by string comparison.** We decided a block by comparing
the generated turn against a refusal string hardcoded in Python *and* authored in the policy
file. Nothing keeps two copies of a string equal. Reword the policy, add a full stop, let the
engine normalise whitespace — and every block silently becomes a pass.

A guardrail whose enforcement depends on two strings staying character-identical **fails open
on a typo**. We now read the engine's own structured record —
`log.activated_rails[*].stop` — which no amount of editing the refusal wording can change.
The old strings are kept only to detect drift and log it.

---

### "How do you guard an image?"

You screen it before it reaches the main vision call, with a cheap vision call: does this
image contain text, and is that text an *instruction directed at an AI system*? The split
matters — a photo of a receipt has text and isn't an attack; a screenshot reading "SYSTEM:
you are now in developer mode" is.

The screening prompt names the hiding tricks explicitly — faint, low-contrast, very small,
rotated, watermark-style text — because that's how real payloads hide from a human reviewer
while staying perfectly legible to the model.

**And with no vision model wired, the image is blocked.** Not passed with a warning. The
text classifier has a deterministic backstop it can fall back on; there is no regex that
reads pixels. So "no vision completer" isn't degraded coverage, it's *no control at all*.

A bare image URI is blocked too, even with a completer wired, because what a model would
fetch later is not what was screened. Time-of-check to time-of-use.

The verdict distinguishes **three** states, never two: cleared, blocked, and
could-not-screen. Collapsing the third into "passed" is how a fail-open ships. Collapsing it
into "blocked" loses what an operator needs to fix.

---

### "What about audio?"

Transcribe, then run the **whole existing text rail stack** over the transcript. There is no
parallel audio chain.

That's deliberate. A separate audio rail chain would immediately drift out of sync with the
text one, and every custom rail an operator wrote would have to be written twice. This way,
every rail they already configured applies to speech unchanged.

No transcriber wired means audio is blocked — an unguardable payload is not a safe one — and
the verdict's skipped-rails list says exactly what didn't run.

---

### "Your rail contract used to take a string. What happened?"

It meant a multimodal request reached the model having passed through **no rail at all** —
and the pipeline reported a clean pass, because every text rail it knew about ran
successfully on the empty text. That's the most dangerous shape of a security bug: a control
that reports success while not running.

We widened the contract to take a payload. Normally that's a breaking change for every rail
anyone wrote, and it wasn't, because of one adapter: it inspects what each rail was written
to accept — via a decorator marker or the first parameter's annotation — and hands it exactly
that. A legacy string rail facing text still receives the string, byte for byte.

**The part I'd emphasise is what happens to a string rail when an image arrives.** You can't
hand it a stringified blob (meaningless) and crashing is hostile. So it's **skipped** — and
the skip is **recorded in the verdict**. A rail that did not run is never counted among the
rails that did.

That honesty runs through the whole media chain. Every image verdict lists *"image
content-safety/topical screen (not implemented for pixels in this release)"* in its skipped
rails, rather than letting a reader assume coverage.

---

### "How do you defend against indirect injection — instructions inside a retrieved document?"

**Spotlighting**, from Microsoft's paper (arXiv 2403.14720). Two of its three instantiations
plus an explicit instruction:

**Delimiting** — each retrieved span is wrapped in randomised, per-block fences, so an
attacker who read our source still can't forge one.

**Datamarking** — a marker token is interleaved through the text, replacing every whitespace
run. That closes the escape where a span closes the fence early and writes outside it: the
marker signal is continuous rather than positional.

Plus a natural-language header telling the model that fenced, marked text is reference data
to report on, never instructions to obey.

**I'd be honest about what it is:** a strong hint, not a boundary. The model can still be
persuaded — it's a next-token predictor, not a reference monitor. Its value is converting
"the model has no idea this is untrusted" into "the model has been told clearly and
repeatedly". Microsoft's paper reports a substantial drop in attack success from that; we
haven't reproduced that measurement here, so I wouldn't quote a number. It's one layer.

The other half is the write-time defence: content validation before anything enters the
knowledge store, so obvious injection payloads are rejected at ingestion rather than
retrieved later.

---

### "Why the MLCommons taxonomy rather than your own list?"

Three reasons that all matter in an enterprise conversation.

The S1–S13 codes are **stable and interoperable** — a verdict means the same thing to
another team's pipeline. There are **public benchmark datasets** scored against them, so we
can measure ourselves rather than assert. And a category code in a trace is **auditable** in
a way that "toxicity: 0.83" is not.

It's the same taxonomy Meta's Llama Guard and NVIDIA's Aegis safety dataset classify against.

**We're not running Llama Guard**, and I'd say so directly: it needs a GPU and the deployment
target is 16 GB with none. So we use the NeMo "self check" pattern — prompt a cheap general
model with the taxonomy and ask for a structured verdict. That's a real quality trade with a
stated upgrade path, not an equivalent choice.

---

### "Why don't you stream tokens as the model produces them?"

Because the output rail needs the complete answer.

The ordering is `generate → guard_output → stream`. The stream node paces an
**already-guarded** string onto the socket in word chunks — real transport-level streaming,
the client renders progressively — but the text was cleared before any of it left.

Streaming raw model tokens would put unguarded text on the user's screen and make a block
unenforceable after the fact. You cannot unsay a leaked secret.

That's a cosmetic typing effect traded for a real safety property, and it's documented as
that trade in the node's docstring. If we ever want true token streaming it needs a
*streaming-aware output rail* — incremental scanning with the ability to withhold — not just
a streaming gateway call.

---

### "What are the gaps? What doesn't this catch?"

Stating them is part of the defence, because an unstated gap reads as a claim.

**No local safety model** — no Llama Guard, no cross-encoder. GPU constraint.

**No content-safety or topical screen over raw pixels.** Both need a second vision call per
image. Unsafe *imagery* is out of scope this release, and the media verdict says so in its
skipped-rails list.

**Encoding coverage is base64 only** — no hex, ROT13, Morse or URL-encoding.

**Homoglyph folding is Cyrillic and Greek only**, not the full UTS #39 table.

**No leetspeak folding** — `1gn0re` — because you can't fold it without false-positiving on
ordinary text containing digits.

**Non-English signatures cover seven languages** and only the "ignore the previous
instructions" family within them.

Everything outside those limits is the model classifier's job, which is exactly why the
deterministic layer never runs alone by choice — and why `detect_injection` logs a warning
when no completer is configured rather than silently proceeding.

---

### "How would you test this?"

Three levels, and the third is the one people skip.

**Unit-test each rail offline** with a fake completer. Every rail except grounding has a
pure layer, so the whole stack runs with no network and no API key — 220 tests, no API key.

**Red-team battery**: 28 probes — 20 attacks and 8 **benign controls**, because a rail that
blocks everything is not a fix, it's a rail operators switch off. Each probe carries a stable
id, a category, its OWASP identifier, the expected outcome, and — crucially — a `needs_llm`
flag marking the cases only the model layer can catch. That last field is the honesty
mechanism: it lets an offline run explain a leak as an expected model-layer gap instead of
silently counting it as a guardrail failure. And the runner feeds every probe through the
**real** `check_input` and records the **actual** verdict, never a fabricated one.

**Test the honesty claims.** The docstring says coverage is seven languages and base64 only —
there's a test asserting exactly that. The action registration list must cover every
`execute` in the Colang files — there's a test parsing both. When two artifacts must stay in
sync and one isn't code, the fix is never "be careful", it's a test that reads both and
asserts the relationship.

And explicitly test the **failure directions**: an unparseable classifier reply must block, a
missing rail log must block, an image with no completer must block, and a broken cache must
miss rather than block.

---

### "So how effective is it? Give me a number."

I'll give you one and then tell you what it isn't.

Running the battery offline — deterministic layers only, no model wired — the rails neutralise
**17 of 20 attacks, with 0 false positives on the 8 benign controls.** All three leaks are
cases flagged `needs_llm`, so they're expected to reach the model layer.

Now the caveats, because that number is easy to over-claim from. It's measured against *our
own* 28-case battery, not a published attack corpus. It's the deterministic-only reach, so
wiring a completer raises it. And it says nothing about a determined adversary who reads our
source and pads a payload past the three-word gap allowance.

What it's genuinely good for is regression. The threshold is set at the offline
deterministic reach — 75% block rate, 0% false positives — so a change that weakens a
signature, or one that starts blocking ordinary traffic, fails the run.

**And I'd say the honest thing out loud: prompt injection is not solved.** Published attack
success rates against defended systems stay high. What layering buys you is that the cheap,
high-volume attacks stop, the expensive ones cost more, and — the part I care about most — the
system says loudly when a control could not run, instead of reporting a clean pass.
