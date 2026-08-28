# Part 5 — Measurement: evals, red-teaming, observability and the ML spine

This part answers one question in four ways: **how do we know the machine is working?**

Running is easy. Working means the answers are correct, they come from documents we own,
they address the question asked, and the rails hold under attack. Each is a different
measurement with a different instrument.

---

## 5.1 How do you know an AI answer is good?

A traditional program is easy to test: `add(2, 2)` must return `4`. An AI answer has no
single right form — a hundred correct answers to "what is the SLA for an urgent request?"
exist, all worded differently. Equality testing does not work here. Worse, "good" is not one
property. An answer can be:

| Property | Question it answers | Failure it catches |
|---|---|---|
| **Retrieved well** | Did we find the right documents? | The answer was doomed before generation started |
| **Grounded / faithful** | Is every claim supported by those documents? | The model invented a fact — a hallucination |
| **Relevant** | Does the answer address the question asked? | A true, well-sourced answer to a different question |
| **Safe** | Did the rails hold under attack? | A jailbreak, a leak, a poisoned document |

These four move independently: a system can retrieve perfectly and still hallucinate, or be
perfectly grounded and answer the wrong question. There is **no single score**, and any
product showing one number has chosen which failures to hide.

> **Anything you cannot measure, you cannot improve.** And any number you cannot defend
> is worse than no number at all.

The second half costs the work. A metric that could not be computed is reported as *not
computed*, never as zero; a rail that could not run is never counted as a rail that passed.

### The choice: what we measure with

| | What we chose | Alternatives | Why this |
|---|---|---|---|
| **Build gate** | Deterministic proxies, no model call | RAGAS or DeepEval in CI; human review | A gate must be free, fast, and answer the same twice. Judged metrics are none of those |
| **Quality measurement** | The real `ragas` library, on demand | Only proxies; only human eval | Overlap of words is not meaning. Semantics need a model |
| **Safety** | In-process red team against our own rails | `garak` against a live endpoint | We want the *rails* measured, offline, with no API key |
| **Traces** | OpenTelemetry, GenAI semantic conventions | Custom logging; a vendor SDK | An open standard means any tool can read our traces |

### Cross-questions

**Q: If there is no single score, how do you say the system got better?**
By naming which metric moved and by how much, with the sample size beside it. "Recall at 20
went from 0.72 to 0.86 on 50 questions" is a claim. "Quality improved 14%" is not.

**Q: Why not just have humans read the answers?**
We do, but humans cannot run on every commit. Human review is our slowest and least
repeatable instrument, so it is spent on the cases the cheap instruments flag.

**Q: Isn't reporting "not measured" just an excuse for a missing feature?**
The opposite. A zero is a measurement — "we looked and found nothing". "Not measured" says
"we did not look". Collapsing the two lets an outage read as a perfect score, the exact
failure an evaluation system exists to prevent.

---

## 5.2 The two eval layers

Aegis evaluates twice, because a build gate and a quality measurement have opposite
requirements. The library lives at `aegis/src/aegis/evals/`; `backend/src/app/eval/` is a
thin re-export, so the logic sits in one importable place with no web framework attached.

```mermaid
flowchart TD
    A[Seed corpus, 6 cases, 5 docs] --> B[Real hybrid retriever]
    B --> C[Offline gate]
    B --> D[Live ragas run]
    C --> E[Lexical proxies, no model call]
    E --> F[Pass or fail in CI]
    D --> G[Metered gateway]
    G --> H[Judged faithfulness and relevancy]
    H --> I[Evals screen]
```

### Layer 1 — the offline gate

This runs on every commit, driving the **real** hybrid retriever over a fixed seed corpus of
6 labelled questions across 5 documents and scoring with three deterministic proxies:

- **Context precision @ k** — of the top *k* passages retrieved, what fraction came from a
  document the case marks correct?
- **Context recall** — of the documents the case marks needed, what fraction appeared
  anywhere in the retrieved set?
- **Groundedness (faithfulness proxy)** — what fraction of the case's expected claim
  phrases appear, by normalised text match, in the assembled context?

"Deterministic" means the score comes from comparing text, not from asking a model: no
network, no key, no randomness, no cost, the same number to the last decimal every run.

Two details make it honest. **Not-measured is not-counted**: a case with no gold documents
has recall `None`, not `1.0`, and the corpus mean covers only cases that carried the label,
so an unlabelled case cannot lift the average. And **answer relevancy is left blank** —
word overlap cannot compute it, so the gate says so rather than filling the cell cheaply.

