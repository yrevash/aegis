# Evals & LLM-Ops — the theory

Metric definitions, judge reliability, the statistics of small eval corpora, and the
control theory of a self-modifying system.

---

## 1. The retrieval metrics, defined

Let $R_k$ be the top-$k$ retrieved sources and $G$ the gold document set for a case.

**Context precision@k**

$$P@k = \frac{|\{r \in R_k : \text{doc}(r) \in G\}|}{|R_k|}$$

*Of what we ranked highest, how much was relevant?* At $k=1$ this asks the sharpest
possible question: **is the top result the right one?** That is the right $k$ for a
single-gold corpus, because it is the only setting where the metric can distinguish a
correctly-ranked system from one that merely retrieved the right thing somewhere.

**Context recall**

$$R = \frac{|\{g \in G : g \in \text{docs}(R)\}|}{|G|}$$

*Of what we needed, how much did we surface at all?* Over the whole result list, not the
top $k$ — recall is about presence, precision about position.

**Groundedness (faithfulness proxy)**

$$F = \frac{|\{c \in C : \text{normalise}(c) \subseteq \text{normalise}(\text{context})\}|}{|C|}$$

for expected claims $C$. *Could a faithful answer cite these from what was retrieved?*

Normalisation matters more than it looks: lowercase, and collapse every run of
non-alphanumeric characters to a single space. That neutralises punctuation, newlines,
markdown fences, and — specifically — **spotlight datamarking**, where retrieved text is
delimited with a marker character between words as a prompt-injection defence. Without
normalisation, `original▁payment▁method` fails to match the claim `original payment
method`, and a security control silently tanks your quality metric.

### Precision/recall trade-off

Return more and recall rises while precision falls. Return one and the reverse. This is why
both are reported and why a single "retrieval quality" number is a lie: they measure
opposite failure modes.

### These are proxies

RAGAS defines *context precision*, *context recall* and *faithfulness* and computes them
with an LLM. Computing them by token and substring overlap is a **deterministic proxy for
the same idea**, not the library's metric.

Two properties make the proxy worth having: it is free, and it is **exactly reproducible**,
so a CI gate can assert on it. Two properties make it limited: it cannot see paraphrase,
and it cannot see contradiction.

RAGAS **answer relevancy** cannot be proxied this way at all — it needs a generation plus a
semantic-similarity model. The honest handling is to surface it as *not computed* rather
than substitute something that looks like it.

---

## 2. LLM-as-judge: what the literature says

Zheng et al. (2023), *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*, is the
reference. Its headline finding is that a strong judge agrees with human preference at
roughly the rate two humans agree with each other — around 80% — which makes it a usable
instrument.

It also enumerates the biases, and you should be able to name them:

| Bias | Effect |
|---|---|
| **Position** | In pairwise comparison, the first-presented answer is favoured. Mitigate by evaluating both orders. |
| **Verbosity** | Longer answers score higher, independent of quality. |
| **Self-enhancement** | A judge favours outputs from its own model family. |
| **Limited reasoning** | Judges are weak at grading maths and code. |

**Pointwise vs pairwise.** Pointwise ("score this 0–1") is cheap and its absolute values
drift. Pairwise ("which is better?") is more reliable and costs $O(n^2)$ comparisons, or
$O(n \log n)$ with a tournament.

Aegis uses **pointwise**, which is the right call for a gate that compares two candidates
on the *same* corpus: absolute drift largely cancels when you subtract, and the cost is
linear.

### Choosing a judge model, and the failure it invites

A **reasoning** model is the better judge. It also emits `<think>…</think>` preambles,
markdown fences, and prose around its JSON. That is *routine formatting drift*, not a
failure — and treating it as one turns a working judge into an outage.

So the parser has to be deliberately two-tier:

- **Tolerant** of expected drift: strip think-tags, strip fences, extract the first
  balanced `{...}` from surrounding prose.
- **Intolerant** of anything genuinely unusable: raise a named error rather than return a
  number.

The distinction is the whole design. A parser that is uniformly strict produces false
failures; one that is uniformly lenient produces the vacuous-pass catastrophe.

### The self-grading trap

Groundedness asks: *is every claim in the ANSWER supported by the CONTEXT?*

If you pass the context in as the answer, every claim is trivially supported. The judge
returns ~1.0, on any corpus, for any system. The number is structurally guaranteed and
means nothing — and it appears on the report labelled as a model-graded score.

