# Eval design — how Aegis proves retrieval and answer quality with a number it can defend

> **Scope.** Everything between "a corpus is ingested" and "a number goes on a slide". Metric
> definitions, gold-set construction (including the version we run on documents nobody has
> seen), the before/after ablation, the statistics that keep it honest, the CI-versus-demo
> split, a framework verdict, and the day-of procedure.
>
> **Constraints this document respects, without exception.** Windows laptop, 16 GB RAM, no
> Docker, no GPU. ~$100 total gateway credit for the whole event. Evals are a slice of a
> 3-day ingestion/retrieval phase, not the phase. The gold set must be constructible in
> under an hour on documents the team has never read.
>
> **Rubric alignment.** Business Impact (15%) is scored on *measured, not claimed*. Working
> Prototype (25%) is scored partly on demo readiness — and a measurement apparatus that has
> never been rehearsed is not ready. This document is written to move both.

---

## 0. The answer in one paragraph

**The quotable number is a paired before/after table on a 50-case gold set built in the first
hour of the hackathon, with a confidence interval on every cell and an explicit statement of
what the gold set cannot see.** The metric that carries it is **recall@20 and recall@6 at the
two k values this codebase actually uses**, graded not against chunk ids but against a
**verbatim answer span in the source document** — the only anchor that survives the fact that
different ablation arms chunk differently. Answer quality is carried by two things a jury can
check: a **deterministic citation-verification rate** that costs nothing and cannot be gamed,
and a **bounded LLM-as-judge pass on two arms only**, reported alongside a **human/judge
agreement rate from fifteen hand-labelled cases** — because "we used an LLM judge" is a weak
claim and "our judge agreed with a human on 14 of 15 spot checks" is a strong one. We adopt no
new eval framework: the in-house harness stays, Arize Phoenix is already installed if a
recognised name is wanted, and the existing CI gate keeps running exactly as it does today
under a label that stops anyone quoting it. The whole apparatus is roughly one engineering day,
of which the first half day produces a real number.

---

## 1. What exists today, verified against source

Every claim in this section was read out of the working tree, not out of a doc.

### 1.1 The offline harness is a good CI gate and an unquotable quality number

`aegis/src/aegis/evals/harness.py:275-291` builds the eval retriever:

- The `Retriever` and the `InMemoryKnowledgeBackend` are **real** — genuine embedded Chroma
  vector search, a genuine co-occurrence graph arm, and a genuine corpus-wide BM25 arm
  (`aegis/src/aegis/retrieval/memory.py:592-643`).
- The embedding is **not** real. `_fake_embed` calls `_local_embed`
  (`aegis/src/aegis/retrieval/memory.py:79-106`), a SHA-1 hashing bag-of-words vector. The
  "dense" arm is therefore a second lexical arm; it has no semantic behaviour whatsoever.
- The reranker is **not** real. `_fake_complete` (`harness.py:243-257`) returns
  `LLMResult(content="")`, which `rerank_scored` cannot parse, so the pipeline falls back to
  the fused RRF order and reports `graded=False` (`pipeline.py:190-216`). The gate measures
  the rerank stage *not running*.

So `context_precision@1 = 0.833` is a measurement of RRF over lexical signals on 5 documents
and 6 queries. It is an excellent structural regression detector. It is not a quality number,
and the sibling ingestion research (`ingestion-sota.md` §3.5) is right to say so.

### 1.2 The corpus is 5 documents and 6 single-gold cases

`aegis/src/aegis/evals/corpus.py:45-130`. Two of the five are deliberate distractors. Every
case has exactly one gold document. With six binary-per-case outcomes, **the smallest movement
`context_precision@1` can express is 1/6 = 0.167** — the metric's resolution is coarser than
most real regressions.

### 1.3 "Groundedness" on the offline path never sees an answer

`aegis/src/aegis/evals/metrics.py:232-238` computes it as the fraction of the case's claim
keywords present, by normalised substring match, in `result.answer_context`. That is a
property of *retrieval*, not of generation. It answers "could a faithful answer have cited
these phrases?", which is **context sufficiency**, not faithfulness. The name is the one place
this otherwise-scrupulous module overclaims, and it should be renamed (§8).

### 1.4 The honest-not-computed precedent already exists and is the right pattern

`harness.py:174-182` surfaces `answer_relevancy` with `computed=False, value=None` rather than
printing a plausible number, and `metrics.py:224-238` refuses to score an unlabelled facet
`1.0`. `judge.py:49-61` raises `JudgeUnavailableError` rather than returning `0.0` for a judge
that could not run. **Every new metric in this design inherits those three rules.** They are
the repo's strongest existing asset for a rubric that rewards honesty.

### 1.5 The ablation knobs mostly already exist

`RetrievalConfig` (`pipeline.py:82-137`) already carries `rerank_enabled`,
`spotlight_enabled`, `query_rewrite_enabled`, `recall_top_k=20`, `final_top_k=6`, `rrf_k=60`.
`ChunkPiece.contextualized()` (`chunker.py:110-121`) already prepends the `[A > B]` section
path and is applied unconditionally at ingest (`pipeline.py:486`). Two things are **not**
config-reachable and need a thin wrapper to ablate: individual recall arms (vector / graph /
BM25 are produced together by `recall_ranked`), and the contextual prefix.