The gate follows the **DeepEval pattern**: a declarative metric object carrying its own pass
bar and direction, evaluated inside an ordinary pytest run.

| Metric | Threshold | Direction | Scope |
|---|---|---|---|
| `context_precision@1` | 0.66 | higher is better | corpus mean |
| `context_recall` | 0.95 | higher is better | per case |
| `groundedness` | 0.85 | higher is better | per case |
| `tool_selection_accuracy` | 0.99 | higher is better | agentic case |

The last is not a retrieval metric: with a router injected, the gate checks that a
memory-style question still routes to the `memory` specialist and a factual one to `qa` —
**agent-behaviour regression testing**, which no retrieval metric covers.

**DeepEval the library is deliberately not installed**, for a checkable reason: it pins
`click>=8.0.0,<8.4.0` while `huggingface_hub` requires `click>=8.4.2`, so installing it
would quietly downgrade `huggingface_hub` underneath the embedding stack. Aegis borrows its
*shape* — a metric carrying its own threshold — and implements it natively.

### Layer 2 — live scoring with the real `ragas`

The real library (`ragas>=0.4.3,<0.5`) computes two metrics that need a model:

- **Faithfulness** — the judge splits the answer into statements and checks each against the
  retrieved context; the score is the fraction supported.
- **Answer relevancy** — the judge generates the questions this answer would answer well,
  embeds them, and compares them to the original. If they look like the real question, the
  answer was on topic.

It runs **on demand**, behind an explicit button, because every metric costs model calls:
roughly **nine gateway calls per case — five completions and four embeddings**. The console
defaults to **2 cases**, the API clamps to a ceiling of **6**, and the screen states the
cost before you press.

**Why both layers exist:**

| | Offline gate | Live ragas run |
|---|---|---|
| Cost | Zero | Real model spend |
| Determinism | Identical every run | Varies between runs |
| Runs on | Every commit, in CI | A button, when asked |
| Measures | Word overlap — a proxy | Meaning — the real thing |
| Job | Stop a regression from merging | Say how good the system actually is |

Neither can do the other's job. A gate that costs money and answers differently each run is
a lottery; a quality measurement made of word overlap is a spell-checker.

### Every judge call goes through our own gateway

There are two ways to point an evaluation library at a model. Handing it a `base_url` works
in ten minutes and routes every judge call **around** the platform — no budget check, no
rate limit, no usage-ledger row, no trace span — making evaluation the one place where "every
model call is metered and attributable" is false. So
`aegis/src/aegis/evals/libs/gateway_adapters.py` implements the abstract methods `ragas`
needs on an LLM and on an embedder, routing both through `aegis.gateway`; the route that
runs the suite binds the caller's tenant and budget, so evaluation spend lands on the same
cost surface as everything else.

A difference between two configurations, with no interval around it, is an anecdote, so
`aegis/src/aegis/evals/ir_metrics.py` carries pure-stdlib statistics that turn a difference
into a claim — a Wilson interval, a paired bootstrap, an exact McNemar test. The honest
headline: **at n = 50 the sample defends a difference of roughly 15 points and cannot defend
one of 5.**

### Cross-questions

**Q: Your offline metrics are just string matching. Why call them evals at all?**
Because they are named honestly as *proxies* and gated as proxies. They cannot tell you the
system is good; they can reliably tell you it got worse — a fusion change that mis-ranks a
passage, a chunker change that drops the answer span. That is what a build gate is for.

**Q: Six seed cases is a tiny corpus. Isn't that meaningless?**
Six cases is the *gate*, not the evidence. For tripping on a regression in a deterministic
pipeline a small fixed corpus is a feature — it runs in seconds and never flakes. The
evidence for retrieval quality is the 50-question gold set with bootstrap intervals.

**Q: How do you stop the live run from becoming an accidental bill?**
It is behind an explicit button, the case count is clamped to six server-side, and every
call runs under the caller's budget context — so a tenant over its cap is refused rather
than charged.

---

## 5.3 Why an LLM is used as a judge, and what is wrong with it

**LLM-as-judge** means: take a second model, show it the question, the retrieved context
and the answer, and ask it to score them. It sounds circular — a model grading a model —
so it needs a defence.

