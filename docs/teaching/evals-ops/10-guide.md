# Evals and LLM-Ops

The module that answers "did that change make things better?" — and then lets the system
improve itself.

---

## 1. What it is

You edit the system prompt to make the agent friendlier. You type five questions at it.
All five look better. You ship it.

Two of those five were the questions annoying you last week. The other three you invented
on the spot. Nobody re-runs last month's five. And your one sentence about tone also
nudged the model away from citing sources — which none of your five questions checked.

The regression is in the sixth question, and there is no sixth question.

That is not a discipline problem. It is structural, and specific to systems built on
language models:

- **There is no single correct output.** Twenty phrasings can all be right, so comparing
  strings tells you nothing.
- **Changes are non-local.** A sentence about tone can change which tool gets picked.
- **The failure is fluent.** A wrong answer looks exactly like a right one.

So quality drifts downward one plausible improvement at a time, and every step looked
like an improvement when it was made.

This module does two things. **Evals** score the system on a fixed set of cases, the same
way every time, so a change becomes reviewable. **LLM-Ops** builds on that: because there
is now a real measurement, the system can propose a better prompt, score it, and ship or
escalate it.

---

## 2. How it works in Aegis

### Layer one: the offline gate

This runs in CI. No network, no API key, same three numbers every time:

```
$ python -m aegis.evals.harness

Eval over 6 cases:
  context_precision@1 = 0.833  (>= 0.66)
  context_recall      = 1.000  (>= 0.95)
  groundedness        = 1.000  (>= 0.85)
PASS
```

The corpus is five short documents and six cases. Two documents are deliberate
**distractors**, so a refund query has something to out-rank. Each case carries a query,
the document a correct retrieval must surface, and the phrases a grounded answer ought to
cite. Everything is a constant, which is why the gate is deterministic.

The three numbers:

| Metric | Question it asks |
|---|---|
| **context precision@1** | Did we rank the right document *first*? |
| **context recall** | Did the right document appear in the results *at all*? |
| **groundedness** | Are the phrases a good answer needs actually in the retrieved text? |

Both retrieval numbers are reported because they move in opposite directions. On one of
the six cases the pipeline returns the gold document second, behind a login runbook: that
case scores 0.0 on precision and 1.0 on recall from the same retrieval. Five of six rank
correctly, and 5/6 is the 0.833 above. Return more documents and recall rises while
precision falls, so a single "retrieval quality" number would have to hide one of those
movements.

These are **deterministic proxies**, computed with token and substring overlap. They
share their names and their ideas with RAGAS, a library that computes them with a
language model. Ours are free and exactly reproducible, so CI can assert on them; they
cannot see paraphrase or contradiction. Calling them RAGAS metrics would be an overclaim.
Answer-relevancy cannot be proxied this way at all, so the report reports it as not
computed rather than printing a plausible number.

Two rules from this layer worth carrying:

- **Claims are matched on a normalised form** — lowercase, punctuation collapsed to
  single spaces. Without it, the prompt-injection defence that inserts marker characters
  between retrieved words would silently tank groundedness across the whole corpus.
- **An unlabelled facet is `None`, not 1.0**, and `None` fails the gate. A case with no
  gold documents contributes to neither the numerator nor the denominator. Otherwise
  broadening the corpus before you have labelled it quietly raises the headline score and
  props up a threshold while a real regression runs underneath.

### Layer two: the judge

Retrieval is necessary and not sufficient — a system can surface exactly the right
document and then write an answer that contradicts it. So a second layer generates an
answer from the retrieved context and hands a strong model the question, the context and
the answer, asking it to score **groundedness** (is every claim supported?) and
**relevance** (does it address the question?).

That sees paraphrase, contradiction and hedging. It costs a model call, it is not
deterministic, and it can fail. Three rules keep it honest:

- **The judge grades a generated answer against the retrieved context** — never the
  context against itself, which would score ~1.0 by construction, forever, on any system.