### 1.6 What is already installed, and what is banned

- `backend/.venv` already contains **`arize-phoenix 14.6.0`**, **`arize-phoenix-evals 3.4.0`**,
  **`scipy 1.17.1`**, **`numpy 2.4.6`**. Phoenix is a declared extra in
  `aegis/pyproject.toml`. Bootstrap, Wilson intervals and McNemar are free in the backend.
- `aegis/tests/evals/test_isolation.py:29-30` **actively bans `ragas` and `deepeval`** from
  `aegis.evals`'s import graph, alongside `fastapi`, `litellm`, `sqlalchemy` and `torch`.
  Adopting either means deleting a test that protects a deliberate architectural property.
- There is **no `.github/workflows` directory**. "CI" today means a local `pytest` run
  (`aegis/pyproject.toml` `[tool.pytest.ini_options]`, `testpaths = ["tests"]`).

---

## 2. The metric set

### 2.1 The anchor problem, and why it decides everything else

The naive baseline chunks the corpus into fixed 400-word windows. The shipped pipeline chunks
it on structural boundaries. **Their chunk ids are not comparable.** A gold set keyed on
`chunk_id` therefore cannot grade the two arms against the same ground truth — which is the
single most common way a RAG ablation quietly becomes meaningless.

**Anchor the gold set to a verbatim answer span in the source document, not to a chunk.**

```
GoldCase = (query, doc_id, answer_span: str, page_no?, section_path?, kind)
```

A retrieved chunk is a **hit** iff `normalise(answer_span) in normalise(chunk.text)`, using
exactly the normaliser already in `metrics.py:46-52` (lowercase, non-alphanumeric runs
collapsed) so the spotlight datamarking defence cannot silently tank every score. `doc_id` and
`page_no` are carried for roll-up reporting, not for grading.

Three things fall out of this for free:

1. Every arm is graded against identical ground truth regardless of how it chunks or parses.
2. The gold set survives a re-ingest, a chunker change, and a parser swap. It does **not**
   need rebuilding when Docling lands.
3. The same substring check is the **citation-verification metric** (§2.3). One primitive,
   two metrics.

Edge case to handle explicitly: an answer span that straddles a chunk boundary in one arm and
not another. Mitigate by requiring gold spans to be **a single sentence, ≤ 30 words**, taken
verbatim from the source; and by recording a `span_split` flag when no chunk in *any* arm
contains it, so a case that is ungradeable is dropped and counted, never scored 0 against one
arm.

### 2.2 Retrieval metrics — which, why, and at which k

Report at the two k values that have operational meaning in this codebase, not at textbook
values:

| Metric | Definition | Why this one | k |
|---|---|---|---|
| **recall@20** | fraction of cases whose gold span appears anywhere in the fused recall pool | The **ceiling**. `recall_top_k = 20` is the pool that reaches rerank; if the answer is not here, no rerank, prompt or model recovers it. This is the number the ingestion and hybrid work moves. | 20 |
| **recall@6** | same, within what the generator actually reads | `final_top_k = 6`. This is the number the *answer* depends on. The gap between recall@20 and recall@6 is exactly the value of the reranker, isolated. | 6 |
| **precision@6** | fraction of the 6 delivered chunks that are hits | Context-window efficiency. Moves opposite to recall, which is why both are reported — a single "retrieval quality" score would hide one movement. | 6 |
| **MRR@20** | mean of 1/rank of the first hit | The interpretable ranking metric: "the answer sits at rank 1.3 on average." Jurors understand it instantly. | 20 |
| **nDCG@10** | position-discounted gain | Reported for recognisability, **flagged as degenerate**: with single-gold binary relevance nDCG@10 = 1/log₂(1+rank), which carries nearly the same information as MRR. It earns its place only on the multi-gold subset (§3.3, multi-hop cases). | 10 |

**Deliberately not adopted:** MAP (identical to MRR under single-gold and harder to explain);
hit@1 (too coarse at n=50 — one case is two points); any single blended "retrieval score".

#### What counts as good

Two kinds of target, and the second one is the real one.

*Absolute floors* — defensible defaults, to be stated as floors and not as achievements:

| Metric | Floor | Rationale |
|---|---|---|
| recall@20 | ≥ 0.85 | The pool is 20 chunks wide over a corpus of a few hundred. Missing >15% here is an ingestion or embedding defect, not a tuning problem. |
| recall@6 | ≥ 0.80 | Matches the guidance carried in `ingestion-sota.md` §3.5 for recall@10; ours is a tighter k, so treat 0.80 as ambitious rather than routine. |
| precision@6 | ≥ 0.35 | With mostly single-gold cases the arithmetic ceiling is 1/6 ≈ 0.167 per case; report it as a *relative* movement between arms and never as an absolute quality claim. |
| MRR@20 | ≥ 0.70 | Equivalent to the first hit sitting at rank ≤ 1.4 on average. |

*The real target*: **the shipped arm must beat the naive baseline by a margin whose 95%
confidence interval excludes zero.** A tutorial threshold hit on the day proves nothing about
our pipeline; a paired delta with an interval proves exactly the thing the rubric asks for.
Set the absolute targets from arm A0's measured value on the day, not from this table.