The invariant: **the judge grades a generated answer against the retrieved context.**
Generation first, then grading of what was generated. Two model calls per case, and the
second one is only meaningful because of the first.

---

## 3. The statistics of a small eval corpus

An eval corpus is small — tens of cases, not thousands. That has consequences people skip.

**Per-case scores are Bernoulli-ish; the mean has real variance.** With $n$ cases and a
true pass rate $p$, the standard error of the observed mean is $\sqrt{p(1-p)/n}$. At
$n = 20, p = 0.8$ that is about **0.09**. A move from 0.80 to 0.85 is inside the noise.

Two practical consequences:

- **Set thresholds conservatively below observed performance** — high enough that a real
  regression in fusion, assembly or corpus mapping trips them, low enough that noise does
  not.
- **Be sceptical of small margins.** A gate margin of 0.0 means "strictly better on this
  sample", which is a weak statement on 3 cases. It is a deliberate choice — the
  alternative is never promoting anything — but it must be named as such.

**The denominator is where the bugs are.** Two constructions to keep separate:

$$\text{mean}_{\text{wrong}} = \frac{\sum_{\text{labelled}} s_i + |\text{unlabelled}|}{n_{\text{total}}} \qquad \text{mean}_{\text{right}} = \frac{\sum_{\text{labelled}} s_i}{n_{\text{labelled}}}$$

The left one scores unlabelled cases 1.0 and averages over everything, so **adding
unlabelled cases raises the mean**. The right one is a mean over contributors only, and it
reports the contributor count alongside so nobody mistakes a 2-case mean for a 40-case one.

And when $n_{\text{labelled}} = 0$, the metric is `None` — *not measured* — which must
**fail** the gate. A gate cannot report clearing a bar it never measured against.

---

## 4. Trace-level evaluation and the diagnosis signal

Step-level scoring produces one row per graded facet, namespaced by the span kind that
produced it — retrieval, tool, guardrail — plus the answer.

**Why namespacing matters.** Diagnosis clusters failures by facet. If every row is just
`score`, "tool selection is failing" is not expressible.

**Volume is not a signal — rate is.** This is the part people get wrong.

Take the top *N* failing rows and tally by metric. A facet that ran 500 times with 20
failures produces a bigger tally than one that ran 25 times with 15. The raw count ranks
the *first* as the worse offender, and points the optimiser at the healthy facet.

$$\text{failure rate}(m) = \frac{\text{failures}(m)}{\text{graded}(m)}$$

So you need the **denominator** — how many rows of that facet were graded at all over the
same window the failures were drawn from — and you steer by rate.

Two implementation details that fall out of that:

**Window by a monotonic id, not a timestamp.** Ids are monotonic with insertion and
compare identically on every dialect. A server-side `CURRENT_TIMESTAMP` stored as a naive
string on SQLite does not compare against a timezone-aware Python bind parameter, so a
`ts >=` window silently returns nothing.

**Clamp the denominator.** A row written *after* the window query would make a rate > 1.
`total = max(total, failures)` keeps the arithmetic sane.

**Show the clean facets too, at 0%.** "Retrieval is fine, tools are not" is legible;
retrieval's absence from a list is not — the optimiser cannot distinguish "healthy" from
"never graded".

---

## 5. Change-risk classification

A deterministic classifier over `(old_prompt, new_prompt, old_config, new_config)`. No
model call — the classifier that decides whether a model's proposal is safe must not itself
be a model.

**Diff size.** Using `difflib.SequenceMatcher` over lines:

$$\text{changed fraction} = \frac{\max(|old|,|new|) - \sum \text{matching block sizes}}{\max(|old|,|new|, 1)}$$

Above a high threshold → HIGH. Below a low threshold → a precondition for LOW.

**Safety-term counting.** For each term in a watchlist — *ignore, guardrail, safety, tool,
approval, never, policy, system prompt* — compare **whole-word occurrence counts** between
the two prompts. Any change → HIGH.

Counting occurrences rather than presence is deliberate: dropping one of three "never"
constraints leaves the word present, and a presence check would miss it entirely.

The threat model is specific. **A prompt optimiser told "make the agent stop making these
mistakes" will remove a constraint that was causing refusals** — which *improves* the eval
score, because the eval measures helpfulness, not the constraint. Term counting catches
exactly that class and routes it to a human. It over-triggers; the asymmetry of cost makes
that correct.

