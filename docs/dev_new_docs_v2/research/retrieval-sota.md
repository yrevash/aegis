# Retrieval SOTA — what actually improves answer quality between a chunk and an answer

> **Scope.** Everything *after* a chunk exists: what goes in it, what happens to the query,
> how lists get fused and reranked, what gets indexed alongside, and how iterative loops
> behave. The parsing/ingestion half is covered by
> [`ingestion-sota.md`](./ingestion-sota.md) and is not repeated here — where the two touch
> (chunk enrichment, table serialisation, parser config) this document supplies the
> *retrieval-quality* evidence and defers the mechanics.
>
> **Governing principle for this document.** Every configuration number here has to be
> justified by measured retrieval quality, not by a library default and not by preference.
> Where the evidence for a default is thin, that is stated as thin rather than dressed up.
>
> **Constraints respected without exception.** Windows laptop, 16 GB RAM, no Docker, no GPU,
> CPU only. Postgres + Neo4j Desktop + Memurai native, embedded ChromaDB in-process.
> Embeddings via an API gateway with ~$100 total credit. ~3 engineering days for the
> ingestion/retrieval phase, inside ~13 days to the hackathon.

---

## 0. The answer in one paragraph

**The 2026 evidence says retrieval quality is won in three places, in this order:
(1) having a real corpus-wide lexical arm, (2) reranking, (3) putting the right
*deterministic* metadata in front of the chunk. Everything else — HyDE, multi-query,
query decomposition, reflection loops, RAPTOR, late interaction, semantic chunking —
is either contested, domain-specific, or actively negative.** The single cleanest number
in the literature is from T2-RAGBench (23,088 queries, 7,318 text-and-table documents,
`arXiv:2604.01733`): hybrid BM25+dense fused with RRF gets Recall@5 = 0.695; adding a
cross-encoder reranker takes it to **0.816**; adding Anthropic-style LLM contextual
enrichment to every chunk takes it only to **0.717**. Reranking is worth roughly *five and
a half times* what per-chunk LLM enrichment is worth, and it is a per-query cost rather
than a per-chunk one. On the same benchmark **BM25 alone (0.644) beats the state-of-the-art
dense embedder alone (0.587)**. For the user's specific question — what else belongs in a
chunk — the decisive study is the ECIR 2026 metadata paper (`arXiv:2601.11863`): prefixing
structured metadata lifted Context@5 from 33.3 % to 55.0 %, and the *field ablation* shows
the strongest signals are **document identity and date**, with **section headings a
secondary, chunk-localisation signal**. Aegis prepends the heading path and nothing else —
so we have the weaker half of the winning technique and are missing the stronger half, and
it costs zero model calls to fix.

**The most consequential finding about our own code:** in production, `LightRAGBackend`
implements `recall_ranked` but **not** `keyword_recall`. The pipeline therefore falls into
its pool-scoped branch, where BM25 only re-orders the ~20 candidates the dense/graph arms
already returned. **We do not have a corpus-wide lexical arm at all** — and corpus-wide
lexical retrieval is the most consistently valuable single arm in the entire 2026
literature. The code is scrupulously honest about this (`KeywordReport(scope="pool")`,
`adds_recall=False`), which is to its credit, but honesty about a missing arm is not the
same as having it.

---

## 1. Chunk enrichment — what else belongs in or alongside a chunk

### 1.1 The heading-path question, answered directly

Aegis's `ChunkPiece.contextualized()` prepends `[A > B]` to the chunk body before embedding,
and — importantly — stores that prefixed text as the chunk, so the lexical arm and the
reranker see it too. Here is the actual evidence for and against that.

**Evidence FOR (moderate, and more nuanced than usually claimed):**

| Source | What it measures | Result |
|---|---|---|
| ECIR 2026, *Utilizing Metadata for Better RAG* (`arXiv:2601.11863`), SEC filings + RAGMATE-10K | Field-level ablation of an 8-field metadata prefix | Removing **section headings** produced a *modest* drop in Context@K and **no effect at all** on Title@K. The authors' framing: global identifiers drive document-level accuracy; **section cues primarily aid chunk-level localisation**. |
| HiChunk (`arXiv:2509.11552`), HiCBench + Qasper | Hierarchical structure-aware chunking + hierarchy-aware retrieval vs fixed-size | On **evidence-dense** HiCBench: Evidence Recall **74.06 → 81.03** (+6.97 pp), Fact-Coverage 63.20 → 68.12, Rouge 35.70 → 37.29. On **sparse-evidence** Qasper: 64.50 → 65.53 (**+1.03 pp — near nothing**). |
| HiChunk, hierarchy-depth ablation | How many levels of hierarchy to use | Performance "gradually improves" from **L1 to L3**, with **minimal gains beyond level 3**. |
| Practitioner ablation on breadcrumb prepending (dev.to, heading-aware chunking) | Prepend breadcrumb into chunk *text* vs store it in a metadata field | Prepending into the text makes **both** the vector and the BM25 arm rank the correct chunk higher. LangChain's `MarkdownHeaderTextSplitter` — which strips the heading and puts the path in metadata — is explicitly called out as **suboptimal for hybrid retrieval, because the BM25 side never sees the section name.** |

**Evidence AGAINST / limiting:**

- The heading path is the *weakest* of the metadata fields the ECIR ablation tested. Removing
  it hurt less than removing company name or fiscal year, by a wide margin.
- HiChunk's own result shows the gain **collapses to ~1 point on sparse-evidence corpora**.
  If the jury hands us documents where each question is answered by one isolated passage,
  hierarchy buys almost nothing. If they hand us dense reports where evidence is spread
  across a section, it buys ~7 points of evidence recall.
- Nobody has published a clean isolated ablation of "heading path prefix, all else equal, on
  a general corpus". The strongest evidence is a *field-removal* ablation inside a
  multi-field prefix, plus a chunking study that bundles hierarchy-aware chunking with
  hierarchy-aware retrieval (Auto-Merge). **The direction is reliable; the magnitude for our
  corpus is unknown until we measure it.**

**Verdict on the heading path.** Keep it — it is on the right side of the evidence, it is
free, and prepending it into the *text* (rather than only into metadata) is specifically the
correct choice for a hybrid pipeline. **But it is the junior partner.** Two honest
refinements the evidence supports:

1. **Cap the path at ~3 levels** (HiChunk: gains saturate past L3). Deepest-3 rather than
   full path, so a 6-level document does not spend 30 tokens of every chunk on breadcrumbs.
2. **It is not the enrichment that matters most.** See below.

### 1.2 What else goes in the chunk — ranked by evidence

This is the user's real question. Ranked by *evidence strength × (1 / cost)*.

