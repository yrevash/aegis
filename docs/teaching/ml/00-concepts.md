# ML — the concept, from zero

No code. What supervised learning is, why a model's confidence is not a probability, and
why in this system the machine-learning signal is **evidence** and never a decision.

---

## The problem this module exists for

An agent is about to answer a support question. It would help to know: *how long will
this ticket actually take to resolve?*

That is not something a language model knows. It is not in any document you could
retrieve. It is a pattern in **your historical data** — thousands of past tickets with
their priority, category, channel, queue depth, and the number of hours they actually
took.

Learning that pattern is what supervised machine learning does. And it is a genuinely
different kind of capability from everything else in this system: retrieval finds text,
the language model writes text, and this predicts a number from structured columns.

---

## Supervised learning in one page

You have rows. Each row has **features** (the inputs you know) and a **label** (the thing
you want to predict).

| priority | category | queue_depth | tenure_months | → | resolution_hours |
|---|---|---|---|---|---|
| urgent | billing | 3 | 24 | | 2.1 |
| low | technical | 41 | 2 | | 38.6 |

**Training** means finding a function $f$ that maps features to label with small error on
these rows. **Inference** means calling $f$ on a row you have never seen.

Two flavours:

- **Regression** — the label is a number (*resolution hours*). Output: a number.
- **Classification** — the label is a category (*will this escalate: yes/no*). Output: a
  label, usually with per-class scores.

The central danger is **overfitting**: a model can memorise the training rows perfectly
and be useless on new ones. Which is why you never judge a model on data it was fitted
on — you hold rows out.

### Why trees, not neural networks

For **tabular** data — rows and columns, mixed numeric and categorical — gradient-boosted
decision trees consistently match or beat neural networks, train in seconds on a CPU, and
need no GPU. This is not folklore; it is the finding of repeated benchmark studies.

A **decision tree** asks a sequence of yes/no questions (*is queue depth > 20? is priority
urgent?*) and lands in a leaf holding a prediction. One tree is weak. **Gradient
boosting** builds trees in sequence, each one fitting the *errors* of everything before
it, and sums them. That turns many weak rules into one strong model.

**An ensemble** goes further: fit two different learners and average them. Different
implementations make different mistakes, and averaging reduces variance. It costs twice
the training time, which for a CPU-second model is nothing.

---

## Why a model's confidence is not a probability

Ask a classifier for its probability and it will say `0.93`. The natural reading is *if I
saw a hundred cases like this, about 93 would be positive.*

That reading is usually wrong.

A model is **calibrated** if, among all predictions it scores 0.9, about 90% are actually
positive. Most models are not. Modern neural networks are famously **over-confident**
(Guo et al., 2017 — *On Calibration of Modern Neural Networks*): the accuracy went up and
the calibration got *worse*. Boosted trees have their own miscalibration, often in the
other direction.

The reason is structural. Training minimises a loss — cross-entropy, squared error — and
nothing in that objective requires the output to be a frequency. The number is a score
that happens to live in $[0,1]$.

### Why this matters more here than in a typical ML system

The output of this module is **injected into a prompt as evidence** and then paraphrased
by a language model into a sentence a human reads. "The model predicts 4 hours with 90%
confidence" is a claim the human will act on.

If that 90% is really 60%, you have not made a modelling error. You have manufactured a
false assurance, in fluent prose, at the point of decision.

---

## Conformal prediction: uncertainty you can actually defend

Conformal prediction gives you a **distribution-free, finite-sample** guarantee. Those
three words are the whole pitch:

- **Distribution-free** — no assumption that errors are Gaussian, or anything else.
- **Finite-sample** — it holds for the data you have, not asymptotically.
- **Model-agnostic** — it wraps any model. It does not care what is inside.

### How split conformal works

1. Split your data into **train** and **calibration**, disjoint.
2. Fit the model on train only.
3. On calibration, compute how wrong the model was for each row — its **residuals**.
4. To get a 90% interval: take the 90th percentile of those residuals, call it $q$.
5. For a new prediction $\hat y$, the interval is $[\hat y - q, \hat y + q]$.

That is it. The guarantee — the true value falls in the interval at least 90% of the time
— follows from **exchangeability**, not from any distributional assumption.

For classification the analogue is a **prediction set**: instead of one label, return the
set of labels that cannot be excluded at the confidence level. A **singleton** set is a
confident call. A two-label set says *the model genuinely cannot separate these*, which
is far more useful than a bare label with a score of 0.51.

### The assumption, and how it breaks

**Exchangeability** means the calibration data and the future data are drawn from the same
distribution — informally, that any ordering of them is equally likely.

Two ways to void it:

**Time series split randomly.** If you shuffle time-ordered data, calibration rows come
from *after* some test rows. You have leaked the future into calibration. The guarantee is
void, and — worse — the reported numbers look *better* than reality, because the model has
effectively seen the future. Time series must be split **chronologically**.

**Distribution shift.** The world changes; your calibration set is from last year. The
guarantee degrades silently, because nothing in the arithmetic notices.