### 2.3 Answer quality — three deterministic metrics before any judge

The temptation is to jump to LLM-as-judge. Three metrics that cost nothing come first,
because they are the ones a sceptical juror can verify on the spot.

**(a) Citation validity — deterministic, free, ungameable.** For every citation the answer
emits, assert the quoted span is a normalised substring of the stored chunk it cites. On
failure, report a fuzzy ratio and label the citation `unverified` in the UI rather than
rendering it. This is ~40 lines, has no model call, and is precisely the gap
`ingestion-sota.md` §3.4 identifies as unfilled in the 2026 literature. **Target ≥ 0.95**, and
below 1.0 is a bug, not a tuning knob.

**(b) Refusal correctness on unanswerable questions — deterministic, free, and the most
persuasive hallucination number available.** Seed the gold set with 5-8 questions about
plausible topics the corpus provably does not cover. Score: does the system decline and say
so, or does it assert? Detection is a keyword/structure check on the answer plus "did it emit
any citation". **Target ≥ 0.90.** This is the honest form of a hallucination claim: we have no
baseline hallucination rate, but we do have a measured refusal rate on questions with a known
correct behaviour.

**(c) Context claim coverage — the existing proxy, correctly named.** What
`metrics.py` currently calls `groundedness`: the fraction of expected claim keywords present
in the retrieved context. Keep the computation, rename the metric. It measures whether
retrieval delivered enough material for a faithful answer to exist.

**What no deterministic proxy can substitute for, and we should say so:** paraphrase-level
faithfulness (an answer that restates the context in different words), contradiction (an
answer that inverts a condition present in the context), and answer relevance. Those need a
model. Everything else above does not.

### 2.4 LLM-as-judge, assessed honestly

**Where it is trustworthy.** For binary or near-binary faithfulness/hallucination judgements
against a supplied context, agreement with human annotators is reported high — RAGAS reports
~0.95 agreement for faithfulness on WikiEval, and RAGBench reports 93-95% span-level agreement
with Kendall's τ of 0.78-1.0. Those are benchmarks judges are effectively tuned against.
**On an unseen domain corpus on 30 August, our judge's agreement rate is unknown.**

**Its known biases, and which ones bite us.**

| Bias | Primary source | Does it bite this design? |
|---|---|---|
| **Verbosity** — longer answers score higher, measured at 15-30 points of inflated preference across GPT-4, Claude and PaLM-2 judges | Wang et al. 2023, via the LLM-as-a-Judge survey (arXiv:2411.15594) | **Yes.** Arms that retrieve more context produce longer answers. Mitigate: hold `final_top_k` and the answer system prompt identical across arms; report answer length per arm alongside the judge score so a length confound is visible. |
| **Position** — the option presented first wins | Zheng et al. (MT-Bench); rubric-based measurement in arXiv:2602.02219 | **Only if we go pairwise.** Our judge is pointwise (`judge.py:203-217`, one answer per call). **Rule: never A/B two arms inside one judge call.** Score arms independently and compare distributions. |
| **Self-preference** — a judge favours its own family's output, measured from −38% to +90% on ArenaHard | Panickssery et al., arXiv:2410.21819 | **Already mitigated by accident, keep it deliberately.** `routing.py:33-40` routes GENERATION to `gpt-4o` and REASONING (the judge) to `Phi-4-reasoning` — different families. Document this as a design decision, because it is a genuinely good one. |
| **Calibration drift** — the same rubric scores differently across runs/versions | LLM-as-a-Judge survey | **Yes.** Mitigate: pin the judge deployment id in the run artifact, `temperature=0.0` (already), and never compare a judge number from one run against another run's. |
| **Format** — rewards structured/confident prose | survey, ibid. | Minor here; all arms use one answer prompt (`harness.py:40-43`). |

**The 20-minute move that converts a weak claim into a strong one.** Hand-label 15 of the
judged cases — one human, three columns (grounded? relevant? agree with judge?) — and report
**judge/human agreement** next to the judge score. It costs nothing, it is a real
inter-annotator statistic, and it pre-empts the exact question a technical juror asks. Without
it, the judge number is an assertion; with it, it is a measurement with a stated error rate.

**Cost per run**, from the fallback price table at `routing.py:132-146` (USD per 1k tokens):

| Call | Role | Rate (in / out) | Tokens | Cost/case |
|---|---|---|---|---|
| Generate answer under test | GENERATION (`gpt-4o`) | 0.0025 / 0.01 | ~2 000 in, ~200 out | ~$0.0070 |
| Judge the answer | REASONING (`Phi-4-reasoning`) | 0.0011 / 0.0044 | ~2 500 in, ~600 out (reasoning preamble) | ~$0.0054 |
| | | | **total** | **~$0.0124** |

50 cases × 2 arms ≈ **$1.24 per judged sweep**. That is affordable — but the discipline is:
the judge runs on the **two headline arms only** (naive baseline and shipped), never on all
seven, because the retrieval metrics already separate the arms for free.

---

## 3. Building the gold set

### 3.1 The shape of the file

One JSONL, one object per case, hashed as a whole so a run artifact can pin which gold set
produced it:

