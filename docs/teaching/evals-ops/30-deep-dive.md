# Evals & LLM-Ops — deep dive: when the gate stopped being able to fail

This module contains the single most dangerous bug in the codebase, and it is worth
understanding exactly why: **a control that cannot fail is worse than no control**,
because it produces the paperwork of safety with none of the substance.

---

## Bug 1 — the release gate passed VACUOUSLY when the judge failed

### The chain, one function at a time

**`_parse_verdict` returned `0.0` on any parse error.**

The judge runs on a **reasoning** model — DeepSeek-R1 or Phi-4-reasoning — which routinely
wraps its JSON in a `<think>…</think>` preamble, a markdown fence, or a sentence of prose.
Strict `json.loads` on the raw content fails on all of those. The parser caught the
exception and returned a `JudgeVerdict(0.0, 0.0)`.

**`make_eval_fn` averaged those zeros.** The scorer runs three seed cases, blends
groundedness and relevance, and returns the mean. All zeros in, `0.0` out.

**And `eval_fn` is called twice** — once for the draft, once for the baseline. A broken
judge scores **both** `0.0`.

**Then `release()` tests:**

```python
if draft_score < baseline_score + effective_margin:
    # reject
```

with the default `margin = 0.0`. So:

```
0.0 < 0.0 + 0.0   →   0.0 < 0.0   →   False
```

The rejection branch does not fire. **The gate passes.** And in `tiered` mode a low-risk
draft is then **auto-promoted to production**.

### Now say what that means operationally

A judge outage — one bad deployment, one rate limit, one model that started prefacing its
JSON with `<think>` — **auto-promotes every candidate prompt to production.**

Not "fails to catch a bad prompt". *Promotes every single one.* The optimiser proposes,
the gate rubber-stamps, and the system rewrites its own instructions with no measurement
behind any of it.

And the failure is completely silent. The API returns `outcome: "promoted"`, `eval_score:
0.0`, `baseline_score: 0.0`. Two zeros on a dashboard read as "the eval scored badly" — a
quality problem — not as "the eval did not run" — a control failure. Nobody investigates a
promotion that reported a number.

### Why `0.0` was chosen in the first place

It looks defensive. *"If we can't grade it, score it worst."*

The reasoning is wrong for one specific reason: **the gate is a comparison, not a
threshold.** In a threshold gate, scoring 0.0 on failure is conservative — it fails. In a
*comparative* gate, a constant applied to both sides **cancels**, and the comparison
degenerates.

That generalises well beyond this codebase: *a conservative default is only conservative
relative to the operation that consumes it.*

### The fix, in two halves

**Half one: tolerate real drift.** The parser now salvages the formats the judge actually
produces (`aegis/src/aegis/evals/judge.py:109-138`): strip `<think>` blocks — including
unterminated ones (`_THINK_BLOCK`, `:31`) — strip markdown fences (`_FENCE`, `:34`), and
extract the first **balanced** `{...}` object via a depth scan (`:126-137`). Formatting
drift is expected behaviour, not a failure, and treating it as one would turn a working
judge into an outage.

**Half two: raise on anything genuinely unusable.**
`JudgeUnavailableError` (`judge.py:49`), whose docstring is the clearest statement of the
principle in the codebase:

> *"This exists so a judge outage is distinguishable from a genuine `0.0`. Any caller that
> gates a release on the judge MUST let this propagate (fail closed) ... A control that
> cannot run must stop the release, not wave it through."*

`make_eval_fn` (`gate.py:130`) deliberately does not catch it, with a comment at
`:128-129` saying so in the imperative:

```python
# An unparseable judge reply raises out of here on purpose — see the
# docstring. Do NOT wrap this in a try/except that yields 0.0.
```

`release()` documents the propagation in its `Raises:` section (`release.py:312-315`): the
release is abandoned, the draft is left DRAFT, nothing is staged.

**A real `0.0` still parses as `0.0`.** The fix distinguishes "scored zero" from "could not
score" without losing the ability to score zero.

### The sibling with the identical shape: NaN

`NaN < x` is **False for every x**. So a NaN score sails through the same comparison and is
promoted, for exactly the same reason.

Two defences, at two layers. `_parse_verdict` rejects NaN and infinity as unparseable
(`judge.py:160-165`). And `release._require_score` (`release.py:231`) coerces to float and
requires `math.isfinite` (`:249-253`), raising a `ValueError` naming which side failed:
*"eval gate cannot run: draft eval_fn returned a non-finite score."*

Defence at both layers is deliberate — the eval function is **injected**, so the gate
cannot assume the parser is the only source of a score.

### And the third member of the family

`make_eval_fn` raises `RuntimeError` when **no case could be graded** (`gate.py:133-137`):
*"the gate cannot pass on an empty measurement (fail closed)."*