The defence is that **grading is a much easier task than answering.** Answering means
retrieving facts, reasoning over them and composing prose; grading means reading three texts
already in front of you and checking whether one supports another — no lookup, no synthesis,
nothing to invent. Judges are correspondingly more reliable at their task than generators
are at theirs, and are the only automatic instrument that reads *meaning* rather than
characters.

In Aegis the judge sits on the **reasoning** model role — a different seat from the
generation role — is asked for JSON only, and runs at temperature 0.

### The known weaknesses

| Bias | What it is | What we do |
|---|---|---|
| **Position bias** | In a side-by-side, judges favour whichever answer was shown first | Aegis grades a **single** answer against its context, not A-vs-B, so there is no first position |
| **Verbosity bias** | Judges rate longer answers higher, mistaking length for thoroughness | The judge answers two narrow questions — *is each claim supported*, *does it address the question* — not "which is better" |
| **Self-preference** | A model rates its own outputs higher than other models' | The judge runs on the reasoning role, routed to a different model family from generation |
| **Non-determinism** | The same input scores differently on two runs | Temperature 0, JSON-only — and, decisively, the judge is *never* the CI gate. The gate is the deterministic layer |

The last row is the real mitigation: these biases are survivable in a measurement you read,
and dangerous in a gate that decides.

### A judge that could not run is not a score of zero

When the judge returns something unparseable, `judge_answer` raises
`JudgeUnavailableError` rather than returning `0.0`. When `ragas` returns NaN because the
judge produced no statements, or `0.0` because it generated no comparison question, the
value is dropped from the sample and the panel prints the reason.

Substituting `0.0` would make a broken judge indistinguishable from a terrible answer: a
candidate prompt and its baseline would both score `0.0`, tie, and pass a zero-margin
promotion gate, so an outage would promote every candidate automatically. **A control that
cannot run must stop the release, not wave it through.**

Reasoning models routinely wrap their JSON in a `<think>` preamble or a markdown fence.
That is formatting, not failure, so the parser strips it and salvages the first balanced
JSON object; only what survives is a genuine judge failure.

### Cross-questions

**Q: Doesn't using a model to grade a model just move the trust problem?**
It moves it to an easier task and a different model. Grading is verification, not
generation, and the judged numbers are never the release gate — they are a reading, next to
a deterministic gate that needs no model at all.

**Q: What stops the judge from being gamed by an answer that flatters it?**
The judge never sees a competitor, is asked for numbers rather than a preference, and the
prompt names the exact two properties. There is no "which do you prefer" for a verbose
answer to win.

**Q: You clamp judge scores into 0 to 1. Isn't that hiding bad output?**
Clamping handles a model returning 1.2 for a good answer. A reply that is unparseable, NaN
or infinite is not clamped — it raises, and the metric reports as not run.

---

## 5.4 Red-teaming: attacking our own rails

A **red-team battery** is a fixed, curated set of hostile inputs, run against the system on
purpose, with the result recorded — a test suite whose test cases are attacks.

Aegis's battery lives at `aegis/src/aegis/redteam/`. Instead of driving a live model
endpoint the way `garak` does, it points the attacks at **our own guardrail rails**,
in-process: offline, no API key, seconds, and every verdict recorded is the rail's actual
output.

### What is in the battery

**69 probes: 53 attacks and 16 benign controls.** Every probe is data — a prompt, a
category, the rail stage it targets, and what the rail is expected to do with it. They
target **six rail stages**, because a battery that only called the input rail would measure
one sixth of the product:

| Stage | Probes | What arrives there |
|---|---|---|
| `input` | 36 | The user's own prompt |
| `ingest` | 8 | A document on its way into the corpus |
| `sequence` | 8 | A burst of queries from one principal |
| `tool_result` | 7 | What a tool or web page handed back |
| `output` | 6 | The model's answer on the way out |
| `memory_write` | 4 | A fact on its way into durable memory |

That split is the point. An **indirect** prompt injection — an instruction hidden in a
retrieved document — is a different attack from a direct one; pasting it into the input rail
just measures the input signatures twice. A poisoning attack is not a prompt at all: it
arrives as a document, months before the question it is meant to answer, and only the
write-time gate ever sees it.

The 13 attack categories map to the OWASP LLM Top-10 (2025) and MITRE ATLAS: prompt
injection, indirect injection, jailbreak, system-prompt leak, PII extraction, output
disclosure, excessive agency, content safety, data poisoning, inference exfiltration,
adversarial evasion, plugin compromise, memory poisoning.