| # | Candidate | Evidence | Ingest cost | Verdict |
|---|---|---|---|---|
| **1** | **Document identity** (title / issuer / filename) as a prefix | **Strong.** ECIR 2026 field ablation: removing company name + year caused **severe degradation in both Context@K and Title@K at every K**. Full prefix lifted Context@5 from 33.3 % → 55.0 % (OpenAI embedder) and 26.7 % → 61.7 % (bge-m3). | **Deterministic. Zero model calls.** | **ADOPT NOW.** This is the biggest free win available anywhere in the retrieval stack. |
| **2** | **Temporal marker** (date / period / fiscal year / version) | **Strong.** Same ablation, same severity — company **and year** were removed together as the strongest joint signal. Independently, the enterprise failure literature names conflicting-version retrieval (old policy vs new policy) as a top production failure. | **Deterministic** when the parser, filename or frontmatter carries a date; skip the field when it does not rather than guessing. | **ADOPT NOW.** |
| **3** | **Document type** (form type / doc class) | **Moderate.** One of the 8 fields in the winning ECIR prefix; not isolated individually. | Deterministic. | **ADOPT NOW** (it rides along free with 1 and 2). |
| **4** | **Heading path, capped at 3 levels** | **Moderate.** §1.1. Real on evidence-dense corpora (+7 pp evidence recall), near-zero on sparse ones; saturates at depth 3. | Deterministic. **Already built.** | **KEEP, cap depth.** |
| **5** | **Table / figure caption bound to its object** | **Moderate.** TableRAG (`arXiv:2506.10380`) on flattening loss; the multimodal-RAG literature names "figure captions retrieved without the figure they describe" and "partial caption from an adjacent unretrieved chunk" as concrete, named failure modes. | Deterministic from a structured parse. | **ADOPT NOW** — ingestion phase owns the mechanics. |
| **6** | **A natural-language summary as the *embedded* text for a table** (grid stays the payload) | **Moderate.** TableRAG; consistent practitioner finding that a pipe grid is a poor embedding target and a good reading target. | Deterministic (template) or one cheap call per table — **tables are ~1 % of chunks, so even the LLM version is affordable**. | **ADOPT NOW.** |
| **7** | **Neighbouring-chunk context / parent-child expansion** | **Moderate.** SCAR (`arXiv:2606.16661`) shows continuity-weighted neighbour expansion beats plain top-k *and* beats naive neighbour concatenation — i.e. **indiscriminate expansion degrades results**. H-RAG at SemEval-2026 (`arXiv:2605.00631`) uses the same shape. Practitioner reports of 15–30 % answer-accuracy gains are unrefereed and should not be quoted as fact. | **Retrieval-time expansion + metadata.** No new embedding, no model call. | **ADOPT IF TIME.** Gate the expansion on similarity, per SCAR — do not just glue neighbours on. |
| **8** | **Page number / position-in-document** | **No retrieval-quality evidence.** This is a *provenance and citation* feature, and a very good one, but no paper shows it improves recall. | Deterministic. | **ADOPT NOW for citations. Do not claim a retrieval gain for it.** |
| **9** | **LLM-generated situating context per chunk** (Anthropic contextual retrieval) | **Strong but smaller than its reputation once you already have hybrid+rerank.** Anthropic: top-20 failure 5.7 % → 3.7 % contextual embeddings, → 2.9 % + contextual BM25, → 1.9 % + rerank. But on T2-RAGBench, measured *on top of hybrid RRF*, it is **0.695 → 0.717 Recall@5 (+2.2 pp)** versus reranking's **+12.1 pp**. | **One LLM call per chunk.** ~$1.02 / M document tokens with prompt caching; wall-clock cost on a metered gateway during a live ingest is the real constraint. | **ADOPT IF TIME — after rerank and BM25, never before.** Measure the delta against the free deterministic prefix before paying for it. |
| **10** | **Generated hypothetical questions per chunk (HyPE)** | **Large claimed effect, moderate-to-weak methodology.** `arXiv:2607.29402`, 6 datasets: claim recall 53.6 % → **71.5 %**, context precision 42.3 % → **63.5 %**, hallucination 26.0 % → **19.9 %**, overall F1 27.9 % → **37.6 %**; HyDE was **no better than naive** (52.6 / 41.6). **Caveats that matter:** metrics are RAGChecker LLM-judged, not IR ground truth; one generator model (Mistral-NeMo); **noise sensitivity got *worse***; MS MARCO showed no gain (saturation); and **it is never compared against hybrid + rerank**, so its headroom over our actual baseline is unmeasured. | **One LLM call per chunk**, plus the index grows **m×** (one vector per hypothetical question). | **ADOPT IF TIME, as a measured experiment only.** It is the most interesting unproven idea on this list. Do not ship it unmeasured. |
| **11** | **Keyword / entity list appended to the chunk text** | **Weak — no paper isolates this as a win.** The entity signal that *is* well evidenced is a separate entity index (which LightRAG already builds), not entity words stuffed into the chunk body. Stuffing terms into chunk text also perturbs BM25 document length and IDF, so it can quietly hurt the lexical arm. | Deterministic (RAKE/YAKE) or a model call. | **REJECT as chunk-text enrichment.** Keep entities as their own index. |
| **12** | **A document-level summary prepended to every chunk** | **Negative.** Anthropic explicitly tested generic document summaries and summary-based indexing and reported *"very limited gains"* / *"low performance"*. ARAGOG's document-summary index likewise failed to distinguish itself. | One call per document (cheap!) — but the cheapness is not the issue. | **REJECT.** The cheapness is a trap; it dilutes every chunk's embedding with the same text. |

### 1.3 The two implementation rules the evidence is unambiguous about

1. **Prefix, not suffix.** ECIR 2026 tested both: **suffix placement was never optimal;
   prefix consistently outperformed it.** Our `contextualized()` already prefixes. Correct.
2. **Into the text, not only into metadata.** The metadata must be inside the string that
   gets embedded *and* indexed lexically, otherwise the lexical arm is blind to it. Our
   `pipeline.ingest()` stores `piece.contextualized()` as `Chunk.text`, so the prefix reaches
   the vector store, the keyword pass, the reranker and the answer context. **Correct, and it
   is the specific thing LangChain's header splitter gets wrong.**

There is also a third strategy the ECIR paper found *better still* than prefixing:
**a "unified" dual-encoder that embeds metadata and content and fuses them into one vector**
(Context@5 63.3 % vs prefix 55.0 % on the OpenAI embedder). It is out of reach for us — it
needs control of the embedding function, and we embed through a gateway. Worth knowing that
the ceiling above prefixing exists; not worth chasing in 13 days.

Two of the ECIR paper's *negative* results are directly relevant to §2 below:
**late fusion of a separate metadata index barely beat the baseline (36.7 % vs 33.3 %)**, and
**LLM-based query reformulation to surface metadata constraints underperformed simple
prefixing (41.4 % vs 55.0 %)**. Putting the metadata in the chunk beat both alternatives.

---

## 2. Query-side techniques — which ones actually pay

The headline: **on enterprise Q&A, the query-side techniques with the best press have the
worst evidence.** This is the area where "sounds impressive in a pitch" and "real quality
win" diverge most sharply.

### 2.1 HyDE — contested, and the contest resolves against us

| Source | Domain | HyDE result |
|---|---|---|
| T2-RAGBench (`arXiv:2604.01733`) | Financial text + tables, 23 k queries | **Recall@5 0.544 — worst of all 10 strategies, below plain dense (0.587) and far below BM25 (0.644).** Authors' explicit guidance: *"Avoid HyDE for domains with precise numerical or entity-centric queries"* — the generated hypothetical documents hallucinate plausible-but-wrong figures. |
| ARAGOG (`arXiv:2404.01037`) | AI/ArXiv research papers | *"HyDE and LLM reranking significantly enhance retrieval precision."* |
| *Out of Style* (`arXiv:2504.08231`) | Linguistic-variation robustness | HyDE **improves robustness to reworded queries** but **reduces retrieval effectiveness on original queries by 5.43 %**. |
| HyPE study (`arXiv:2607.29402`) | 6 mixed datasets | HyDE was **indistinguishable from naive RAG** (claim recall 52.6 % vs 53.6 %). |
| Anthropic contextual-retrieval writeup | Mixed (code, fiction, ArXiv, science) | Tested HyDE, reported *"very limited gains"*. |

