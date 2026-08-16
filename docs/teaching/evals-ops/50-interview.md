# Evals & LLM-Ops — interview questions and answers

The vacuous-pass story is the single strongest thing in this document. Learn it properly.

---

### "How do you know a prompt change made things better?"

Three layers, because no single one is sufficient.

**An offline deterministic gate.** A fixed corpus of labelled cases — each carrying the
gold documents a correct retrieval must surface and the claim keywords a grounded answer
should be able to cite. We score context precision@1, context recall and groundedness with
pure token and substring overlap. No model calls, so it is free, instant and exactly
reproducible, and it runs in CI on every commit. Six cases today, scoring 0.833 / 1.000 /
1.000 against floors of 0.66 / 0.95 / 0.85 — one case ranks its gold document second, which
is where the 0.833 comes from.

**An LLM-as-judge pass.** A reasoning model grades a generated answer for groundedness and
relevance against the retrieved context. It sees paraphrase and contradiction that lexical
overlap cannot. It costs money, it is non-deterministic, and it can fail — which turns out
to matter enormously.

**Trace-level evaluation.** After a run, grade each *step* separately — retrieval, tool
selection, guardrail verdict — not just the answer. An answer score of 0.4 tells you the
run was bad; per-step scores tell you *which part*, which is what diagnosis needs.

**And the honest framing on layer one:** those are *deterministic proxies* for the RAGAS
metric ideas, computed with overlap rather than an LLM. Calling them RAGAS metrics would be
an overclaim.

---

### "Why isn't 'I tried it and it looks better' enough?"

Because you tried the five questions you were thinking about, and the regression is in the
sixth.

Nobody re-checks last month's five, so quality drifts monotonically downward, one plausible
improvement at a time. And LLM changes are non-local — adding a sentence about tone can
change tool selection or quietly stop the model citing sources.

A fixed corpus makes the change *reviewable*. Without one, "improve the prompt" is not
engineering.

---

### "Tell me about the worst bug you found."

The release gate passed **vacuously** when the judge failed, and auto-promoted every
candidate prompt.

The chain has four links. The judge runs on a reasoning model, which routinely wraps its
JSON in a `<think>` preamble or a markdown fence. The parser caught the resulting decode
error and returned a verdict of **0.0**. The scorer averaged those zeros. And the scorer is
called **twice** — once for the draft, once for the baseline — so both scored 0.0.

Then the gate tests `draft < baseline + margin`, with a default margin of 0.0. So
`0.0 < 0.0` is **False**, the rejection branch does not fire, the gate passes, and in
tiered mode a low-risk draft is auto-promoted to production.

**Say what that means operationally.** A judge outage — one rate limit, one deployment that
started emitting think-tags — promotes *every single candidate prompt* to production. Not
"fails to catch a bad one". Promotes all of them, with no measurement behind any of it.

And it is silent. The API returns `promoted`, with `eval_score: 0.0` and
`baseline_score: 0.0`. Two zeros read as "the eval scored badly" — a quality problem — not
"the eval did not run" — a control failure.

---

### "Why did returning 0.0 seem like the right thing?"

Because it *looks* defensive: if we cannot grade it, score it worst.

The reasoning fails for one specific reason: **the gate is a comparison, not a threshold.**
In a threshold gate, a conservative constant fails safely. In a comparative gate, a constant
applied to *both sides* cancels, and the comparison degenerates.

That is the transferable insight: **a conservative default is only conservative relative to
the operation that consumes it.**

---

### "So how is it fixed?"

Two halves, and both are necessary.

**Tolerate real drift.** The parser now strips `<think>` blocks — including unterminated
ones — strips markdown fences, and extracts the first *balanced* `{...}` object from
surrounding prose with a depth scan. That formatting is expected behaviour from a reasoning
model, and treating it as failure would turn a working judge into an outage.

**Raise on anything genuinely unusable.** A named `JudgeUnavailableError`, which
deliberately propagates out of the scorer and abandons the release with the draft left
DRAFT. There is a comment in the source saying "do NOT wrap this in a try/except that
yields 0.0", because the next person to see it will want to.

A real 0.0 still parses as 0.0. The fix distinguishes "scored zero" from "could not score"
without losing the ability to score zero.

**And the sibling with the identical shape: NaN.** `NaN < x` is False for every x, so a NaN
score sails through the same comparison. It is rejected at two layers — the parser treats
NaN and infinity as unparseable, and the gate itself requires a finite float before it will
compare anything. Two layers because the eval function is *injected*, so the gate cannot
assume the parser is the only source of a score.

There is a third family member: zero graded cases raises too. Empty is not zero either.

The rule underneath all three: **a control that cannot run must stop the release, not wave
it through.**

---

### "Tell me another one."

Unlabelled eval cases scored a perfect 1.0 and inflated the corpus mean.