Empty is not zero either. Same family, same rule.

---

## Bug 2 — unlabelled cases scored a perfect 1.0 and inflated the corpus mean

### What was happening

`score_case` produced three numbers per case. For a case carrying **no gold documents**,
recall was computed as… well, there is nothing to be right or wrong about. The
implementation returned **1.0**. Same for groundedness on a case with no claims.

Then `aggregate` averaged over **all** cases.

### The arithmetic

$$\text{mean} = \frac{\sum_{\text{labelled}} s_i + |\text{unlabelled}| \times 1.0}{n_{\text{total}}}$$

Every unlabelled case adds a full 1.0 to the numerator and 1 to the denominator. So the
mean is dragged **toward 1.0**.

Concretely: 10 labelled cases averaging 0.80 gives a mean of 0.80. Add 10 unlabelled cases
and the mean becomes `(8.0 + 10.0) / 20 = 0.90`. Nothing about retrieval changed. The
number went up by 0.10.

### Why it is a trap rather than a mistake

The action that triggers it is a **good** one. Someone broadens the eval corpus — adds
cases for a new document type, or new phrasings — and does not have gold labels for them
yet. That is normal, incremental, and correct behaviour.

And it silently raises the gate's headline metric, which can hold the threshold up while a
real regression runs underneath. The corpus grew, the score improved, and the system got
worse.

### The fix

**`None` means not measured** (`metrics.py:226-238`):

```python
recall = (
    sum(gold in retrieved_set for gold in case.gold_doc_ids) / len(case.gold_doc_ids)
    if case.gold_doc_ids
    else None
)
```

with the comment at `:225-226`: *"An unlabelled facet is NOT a passing facet — it is an
absent measurement, and scoring it 1.0 would let unlabelled cases pad the corpus mean."*

**Average over contributors only** (`metrics.py:270-278`): filter out the `None`s, mean the
rest, and report `recall_cases` / `groundedness_cases` so nobody mistakes a 2-case mean for
a 40-case one.

**An unmeasured metric FAILS.** This is the half people miss. If *no* case carries gold
docs, the aggregate is `None` — and `None` must not pass:

- `EvalReport.failures()` (`harness.py:110-113`, `:118-121`) emits *"context_recall was not
  measured: no eval case carries gold_doc_ids"* as a **failure reason**.
- `passed` (`harness.py:347-353`) requires each metric to be **not `None` and** above
  threshold.

*"the gate cannot report clearing a bar it never measured against."*

### The honest sibling

`metric_configs()` (`harness.py:128`) surfaces RAGAS **answer relevancy** as
`computed=False, value=None, cases=0` (`:173-181`). It genuinely cannot be computed offline
— it needs a generation plus a semantic-similarity model. It is reported as not-computed
rather than faked, and `to_eval_rows` (`:213`) omits it entirely because *"a row must carry
a real `score`."*

**Same principle, applied proactively rather than after a bug.**

---

## Bug 3 — the judge graded the retrieved context against itself

### What was happening

`evaluate(..., complete=...)` ran the LLM-as-judge pass. The call was, in effect:

```python
judge_answer(case.query, result.answer_context, result.answer_context, complete=...)
```

The **context** was passed as both the context and the answer.

### Why the number was meaningless

The judge's job is: *is every claim in the ANSWER supported by the CONTEXT?*

If the answer **is** the context, every claim is supported by construction. Groundedness
comes back ~1.0 on every case, for every corpus, for every system, forever.

And it appeared on the report as `judge.groundedness` — labelled a **model-graded score**.
A number with zero signal in it, presented as the sophisticated measurement that the
deterministic proxies cannot provide.

### Why nobody noticed

Because ~1.0 is *plausible*. A well-grounded RAG system should score high on groundedness.
A dashboard showing 0.97 looks like a system working well, not like a metric that is
structurally incapable of showing anything else.

It also never moved — which, if you were watching for it, is the tell. **A metric that
never changes is not measuring.**

### The fix

Generate an answer, then grade **that**. `harness.py:324-344`:

```python
generation = await complete(
    ModelRole.GENERATION,
    [{"role": "system", "content": _ANSWER_SYSTEM},
     {"role": "user", "content": f"{case.query}\n\nContext:\n{result.answer_context}"}],
    temperature=0.0,
)
verdicts.append(
    await judge_answer(case.query, result.answer_context,
                       getattr(generation, "content", "") or "", complete=complete)
)
```

with the comment at `:319-323`: *"passing the context in as its own answer made
groundedness ~1.0 by construction — a number with no signal in it, yet surfaced on the
report as a model-graded score. So generate first, then grade what was generated."*

Two model calls per case now. The second one is only meaningful because of the first.

