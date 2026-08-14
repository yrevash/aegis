# Evals & LLM-Ops — the concept, from zero

No code. Why "it looks better" is not an answer, what the three evaluation layers are, and
what a closed improvement loop actually requires.

---

## The problem

You change a system prompt. Is the system better?

For ordinary software this question has a boring answer: run the tests. A function either
returns 4 or it does not.

For an LLM system nothing about that transfers:

- **There is no single correct output.** Twenty phrasings can all be right.
- **The output is non-deterministic**, and even at temperature 0 a provider change moves
  it.
- **Changes are non-local.** Adding one sentence about tone can change tool selection, or
  quietly stop the model citing sources.
- **The failure mode is fluent.** A wrong answer looks exactly like a right one.

So people fall back on "I tried five questions and it looks better." That fails for a
reason worth naming precisely: **you tried the five questions you were thinking about.**
The regression is in the sixth. And nobody re-checks the five from last month, so quality
drifts monotonically downward one plausible improvement at a time.

**Evaluation is what makes a change reviewable.** Without it, "improve the prompt" is not
engineering.

---

## Three layers, three different jobs

No single technique is enough. Serious systems run all three, and they trade off along the
same axes every time: cost, determinism, and what they can actually see.

### Layer 1 — the offline deterministic gate

A **fixed corpus** of labelled cases. Each carries a query, the document(s) a correct
retrieval must surface (**gold docs**), and the claim keywords a grounded answer should be
able to cite.

Score with **deterministic, lexical** measures — no model calls at all:

- **context precision@k** — of the top *k* retrieved sources, what fraction came from a
  gold document? *Did we rank the right thing first?*
- **context recall** — of the gold documents, what fraction appear anywhere in the
  results? *Did we surface what the answer needs?*
- **groundedness** — what fraction of the expected claims appear in the assembled context?
  *Could a faithful answer cite them?*

Free, instant, and **deterministic**, so it runs in CI on every commit and a regression is
a red build.

What it cannot see: whether the *answer* was any good. It measures retrieval, which is a
proxy — necessary, not sufficient.

**These are proxies, not the library metrics they are named after.** RAGAS defines these
concepts and computes them with an LLM; computing them with token overlap is a
*deterministic proxy for the same idea*. Calling them "RAGAS metrics" would be an
overclaim, and the distinction matters when someone asks what the number means.

### Layer 2 — LLM-as-judge

Give a strong model the question, the retrieved context, and the answer, and ask it to
score **groundedness** (is every claim supported by the context?) and **relevance** (does
it address the question?).

This sees what lexical overlap cannot: paraphrase, contradiction, hedging, an answer that
uses all the right words and says the wrong thing.

The costs are real:

- **Money and latency** — one or two model calls per case.
- **Non-determinism** — the same answer can score differently on two runs.
- **Bias** — judges favour longer answers, and favour outputs from models like
  themselves.
- **It can fail.** And this is the important one.

**A judge that cannot produce a verdict has not scored zero.** Those are different
statements, and a system that conflates them will do something catastrophic — see
[`30-deep-dive.md`](30-deep-dive.md).

Two rules save you here. **Judge a generated answer, never the context against itself** —
scoring the context as though it were the answer makes groundedness ~1.0 by construction,
a number with no signal in it. And **a judge failure must propagate**, not become a
number.

### Layer 3 — trace-level evaluation

The first two grade the *answer*. This grades the **steps**.

Given a completed run, score each facet separately: did retrieval surface relevant
context? was the chosen tool appropriate? was the guardrail verdict right for that input?

Why it matters: an answer score of 0.4 tells you the run was bad. It does not tell you
*which part*. Per-step scores turn "quality dropped" into "tool selection is failing 40% of
the time while retrieval is fine" — which is actionable, and is exactly what the diagnosis
stage needs.

---

## The evaluation trap that outranks all the others

Two ways to score a case that has no label for a metric:

**Score it 1.0.** Nothing to check, so nothing failed.
**Score it "not measured."**

The first is a disaster, and the mechanism is arithmetic. If unlabelled cases score 1.0
and the corpus mean is taken over *all* cases, then **adding unlabelled cases raises the
mean**. Someone adds twenty unlabelled cases to broaden coverage — a good instinct — and
the gate's threshold is now held up by cases that measure nothing, while a real regression
runs underneath.

The correct rule: **an unlabelled facet is an absent measurement, not a passing one.** It
contributes to neither the numerator nor the denominator. And a metric that *nothing*
labelled is `None`, which must **fail** the gate — a gate cannot report clearing a bar it
never measured against.

The same principle covers a metric you honestly cannot compute offline. RAGAS *answer
relevancy* needs a generation plus a semantic-similarity model. Reporting it as
`computed: false, value: null` is honest. Reporting a plausible number is not.

---

## The LLM-Ops loop

Evaluation tells you where you are. A **loop** is what moves you.

```
Trace  →  Eval  →  Diagnose  →  Gate  →  Release  →  (back to Trace)
```