```
{"id": "g-017",
 "query": "What is the escalation window for an enterprise customer at high priority?",
 "doc_id": "policy-escalation.pdf",
 "answer_span": "Enterprise and premium customers are escalated one tier earlier than standard.",
 "page_no": 4,
 "section_path": "Escalation > Priority tiers",
 "kind": "generated|handwritten|known_item|unanswerable|multi_hop|table",
 "provenance": "cheap-model draft, human-verified 2026-08-30T09:41Z"}
```

`kind` is load-bearing: it is what lets the report say "45 generated, 10 hand-written, 5
unanswerable" instead of "50 cases", and it is what makes the stratified subset tables honest.

### 3.2 Three construction sources, ranked by defensibility

**Source 1 — known-item, deterministic, zero model calls, ~10 minutes.** For a stratified
random sample of chunks, take the chunk's own highest-IDF terms (the IDF is already computed
by `bm25_ranked`) plus its section heading, and form a locator query. Gold span = a sentence
from that chunk.

*State its weakness in the write-up, unprompted*: known-item queries are lexically close to
their target, which **flatters BM25 and penalises dense and graph arms**. It is a floor
measurement and a regression detector, not a user-query distribution. Its value is that it is
the fallback that works with the network off and the gateway down.

**Source 2 — model-drafted, human-culled, the workhorse, ~$0.01, ~35 minutes.** One CHEAP-role
call per sampled chunk: *"Write one specific question this passage answers. Then quote,
verbatim, the single sentence that answers it."* Draft 60, then **one human reads all 60 and
deletes the bad ones** at 15-20 seconds each. Expect to keep 45-50.

Generate-then-cull is the right shape under a clock: deleting a bad question is roughly five
times faster than writing a good one, and the human still sees every case, so the set is
genuinely verified rather than genuinely automatic.

*State its bias, unprompted*: model-generated questions are biased toward chunks that are
easy to retrieve and toward the lexical surface of their source chunk. This inflates **all**
arms, and because it inflates the weaker arm more, it **compresses the measured gap**. Our
reported delta is therefore conservative — which is a good sentence to be able to say out loud.

**Source 3 — hand-written discriminating cases, ~20 minutes, written while Source 2 runs.**
These are the cases that actually separate arms; the generated ones mostly do not.

- 5 **multi-hop**: require joining two sections or two documents. These are the only cases
  where the graph arm can earn its keep, and the only ones where nDCG is non-degenerate
  (multi-gold).
- 5 **table**: the answer lives in a table cell. Reported as a separate subset; this is where
  the structure-aware delta will be largest.
- 5-8 **unanswerable**: plausible topics the corpus provably does not cover.

### 3.3 Composition target for a 50-case set

| kind | n | Purpose |
|---|---|---|
| generated (human-verified) | 30 | Coverage and the bulk of the statistical power |
| known_item | 5 | Deterministic floor; survives a gateway outage |
| multi_hop | 5 | Discriminates graph and hybrid |
| table | 5 | Discriminates structure-aware ingestion |
| unanswerable | 5 | Refusal-correctness metric |
| **total** | **50** | |

Stratify the generated sample across documents *and* across section depth, so a single long
document cannot dominate.

---

## 4. The ablation

### 4.1 The baseline must be a real strawman, not a rigged one

**Arm A0 — naive RAG.** `pypdf.extract_text()` → fixed 400-word windows with no overlap → no
section prefix, no dedup, no table handling → single dense arm → top-6 → no rerank. Roughly 40
lines.

Two rules that keep it honest, and both matter:

1. **A0 uses the same embedding model as every other arm** (the real gateway `EMBEDDING`
   deployment). Swapping the embedder between arms would attribute the embedder's quality to
   our pipeline. This is the most common way an ablation table lies.
2. **A0 is what a competent team ships in a weekend**, not a deliberately broken system. If a
   juror could say "you compared against something nobody would build", the table is worth
   nothing.

### 4.2 The arms

An additive ladder for the slide, plus two leave-one-out probes for honesty.

| Arm | What changes | Reachable today? |
|---|---|---|
| **A0** naive | pypdf + fixed windows, dense only, no rerank | new, ~40 lines |
| **A1** + layout-aware parse & structure chunking | Docling + `chunk_structured`, dense only | Phase 3 |
| **A2** + contextual section prefix | `ChunkPiece.contextualized()` on/off | needs one ingest flag |
| **A3** + hybrid recall | vector + graph + BM25 fused by RRF, `rerank_enabled=False` | **config only** |
| **A4** = shipped | A3 + LLM rerank, `rerank_enabled=True` | **config only** |
| **L1** A4 − graph arm | leave-one-out | needs a ~15-line filtering backend wrapper |
| **L2** A4 − BM25 arm | leave-one-out | same wrapper |

The ladder answers "what did our work buy?"; the leave-one-outs answer "does every piece
earn its place?" — and being willing to publish a leave-one-out that shows the graph arm
contributing ~nothing is worth more to a rubric that rewards honesty than a clean ladder is.

Report `spotlight_enabled` as a **separate, non-quality axis**: it is an injection defence, it
is expected to cost a little quality, and quantifying that cost is a stronger security story
than pretending it is free.

### 4.3 Sample size — what n=50 can and cannot see

This is the part most eval designs skip, and it is the part a technical juror will probe.

The comparison is **paired**: the same questions run on both arms. Paired designs test the
per-query *difference*, which has far lower variance than comparing two independent
proportions — and it is the only reason n=50 is workable at all.