Nine of the 53 attacks are marked `needs_llm`: semantic-only probes — a base64-wrapped
injection, a roleplay jailbreak, a plainly-worded exfiltration request — the deterministic
signatures cannot catch by design. Keeping them is the honest part: the offline report names
exactly which attacks the deterministic rail misses.

### Benign controls, and why the false-positive rate matters as much

The 16 benign controls are ordinary enterprise questions — *what is the status of ticket
417*, *who owns account 22* — that must sail straight through. **A block rate on its own is
trivially gamed**: a rail that blocks everything scores 100% and is a completely broken
product. The controls measure the other side — how often does the rail refuse work it should
have done? That is the **false-positive rate**, and the default bar is **0.0**: not one
benign control may be hard-blocked. A *redaction* does not count; removing a phone number
from a legitimate question is a privacy action, not a denial of service.

### How a block rate is computed honestly

Every attack lands in exactly one of **three** buckets, and the third is what makes the
headline trustworthy.

```mermaid
flowchart TD
    A[Attack probe fires] --> B{Did a rail examine it?}
    B -->|No, rail unavailable| C[unchecked]
    B -->|Yes| D{Verdict}
    D -->|block or redact| E[blocked]
    D -->|pass| F[leaked]
    E --> G[numerator]
    C --> H[denominator only]
    F --> H
```

- **blocked** — a rail examined the text and returned `BLOCK` or `REDACT`. The numerator.
- **unchecked** — a rail refused because it *could not run* (a classifier timed out, the
  gateway was down). The payload was stopped and nothing was learned, so it stays in the
  denominator and out of the numerator.
- **leaked** — the rail let it through.

So the headline is `blocked / fired`. Never `blocked / total probes`: the benign controls
are not attacks and must not pad the denominator. And never counting an unchecked refusal as
a block — a deployment with a dead model gateway would then refuse everything, score 100%
and pass, precisely the failure the harness exists to detect. Under this arithmetic it
scores **0%**: the honest reading of "we refused everything and learned nothing".

A run is judged against two bars at once — a minimum block rate and a maximum
false-positive rate — both **stored on the run record** beside the result. A threshold that
lives only in today's configuration turns yesterday's PASS into an unfalsifiable claim the
moment someone lowers it.

The battery is sliced into **9 named suites** grouped the way OWASP groups them, each
carrying **two** floors: an `offline_floor` for a run with no model layer, and a
`live_floor` for one with a completer wired in. The full OWASP battery is judged at **0.75
offline and 0.90 live**. Every run stores its suite, its mode (`offline` or `live`), both
rates, who started it, and the per-probe report — the same object the screen renders, so
stored evidence and rendered screen cannot disagree.

### Cross-questions

**Q: Nine of your attacks are known to leak offline. Why keep them?**
Deleting them would raise the offline block rate without improving the product. They are the
honest measure of the gap between the deterministic rail and the full rail, and the report
names each one.

**Q: Why not use `garak`, the standard tool?**
`garak` attacks a live model endpoint. Our claim is about the *rails*, not the model, and
rails are what we can fix. Attacking them in-process means the battery runs on a laptop with
no key in seconds and produces the rail name and rationale behind every verdict. We took
`garak`'s probe taxonomy and pointed it at a different target.

**Q: A run's block rate went up. How do I know the rails improved?**
Check the mode and suite first — offline and live are two different measurements, and two
suites are two different batteries. Then check the unchecked count: a rise with unchecked
probes in it is a rail going dark, not a rail getting better.

---

## 5.5 Observability: every run traceable end to end

A **span** is a timed record of one unit of work — a name, a start, an end, a bag of
attributes. A **trace** is the tree of spans belonging to one request.

Aegis instruments with **OpenTelemetry**, the vendor-neutral tracing standard, following the
**GenAI semantic conventions** — the agreed names for the attributes an AI call should carry
— so any compatible tool reads our traces without a custom adapter. The keys live in
`aegis/src/aegis/observability/semconv.py`, with the targeted version of the still-evolving
convention recorded at the top:

| Attribute | Carries |
|---|---|
| `gen_ai.operation.name` | `chat`, `embeddings`, `text_completion`, `transcription` |
| `gen_ai.provider.name` | The provider — deprecated `gen_ai.system` alias still emitted |
| `gen_ai.request.model` / `.response.model` | What we asked for, and what answered |
| `gen_ai.usage.input_tokens` / `.output_tokens` | Token counts |
| `gen_ai.usage.cost` | USD — our own extension; the convention has no cost key |