**Trace** — every run is instrumented, so there is something to grade.
**Eval** — grade the answer and the steps; persist one row per graded facet.
**Diagnose** — cluster recent failures, and ask a model to propose a better prompt.
**Gate** — score the proposal against the current baseline on a real eval.
**Release** — promote it, or escalate it to a human, based on the *risk of the change*.

The whole design question is: **how much of that is allowed to be autonomous?**

---

## Prompt versioning, and why it is the enabling piece

If the loop is going to change the system's instructions, those instructions have to be a
**first-class versioned object**, not a string in a source file.

The lifecycle:

| Status | Meaning |
|---|---|
| **draft** | Proposed by the optimizer or a human. Not live. |
| **staged** | Passed the eval gate; awaiting human approval. |
| **active** | The one live version for its key. At most one. |
| **archived** | A former active version, retained for rollback and audit. |

Four properties follow from that table:

**Rollback is one call.** Reactivate the previous version. No redeploy.
**Nothing is deleted.** Every version is auditable — you can answer "what were the
instructions when that answer was produced?"
**There is a floor.** When nothing is active, the hand-authored adapter prompt is the
baseline. The loop builds *on* it and can never go below it.
**The hot path never reads the database.** An in-process cache holds the active version;
promotion updates it.

That last one has a subtle failure mode worth flagging now: if the cache is updated when
the row is *written* rather than when the transaction *commits*, a rollback leaves the
cache serving a prompt that never existed.

---

## Tiered release: gate on the risk of the change

An eval gate says "the draft is better." That is not sufficient to ship it autonomously,
because the eval measures a **sample** and the change applies to **everything**.

So classify the change, then gate by class — the same pattern as tool risk tiers:

- **low** — a small wording nudge; no safety, tool or policy terms touched; config
  unchanged or a bounded tweak of a known-tunable key.
- **high** — a large diff, *or* any change in how often a safety/guardrail/tool/approval
  term appears, *or* a change to a model/tool/permission config field.
- **medium** — everything in between.

Then three autonomy modes: `auto` promotes any eval-passing draft; `manual` always
escalates; **`tiered`** — the enterprise default — auto-promotes low risk and escalates
medium and high to a durable human approval inbox.

**Why safety-term counting is the right heuristic despite being crude.** A prompt
optimiser told "make the agent stop making these mistakes" will cheerfully drop a
constraint that was causing refusals. That change *improves* the eval score, because the
eval measures helpfulness and not the constraint. Counting occurrences of guardrail and
policy vocabulary catches exactly that class of change and routes it to a human. It has
false positives; the asymmetry of cost makes that the right trade.

---

## What makes a gate real

An eval gate is only worth having if it can **fail**. Three ways gates quietly stop
failing:

**Vacuous pass.** If both the draft and the baseline score the same fixed number — say
because the scorer errored and returned 0.0 for both — then `draft < baseline + 0` is
False, and the gate *passes*. It promotes everything. This is not hypothetical; see
[`30-deep-dive.md`](30-deep-dive.md).

**A constant scorer.** If the score does not actually depend on the candidate prompt, the
gate is comparing a number to itself. The scorer must *generate an answer under the
candidate prompt* and grade that answer, or it measures nothing.

**NaN.** `NaN < anything` is False. A NaN score sails through any comparison-based gate.
It has the identical vacuous-pass shape and needs the identical treatment: refuse to gate
on an unusable measurement.

The unifying rule: **a control that cannot run must stop the release, not wave it
through.** Fail closed.

---

## Rollback is harder than it looks

"Reactivate the previous version" needs a definition of *previous*.

The naive one: order archived versions by when they were last active, take the newest.

Now roll back twice. The first rollback archived v3 and reactivated v2. The second
rollback looks for the most-recently-active archived version — and **v3 is it**, because
its activation timestamp is more recent than v2's. So the second rollback re-promotes the
broken version you just rolled back from. Rollback oscillates instead of walking history
backwards.

The fix is to make the activation timestamp mean "**is a valid revert target**" and to
*clear* it on the version you roll back *from*. A pleasant side effect: a rejected draft —
archived but never live — has no timestamp and is therefore never a revert target either.

---

## What you should now be able to explain

- Why "it looks better" fails, and the specific mechanism of quality drift
- The three evaluation layers, what each sees, and what each costs
- Why lexical proxies are proxies, and why naming them after a library would overclaim
- Why an unlabelled case scoring 1.0 inflates a corpus mean and masks regressions
- Why an unmeasured metric must fail the gate rather than pass it
- The Trace → Eval → Diagnose → Gate → Release loop
- Why prompts must be versioned objects with a floor, and what the cache-publish hazard is
- Change-risk tiers, and why counting safety terms is the right crude heuristic
- Three ways a gate stops being able to fail: vacuous pass, constant scorer, NaN
- Why rolling back twice can re-promote the version you just rolled back from

**Next:** [`10-theory.md`](10-theory.md).