- **A judge that cannot be parsed raises, it does not return `0.0`.** Reasoning models
  wrap JSON in `<think>` blocks and markdown fences, so the parser salvages
  progressively — raw text, then stripped, then the first balanced `{...}`. Anything left
  over raises `JudgeUnavailableError`. A real 0.0 still parses as 0.0: "scored zero" and
  "could not score" must stay different values.

### The loop

```
Trace  →  Eval  →  Diagnose  →  Gate  →  Release  →  (back to Trace)
```

**Trace** — every run is instrumented, so there is something to grade.
**Eval** — grade the answer *and each step*, persisting one row per graded facet.
**Diagnose** — cluster recent failures and ask a model for a better prompt.
**Gate** — score the proposal against the current baseline on a real eval.
**Release** — promote it, or send it to a human, based on the risk of the change.

Grading the steps separately is what makes diagnosis possible. A single answer score of
0.4 tells you the run was bad, not which part was bad. So each retrieval, tool call and
guardrail check gets its own metric — `step:retrieval`, `step:tool`, `step:guardrail` —
graded by a cheap model, or by a lexical fallback offline. Grading runs after the run and
never breaks the run it is grading.

For any of that to work, the system's instructions stop being a string in a source file
and become a **versioned row**:

| Status | Meaning |
|---|---|
| `draft` | Proposed by the optimiser or a human. Not live. |
| `staged` | Passed the eval gate; awaiting human approval. |
| `active` | The one live version for its key. At most one. |
| `archived` | A former active version, kept for rollback and audit. |

Rollback is one call, nothing is ever deleted, and the hot path reads an in-process cache
rather than the database. There is also a **floor**: when nothing is active, the baseline
is the hand-authored adapter prompt. The loop builds on that prompt and can never go
below it.

Two rules the registry enforces. **Rolling back twice must walk history backwards**, so
rolling back clears the `activated_at` marker on the version you rolled back *from* —
otherwise the second rollback re-promotes the broken version you just escaped. And **a
release is decided once**: the approval row is claimed with a conditional
`UPDATE … WHERE status = 'PENDING'` before the draft is touched, in one transaction, so a
double-clicked approve returns `already_decided` instead of acting twice.

### Diagnosis ranks by rate, not count

Two facets over the same window: `step:retrieval` failed 20 times out of 500 graded;
`step:tool` failed 15 times out of 25. Rank by raw count and the optimiser is pointed at
retrieval, which is failing 4% of the time, while tool selection fails 60% of the time.

So `diagnose` computes a denominator over the same window and steers by rate. The
breakdown always names all four known facets even at zero, so a clean facet reads as
"0% of N" rather than as a silent absence. The optimiser is allowed to do exactly one
thing with the result: write a **draft**.

### What decides whether a change ships

The eval measures helpfulness. Suppose the optimiser is told "stop refusing these
requests" and does the most effective possible thing — it deletes the line that says
*"Never issue a refund above $1,000 without confirming the invoice."* The draft is more
helpful with that constraint gone. It beats the baseline. The eval gate passes it.

The eval gate is not wrong; it measured what it measures. So a second, independent
question is asked about every draft, and it is asked **deterministically** —
`classify_change` makes no model call, because the classifier that decides whether a
model's proposal is safe must not itself be a model.

**HIGH** if the changed-line fraction exceeds 0.40, or any safety term's whole-word
count changed, or a config key containing `model`/`tool`/`permission`/`role`/`scope`
changed. **LOW** if the diff is at or under 0.15 and config is unchanged or a bounded
tweak of a known-tunable key. **MEDIUM** otherwise.

The watchlist is eight terms: *ignore, guardrail, safety, tool, approval, never, policy,
system prompt*. Our deleted line contains `never`, so its count drops from 3 to 2 and the
change is HIGH — regardless of what the eval said. Note it counts occurrences rather than
checking presence; the prompt still contains two other "never" constraints, so a presence
check would have missed it entirely.

The heuristic over-triggers on purpose. A false escalation costs a human thirty seconds.
A false promotion removes a safety constraint from production.

| Autonomy mode | Behaviour |
|---|---|
| `auto` | Promote any eval-passing draft, whatever the risk |
| `manual` | Always stage for human approval |
| `tiered` *(default)* | Promote only at or below `auto_promote_ceiling` (default `low`); everything else stages |