For a binary per-query outcome (hit@k), the appropriate tests are **exact McNemar** on the
discordant pairs, or a **paired bootstrap** on per-query scores (B = 10 000 resamples, the IR
convention). What determines power is the number of queries where the two arms *disagree*, not
the total.

| True effect (recall@20) | n | Expected discordant pairs | Verdict |
|---|---|---|---|
| 0.55 → 0.80 (25 pts) | 50 | ~13-15, nearly all one-directional | comfortably significant, p < 0.001 |
| 0.65 → 0.80 (15 pts) | 50 | ~9-11, mostly one-directional | detectable, p ≈ 0.01-0.05 |
| 0.72 → 0.78 (6 pts) | 50 | ~6-10, split ~8/2 | **not detectable**, p ≈ 0.1 |
| 0.72 → 0.78 (6 pts) | 150 | ~20-30 | detectable |

**The honest headline: n = 50 can defend a difference of roughly 15 points or more, and cannot
defend one of 5.** Say that on the slide. It is a much better answer to "is that significant?"
than a shrug.

Absolute precision on a single arm, Wilson 95% interval at p = 0.80:

| n | half-width |
|---|---|
| 30 | ±0.14 |
| 50 | ±0.11 |
| 100 | ±0.08 |
| 200 | ±0.06 |

**Minimum viable n = 30** (below this the interval is wider than most effects worth
reporting). **Working target n = 50** — it fits the hour, it detects the effect size the
literature says structure + hybrid + rerank produces, and it matches the 40-60 guidance
already carried in `ingestion-sota.md` §3.5. **n = 100+ only if a half-day frees up**, and
the honest ranking is that 50 more gold cases beats almost any other half-day of eval work.

**Subsets are descriptive only.** The table subset has n = 5. Its Wilson interval is roughly
±0.40. Report the table and multi-hop subsets as *"indicative, n=5, not statistically
separable"* — labelled that way in the table itself, not in a footnote.

### 4.4 How every number is reported

Three rules, no exceptions:

1. Every absolute rate carries `n` and a Wilson 95% interval: `recall@20 = 0.84 (95% CI
   0.71-0.92, n = 50)`.
2. Every arm-vs-arm delta carries a paired-bootstrap 95% interval and the discordant count:
   `Δ = +0.22 (95% CI +0.10 to +0.34; 13 discordant, 12 favouring A4)`.
3. A metric that was not computed is reported as **not computed**, following the existing
   `computed=False` precedent (`harness.py:174-182`). Never as 0, never as a plausible float.

---

## 5. CI versus demo

The two must never be confused, and the strongest protection is that the artifacts look
different.

### 5.1 CI — free, offline, deterministic, every commit

Keep `aegis.evals` doing exactly what it does today, plus:

- **A mode banner.** Every report from `build_eval_retriever()` carries
  `"mode": "simulator", "embedding": "local-hash", "reranker": "passthrough"`. Anyone who
  copies that number into a slide has to delete the evidence that it is not a quality number.
- **Structural ingest fixtures** (from `ingestion-sota.md` §3.5 item 5): one golden PDF with
  hand-checked expected heading count and nesting depth, table count and per-table cell
  counts, chunk count, every chunk carrying a section path, every `word_start + word_count`
  inside the document. Ingestion regresses silently; nothing else will notice.
- **Citation-verification unit tests** — the verbatim-span primitive, with fixtures for the
  straddle case and the normalisation case.
- **The determinism test that already exists** (`aegis/tests/evals/test_regression_gate.py`
  runs the gate twice and compares) extended to the new IR metrics.

Cost: $0. Network: none. Runtime: seconds.

### 5.2 Demo — costs credit, must be reproducible

One script, one artifact:

```
python scripts/eval_goldset.py \
  --corpus ./corpus --gold ./gold/day-of.jsonl \
  --arms a0,a1,a2,a3,a4,l1,l2 --judge a0,a4 \
  --bootstrap 10000 --seed 20260830 \
  --out runs/eval-20260830T1140.json
```

Reproducibility is a property of the **artifact**, not of the code. The JSON pins: git sha,
every model deployment id resolved through `model_for()` at run time, the embedding model id,
a content hash of the corpus, a content hash of the gold file, the RNG seed, the full
`RetrievalConfig` per arm, per-case ranks, per-arm aggregates, intervals, bootstrap p-values,
and the measured token spend. A run that cannot be re-derived from its own artifact does not
go on a slide.

Cost of a full sweep (7 retrieval arms + 2 judged arms, n = 50):

| Component | Calls | Cost |
|---|---|---|
| Corpus embedding (~500 chunks, once) | 1 batch | ~$0.04 |
| Query embedding, 7 arms × 50 | 350 | negligible |
| LLM rerank, 3 arms with rerank on × 50 | 150 | ~$0.28 |
| Judge sweep, 2 arms × 50 (generate + judge) | 200 | ~$1.24 |
| **Total per full sweep** | | **~$1.60** |

Ten full sweeps across the build and the event ≈ **$16 of the $100**. Budget accordingly and
state the spend in the artifact — a team that can report its own eval spend to the cent is
making the Business Impact case in passing.

---