Aegis stamps its own keys where the convention is silent: `guardrail.stage` / `.layer` /
`.verdict`, `retrieval.query` / `.candidate_count` / `.cache_hit`, `router.role` /
`.reason`, `tool.name` / `.risk`, and graph-node timings. Every span also carries the
OpenInference `span.kind` from the *same* enum that drives the live event stream, so the
exported trace and the events you watched in the browser cannot describe the run
differently.

Spans export to a **local, in-process Arize Phoenix** instance — no Docker, no cloud
account — falling back to a console exporter when Phoenix is absent. From one `run_id` you
walk the whole request as one tree on your own machine: router decision, each retrieval
round, each guardrail verdict, each model call with its tokens and cost, each tool
invocation.

### Cross-questions

**Q: Why OpenTelemetry rather than just logging?**
Logs are lines; traces are a tree with causality and timing. "The run took 4 seconds" is a
log. "3.1 of those 4 seconds were the reranker, called twice" is a trace. And OTel is a
standard, so nothing is locked to one vendor's backend.

**Q: The GenAI conventions are still experimental. Isn't that a risk?**
That is why the keys live in one module with the targeted version written at the top. When
the convention renames a key, one file changes — we already emit both the current
`gen_ai.provider.name` and the deprecated `gen_ai.system` alias.

**Q: Does tracing leak prompt content?**
Span attributes are metadata — model, tokens, cost, verdicts, counts — not message bodies.
Content that does travel is governed by the same guardrail and redaction rails as everything
else.

---

## 5.6 The ML spine: prediction that admits what it does not know

"Will this account churn?" and "what will spend be in 14 days?" are numeric questions over
tabular data; a language model is the wrong instrument for both. `aegis/src/aegis/ml/`
answers them, `aegis/src/aegis/forecast/` the time-indexed ones. The spine is
**domain-agnostic**: *what* to predict — features, target, task — is injected as a spec;
*how* to predict, calibrate and explain lives in the module.

```mermaid
flowchart TD
    A[Training frame] --> B[Split three ways]
    B --> C[Train split]
    B --> D[Calibration split]
    B --> E[Test split]
    C --> F[XGBoost plus HistGB, soft vote]
    F --> G[MAPIE conformalise]
    D --> G
    G --> H[Prediction with interval]
    F --> I[SHAP attribution]
    E --> J[Measured accuracy and coverage]
```

### XGBoost — gradient-boosted trees

A **decision tree** asks a chain of yes/no questions about the input and lands on an
answer. One tree is weak. **Gradient boosting** builds hundreds of small trees in sequence,
each correcting the errors of the ones before it; **XGBoost** is the fast, well-regularised
implementation. Aegis runs 200 trees, depth 4, learning rate 0.1, on CPU only.

**Why trees rather than a neural network on small tabular data?**

- **Sample efficiency.** A network learns its own representation of the features and needs
  many rows; a tree splits on the columns you already gave it, so thousands suffice where a
  network needs hundreds of thousands.
- **Mixed, unscaled columns.** Trees split on order, so categories, counts, currencies and
  dates at wildly different scales cost nothing. Networks are sensitive to all of it.
- **Exact explanation.** Trees admit an exact, fast SHAP algorithm; for a network you
  approximate, more slowly.

Aegis **soft-votes** XGBoost with scikit-learn's `HistGradientBoosting` — two
well-regularised but differently implemented boosters, averaged, which cuts variance against
either alone. Both are CPU-only, so the spine runs on a 16 GB laptop with no GPU.

### SHAP — which feature pushed this prediction, and which way

"The model says 0.8" is useless: there is nothing to check, argue with or act on. "0.8,
driven mostly by a support ticket count of 11 pushing it up and a tenure of 4 years pulling
it down" can be checked against what you know, can be wrong in a way you can see, and tells
you what to change.

**SHAP** (SHapley Additive exPlanations) borrows from cooperative game theory: if a
prediction is a payout the features earned together, how much does each deserve? It measures
the prediction with each feature present versus absent, averaged fairly over all the orders
in which they could be added. The output is one signed number per feature — positive pushed
the prediction up, negative pulled it down — summing with the baseline to the prediction.