---

## 3. How you use it in code

Two packages with a one-way dependency: `aegis.ops` imports `aegis.evals`, never the
reverse. Neither pulls in FastAPI or a model client.

```python
from aegis.evals import evaluate, DEFAULT_THRESHOLDS, run_regression_gate
from aegis.ops import configure_ops, evaluate_run, diagnose, release, decide_release

report = await evaluate()                       # offline, deterministic, no network
report = await evaluate(complete=my_complete)   # + the LLM-as-judge pass
report.passed                                   # bool, gated on the deterministic proxies
```

Or from a shell:

```
python -m aegis.evals.harness      # human-readable report + exit code
python -m aegis.evals.regression   # the same measurements, per-case
```

`configure_ops` is where a host binds every seam — the prompt floor renderer, the session
factory, the tenant-scope binder, the durable approval writer, and the host's own
`Approval` ORM class. The backend calls it once at import.

The two ops calls that matter:

```python
result = await diagnose(session, prompt_key="agent.system", complete=my_complete)
# → writes a DRAFT parented to the current active version

outcome = await release(
    session,
    draft_version_id=result.draft_version_id,
    eval_fn=my_eval_fn,              # async (system_prompt) -> float
    approval_enqueue=my_enqueue,     # async (...) -> approval id
    autonomy="tiered",
)
outcome.outcome   # 'promoted' | 'staged_for_approval' | 'rejected'
outcome.risk      # ChangeRisk.LOW | MEDIUM | HIGH
```

`eval_fn` is injected, and it has one non-negotiable property: it must **generate answers
under the candidate prompt**. A scorer that does not depend on the prompt returns the same
number for the draft and the baseline, and the gate passes everything.

### Settings worth changing

`LoopParams`, overridable per call or process-wide via `configure_ops`:

| Field | Default |
|---|---|
| `eval_margin` | `0.0` — the draft must be strictly better |
| `high_diff_fraction` | `0.40` |
| `low_diff_fraction` | `0.15` |
| `safety_terms` | ignore, guardrail, safety, tool, approval, never, policy, system prompt |
| `critical_config_markers` | model, tool, permission, role, scope |
| `auto_promote_ceiling` | `"low"` |

And the CI thresholds in `DEFAULT_THRESHOLDS`: precision ≥ 0.66, recall ≥ 0.95,
groundedness ≥ 0.85, at `precision_k = 1`. They sit below the observed scores so normal
runs pass, but close enough that a real regression trips them — observed precision is
0.833 against a 0.66 floor, so one more mis-ranked case out of six still passes and two
do not.

The console reaches all of this through the `/ops/*` routes in
`backend/src/app/api/routes.py`, all behind `require_admin_or_ai_team`.

---

## 4. Why it helps us

**A prompt change becomes reviewable.** Without a gate, "improve the prompt" is not
engineering — it is five questions and a feeling.

**A quality regression turns into a red build.** The offline gate needs no key and no
network, so it runs on every commit.

**A control that cannot run stops the release.** The release gate is a *comparison*, not
a threshold. If a broken judge returned `0.0` instead of raising, draft and baseline
would both score `0.0`, `0.0 < 0.0` would be false, the rejection branch would not fire,
and every candidate prompt would auto-promote. Two zeros on a dashboard read as *the eval
scored badly*, not *the eval did not run*. NaN and zero graded cases are the same bug in
different clothes. All three fail closed.

**The loop can improve the prompt and cannot remove the rules it operates under.** The
risk classifier is deterministic and it counts safety vocabulary, so a constraint deletion
goes to a human whatever the score said.

**Every version is recoverable.** Rollback is one call, history walks backwards, and
nothing is deleted.

Two honest limits. The release eval is three cases at a margin of zero, so it is a smoke
test against regression, not a proof of improvement — the safety weight is carried by the
risk classifier, not the score. And the corpus is a fixture, not a benchmark.

**Next:** [`40-diagrams.md`](40-diagrams.md)