A case carrying no gold documents has nothing to be right or wrong about, and the code
returned 1.0. Then the aggregate averaged over **all** cases.

The arithmetic is unforgiving. Take a corpus of one labelled case that scores groundedness
**0.0** — a total retrieval failure — plus nine cases nobody has labelled yet. The old mean
is `(0.0 + 9 × 1.0) / 10 = 0.90`, which **clears the 0.85 groundedness threshold**. Under the
fix the mean is `0.0` over one contributor, and it fails. Same corpus, same retrieval; one
number says the system is fine and the other says it is completely broken.

**What makes it a trap is that the triggering action is a good one.** Someone broadens the
corpus for a new document type before they have labelled it — normal, correct behaviour —
and silently raises the headline metric, which can hold the threshold up while a real
regression runs underneath.

The fix: an unlabelled facet is `None` — *not measured* — and the mean is over contributors
only, with the contributor count reported so nobody mistakes a 2-case mean for a 40-case
one. **And an unmeasured metric FAILS the gate**, which is the half people miss. A gate
cannot report clearing a bar it never measured against.

The same principle proactively: RAGAS answer relevancy genuinely cannot be computed offline
— it needs a generation and a similarity model — so it is surfaced as
`computed: false, value: null` rather than faked.

---

### "And the judge one?"

The judge was grading the retrieved context against itself.

The call passed the context in as both the context **and** the answer. The judge's question
is "is every claim in the ANSWER supported by the CONTEXT?" — so if the answer *is* the
context, every claim is supported by construction. Groundedness came back ~1.0 on every
case, for any corpus, for any system.

And it appeared on the report as a **model-graded score** — the sophisticated measurement
the deterministic proxies cannot provide.

Nobody noticed because ~1.0 is *plausible*: a well-grounded RAG system should score high.
**The real tell is that it never moved. A metric that never changes is not measuring.**

The fix generates an answer from the retrieved context first, then judges that answer. Two
model calls per case, and the second is only meaningful because of the first.

The same structure is what makes the release scorer a real gate: it generates under the
**candidate** prompt and judges that, so the score genuinely moves with the prompt. A
scorer that does not generate under the candidate cannot be prompt-dependent — and a gate
comparing a constant to itself is the vacuous-pass bug in different clothes.

---

### "How does a change get promoted?"

An eval gate plus a **change-risk classifier**, then a tiered decision.

The eval gate is necessary and not sufficient: it measures a *sample*, and the change
applies to *everything*. So the diff is classified — deterministically, with no model call,
because the classifier that decides whether a model's proposal is safe must not itself be a
model.

**HIGH** if the diff exceeds 40% of lines, *or* any safety term's whole-word count changed,
*or* a config key containing model/tool/permission/role/scope changed. **LOW** if the diff
is under 15% and config is unchanged or a bounded tweak of temperature/top_k/top_p.
**MEDIUM** otherwise.

Then three autonomy modes. `auto` promotes anything that beat the baseline; `manual` always
escalates; **`tiered`** — the default — auto-promotes at or below a configurable ceiling
(default `low`) and escalates the rest to a durable approval inbox where a human decides.

---

### "Why count safety terms? That seems crude."

It is crude, and it targets a specific threat.

A prompt optimiser told "make the agent stop making these mistakes" will cheerfully **drop
a constraint that was causing refusals**. That change *improves* the eval score, because
the eval measures helpfulness and not the constraint. The gate would pass it.

Counting occurrences of guardrail, policy, tool and approval vocabulary catches exactly
that class and routes it to a human.

**Counts, not presence** — deliberately. Dropping one of three "never" constraints leaves
the word present, and a presence check would miss it entirely.

It over-triggers: any legitimate rewrite touching that vocabulary escalates. Given the
asymmetry — a false escalation costs a human thirty seconds, a false promotion removes a
safety constraint from production — that is the right side to be wrong on.

It is also the control-theory answer to runaway: **the loop can improve the prompt and
cannot autonomously remove the rules it operates under.**

---

### "How do you roll back?"

One call. Archive the current active, reactivate the previous one. No redeploy — the
harness reads an in-process cache of the active version.

**And there is a bug here worth telling.** The naive implementation orders archived
versions by when they were last active and takes the newest.

Roll back twice and it breaks. The first rollback archives v3 and activates v2, but v3
keeps its activation timestamp. The second rollback looks for the most-recently-active
archived version — and **v3 is it**, because its timestamp is newer than v1's. So the
second rollback re-promotes the version you just rolled back from. It oscillates between v2
and v3 and can never reach v1.

Picture when that fires: an incident, the operator rolls back, it helps a bit, they roll
back again to go further into history — **and the broken prompt comes straight back**, as a
result of doing the documented thing.

The root cause is one field answering two questions: *when was this last live* and *is this
a valid revert target*. Those coincide until you roll back.