The same structure is what makes `make_eval_fn` a real gate (`gate.py:111-131`): it
generates under the **candidate** prompt and judges that. A scorer that does not generate
under the candidate cannot be prompt-dependent, and a gate comparing a constant to itself
is the vacuous-pass bug wearing different clothes.

---

## Bug 4 — rolling back twice re-promoted the version you just rolled back from

### What was happening

`rollback(prompt_key)` selected the most-recently-active archived version:

```sql
SELECT * FROM prompt_versions
WHERE prompt_key = ? AND status = 'archived'
ORDER BY activated_at DESC
LIMIT 1
```

archived the current active, and reactivated the selection.

### Trace two rollbacks

State: v1 (archived, activated at t1), v2 (archived, t2), v3 (**active**, t3).

**First rollback.** v3 → archived, `activated_at` still t3. Candidates: v1 (t1), v2 (t2).
Newest is v2 → **v2 becomes active**. Correct.

**Second rollback.** v2 → archived, stamped `now()`. Candidates: v1 (t1), **v3 (t3)**. And
$t_3 > t_1$. So **v3 is selected** — the version you just rolled back from goes straight
back into production.

The system oscillates between v2 and v3 and can never reach v1.

### The scenario that makes it real

An incident. A promoted prompt is causing bad answers. The operator rolls back — better.
Something still looks off, so they roll back again to go further into history.

**The second rollback restores the broken prompt.** During an incident, on the operator's
own action, as a result of doing the documented thing.

### The root cause

`activated_at` was being asked to answer two different questions:

- *When was this last live?* (a historical fact)
- *Is this a valid revert target?* (a lifecycle state)

Those coincide until you roll back, at which point they diverge — and the query needed the
second while the field held the first.

### The fix

Redefine the field, and clear it on the version being rolled back **from**
(`registry.py:246-250`):

```python
current = await get_active(session, prompt_key)
if current is not None:
    current.status = PromptStatus.ARCHIVED
    # Retire it as a revert target: it is the version we are rolling back FROM.
    current.notes = _note_rollback(current, now)
    current.activated_at = None
```

and require the marker on candidates (`registry.py:233-239`):

```python
.where(
    PromptVersion.prompt_key == prompt_key,
    PromptVersion.status == PromptStatus.ARCHIVED,
    PromptVersion.activated_at.is_not(None),
)
.order_by(PromptVersion.activated_at.desc(), PromptVersion.version.desc())
```

Now repeated rollbacks **walk history backwards**.

**Two things fall out for free.** A rejected draft — ARCHIVED but never live — has a null
`activated_at`, so it can never be a revert target. And the historical fact is not lost:
`_note_rollback` (`registry.py:258`) appends an audit line to `notes` recording the
deactivation and the previous `activated_at`.

The docstring at `registry.py:223-227` records the whole thing in place.

---

## The related fixes shipped alongside

### Release decisions are idempotent

Human decisions arrive over a network: double-clicked, retried by a proxy, replayed out of
order.

**Two guards, at two layers.**

A **lifecycle guard** — only a STAGED version may be decided (`release.py:437-442`). The
docstring at `:414-420` states the failure: a second approve re-promotes, archiving
whatever legitimately replaced it; and a reject arriving after an approve archives the
now-ACTIVE version, *"leaving the `prompt_key` with no active version, so every run
silently drops to the floor prompt."*

An **atomic claim** — `decide_release` (`gate.py:310-321`) claims the durable row with
`UPDATE ... WHERE id = ? AND status = PENDING` and checks `rowcount`, **before** the draft
is touched, in one transaction. Zero rows means already decided, and the response carries
the **recorded** decision rather than the requested one (`:322-342`). If
`apply_release_decision` then raises because the version is not STAGED, the transaction —
claim included — rolls back and the row stays PENDING and decidable (`:346-350`).

The mirror guard at the other end: only a **DRAFT** may be released (`release.py:322-328`),
so a version cannot be released twice.

### The active-prompt cache publishes on commit, not on flush

`promote`/`rollback` deliberately leave the transaction open for the caller. Caching at
flush time publishes a prompt that may never be committed — *"a caller rollback (or a
crash) would leave `_ACTIVE_CACHE` serving a phantom system prompt to every run through the
synchronous hot path"* (`registry.py:56-61`), and nothing would correct it until the next
`refresh_cache`, which only runs at startup.

`_cache_on_commit` (`registry.py:53`) binds a **one-shot** `after_commit` listener and
snapshots the payload immediately, because the commit expires the ORM object.

**The cache is now exactly as durable as the row it mirrors.** That sentence is the
invariant.

### Diagnose ranks by failure RATE, not volume

A facet graded 500 times with 20 failures is healthier than one graded 25 times with 15 —
but the raw tally ranks the first as the worse offender and points the optimiser at the
healthy facet (`diagnose.py:198-201`).