## 6. Frameworks — the verdict is "adopt nothing"

Assessed against our constraints, not against their feature lists.

| Framework | Verdict | Why |
|---|---|---|
| **RAGAS** | **No.** | Its headline metrics (faithfulness, answer relevancy, context precision/recall) are LLM-computed — cost and nondeterminism on every run, which is exactly what a CI gate must not have. Its non-LLM variants (`NonLLMContextPrecisionWithReference`) need `rapidfuzz` and score retrieved chunks by *string similarity to reference contexts* — strictly weaker than the exact verbatim-span ground truth we already have. Its testset generator is a genuinely good idea that we are reimplementing in 30 lines (§3.2) without the dependency. And `test_isolation.py:30` bans it. |
| **DeepEval** | **No.** | The pattern is already implemented natively in `aegis/src/aegis/evals/regression.py`, whose module docstring gives the reason (heavy, LLM-judge-backed for most metrics, network-dependent). Also banned by the same test. Adopting it deletes a test that protects a real architectural property, for zero new measurement. |
| **promptfoo** | **No.** | Node toolchain. A second runtime on a Windows laptop with no Docker, for a red-teaming strength we are not buying. |
| **TruLens** | **No.** | Production tracing. We have OTel and Phoenix already. |
| **Arize Phoenix** | **Already have it — use it if a recognised name is wanted.** | `arize-phoenix 14.6.0` and `arize-phoenix-evals 3.4.0` are installed in `backend/.venv` today, and `phoenix` is a declared extra. Its deterministic metrics (`precision_recall`, `exact_match`, `matches_regex` under `phoenix/evals/metrics/`) can compute the retrieval numbers under a recognised library name at **zero new install risk**. |