### The calibration set must be big enough

Split conformal takes the $\lceil (n+1)\alpha \rceil$-th smallest residual as the interval
half-width. If that rank exceeds $n$, **no finite quantile exists** — the requested level
is unattainable no matter how good the data is. A five-row calibration set cannot support
a 90% interval, arithmetically.

Systems that do not check this either crash or, worse, return something.

### Requested is not achieved

This is the trap that matters most in practice.

You *request* 90% coverage. Whether you *achieve* it is a measurement, made on a **third**
split that neither training nor calibration touched: count how often the true value fell
inside the interval.

Reporting the requested level as though it were measured is not a rounding error. It is
printing your configuration and calling it a result — and it is invisible, because 0.9 is
exactly what a correct system would report if it happened to hit the target.

**The only coverage number worth trusting is one that can disappoint you.**

---

## SHAP: attribution, not causation

The other half of trustworthiness is *why*.

**SHAP** (SHapley Additive exPlanations, Lundberg & Lee, 2017) borrows the **Shapley
value** from cooperative game theory. The setup: players cooperate to produce a payout;
how do you fairly divide it? Shapley's answer is the average marginal contribution of
each player across all possible orderings, and it is the *unique* division satisfying a
short list of fairness axioms.

Map features to players and the prediction to the payout, and you get a signed
contribution per feature, with a property that makes it auditable:

$$f(x) = \phi_0 + \sum_i \phi_i$$

The base value plus the attributions equals the prediction, exactly. Nothing is
unaccounted for.

Exact Shapley values require evaluating all $2^n$ feature subsets. **TreeSHAP** exploits
tree structure to compute them exactly in polynomial time, which is why tree models are
the practical choice when you want explanations.

### What SHAP does *not* tell you

It tells you what moved **this model's output** for **this row**. It does not tell you
what causes the outcome in the world. If two features are correlated, the model may lean
on one and SHAP will attribute to the one the model used — which is a true statement about
the model and possibly a false one about reality.

Say "these are the drivers of the model's prediction", not "these are the causes."

### The attribution must be *of the class you showed*

For a binary classifier, tree explainers emit a margin toward **class 1**. If the model
predicted class 0 and you display those raw values, every driver's sign reads backwards
next to the prediction — "long queue depth pushed this *down*" when it pushed it *up*
toward the class you did not predict.

The explanation is not an independent artefact. It must be an explanation **of the thing
displayed beside it**.

---

## The rule that governs this whole module: ML informs, risk gates

Here is the design decision that most separates this system from a demo.

The prediction, its interval, and its top drivers are **injected into the agent's context
as evidence**. They inform the answer.

They never decide whether the run stops for a human. That decision is made by the **risk
tier of the tool being called**.

### Why confidence-based gating is wrong

The intuition — "stop for a human when the model is unsure" — fails in exactly the case
that matters.

A model is most dangerous when it is **confidently wrong**. Out-of-distribution inputs,
adversarial inputs, and plain novelty all routinely produce high confidence with no
grounding. Gating on confidence means the gate opens precisely when it should have
closed.

Tool risk does not have that failure mode. A refund of $4,200 is high-risk whether the
model feels sure or not. The gate is a property of the *action*, which is a fact, rather
than of the *model's feeling about the action*, which is an artefact.

The corollary makes it operationally safe too: because ML never gates, a **missing or
failed prediction is not a failure**. The evidence is simply omitted. The system answers
without it, honestly, instead of blocking or fabricating.

---

## The honesty signals, and why they are part of the response

A prediction is not self-describing. The same number can mean completely different things:

- **`data_source`** — was this model trained on real domain data, on a spec-provided
  frame, or on a built-in **noise synthesiser**? A synthetic model produces a beautifully
  formatted prediction with no signal in it whatsoever.
- **`imputed_features`** — which inputs did the caller not supply, so were filled from
  training medians? A caller who mistypes every feature name gets a fully confident answer
  about the median training row, with nothing saying so.
- **`unknown_features`** — which keys did the caller send that are not model features and
  were silently ignored?

These ride on the response object itself, not in a log. Downstream code — and the UI — has
to be able to discount the evidence on those signals alone.

**The general principle:** if a number can be produced with no signal in it, the response
must carry the fact.

---

## What you should now be able to explain

- Supervised learning, features vs labels, regression vs classification, overfitting
- Why gradient-boosted trees beat neural networks on tabular data
- What calibration means, and why a model's raw score is not a probability
- Split conformal prediction, and what distribution-free and finite-sample buy you
- The exchangeability assumption, and the two ways it silently breaks
- Why a calibration set has a hard minimum size for a given confidence level
- Why requested coverage and measured coverage must be different fields
- What SHAP attributes, and why it is not causation
- Why a binary explanation must be of the class actually displayed
- Why ML informs and risk gates, and why confidence-based gating fails when it matters
- Why `data_source` and `imputed_features` belong on the response

**Next:** [`10-theory.md`](10-theory.md) — the maths and the published work.