So `diagnose` computes a **denominator** over the same window (`:202-219`) and steers by
rate (`_failure_rates`, `:103`; ordering at `:251-255`). The user prompt literally says
*"fix the highest RATE, not the highest count"* (`:264-265`).

Two implementation details that matter:

**The window is by `id`, not `ts`** (`:204-208`). Ids are monotonic with insertion and
compare identically on every dialect; a server-side `CURRENT_TIMESTAMP` stored as a naive
string on SQLite does not compare against a tz-aware Python bind parameter, so a `ts >=`
window silently returns nothing.

**The denominator is clamped** (`:222-223`): `totals[m] = max(totals.get(m, 0), count)`, so
a row written after the window query cannot produce a rate greater than 1.

And the breakdown always names the known facets, *"so a clean facet reads as '0% of N'
rather than as a silent absence"* (`:250-260`).

### Trace-eval reports the mean per facet, not the last row

A run usually has several steps of the same kind — two retrievals, three tool calls — each
persisting its own `EvalResult` row. `dict(written)` would keep only the **last** row per
facet, so the returned metrics map would contradict both the persisted rows and the
`overall` computed from all of them (`trace_eval.py:381-388`).

Small bug, and the class it belongs to is worth naming: **a summary that disagrees with the
rows it summarises destroys trust in both.**

### Draft version allocation under concurrency

`max(version) + 1` is check-then-act against a unique index, so two concurrent diagnose
passes can pick the same number. The loser's `IntegrityError` would surface **after** its
optimiser LLM call had already been paid for, throwing the rewritten prompt away.

So the insert is retried inside a **SAVEPOINT** with a freshly-read max
(`registry.py:118-152`), up to 5 attempts, and only a persistently-contended key gives up —
loudly, by re-raising (`:153`). The SAVEPOINT matters: a collision rolls back only this
INSERT, leaving the caller's outer transaction usable.

---

## Failure modes worth being able to enumerate

**The optimiser returns garbage.** `_parse_optimized_prompt` (`diagnose.py:128`) yields
`None` for non-JSON, a non-dict, or a missing/blank `system_prompt`. And the whole
optimiser call is wrapped so a transport failure yields **no draft**, not a crash
(`:270-283`). No draft is a safe outcome; a garbage draft is not — and either way the
release gate is still downstream.

**No failures to diagnose.** Returns immediately with `draft_version_id=None` without
touching the registry (`diagnose.py:229-235`). Nothing to fix means nothing to write.

**Trace-eval must never raise into its caller.** Per-metric failures are caught and logged
(`trace_eval.py:339-352`), and an outer `try` swallows anything else, so the caller always
gets a `RunEval` (`:12-16`). It runs off the hot path, post-run, and grading must never
break the run it is grading. **Rows are flushed, not committed** — the caller owns the
transaction boundary.

**The judge is off by default.** `judge_enabled()` reads `TAIF_EVAL_LLM_JUDGE`
(`judge.py:64`), so a normal `pytest` run never touches the network. The judge is also
**inject-only**: `complete=None` raises `ValueError` rather than lazily reaching for a host
completer (`judge.py:198-202`).

**Semantic cache contamination between eval cases.** Both `evaluate` (`harness.py:315`) and
`make_eval_fn` (`gate.py:116-117`) build a **fresh retriever per case**, *"so an earlier
query can never semantic-hit a later one."* Without that, case *n* could be answered from
case *n−1*'s cached result and the corpus would silently measure fewer cases than it
appears to.

**The eval corpus is tiny.** Three cases per release candidate (`DEFAULT_EVAL_SUBSET = 3`,
`gate.py:54`) and a default `eval_margin` of `0.0` — "strictly better on this sample". At
n=3 that is a weak signal, and it is a deliberate cost/latency trade (retrieve + generate +
judge per case). Both knobs are configurable. **Name this as a limitation rather than let
it be found**: the gate is a smoke test against regression, not a proof of improvement.

---

## The invariants worth naming

1. **A control that cannot run stops the release.** Judge failure, non-finite score, zero
   graded cases — all fail closed.
2. **A failure and a zero are different values.** `JudgeUnavailableError` is not `0.0`.
3. **Not measured is not passed.** `None` fails the gate; unlabelled cases contribute to
   neither numerator nor denominator.
4. **The judge grades a generated answer**, never the context against itself.
5. **The scorer must be prompt-dependent**, or the gate compares a constant to itself.
6. **A version is released once and decided once**, enforced by a lifecycle guard and an
   atomic claim.
7. **The cache is exactly as durable as the row it mirrors** — publish on commit.
8. **Diagnosis ranks by rate**, and a clean facet is shown at 0% rather than omitted.
9. **The floor is hand-authored.** The loop builds on the adapter prompt and can never go
   below it.

**Next:** [`40-diagrams.md`](40-diagrams.md).