**Reading this honestly:** HyDE helps when the query is conceptual and abstract and the
corpus is prose (ARAGOG's domain). It hurts when the query names an entity, an identifier or
a number (T2-RAGBench's domain). **We do not know which the blind problem statement will be,
and the downside is larger and better replicated than the upside.** Three of five sources
range from neutral to negative. **Reject as a default.** If it ships at all it must be a
flag, off, with a measured golden-set justification for turning it on.

### 2.2 Multi-query / RAG-Fusion — negative or marginal, three independent sources

- T2-RAGBench: Multi-Query + RRF gets **Recall@5 0.640** — *below* plain BM25 (0.644) and
  well below hybrid RRF (0.695). It costs N× the retrieval and an LLM call, and loses.
- ARAGOG: *"Multi-query approaches underperformed."*
- *Scaling RAG with RAG Fusion: Lessons from an Industry Deployment* (`arXiv:2603.02153`):
  the deployment concluded the latency and token cost **did not justify the quality
  improvement**, and single-query retrieval remained more practical.

**Reject.** Three sources, two of them measured against a real baseline, one of them a
production post-mortem.

### 2.3 Query decomposition — dangerous, and the failure mode is spectacular

*Agent-Orchestrated Adaptive RAG* (`arXiv:2606.05658`) is the cleanest measurement:

| Benchmark | Metric | Baseline | + Decomposition |
|---|---|---|---|
| DevOps (structured) | Overall | 0.814 | **0.855** |
| DevOps | MRR | 0.556 | **0.722** |
| DevOps | Latency | 21 s | **48 s** |
| MuSiQue (multi-hop) | Overall | 0.786 | 0.809 |
| MuSiQue | **MRR** | 0.469 | **0.102** ⚠ |
| MuSiQue | **Success@5** | 1.000 | **0.063** ⚠ |
| MuSiQue | Latency | 21.8 s | **74.9 s** |

Decomposition **collapsed ranking precision on the genuinely compositional benchmark** —
Success@5 went from perfect to 6 %. The authors' own framing: decomposition *"broadens
retrieval but fragments ranking signals."* The component-ablation study
(`arXiv:2606.21553`, HotpotQA, local 7B) independently found query decomposition showed
*"minimal gains relative to computational overhead"*, with a measured **16.7 s/query**
decomposition overhead.

The one genuinely positive variant is **LLM-free** decomposition: ToR-Lite
(*Applied Sciences* 16(8):3966) gets +6.03 pp Hits@10 on MultiHop-RAG at 3.18× the speed of
LLM-based decomposition — but that is a different technique from what a hackathon demo would
build.

**Reject as a default. Do not put it behind a router either** — the router would have to
decide correctly which regime a blind corpus is in, and the downside case loses 94 points of
Success@5.

### 2.4 Query rewriting for conversational context — the one that survives

Our `query_rewrite.py` does *coreference/ellipsis resolution against conversation history*.
That is **not** the same technique as query expansion, and it is not what the negative
results above are about. Resolving "what about its refund window?" into a standalone query
is a correctness fix for multi-turn retrieval, and the multi-turn RAG literature (Sifei at
SemEval-2026, `arXiv:2606.28352`, "Hybrid Retrieval and Query Rewriting for Multi-Turn RAG")
treats it as a standard component. Our implementation's failure semantics — every failure
mode collapses to an honest no-op with `changed=False` — are exactly right. **Keep.**

One caution: the ECIR metadata paper's negative result on *metadata-aware query
reformulation* (41.4 % vs 55.0 % for prefixing) says **do not extend the rewriter to inject
metadata constraints into the query**. Put the metadata in the chunk instead.

### 2.5 Step-back prompting — evidence is thin, essentially absent for RAG

Step-back prompting has a real reasoning-benchmark provenance but **no RAG retrieval
benchmark in this survey isolated it**. It does not appear in T2-RAGBench, ARAGOG, RAGSmith's
selected modules, or the agentic ablations. **The evidence here is thin. Reject on absence of
evidence, not on evidence of absence** — say it that way if asked.

### 2.6 Query routing — a cost lever, not a quality lever

RAGRouter-Bench (`arXiv:2604.03455`, 7,727 queries, 4 domains) is the honest baseline study:

- Best router (TF-IDF + SVM): **93.2 % accuracy, 28.1 % token savings** — recovering ~80 % of
  the 35.2 % theoretical maximum under perfect routing.
- The paper's own warning: a majority-class baseline achieves **60 % savings at 0.231
  macro-F1** — *"optimizing cost without accuracy is misleading."*
- Routing on query text alone is structurally limited: optimal paradigm selection needs
  query–corpus interaction, which the router cannot see.

Adaptive-RAG's T5-Large complexity classifier reaches 85.9 % accuracy and matches
always-expensive baselines at lower cost. **Note what that claims: parity at lower cost, not
better answers.**

**Verdict: routing is a budget technique.** With ~$100 of credit it is defensible, and a
TF-IDF+SVM router is cheap and offline-capable. But it is not on the quality path, and a
router that mis-routes is strictly worse than no router. **Adopt-if-time, framed as cost
control, never pitched as accuracy.**

### 2.7 Query-side summary table

| Technique | Evidence | Cost | Verdict |
|---|---|---|---|
| Conversational query rewrite (ours) | Moderate, standard component | 1 cheap call, only with history | **Keep** |
| Query routing | Moderate — cost only | 1 classifier, ~ms | **Adopt if time, as cost control** |
| Step-back prompting | **Thin/absent for RAG** | 1 call | **Reject (no evidence)** |
| HyDE | **Contested; negative on entity/numeric queries** | 1 call/query | **Reject as default** |
| Multi-query / RAG-Fusion | **Negative ×3** | N× retrieval + 1 call | **Reject** |
| Query decomposition | **Catastrophic on multi-hop ranking** | +16–53 s/query | **Reject** |

---

## 3. Fusion and reranking

### 3.1 Is RRF still right in 2026?

**Mostly yes, with an honest asterisk.**

- **For:** T2-RAGBench shows hybrid RRF beating both constituents decisively — BM25 0.644,
  dense 0.587, **hybrid RRF 0.695** Recall@5. RRF needs only ranks, so it is immune to the
  incomparable score scales of cosine, graph-proximity and BM25 — which is exactly our
  situation with three heterogeneous arms. It needs no training data, which matters when the
  corpus is unknown until the day.
- **Against:** *An Analysis of Fusion Functions for Hybrid Retrieval* (`arXiv:2210.11934`)
  finds **convex combination (CC) outperforms RRF in both in-domain and out-of-domain
  settings**, that **RRF is sensitive to its parameters** (contradicting the folklore that
  k=60 is safe), and that CC is **sample-efficient — one parameter, a small tuning set.**

**Verdict for Aegis: keep RRF.** CC's advantage requires a tuning set, and on the day we will
have a 40–60 case golden set at most, built from the same corpus we would tune on — that is
how you overfit a fusion weight. RRF's *weightlessness* is genuinely the right default under
corpus uncertainty. **But stop describing weightlessness as unambiguously superior** — the
docstring in `fusion.py` and `docs/learn/10-architecture.md` currently present it that way,
and the literature says it is a robustness trade, not a free lunch. If the golden set gets
built and a spare hour appears, a single tuned convex-combination weight is a legitimate
measured upgrade.

**Do not touch `rrf_k = 60`** without a golden set. It is the community default and the
paper that criticises RRF's parameter sensitivity does not hand us a better value.

### 3.2 Reranking is the biggest lever in the entire stack

Four independent sources put reranking first:

| Source | Measurement |
|---|---|
| T2-RAGBench (`arXiv:2604.01733`) | Hybrid RRF → Hybrid + cross-encoder rerank: **Recall@5 0.695 → 0.816 (+12.1 pp), MRR@3 0.433 → 0.605 (+17.2 pp)**. Authors: *"reranking is the single most impactful component."* |
| Anthropic contextual retrieval | The largest single step of its ladder: **2.9 % → 1.9 %** top-20 failure. |
| *Rerank Before You Reason* (`arXiv:2601.14224`) | Reranking at depth 50 lifted NDCG@5 from **19.72 → 46.05 (+133 %)**. *"Allocating budget to reranking yields better returns than increasing search-agent reasoning effort."* |
| *Dissecting Agentic RAG* (`arXiv:2606.21553`) | Of four agentic components, reranking was one of only two with a favourable cost-benefit profile. |

**How deep to rerank.** `arXiv:2601.14224` measured the curve: d=10→20 gains ~5–7 NDCG@5
points; d=20→50 gains diminish to ~3.5–10; **beyond d=50, diminishing returns.** Their
recommendation is **d=20 as the cost-effectiveness optimum.** Our
`RetrievalConfig.recall_top_k = 20` is therefore **already at the evidence-backed setting** —
which is worth saying out loud, because it was not chosen from this evidence. If latency
budget appears, d=50 is the next measured step, not d=100.

**Which reranker on CPU.** The strongest CPU-viable options as of August 2026:

| Model | Params | Quality | Note |
|---|---|---|---|
| `jinaai/jina-reranker-v1-tiny-en` | 33 M | floor | ships in `fastembed` `TextCrossEncoder`, no torch |
| `jinaai/jina-reranker-v1-turbo-en` | ~38 M | better latency/quality point | ships in `fastembed` |
| `BAAI/bge-reranker-v2-m3` | 278 M | BEIR nDCG@10 ≈ **51.8**, multilingual | the usual production default |
| `mixedbread-ai/mxbai-rerank-base-v2` | 0.5 B | BEIR nDCG@10 **55.57**, Apache-2.0, 100+ languages | **~4.5× lower latency than bge-v2-m3** in the vendor's own A100 comparison (0.67 s vs 3.05 s on NFCorpus) |
| `mixedbread-ai/mxbai-rerank-large-v2` | 1.5 B | BEIR nDCG@10 **57.49** | too large for 16 GB alongside Neo4j's JVM |

**Recommendation: benchmark `jina-turbo`, `bge-reranker-v2-m3`, and `mxbai-rerank-base-v2` on
our machine and pick on measured quality-per-millisecond.** Do not lock to the 33 M tiny
model on principle — it is the floor, not the target. Note honestly that mxbai's latency
figures are the vendor's own and are GPU-measured; the CPU ordering must be re-measured.
The `~8 ms/pair` CPU figure for a 278 M model that the ingestion doc quotes is third-party
and unverified on our hardware.

### 3.3 Our LLM reranker: three specific problems the evidence names

`reranker.py` sends one gateway call that scores all candidates 0–10 and returns JSON. That
is a **pointwise-scored listwise prompt**. The literature flags three issues:

1. **Positional bias.** *LLM-based Listwise Reranking under the Effect of Positional Bias*
   (`arXiv:2604.03642`, ECIR/Springer) finds passages positioned toward the **end of the
   input are systematically less likely to be moved to top positions**, from both
   architectural bias and imbalanced relevant-document placement. Our prompt enumerates
   candidates `[0] … [19]` in fused order, so the bias compounds with the fusion order rather
   than correcting it.
2. **Cost.** `arXiv:2601.14224` measures cross-encoders at **γ = 0.32 relative cost** versus
   listwise LLM reranking, and finds they *"outperform listwise reranking on efficiency."*
   Our LLM rerank is the dominant per-query model call in the whole pipeline.
3. **Quality is not obviously better.** `arXiv:2608.09650` reports a **109 M cross-encoder
   fine-tuned with ListNet beating a 4 B LLM instruction reranker by 2.6 pp NDCG@3 at 37×
   fewer parameters.** Zero-shot GPT-4-class listwise reranking (RankGPT, avg nDCG@10 53.68
   on BEIR) is strong, but it is a frontier model, not our `ModelRole.CHEAP`.

**A local ONNX cross-encoder would be cheaper, deterministic, offline-capable, free of
positional bias, and reproducible in evals.** The ingestion research already ranked this
#1 on its more-time list. This document ranks it **adopt-now**, and puts BM25 next to it.

### 3.4 Context ordering after rerank — the "lost in the middle" folklore does not replicate

*Lost in the Evidence? Reproducing Document Position and Context Size Effects in RAG*
(`arXiv:2605.27105`) attempted to reproduce the U-shaped position curve on LLaMA-3.1-8B and
Mistral-NeMo-12B and **did not recover it**: accuracy was *"comparatively flat across
positions, with only a slight upward trend."* Their practical guidance:

- **Single-hop:** retrieve *more* passages; performance improves with context and is
  **order-stable**.
- **Multi-hop:** placement matters more than count; **reverse ordering** helps at larger
  context sizes.

**Implication for us: do not build a "sandwich" reordering step.** It is a folklore
mitigation for an effect that does not reproduce on current models, and `final_top_k = 6`
is far inside the flat region anyway. If anything, the evidence supports **raising
`final_top_k`** for single-hop questions, which is a one-line experiment on the golden set.

---

## 4. Multi-vector and hierarchical indexing

| Technique | Evidence | Under our constraints | Verdict |
|---|---|---|---|
| **Parent–child / small-to-big** | Moderate. H-RAG SemEval-2026 (`arXiv:2605.00631`); HiChunk's Auto-Merge is the same idea and contributes to its +6.97 pp evidence recall on evidence-dense data. Widely-quoted 15–30 % accuracy figures are **unrefereed vendor claims — do not repeat them.** | Metadata + retrieval-time expansion. **No new index, no new model.** | **ADOPT IF TIME.** Cheapest hierarchical technique by a wide margin. |
| **Neighbour expansion with a continuity gate (SCAR)** | Moderate. `arXiv:2606.16661`: continuity-weighted expansion beats top-k *and* beats naive concatenation; **naive expansion degrades context.** | Pure retrieval-time logic. | **ADOPT IF TIME** — and if we do parent-child, do it *this* way, gated, not glued. |
| **RAPTOR summary tree** | Real for multi-hop over long documents (ICLR 2024), and the 2026 successors (hierarchical abstract trees, `arXiv:2605.00529`) beat RAPTOR's GMM clustering. But it is an **LLM call per cluster per level**, and Anthropic's negative result on summary-based indexing applies directly. | Ingest-time LLM cost we cannot afford; multi-day build. | **REJECT at 13 days.** |
| **ColBERT / late interaction** | Strong IR pedigree. PLAID gets CPU latency to *hundreds of ms* at 140 M passages — genuinely impressive. | **Requires a local ColBERT model at index *and* query time**, replacing our gateway embedder entirely; PLAID needs **>20 GiB index for 8.8 M passages**. Our corpus is small enough that storage is not the blocker — the **architecture swap** is. | **REJECT.** Not the index size; the fact that it is a different embedding architecture and we have 13 days. |
| **ColPali / visual multi-vector** | Strong on scanned/chart-heavy docs. | GPU at both ends, ~170× index storage, >7 s CPU query latency. | **REJECT permanently** (concurs with `ingestion-sota.md` §1.2). |
| **HyPE multi-vector-per-chunk** | See §1.2 #10. This *is* a multi-vector index — m vectors per chunk — but built from generated questions rather than token embeddings, so it works through a plain embedding API. | m× index growth, 1 LLM call/chunk. | **ADOPT IF TIME, measured only.** |
| **Summary / document-summary index** | **Negative** — Anthropic; ARAGOG. | — | **REJECT.** |

### 4.1 An uncomfortable finding about our graph arm

*BM25 Wins at Scale: A Scaling Study of RAG Paradigms* (`arXiv:2607.26497`) evaluated seven
pipelines across 28 nested corpus tiers from 1,144 to 511,959 documents:

| Method | Bedrock scale (combined score %) | Index build cost (generative tokens) | Scaling exponent |
|---|---|---|---|
| **BM25** | **74.7** | **0** | — |
| File-System Agent | 77.4 | 0 | query cost 39–60× BM25 |
| HippoRAG 2 | 66.2 | 7.5 M | b = 1.01 (linear) |
| DenseRAG | 58.1 | embeddings only | — |
| **LightRAG** | **48.0** | **34.6 M** | **b = 1.36 (super-linear)** |
| MS-GraphRAG | 45.9 | 35.1 M | b = 0.92 |

LightRAG **only completed indexing to 3 M tokens** before hitting a scaling wall, and its
projected full-scale build is ~102 B generative tokens. The authors' conclusion:
*"LLM-built graph indexes are difficult to justify at 10⁵–10⁶ documents unless construction
is near-linear and relational questions dominate"*, and *"for enterprise-style corpora, BM25
is the appropriate default."*

**How to read this fairly.** This is a *scaling* study, and a hackathon corpus is a handful
of documents — well below every crossover point, where LightRAG's build cost is trivial and
its numbers are not catastrophic. The graph is also a **demo and explainability asset**
(live graph viz, provenance, multi-hop narrative) whose value to a jury rubric is real and is
not captured by combined-score-on-QA. **So: do not rip out the graph.** But do stop treating
it as the quality engine. The balanced-evidence reading — corroborated by the meta-analysis
finding that reported GraphRAG gains shrink to **under 10 % for general query distributions
once evaluation biases are controlled** — is that the graph earns its place on relational
questions and costs more than it returns on everything else.

**The actionable consequence: the arm we are missing (corpus-wide BM25) is the one with the
best evidence, and the arm we invested most in (graph) has the weakest.** That asymmetry is
the single most important thing this research found about our architecture.

---

## 5. Agentic retrieval — is `agentic.py` current?

### 5.1 The 2026 literature is markedly cooler on agentic loops than 2025 was

| Source | Finding |
|---|---|
| *Agent-Orchestrated Adaptive RAG* (`arXiv:2606.05658`) | **Reflection consistently underperformed.** DevOps: 0.870 → 0.781 overall, latency 10.6 s → 21.8 s. MuSiQue: 0.721 → 0.666, latency **17.2 s → 103.5 s (6×)**. Conclusion: *"Agentic enhancements are not universally beneficial and must be applied selectively."* |
| *Dissecting Agentic RAG* (`arXiv:2606.21553`) | Of four components, only **reranking and iterative retrieval** had favourable cost-benefit. *"Reflection mechanisms required careful tuning; naive implementations degraded performance."* |
| T2-RAGBench | **CRAG (corrective RAG) got Recall@5 0.658 — below hybrid RRF's 0.695**, despite 63 % of queries triggering its correction path. Query rewriting was *"insufficient to match the complementary strengths of sparse and dense retrieval."* |
| Stop-RAG (`arXiv:2510.14337`) | **LLM sufficiency judges are overconfident and stop prematurely.** Value-based stopping beats confidence-based judgment. |
| FAIR-RAG (`arXiv:2510.22344`) | *"Naive sufficiency judgments are unreliable"*; systems that ask "do we have enough?" without grounding it in explicit **gap identification** loop badly or refine poorly. |
| TrustNLP 2026 taxonomy (`2026.trustnlp-main.27`) | 33 failure modes over 7 stages; **12 have no dedicated peer-reviewed empirical evidence — including all 8 agentic-orchestration modes.** The authors call it an *"evidence desert in the fastest-growing RAG deployment paradigm."* |
| LLM-as-judge calibration literature (`arXiv:2508.06225` and others) | Judges systematically express higher confidence than their accuracy supports, clustering at 90–100 % confidence with accuracy well below the calibration line. |

### 5.2 What `agentic.py` gets right

Genuinely, and by comparison with what the literature says fails:

- **It is bounded** (`max_rounds`, default 2). Every catastrophic result above comes from
  loops that iterate to convergence. A hard cap of 2 is on the safe side of the evidence.
- **It merges evidence rather than replacing it.** Round 2's sources union into round 1's
  rather than overwriting them, so a bad follow-up cannot destroy a good first retrieval.
  This is a materially safer design than the reflect-and-regenerate loops that lost 8–9
  points in `arXiv:2606.05658`.
- **`_merge_cap` takes `max()` of the two rounds' sizes.** The comment explaining why is
  correct and non-obvious: capping at round 1's size makes round 2 structurally unable to
  contribute. Good.
- **It is a retrieval loop, not a generation-reflection loop.** The failures above are
  overwhelmingly *reflection on the answer*. Iterative *retrieval* is one of only two
  components `arXiv:2606.21553` found worth its cost.
- **It degrades to a deterministic verdict with no judge wired**, and it accounts for the
  spend of every internal call.

### 5.3 Three things the evidence says are wrong or risky

1. **The fallback verdict is exactly the documented failure.** `_fallback_sufficiency`
   returns `sufficient = bool(context.strip())` — a non-empty context is declared sufficient.
   That is *premature stopping by construction*, the precise pathology Stop-RAG measures. It
   is defensible as "no judge means no basis to demand more", and it is honestly labelled —
   but it means **the loop is a no-op whenever the judge is absent or unparseable**, which on
   a flaky venue network is a live case. Consider making the no-judge path *configurably*
   one extra round rather than zero, and label it.
2. **The judge sees spotlighted text, not evidence.** `agentic_retrieve` passes
   `result.answer_context` to `assess_sufficiency`, and that string carries the
   Microsoft-Spotlighting delimiters and instruction scaffolding. The judge is grading the
   wrapper along with the evidence. This is a small correctness wart with an easy fix (judge
   the concatenated `Source.text`), and it makes the verdict noisier than it needs to be
   against a judge that the literature already says is poorly calibrated.
3. **A boolean sufficiency verdict is the weaker half of the current design.** Both FAIR-RAG
   and Stop-RAG converge on the same point: sufficiency must be coupled to **explicit gap
   identification** — *what specifically is missing* — not just a yes/no plus a free-form
   follow-up. Our judge does return a `followup_query`, which is halfway there. Asking it to
   name the missing *fact* (not just a query) before proposing the query would move us onto
   the better-evidenced side, and it is a prompt change, not an architecture change.

**Overall verdict on `agentic.py`: the design is current and on the defensible side of the
2026 evidence.** It is bounded, additive, and retrieval-focused — the three properties that
separate the loops that work from the loops that lose 9 points and 6× latency. **Do not
expand it.** The temptation to add reflection, decomposition, or unbounded iteration before
a demo should be resisted; every one of those is measured as negative.

---

## 6. Failure modes — what actually breaks enterprise RAG

### 6.1 The canonical taxonomies

- **Barnett et al., *Seven Failure Points When Engineering a RAG System*
  (`arXiv:2401.05856`)** — still the reference enumeration: missing content; missed
  top-ranked document; not in context after consolidation; not extracted from context; wrong
  format; incorrect specificity; incomplete answer.
- **TrustNLP 2026, *A Systematic Taxonomy of Failure Modes in RAG Systems*
  (`aclanthology.org/2026.trustnlp-main.27`)** — 33 modes over 7 stages (ingestion,
  representation, retrieval, generation, evaluation, deployment, agentic orchestration),
  from a review of 48 sources, with three-level evidence grading. Its central finding is the
  **research-attention asymmetry**: retrieval and generation failures are comparatively
  well-studied; **representation, evaluation and agentic-orchestration failures are
  under-investigated despite frequent production occurrence.**

### 6.2 The failure modes that map onto Aegis, with mitigations that have evidence

| Failure mode | Evidence it is real | Mitigation with evidence | Where we stand |
|---|---|---|---|
| **Missing content / missed top-ranked document** — the gold chunk is never in the pool | Barnett et al.; the strongest structural constraint in IR: *"reranker recall is upper-bounded by the retriever's Recall@k"* | **Corpus-wide lexical arm** (BM25 0.644 alone vs dense 0.587) + **hybrid RRF** (0.695) | ⚠ **We do not have a corpus-wide lexical arm in production.** |
| **Ranking error — right chunk in the pool, not in the top-K** | Barnett et al.; T2-RAGBench's +12.1 pp from rerank | **Cross-encoder rerank at d=20** | Partially: LLM rerank, with positional bias and a per-query cost |
| **Inter-document confusion in repetitive corpora** — near-identical chunks from different documents | ECIR 2026: baseline Context@5 **33 %** on SEC filings; the whole paper exists because of this | **Metadata prefix** (→55 %); the paper shows it increases intra-document cohesion and inter-document separation | ⚠ We prefix headings only — the weakest field |
| **Temporal / conflicting-version retrieval** — old policy vs new policy retrieved together | ERAA-2026 (vendor benchmark, treat with caution): systems at 95 % single-hop dropped to **61 % on conflicting-source reconciliation** | Explicit freshness metadata; **prompt the model to surface conflicts rather than resolve them silently** | ⚠ No temporal marker anywhere in the chunk |
| **Attribution hallucination** — a citation that does not support the claim | `arXiv:2605.06635`, FullCite `arXiv:2606.07130` | **Deterministic post-hoc verbatim-span verification**, ~40 lines, no model call | Covered by `ingestion-sota.md` §3.4 (L3). Still the highest-leverage cheap differentiator. |
| **Table flattening** — a number retrieved without its unit, period or row label | TableRAG `arXiv:2506.10380` | Table as its own object; header repeated on split; NL summary as embedded text | Covered by ingestion phase S5 |
| **Prompt injection through retrieved content** | Well-established | Spotlighting | ✅ We do this, including into the reranker prompt |
| **Silent regression** — nothing throws when recall drops | TrustNLP 2026's "evaluation" stage; the practitioner formulation is exact: *a bad chunk size does not raise an exception, it just shifts the recall distribution* | **A golden set with recall@k, run as a gate** | ⚠ Absent. `ingestion-sota.md` §6 defect 3 already flags this; this document seconds it. |
| **Agentic-orchestration failures** | **TrustNLP 2026: all 8 agentic modes have no dedicated peer-reviewed empirical evidence.** | *There is no evidenced mitigation.* The only defensible posture is to **bound the loop and measure it.** | ✅ Bounded at 2; ⚠ unmeasured |

### 6.3 One number worth internalising

RAGSmith (`arXiv:2511.01386`) ran a compositional search over the *entire* advanced-RAG
technique space — query expansion, HyDE, reranking, MMR, contextual enrichment, hybrid
retrieval, compression — across six domains. The **average gain of the best composition over
vanilla RAG was +3.8 %** (CS +6.9 %, Math +5.1 %, Finance +4.4 %, Law +3.5 %, Medicine
+1.9 %, Defense +1.2 %). It also found **passage compression was never selected by the
optimizer in any domain.**

**Read that as a ceiling on plumbing.** Exhaustively optimising the advanced-RAG stack buys
single-digit percentages. The double-digit moves in this document — hybrid (+5.1 pp),
reranking (+12.1 pp), metadata prefixing (+21.7 pp on a repetitive corpus) — come from
*having the right three components at all*, not from tuning them. Build the three; do not
spend the hackathon tuning.

---

## 7. Parser configuration, judged by retrieval quality

The sibling agent verified Docling's mechanics. These are the config choices where
*retrieval* evidence exists — and where it does not.

| Parameter | Evidence-driven choice | Strength of evidence |
|---|---|---|
| **Heading capture depth** | Capture at least **3 levels**; do not obsess beyond. | **Moderate.** HiChunk's hierarchy ablation: gains improve L1→L3, **minimal beyond L3**. |
| **`repeat_table_headers`** (Docling default `True`) | **Keep `True`.** | **Moderate.** TableRAG's core argument; the dominant table failure is a number retrieved without its row label. Default happens to be right — adopt it *for this reason*, not because it is the default. |
| **`merge_peers`** (default `True`) | **Keep `True`** — undersized fragments are the classic recall leak. | **Weak-moderate.** No isolated ablation found; consistent with the ~9-point recall spread across chunking methods that Chroma's evaluation reports. |
| **TableFormer `ACCURATE` vs `FAST`** | Use **`ACCURATE`** where tables carry the answer. | **⚠ Thin.** No quantitative *downstream retrieval* comparison of the two modes was found. The support is qualitative (merged-cell handling on financial tables) plus the general parser×chunker interaction result (ICSE-SEIP '26, `arXiv:2604.12047`). **Measure the wall-clock cost on our machine in the spike and decide; do not assert a retrieval delta we have not measured.** |
| **OCR** | Per-page text-layer probe; OCR only pages that need it. | **Moderate**, and already owned by `ingestion-sota.md` S0. |
| **Chunker tokenizer alignment** | Docling's own guidance: the chunker's tokenizer should match the embedding model's. We measure in **words**, embedding with a 3072-dim OpenAI model. | **Real mismatch, low priority.** Chunk size correlates with in-corpus nDCG at only **r = 0.08–0.18** (`arXiv:2602.16974`), so a systematic ~25 % sizing error is inside the flat part of the curve. Do not spend the phase on it. |
| **Chunk overlap** (ours: 60/400 = **15 %**) | **Leave it alone.** | **⚠ Contested, and both sides are second-hand.** One 2026 analysis is reported (via a secondary summary citing `arXiv:2601.14123`) to find **no measurable benefit** from overlap at all. A separate summary of ICSE-SEIP '26 reports **25 % overlap as strongest** (MRR 0.529→0.658 FinanceBench, 0.735→0.833 TableQuest). **Neither number was verified against its primary source — both PDFs failed to extract.** An astronomy QA study (`arXiv:2605.25039`) found a mid-range optimum with both 0 and high overlap worse. Direction: mid-range good; magnitude unresolved. 15 % is inside the plausible band. **Changing it is churn without a golden set.** |

---

## 8. The ranked table

Evidence strength: **Strong** = multiple independent measured sources agreeing.
**Moderate** = one solid measured source, or several consistent but weaker ones.
**Weak** = single source, vendor-measured, or LLM-judged metrics only.
**Contested** = credible sources disagree. **Negative** = measured to hurt.

| Rank | Technique | Evidence | Implementation cost | Verdict for Aegis |
|---:|---|---|---|---|
| 1 | **Corpus-wide BM25 arm** (`keyword_recall` on the production backend, over the Postgres `tsvector` the ingestion phase already creates) | **Strong.** BM25 alone 0.644 > dense alone 0.587; hybrid RRF 0.695 (T2-RAGBench). *BM25 Wins at Scale*: BM25 is the enterprise default. Anthropic ladder: 3.7 %→2.9 %. | **0.5 d.** No new dependency; tenant filter is a `WHERE` clause. | **ADOPT NOW.** We are missing the best-evidenced arm in the field. |
| 2 | **Local ONNX cross-encoder reranker** (benchmark jina-turbo / bge-v2-m3 / mxbai-base-v2) | **Strong.** +12.1 pp Recall@5, +17.2 pp MRR@3 (T2-RAGBench); +133 % NDCG@5 (`2601.14224`); largest ladder step (Anthropic); 1 of 2 components that paid in `2606.21553`. | **0.5 d.** `fastembed` `TextCrossEncoder`, no torch; `onnxruntime` ranges intersect cleanly on py3.11. | **ADOPT NOW.** Also removes the dominant per-query gateway call and makes evals reproducible. |
| 3 | **Richer deterministic chunk prefix**: `[doc title · doc type · date/period · heading path (≤3) · p.N]` | **Strong.** ECIR 2026: 33.3 %→55.0 % Context@5; field ablation puts identity+date first. Prefix > suffix. Must be in the *text*, not metadata-only. | **~1 h.** Zero model calls. Touches `ChunkPiece.contextualized()` + ingest metadata. | **ADOPT NOW.** Best evidence-per-hour in the entire document. |
| 4 | **Structure-aware chunking + heading path** | **Moderate.** HiChunk +6.97 pp evidence recall (evidence-dense) / +1.03 pp (sparse); ECIR: section cues aid chunk localisation. | **Built.** | **KEEP** — cap path depth at 3. |
| 5 | **Hybrid + RRF fusion, k=60** | **Strong** for hybrid; **contested** for RRF-vs-CC (`arXiv:2210.11934` favours convex combination, but it needs a tuning set). | **Built.** | **KEEP.** Stop calling weightlessness unambiguously superior. |
| 6 | **Rerank depth d=20** | **Moderate.** `2601.14224`: d=20 is the cost-effectiveness optimum; d=20→50 diminishing; >50 negative returns. | **Built** (`recall_top_k=20`). | **KEEP** — and note it is accidentally already correct. |
| 7 | **Table as its own chunk + NL summary as embedded text + caption bound to object** | **Moderate.** TableRAG; multimodal-RAG caption-detachment failure modes. | Ingestion phase owns it. | **ADOPT NOW** (already promoted in `ingestion-sota.md` §4). |
| 8 | **Golden set (40–60 cases) + recall@k gate** | **Strong, structurally.** Every number in this document is unusable on our corpus without one. TrustNLP: silent regression is an under-studied, frequent production failure. | **0.5 d.** | **ADOPT NOW.** Nothing else here can be *claimed* without it. |
| 9 | **Deterministic verbatim-span citation verification** | **Moderate**, and the 2026 gap nobody fills (`2605.06635`, FullCite `2606.07130`). | **~2 h**, no model call. | **ADOPT NOW.** Owned by ingestion phase S10. |
| 10 | **Parent–child / continuity-gated neighbour expansion** | **Moderate.** H-RAG; SCAR (naive expansion *degrades*, gated expansion helps); HiChunk Auto-Merge. | **0.5 d**, retrieval-time only. | **ADOPT IF TIME.** Gate it; do not just concatenate neighbours. |
| 11 | **Anthropic contextual retrieval** (LLM context per chunk) | **Strong in isolation, small on top of hybrid.** 5.7 %→3.7 % standalone; but **+2.2 pp Recall@5 over hybrid RRF** on T2-RAGBench vs rerank's +12.1 pp. | **1 d + tokens.** 1 LLM call/chunk; ingest wall clock during a live demo is the real risk. | **ADOPT IF TIME**, strictly after #1 and #2, and only if it beats the free prefix on the golden set. |
| 12 | **HyPE (hypothetical questions per chunk)** | **Weak-to-moderate, large effect.** +17.9 pp claim recall, +21.2 pp context precision over naive (`2607.29402`) — but RAGChecker LLM-judged, one generator, **never compared against hybrid+rerank**, noise sensitivity worsened, index grows m×. | **1 d + tokens + m× index.** | **ADOPT IF TIME, as a measured experiment.** The most interesting unproven idea here. Never ship unmeasured. |
| 13 | **Query routing (TF-IDF+SVM)** | **Moderate — cost only.** 93.2 % accuracy, 28.1 % token savings; parity not improvement. | **0.25 d**, offline-capable. | **ADOPT IF TIME, as cost control.** Never pitch as accuracy. |
| 14 | **Convex-combination fusion instead of RRF** | **Moderate but conditional.** Beats RRF in- and out-of-domain (`2210.11934`), needs a small tuning set we would be overfitting. | 1 h + a golden set. | **ADOPT IF TIME, after the golden set exists.** |
| 15 | **Conversational query rewrite** | **Moderate.** Standard multi-turn component; distinct from query *expansion*. | **Built.** | **KEEP.** Do not extend it to inject metadata constraints (ECIR negative result). |
| 16 | **Bounded 2-round agentic sufficiency loop** | **Contested, and we are on the safe side.** Iterative *retrieval* pays (`2606.21553`); *reflection* consistently loses (`2606.05658`); all 8 agentic failure modes are an evidence desert (TrustNLP 2026). | **Built.** | **KEEP, DO NOT EXPAND.** Fix the three items in §5.3. |
| 17 | **Step-back prompting** | **Absent.** No RAG retrieval benchmark in this survey isolates it. | 1 call/query. | **REJECT — no evidence.** |
| 18 | **HyDE** | **Contested→negative for us.** Worst of 10 on T2-RAGBench (0.544); −5.43 % on original queries (`2504.08231`); no better than naive in `2607.29402`; "very limited gains" (Anthropic). Positive only in ARAGOG's conceptual-prose domain. | 1 call/query. | **REJECT as default.** |
| 19 | **Multi-query / RAG-Fusion** | **Negative ×3.** 0.640 < BM25's 0.644 (T2-RAGBench); "underperformed" (ARAGOG); production deployment abandoned it on cost-benefit (`2603.02153`). | N× retrieval + 1 call. | **REJECT.** |
| 20 | **Query decomposition** | **Negative where it matters.** MuSiQue MRR 0.469→0.102, Success@5 1.000→0.063; +16.7–53 s/query. | High. | **REJECT.** |
| 21 | **Answer-reflection loops** | **Negative ×2.** −0.089 / −0.055 overall with 2×–6× latency (`2606.05658`); "naive implementations degraded performance" (`2606.21553`). | High. | **REJECT.** |
| 22 | **Entity/keyword lists inside chunk text** | **Weak/absent.** No paper isolates it; risks perturbing BM25 length normalisation and IDF. | Cheap. | **REJECT** as chunk enrichment; keep entities as their own index. |
| 23 | **Document-summary prefix on every chunk; summary indices** | **Negative.** Anthropic: "very limited gains"/"low performance"; ARAGOG's doc-summary index unimpressive. | Cheap — which is the trap. | **REJECT.** |
| 24 | **Passage compression** | **Negative by omission.** RAGSmith's optimizer **never selected it** in any of six domains. | Medium. | **REJECT.** |
| 25 | **RAPTOR / recursive summary trees** | **Real for long-doc multi-hop**, but LLM call per cluster per level, and Anthropic's summary-indexing negative applies. | 2 d+ and tokens. | **REJECT at 13 days.** |
| 26 | **ColBERT / late interaction** | **Strong IR pedigree**, CPU-viable *latency* via PLAID. | Full embedding-architecture swap; local model at index and query time. | **REJECT** — architecture, not storage, is the blocker. |
| 27 | **ColPali / visual multi-vector** | Strong on scanned docs. | GPU both ends, ~170× storage, >7 s CPU query. | **REJECT permanently.** |
| 28 | **"Sandwich" reordering for lost-in-the-middle** | **Negative/non-reproducing.** `2605.27105` failed to recover the U-curve on current models; accuracy flat across positions. | Cheap. | **REJECT** — mitigating an effect that does not reproduce. |

---

## 9. Buckets

### Adopt now (in this order — the order is the argument)

1. **Corpus-wide BM25 arm.** Implement `keyword_recall` on the production backend over the
   Postgres `tsvector` the ingestion phase is already creating. This turns the honestly
   labelled two-arm system into a genuine three-arm one, and it is the single best-evidenced
   retrieval arm in the 2026 literature. **0.5 d.**
2. **Local ONNX cross-encoder reranker.** Benchmark `jina-reranker-v1-turbo-en`,
   `BAAI/bge-reranker-v2-m3` and `mixedbread-ai/mxbai-rerank-base-v2` on the actual machine;
   pick on measured quality-per-millisecond. Keep the LLM reranker behind a flag as a
   fallback. **0.5 d.**
3. **Richer deterministic chunk prefix.** `[title · type · date · heading path (≤3 levels)]`,
   still in the chunk *text*, still no model call. **~1 h.** Highest evidence-per-hour here.
4. **Golden set + recall@k gate + the naive baseline ablation.** Without it every number
   above is someone else's. **0.5 d.**
5. **Table-as-object with a natural-language embedded summary, and captions bound to their
   object.** (Ingestion phase owns the mechanics.)
6. **Deterministic verbatim-span citation verification.** **~2 h**, no model call, and it is
   the gap the 2026 attribution literature says nobody is filling.

Total: ~2 engineering days for items 1–4, and items 5–6 are already in the ingestion cut.

### Adopt if time (in evidence order)

1. **Parent-child / continuity-gated neighbour expansion** — retrieval-time only, and SCAR
   says to gate it rather than concatenate.
2. **Anthropic contextual retrieval** — measure against the free prefix first; it is
   +2.2 pp on top of hybrid, not the −35 % headline, once you already rerank.
3. **HyPE hypothetical questions** — the most interesting unproven idea; run it as a
   golden-set experiment, not as a shipped default.
4. **Query routing** — cost control, honestly labelled.
5. **Convex-combination fusion** — only once a golden set exists to tune the single weight on.
6. **Raise `final_top_k` above 6 for single-hop questions** — `2605.27105` says more passages
   help and ordering is stable. One-line experiment.

### Reject, with reasons

- **HyDE, multi-query, query decomposition, answer-reflection loops** — measured negative or
  contested-against-us on enterprise-shaped queries; each also costs latency we would be
  spending on stage.
- **Step-back prompting** — no RAG evidence found either way. Rejected for absence of
  evidence, and say it that way.
- **Entity/keyword stuffing, document-summary prefixes, summary indices, passage
  compression** — measured negative or never selected by an optimizer that could have.
- **RAPTOR, ColBERT/late interaction, ColPali** — real techniques, wrong constraints, wrong
  timeline.
- **Lost-in-the-middle reordering** — the effect does not reproduce on current models.

---

## 10. What our pipeline already gets right

Do not let anyone talk us out of these. Each is on the winning side of the evidence:

1. **Structure-aware chunking with a heading-path prefix, stored in the chunk text.**
   Prefix-not-suffix and text-not-metadata-only are both specifically what the ECIR 2026 and
   breadcrumb evidence say to do, and it is what LangChain's header splitter gets wrong.
2. **Hybrid multi-arm recall fused by RRF.** Hybrid beats every single arm in every 2026
   benchmark surveyed. RRF is the right *default* under corpus uncertainty even though
   convex combination beats it when you have tuning data.
3. **`recall_top_k = 20`.** Exactly the measured cost-effectiveness optimum for rerank depth.
4. **A bounded, additive, retrieval-focused agentic loop.** Bounded (not convergent), merging
   (not replacing), retrieval (not answer-reflection) — the three properties that separate
   the loops that work from the ones that lose 9 points and 6× latency.
5. **`_merge_cap = max(...)`.** Subtle and correct.
6. **Honest keyword-scope reporting** (`KeywordReport(scope="pool")`, `adds_recall=False`).
   Most systems would have called that a BM25 arm. Ours refuses to. That honesty is *why*
   this research could find the gap — keep it, and now close the gap.
7. **Spotlighting the reranker's input.** The reranker consumes untrusted retrieved content;
   most implementations forget this.
8. **`RerankOutcome.graded`.** Distinguishing "the model ordered these" from "the call failed
   and this is recall order" is exactly the kind of thing that silently rots elsewhere.
9. **Section-scoped near-duplicate detection.** The reasoning in the `dedup_pieces` docstring
   about "Contact support." under two sections is correct and not obvious.
10. **Conversational query rewrite with no-op failure semantics.** Distinct from query
    expansion, and every failure path collapses to the original query.

---

## 11. What the evidence says is wrong, ranked by cost of being wrong

**1. Production has no corpus-wide lexical arm.** `LightRAGBackend` implements
`recall_ranked` but not `keyword_recall`, so `Retriever._keyword_signal` takes the
pool-scoped branch: BM25 re-scores the ~20 candidates the dense/graph arms already returned,
with IDF computed over 20 documents. It cannot surface a document dense retrieval missed.
Against a literature where **BM25 alone beats a state-of-the-art dense embedder** on
enterprise-shaped corpora, this is the largest quality gap in the system. `memory.py`'s lite
backend *does* implement it — so the interface is proven and the work is a Postgres
full-text query behind an existing protocol.

**2. The chunk prefix carries only the weakest of the well-evidenced metadata fields.** The
ECIR ablation is explicit: removing company + year caused severe degradation; removing
section titles caused a modest Context@K drop and no Title@K effect. We carry the section
title and neither of the others. Fixing this is an hour and zero model calls.

**3. The LLM reranker is the dominant per-query cost, carries documented positional bias, is
non-deterministic in evals, and is a network call on an unreliable venue network.** A 109 M
cross-encoder has been measured beating a 4 B LLM reranker by 2.6 pp NDCG@3; cross-encoders
cost γ=0.32 relative to listwise LLM reranking. Every argument points the same way.

**4. The agentic loop's no-judge fallback declares any non-empty context sufficient.** That
is textbook premature stopping (Stop-RAG), and it makes the loop a no-op precisely when the
network is failing — the case it would be most valuable in. Related: the judge grades
`answer_context`, which carries spotlighting scaffolding, so it is partly grading the
wrapper. Both are small fixes.

**5. `fusion.py`'s docstring and `docs/learn/10-architecture.md` present RRF's
weightlessness as unambiguously superior.** `arXiv:2210.11934` finds convex combination beats
RRF in- and out-of-domain and that **RRF is parameter-sensitive**. Keeping RRF is still the
right call under corpus uncertainty — but the claim should be stated as a robustness
trade-off, not a dominance result. A jury member who knows the fusion literature will notice.

**6. The graph arm carries the weakest evidence-per-unit-cost in the stack.** LightRAG scored
48.0 vs BM25's 74.7 at the smallest tier of `arXiv:2607.26497`, with super-linear (b=1.36)
build cost; controlled meta-analysis puts general-distribution GraphRAG gains **under 10 %**.
At hackathon corpus size none of the scaling walls bite, and the graph's demo/explainability
value to the rubric is real and separate from QA accuracy — **so keep it.** But it should be
positioned as the *relational-question and explainability* arm, not as the quality engine,
and it should not receive further engineering ahead of items 1–3.

**7. `reranker.py`'s module docstring asserts "no local cross-encoder, because the deploy
target is a 16 GB, no-GPU machine."** That premise is now false: `fastembed` 0.8.0 ships
`TextCrossEncoder` over ONNX Runtime with no torch, at 33 M–278 M params, and its
`onnxruntime` range intersects `chromadb`'s cleanly on Python 3.11. The docstring is
enforcing a constraint that has expired, and it is standing in front of the second-highest-
value change available.

---

## 12. Uncertainties, stated plainly

- **No number here was measured on our corpus or our machine.** T2-RAGBench is financial
  text-and-table; ECIR 2026 is SEC filings; HiChunk is evidence-dense reports; ARAGOG is AI
  research papers. **The blind problem statement could be none of these**, and several
  rankings above (HyDE's sign, hierarchy's magnitude, decomposition's sign) flip with domain.
  The golden set is the only thing that would tell us — which is the strongest argument for
  building one.
- **HyPE's headline numbers use LLM-judged RAGChecker metrics with one generator model, and
  the paper never compares against hybrid + rerank.** Its +18 pp claim-recall over *naive*
  RAG is not necessarily any gain over *our* baseline. Treat as promising, not established.
- **The mxbai-rerank-v2 latency comparison is vendor-published and A100-measured.** The CPU
  ordering of jina-turbo / bge-v2-m3 / mxbai-base-v2 must be re-measured on our machine
  before any of it is quoted.
- **The 25 %-overlap MRR figures attributed to ICSE-SEIP '26 could not be verified against
  the primary source** (arXiv abstract carries only the abstract; the PDF did not extract).
  They are reported here as unconfirmed, and they are contradicted by a January 2026 analysis
  finding no measurable overlap benefit. **This is why the recommendation is "do not change
  overlap", not "change it to 25 %".**
- **`arXiv:2607.26497`'s LightRAG result is a scaling study.** Its smallest tier (1,144
  documents) is still far larger than a hackathon corpus. The direction is credible; applying
  its verdict at our scale is an extrapolation and is labelled as one.
- **Several enterprise statistics circulating for 2026** (143-deployment MLOps survey, the
  ERAA-2026 benchmark, "$4.7M in failures") come from vendor and community blogs, not
  peer-reviewed work. They are used here only for framing, never for a number on a slide.
- **The TrustNLP 2026 taxonomy grades evidence but the full per-mode gradings could not be
  extracted** from the PDF. Its headline finding — that all 8 agentic failure modes lack
  dedicated peer-reviewed evidence — is taken from the abstract, which states it directly.

---

## 13. Sources

**Core benchmarks and comparisons**
- *From BM25 to Corrective RAG: Benchmarking Retrieval Strategies for Text-and-Table
  Documents* (T2-RAGBench, 2026) — <https://arxiv.org/html/2604.01733v1>
- *BM25 Wins at Scale: A Scaling Study of Retrieval-Augmented Generation Paradigms* (2026) —
  <https://arxiv.org/html/2607.26497>
- *RAGSmith: A Framework for Finding the Optimal Composition of RAG Methods Across Datasets*
  — <https://arxiv.org/abs/2511.01386>
- ARAGOG — *Advanced RAG Output Grading* — <https://arxiv.org/abs/2404.01037>
- *Out of Style: RAG's Fragility to Linguistic Variation* — <https://arxiv.org/abs/2504.08231>

**Chunk enrichment and metadata**
- *Utilizing Metadata for Better Retrieval-Augmented Generation* (ECIR 2026; RAGMATE-10K) —
  <https://arxiv.org/abs/2601.11863>, <https://arxiv.org/html/2601.11863v1>
- *HiChunk: Evaluating and Enhancing RAG with Hierarchical Chunking* (HiCBench) —
  <https://arxiv.org/abs/2509.11552>
- Anthropic — *Contextual Retrieval* —
  <https://www.anthropic.com/engineering/contextual-retrieval>
- *Bridging the Question–Answer Gap in RAG: Hypothetical Prompt Embeddings* (HyPE) —
  <https://arxiv.org/abs/2607.29402>
- *SCAR: Semantic Continuity-Aware Retrieval for Efficient Context Expansion in RAG* —
  <https://arxiv.org/abs/2606.16661>
- *H-RAG at SemEval-2026 Task 8: Hierarchical Parent–Child Retrieval* —
  <https://arxiv.org/html/2605.00631v1>
- *Orion-RAG: Path-Aligned Hybrid Retrieval for Graphless Data* —
  <https://arxiv.org/abs/2601.04764>
- *TableRAG: A RAG Framework for Heterogeneous Document Reasoning* —
  <https://arxiv.org/abs/2506.10380>
- *Beyond Chunk-Then-Embed: A Comprehensive Taxonomy and Evaluation of Document Chunking
  Strategies for IR* — <https://arxiv.org/html/2602.16974>
- *Empirical Evaluation of PDF Parsing and Chunking for Financial QA with RAG*
  (ICSE-SEIP '26) — <https://arxiv.org/abs/2604.12047>

**Fusion and reranking**
- *An Analysis of Fusion Functions for Hybrid Retrieval* — <https://arxiv.org/abs/2210.11934>
- *Rerank Before You Reason: Analyzing Reranking Tradeoffs through Effective Token Cost* —
  <https://arxiv.org/html/2601.14224>
- *LLM-based Listwise Reranking under the Effect of Positional Bias* (ECIR/Springer) —
  <https://arxiv.org/html/2604.03642>
- *Listwise Cross-Encoder Fine-Tuning vs. Agentic Instruction Tuning for LLM Rerankers* —
  <https://arxiv.org/abs/2608.09650>
- mixedbread — *Baked-in Brilliance: Reranking Meets RL with mxbai-rerank-v2* —
  <https://www.mixedbread.com/blog/mxbai-rerank-v2>
- *PLAID: An Efficient Engine for Late Interaction Retrieval* —
  <https://arxiv.org/abs/2205.09707>

**Query-side techniques**
- *Scaling RAG with RAG Fusion: Lessons from an Industry Deployment* —
  <https://arxiv.org/abs/2603.02153>
- *Lightweight Query Routing for Adaptive RAG: A Baseline Study on RAGRouter-Bench* —
  <https://arxiv.org/html/2604.03455v1>
- *Sifei at SemEval-2026 Task 8: Hybrid Retrieval and Query Rewriting for Multi-Turn RAG* —
  <https://arxiv.org/abs/2606.28352>
- ToR-Lite — *Lightweight Semantic Query Decomposition for Multi-Hop RAG*, *Applied Sciences*
  16(8):3966 — <https://www.mdpi.com/2076-3417/16/8/3966>

**Agentic retrieval**
- *Agent-Orchestrated Adaptive RAG: A Comparative Study on Structured and Multi-Hop
  Retrieval* — <https://arxiv.org/html/2606.05658v1>
- *Dissecting Agentic RAG: A Component Ablation for Multi-Hop QA with a Local 7B Model* —
  <https://arxiv.org/abs/2606.21553>
- *Stop-RAG: Value-Based Retrieval Control for Iterative RAG* —
  <https://arxiv.org/abs/2510.14337>
- *FAIR-RAG: Faithful Adaptive Iterative Refinement for RAG* —
  <https://arxiv.org/abs/2510.22344>
- *Self-RAG: Learning to Retrieve, Generate and Critique through Self-Reflection* (ICLR 2024)
  — <https://arxiv.org/abs/2310.11511>

**Failure modes, position effects and calibration**
- Barnett et al. — *Seven Failure Points When Engineering a RAG System* —
  <https://arxiv.org/abs/2401.05856>
- Garani — *A Systematic Taxonomy of Failure Modes in RAG Systems* (TrustNLP 2026) —
  <https://aclanthology.org/2026.trustnlp-main.27/>
- *Lost in the Evidence? Reproducing Document Position and Context Size Effects in RAG* —
  <https://arxiv.org/html/2605.27105>
- *Overconfidence in LLM-as-a-Judge: Diagnosis and Confidence-Driven Solution* —
  <https://arxiv.org/abs/2508.06225>

**Attribution**
- *Cited but Not Verified: Parsing and Evaluating Source Attribution in LLM Deep Research
  Agents* — <https://arxiv.org/html/2605.06635v1>
- *Explicit Evidence Grounding via Structured Inline Citation Generation* (FullCite) —
  <https://arxiv.org/html/2606.07130>

**Tooling**
- Docling — chunking concepts and `HybridChunker` —
  <https://docling-project.github.io/docling/concepts/chunking/>
- `fastembed` supported models (dense + `TextCrossEncoder` rerankers) —
  <https://qdrant.github.io/fastembed/examples/Supported_Models/>