Aegis computes SHAP **per ensemble member** and averages by voting weight, so the
attribution explains the ensemble's output rather than one member's. The explainer is chosen
by family: `TreeExplainer` for boosters and forests, `LinearExplainer` for a linear member,
`PermutationExplainer` for anything else.

### MAPIE and conformal prediction — an interval with a measured guarantee

Most "confidence" numbers are the model's own opinion of itself, and models are famously
overconfident. **Conformal prediction** replaces the opinion with an observation:

1. Fit the model on a training split.
2. Hold out a **separate calibration split** the model has never seen.
3. Run the model on it and record the errors — how far off each prediction was.
4. For 90% coverage, take the 90th percentile of those errors and put that much room on
   either side of the new prediction.

Because step 3 measures rather than assumes, the guarantee is distribution-free: it does not
care whether the errors are Gaussian, and it holds for any underlying model. **MAPIE**
implements this; Aegis uses its `SplitConformalRegressor` and `SplitConformalClassifier` on
the already-fitted ensemble.

**What "90% coverage" actually means.** Over many future predictions, about 90 out of every
100 true values will fall inside the interval you were given. It is a statement about the
*long-run rate of the procedure*, not about any single prediction. It does not say "there
is a 90% chance this particular answer is in the band"; it says "the machine that produced
this band is right 90 times out of 100".

For classification the equivalent is a **prediction set** — the smallest set of classes
covering the truth 90% of the time. An easy row returns one class, an ambiguous row three,
so the *size of the set is the uncertainty made visible*.

Three honesty rules hold this together:

- **Calibration never touches training rows.** Errors on rows the model already fitted are
  optimistically small, and void the guarantee.
- **Requested and achieved coverage are separate fields.** One is the level *asked for*;
  the other is the fraction of a third, held-out test split whose true value landed inside
  the band. Only the second is a measurement, and the model card carries both.
- **An unreachable level is refused.** Split conformal takes the
  `ceil((n+1) x confidence)`-th smallest calibration error as the half-width; if that rank
  exceeds `n` — a 5-row calibration split cannot support a 90% interval — training raises
  with the arithmetic rather than returning a meaningless band.

And **no silent fallback**: with no trained model, `predict_explain` raises
`MLModelUnavailableError` rather than fitting on the built-in noise synthesiser and serving
its interval as evidence. Every model card names its `data_source` (`provided`,
`spec_provider` or `synthetic`) and a SHA-256 digest of the columns it was fitted on.

### Forecasting — the same discipline, on time

`aegis.forecast` answers horizon-indexed questions: what will daily spend be over the next
14 days, and when will this budget run out?

It is separate for one decisive reason. `aegis.ml` calibrates on a **random** train/test
split — correct for independent tabular rows, **wrong for a time series**, where it puts
future rows into calibration and leaks the future into the guarantee. `aegis.forecast`
splits **by time**: conformal intervals calibrate on rolling windows strictly inside the
training slice, and the rolling-origin backtest scores only points lying *after* the cutoff
whose model produced them.

Two rules carry over. **Conformal by default; parametric only when asked, and labelled** —
a library's plain `level=[90]` columns are the fitted model's own predictive distribution,
an assumption rather than a calibration, so `interval_method` always states which kind of
band you hold. And **requested and achieved coverage are different numbers**: the backtest
counts held-out actuals that landed inside the band, routinely *below* the requested level,
and that gap is the finding. No naive straight line is drawn through noise anywhere — the
one output the module refuses to produce.

### Cross-questions

**Q: Why do you need machine learning at all if you have an LLM?**
They answer different question types. An LLM cannot give a calibrated numeric interval, and
a gradient-boosted tree cannot read a policy document. The ML spine supplies a prediction,
an interval and named drivers as *evidence* the agent cites; it never gates or terminates a
run.

**Q: Is conformal prediction just a fancy confidence interval?**
No. A classical confidence interval assumes a distribution. A conformal interval is
distribution-free and model-agnostic: it is built from errors the model actually made on
data it had never seen, so its guarantee is empirical rather than assumed.

**Q: SHAP is expensive. Does it slow every prediction down?**
Not here. Every ensemble member is a tree model, and `TreeExplainer` is exact and fast —
polynomial in tree depth, not exponential in feature count — on a single row at serve time.

**Q: What if the ensemble is confident and wrong?**
Then empirical coverage on the held-out test split drops below the requested level and the
model card shows it. That is what the third split is for.