The fix redefines the field as the second, and **clears it on the version you roll back
from**. Two free consequences: a rejected draft — archived but never live — has no marker
and can never be a revert target, and the historical fact is preserved separately in a
notes field.

---

### "What stops a double-clicked approval doing something bad?"

Two guards, at two layers.

**A lifecycle guard.** Only a STAGED version may be decided. Without it, a second approve
re-promotes and archives whatever legitimately replaced it — and a reject arriving *after*
an approve archives the version that is now ACTIVE, leaving the prompt key with **no active
version**, so every run silently drops to the floor prompt. The mirror guard is that only a
DRAFT may be released, so a version cannot be released twice.

**An atomic claim.** The durable approval row is claimed with
`UPDATE ... WHERE id = ? AND status = 'PENDING'`, checking `rowcount`, **before** the draft
is touched, all in one transaction. Zero rows means someone already decided it, and the
response returns the **recorded** decision rather than the requested one — which is the
correct semantics for an idempotent operation. And if applying the decision then raises,
the whole transaction including the claim rolls back, so the row stays PENDING and
decidable.

---

### "You cache the active prompt. What breaks?"

The hot path reads the active version synchronously from a process-wide cache rather than
hitting the database per turn.

The failure mode is *when* you publish to it. `promote` and `rollback` deliberately leave
the transaction open for the caller, so caching at **flush** time publishes a prompt that
may never be committed. A caller rollback or a crash would leave the cache serving a
phantom system prompt to every run, and nothing would correct it until the next startup
refresh.

So the publish is bound to the session's `after_commit` event, one-shot, with the payload
snapshotted at bind time because the commit expires the ORM object.

**The cache is now exactly as durable as the row it mirrors.** That is the invariant.

---

### "How does diagnosis decide what to fix?"

It reads the recent failing eval rows for that prompt, tallies by metric, and asks a
reasoning model to rewrite the prompt — writing the result **only as a DRAFT**, never live.

**The important detail is that it ranks by failure RATE, not volume.** A facet graded 500
times with 20 failures is healthier than one graded 25 times with 15 — but the raw tally
ranks the first as the worse offender and points the optimiser at the healthy facet. So we
count every graded row over the same window as the denominator and steer by rate. The
optimiser prompt literally says "fix the highest rate, not the highest count".

Two implementation details fall out. The window is by **row id, not timestamp**, because
ids are monotonic and compare identically on every dialect, whereas a server-side
`CURRENT_TIMESTAMP` stored as a naive string on SQLite does not compare against a
timezone-aware Python parameter — the window would silently return nothing. And the
denominator is clamped, because a row written after the window query would produce a rate
greater than 1.

We also always show the *known* facets, even at 0%, so "retrieval is fine, tools are not"
is legible rather than inferred from an absence.

---

### "What happens if the optimiser returns garbage?"

No draft. Non-JSON, a non-dict, a missing or blank prompt — all yield `None`, and the whole
optimiser call is wrapped so a transport failure yields no draft rather than a crash.

No draft is a safe outcome; a garbage draft is not. And either way the eval gate and the
risk classifier are still downstream of it.

---

### "What's the weakest part of this?"

The eval corpus is small, and I would say so before being asked.

Three seed cases per release candidate, and a default margin of 0.0 — "strictly better on
this sample". At n=3 the standard error of the mean is large enough that a small
improvement is inside the noise. It is a deliberate cost trade: each case is a retrieve
plus a generate plus a judge call, so scoring the whole corpus per candidate is expensive.
Both the subset size and the margin are configurable.

**The honest description is that the gate is a smoke test against regression, not a proof
of improvement.** What actually carries the safety weight is the *risk classifier* and the
tiered escalation — a low-risk wording change auto-promoting on a weak signal is
recoverable in one rollback call; anything touching safety, tools or config goes to a human
regardless of the score.

If I wanted to strengthen it: score the full corpus on a nightly schedule rather than
per-candidate, and require both the fast gate and the nightly one to agree before a promote
sticks.

---

### "How would you test an eval system?"

The hard part is that it grades other things, so its own bugs hide.

**Test the gate's ability to FAIL.** Every one of these bugs is a gate that stopped being
able to fail. So: a judge that raises must abort the release with the draft left DRAFT. A
NaN score must refuse. Zero graded cases must refuse. Those are regression tests confirmed
*failing* on the pre-fix code, which is what makes them credible.

**Test the denominators.** Add an unlabelled case to a fixture corpus and assert the mean
does not move. Assert that a metric no case labelled comes back `None` **and** that the
report fails.

**Test that scores are actually dependent on their input.** The self-grading bug and the
constant-scorer bug are both "this number cannot move". Assert that two materially
different system prompts produce different scores from the release scorer.

**Test the lifecycle as a state machine.** Release twice must raise. Decide twice must
return `already_decided` with the recorded decision. Roll back three times must walk
history backwards, not oscillate — that last one is a three-line test that would have
caught the rollback bug immediately.