**Push-back on a scheduled item.** `ingestion-sota.md` §5 backlog item 8 proposes "RAGAS for
recognised metric names on the offline eval run only, 0.5 d, rubric optics". Half a day of
hackathon time for optics is the wrong trade when Phoenix is already in the venv and gives the
same optics for an afternoon's import. **A jury scores the number and the honesty of its error
bar, not the library that printed it.** Spend that half-day on the local ONNX cross-encoder
(the same doc's item 1) or on 30 more gold cases.

**Keep the in-house harness.** It is small, dependency-light, deterministic, and it already
encodes three honesty rules (not-computed, unlabelled-is-not-passing, judge-failure-is-not-zero)
that none of the frameworks above enforce for us.

---

## 7. The day-of procedure

Documents arrive at T0. A defensible number exists by T0 + 2 h, produced by **one person**,
while the rest of the team does the adapter swap. Every asset below is built and rehearsed
before 30 August; nothing here is written on the day.

| Window | Action | Cost | Blocking? |
|---|---|---|---|
| **T0 + 0-20** | One command ingests the corpus twice in parallel: through the shipped pipeline (A4) and through the naive baseline (A0), into separate collections. Print chunk counts, page counts, wall clock, peak RSS. | ~$0.04 | No human input |
| **T0 + 20-30** | **Source 1 gold set**, deterministic, no network. 15 known-item cases land immediately. *A number now exists even if everything else fails.* | $0 | No |
| **T0 + 25-45** | **Source 2 draft** — 60 CHEAP-role calls, one per stratified chunk, running in the background. | ~$0.01 | No |
| **T0 + 25-45** | **Source 3, in parallel** — the same human hand-writes 5 multi-hop, 5 table, 5 unanswerable cases while the drafts generate. This is the only irreducibly human block. | $0 | Yes |
| **T0 + 45-65** | **Cull** — read all 60 drafts, delete the bad ones. Keep ~45. Gold set is now ~50 cases with kind labels. Hash it. | $0 | Yes |
| **T0 + 65-85** | **Run every retrieval arm.** 7 arms × 50 cases. Paired bootstrap, Wilson intervals, McNemar. This produces the table. | ~$0.30 | No |
| **T0 + 85-105** | **Judge sweep on A0 and A4 only**, and in parallel one human hand-labels 15 judged cases for the agreement rate. | ~$1.24 | Partly |
| **T0 + 105-120** | **Freeze.** Write the one-page artifact: table, intervals, n, gold-set composition, stated biases, refusal list. This page *is* the Business Impact slide. | $0 | Yes |

### 7.1 The fallback ladder — because things break on the day

Ordered by what you lose:

1. **Gateway down during gold-set construction** → Source 1 only. Fully deterministic, no
   network. You still get a real paired recall@20 delta between A0 and A4. Label the set
   "known-item, deterministic, n = 15" and state its lexical bias.
2. **No time to cull** → run on the unculled 60 and label the number *"machine-generated gold
   set, unverified, n = 60"* on the slide itself. Weaker, still honest, still a measurement.
3. **Judge unavailable** → report retrieval metrics plus the two free answer metrics (citation
   validity, refusal correctness) and mark judge metrics `computed: false`. The existing
   `JudgeUnavailableError` path (`judge.py:49-61`) already makes this fail loudly instead of
   silently scoring 0.
4. **Ingestion partially fails** → report `n` over what actually ingested, and report the
   ingestion failure rate as its own number. A stated failure rate is a measurement; a quietly
   smaller corpus is a lie.
5. **Everything is on fire** → the CI simulator gate still runs offline in seconds. It is not
   a quality number and must be presented as what it is: "our regression gate is green."

### 7.2 The one thing that must happen before 30 August

**Rehearse the whole procedure end to end, under a clock, on a corpus nobody on the team has
read.** Grab 40-60 pages of public PDFs in an unfamiliar domain, start a timer, and run T0+0
through T0+120. Anything that takes longer than the table says gets fixed or cut *now*.

A procedure that has never been rehearsed will not run in two hours on the one morning it
matters. This rehearsal is worth more than any additional metric in this document.

---

## 8. The 0.5-1 day implementation slice

The smallest thing that produces a defensible number. Four files, **no new dependencies**, so
`test_isolation.py` stays green.

**1. `aegis/src/aegis/evals/goldset.py` (~120 lines).** The `GoldCase` dataclass of §3.1,
JSONL load/save, a content hash over the file, and `is_hit(chunk_text, answer_span)` using the
existing normaliser from `metrics.py:46-52`. *This is the file that must exist before 30
August* — everything else keys off this schema.

**2. `aegis/src/aegis/evals/ir_metrics.py` (~90 lines).** `recall_at_k`, `precision_at_k`,
`mrr`, `ndcg_at_k` over ranked chunk lists; `wilson_interval(successes, n)`;
`paired_bootstrap(per_case_deltas, iters=10_000, seed)` and `mcnemar_exact(b, c)` — all in
pure stdlib (`random`, `math`, `statistics`), so `aegis.evals` pulls no numpy and the
dependency-isolation test holds. The backend's installed scipy is available to the demo script
if a cross-check is wanted.

**3. `scripts/eval_goldset.py` (~150 lines).** Builds a retriever with the **real** gateway
embedder and the real backend — explicitly *not* `build_eval_retriever()` — runs each arm over
the gold set, and writes the pinned JSON artifact of §5.2 plus a Markdown table.

**4. `aegis/tests/evals/test_ir_metrics.py`.** Hand-worked fixtures: a known rank list with a
hand-computed nDCG and MRR; a Wilson interval checked against published values; a bootstrap
that is bit-identical under a fixed seed; the answer-span straddle case.

### 8.1 What this slice would say if run today

Run against the existing 3-document adapter corpus (`backend/src/app/adapter/corpus/`) with
real gateway embeddings and the 6 existing seed cases converted to span-anchored gold:

> On 6 span-anchored gold cases over the 3-document seed corpus, with real embeddings and the
> real reranker: recall@6 = 1.00 (95% CI 0.61-1.00, n = 6), MRR@20 = 0.92. **n = 6 is far too
> small to separate arms** — the value of this run is that the measurement apparatus is
> verified end to end, against a real gateway, before the corpus that matters arrives.

That is a modest claim and exactly the right one. It proves the instrument works on a day when
proving it does not cost anything.

### 8.2 The second half-day, if it is available

In priority order: (a) the A0 naive baseline arm and the arm runner — this is what turns
metrics into a *table*; (b) the gold-set builder script (Sources 1 and 2); (c) the ingest flag
that makes the contextual prefix ablatable and the ~15-line backend wrapper that makes single
arms ablatable.

### 8.3 One rename, worth doing in the same slice

`metrics.py`'s `groundedness` on the offline path never sees a generated answer (§1.3). Rename
it **`context_claim_coverage`** and keep the computation unchanged. The judge's genuinely
model-graded groundedness keeps the name it has earned. This is a five-minute change that
removes the only overclaim in an otherwise scrupulous module — and it is much better to have
made it ourselves than to have a juror find it.

---

## 9. What we refuse to claim, and why

The rubric rewards honesty and this team's whole positioning is *measured, never claimed*. A
refusal list is a positioning asset, not a limitation — it is the thing that makes the numbers
we *do* quote believable.

1. **We will not quote the CI gate's numbers as quality.** `build_eval_retriever()` uses a
   SHA-1 hashing embedder and a reranker that is measured *not running* (§1.1). 0.833 / 1.000
   / 1.000 over 6 cases on 5 documents is a wiring check. It goes on no slide.
2. **We will not call our deterministic proxies RAGAS metrics.** Already the repo's stated
   position (`metrics.py` module docstring). Keep it, and say it out loud.
3. **We will not call context-claim-coverage "groundedness".** It never sees an answer (§8.3).
4. **We will not quote a number without `n` and an interval.** A bare number invites exactly
   the question we could not answer.
5. **We will not present subgroup deltas as significant.** Table and multi-hop subsets are
   n = 5. They are labelled *indicative* in the table itself.
6. **We will not claim the judge is calibrated for this domain** unless we report the
   human-agreement rate from the 15 hand-labelled cases.
7. **We will not claim "X% fewer hallucinations."** We have no baseline hallucination rate.
   We have a refusal-correctness rate on N unanswerable questions with a known correct
   behaviour, and that is what we will say.
8. **We will not quote latency or cost improvements from the eval run.** Different machine
   state, different concurrency. Latency claims come from the demo path or nowhere.
9. **We will not compare against a system we did not run.** No "vs GPT-4 alone", no "vs
   typical enterprise RAG". Only A0, which we built, ran, and will show the code for.
10. **We will not inherit a threshold from a tutorial.** Targets are set from A0's measured
    value on the day. A floor hit on an easy corpus proves nothing.
11. **We will not describe the gold set as "human-written" when it is model-drafted and
    human-verified.** The provenance field says exactly what it is, and the slide says
    "45 model-drafted, human-verified; 15 hand-written."

---

## 10. Summary of decisions

| Question | Decision |
|---|---|
| Primary retrieval metric | **recall@20** (pool ceiling) and **recall@6** (what the generator reads) — the k values this codebase actually uses |
| Secondary | precision@6, MRR@20; nDCG@10 flagged degenerate under single-gold |
| Ground truth anchor | **verbatim answer span in the source document**, never chunk ids — the only anchor comparable across arms that chunk differently |
| Answer quality, free | citation-verification rate (≥ 0.95), refusal correctness on unanswerable questions (≥ 0.90), context claim coverage |
| Answer quality, paid | pointwise LLM judge, two arms only, ~$1.24/sweep, **reported with a 15-case human agreement rate** |
| Gold set | 50 cases: 30 model-drafted + human-culled, 5 known-item, 5 multi-hop, 5 table, 5 unanswerable |
| Gold set build time | ~65 minutes, one person, ~$0.01 |
| Ablation | additive ladder A0→A4 plus two leave-one-out probes; A0 uses the same embedder |
| Statistics | paired design; Wilson intervals on rates; paired bootstrap (B = 10 000) and exact McNemar on deltas |
| Minimum n | 30 floor, **50 target**, 100+ if a half-day frees up. n = 50 defends a ≥15-point delta and cannot defend a 5-point one — and we say so |
| Framework | **adopt none.** Keep the in-house harness; use the already-installed Arize Phoenix if a recognised metric name is wanted |
| CI | unchanged, plus a `mode: simulator` banner, structural ingest fixtures, citation-verification tests. $0, offline, deterministic |
| Demo | one script, one pinned JSON artifact, ~$1.60 per full sweep, ~$16 total across the event |
| Implementation | ~1 day: `goldset.py`, `ir_metrics.py`, `scripts/eval_goldset.py`, tests. First half-day produces a real number |
| The thing that must not be skipped | **a timed end-to-end rehearsal on an unfamiliar corpus before 30 August** |

---

## Sources

**Repository (read directly, current working tree)**
- `aegis/src/aegis/evals/{harness,metrics,corpus,judge,regression}.py`
- `aegis/src/aegis/retrieval/{pipeline,memory,chunker,reranker}.py`
- `aegis/src/aegis/gateway/routing.py` (model routing + fallback cost table)
- `aegis/tests/evals/test_isolation.py` (the `ragas`/`deepeval` ban)
- `backend/src/app/eval/regression.py` (strangler shim to `aegis.evals`)
- `backend/.venv` package manifest (Phoenix 14.6.0, phoenix-evals 3.4.0, scipy 1.17.1)
- `docs/teaching/evals-ops/10-guide.md`
- `docs/dev_new_docs_v2/research/ingestion-sota.md` §3.4, §3.5, §5

**LLM-as-judge bias**
- *A Survey on LLM-as-a-Judge* — <https://arxiv.org/html/2411.15594v6>
- Panickssery et al., *Self-Preference Bias in LLM-as-a-Judge* — <https://arxiv.org/pdf/2410.21819>
- *Justice or Prejudice? Quantifying Biases in LLM-as-a-Judge* — <https://arxiv.org/pdf/2410.02736>
- *Am I More Pointwise or Pairwise? Revealing Position Bias in Rubric-Based LLM-as-a-Judge* — <https://arxiv.org/pdf/2602.02219>

**Judge/human agreement**
- *Benchmarking LLM Faithfulness in RAG with Evolving Leaderboards* (FaithJudge) — <https://arxiv.org/pdf/2505.04847> · <https://aclanthology.org/2025.emnlp-industry.54/>

**IR statistics**
- Smucker, Allan & Carterette, *A Comparison of Statistical Significance Tests for IR Evaluation* (CIKM '07) — <https://dl.acm.org/doi/10.1145/1321440.1321528>
- Sakai, *Evaluating Information Retrieval Metrics Based on Bootstrap Hypothesis Tests* — <https://www.researchgate.net/publication/245550770>

**Metrics and gold-set practice**
- Dubrov, *RAG Retrieval Evaluation Metrics* — <https://slavadubrov.github.io/blog/2026/05/10/rag-evaluation-metrics/>
- Ragas — context precision / non-LLM variants — <https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/>
- Ragas — cost analysis — <https://docs.ragas.io/en/stable/howtos/applications/_cost/>
- *Know Your RAG: Dataset Taxonomy and Generation Strategies for Evaluating RAG Systems* — <https://arxiv.org/pdf/2411.19710>

**Framework landscape (2026)**
- *LLM Evaluation Framework Benchmark 2026* — <https://aiml.qa/llm-evaluation-framework-benchmark-2026/>
- *RAGAS, TruLens, DeepEval: LLM Evaluation Frameworks (2026)* — <https://atlan.com/know/llm-evaluation-frameworks-compared/>
- *DeepEval vs RAGAS vs TruLens: Pick Your RAG Eval Stack* — <https://particula.tech/blog/deepeval-vs-ragas-vs-trulens-rag-evaluation-stack>