**Config changes.** A change to any key whose name contains a critical marker — *model,
tool, permission, role, scope* — is HIGH. A change confined to known-tunable keys
(*temperature, top_k, top_p*) within a per-key bound can still be LOW. Anything else is at
least MEDIUM.

Then a total order over tiers and a configurable **auto-promote ceiling**: promote iff
`risk ≤ ceiling`. Default ceiling is `low`.

---

## 6. The control-theory framing

A self-improving system is a feedback loop, and loops have failure modes that are easier to
see with that vocabulary.

**Positive feedback.** If the eval and the optimiser share a blind spot, the loop drives
*into* it: each iteration scores better on the thing you measure while the unmeasured thing
degrades. Goodhart's law, mechanised.

**Oscillation.** Promote, regress, roll back, promote again. Damping comes from the margin
(a change must be *strictly* better) and from the DRAFT-only guard (a version can be
released once).

**Runaway.** The loop must not be able to change its own constraints. Which is exactly why
safety-term changes are classified HIGH: the loop can improve the prompt, and it cannot
autonomously remove the rules it operates under.

**The floor.** The registry always falls back to the hand-authored adapter prompt. The loop
builds on it and can never go below it — a hard lower bound on degradation, independent of
anything the optimiser does.

That set — margin, one-shot release, HIGH-risk escalation of constraint changes, and a
prompt floor — is the damping that makes autonomy tolerable.

---

## 7. Idempotency in the release path

Human decisions arrive over a network. They get double-clicked, retried by a proxy, and
replayed out of order.

Two guards, at different layers:

**A lifecycle guard.** Only a DRAFT may be *released*; only a STAGED version may be
*decided*. Without the second one, a replayed approve re-promotes (archiving whatever
legitimately replaced it), and a reject arriving after an approve archives the version that
is now ACTIVE — leaving the prompt key with **no** active version, so every run silently
drops to the floor prompt.

**An atomic claim.** `UPDATE approvals SET status=... WHERE id=? AND status='PENDING'`,
checking `rowcount`. Zero rows means someone already decided it. The claim must happen
**before** the draft is touched, and the whole thing must be one transaction, so a failure
downstream rolls the claim back and the row stays decidable.

A replayed decision then returns the **recorded** decision, not the requested one — which
is the correct semantics for an idempotent operation.

---

## 8. Rollback ordering, formally

Let versions carry `activated_at`, set when promoted.

**Naive rollback:** archive the current active; take the archived version with the greatest
`activated_at`; reactivate it.

Trace two rollbacks from an active v3 (v2 previously active, v1 before that):

1. Roll back once: v3 → archived with `activated_at = t3`; v2 → active.
2. Roll back again: archived candidates are v3 (`t3`) and v1 (`t1`), and $t_3 > t_1$. So
   **v3 is selected** — the version you just rolled back from goes straight back into
   production.

The system oscillates between v2 and v3 and can never reach v1.

**The fix** is to redefine the field. `activated_at` becomes "**is a valid revert
target**", and rolling back *clears* it on the version being rolled back **from**. Now:

1. Roll back once: v3 → archived, `activated_at = NULL`; v2 → active.
2. Roll back again: the only archived candidate with a non-null timestamp is v1. Correct.

Two pleasant side effects. A **rejected draft** — archived but never live — has a null
timestamp and so is never a revert target. And the audit trail is preserved separately, in
a notes field recording each deactivation, so nothing is lost by clearing the marker.

---

## What you should now be able to explain

- The three retrieval metrics, why precision@1 is the sharp question, and why normalisation
  interacts with a security control
- Why these are deterministic proxies and not the library metrics they are named after
- Judge agreement rates and the four named biases; pointwise vs pairwise and the cost
- Why a reasoning-model judge needs a tolerant-but-not-lenient parser
- Why grading the context against itself yields ~1.0 by construction
- The standard error of a small-corpus mean, and why a 0.05 move is noise
- The two denominators, and why the wrong one makes unlabelled cases inflate the mean
- Why diagnosis must rank by rate, and the id-window and clamping details that follow
- The change-risk heuristics and the specific optimiser threat model behind term counting
- Positive feedback, oscillation, runaway, and the four damping mechanisms
- Why a decision needs both a lifecycle guard and an atomic claim
- The rollback ordering bug and the field-redefinition that fixes it

**Next:** [`20-in-aegis.md`](20-in-aegis.md).
