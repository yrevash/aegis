# Synthetic Data Generation — a friendly, day-of guide

**Who this is for:** someone who has never generated synthetic data before and needs to
produce *great* data on hackathon day — data good enough to train the ML spine, fill the
knowledge graph and vector store, and evaluate retrieval honestly.

**The one-sentence version:** we generate a small, fully-typed *world* (customers,
agents, support requests, and knowledge documents) where the **structure is drawn from a
seeded random generator**, the **text is written by an LLM** (with a templated fallback),
the **ML label is a real function of the features** so a model can actually learn it, and
**no real personal data is ever involved**. Everything lives in
[`backend/src/app/adapter/generator.py`](../../backend/src/app/adapter/generator.py) and the
seed corpus in
[`backend/src/app/adapter/corpus/`](../../backend/src/app/adapter/corpus/).

---

## 1. Why synthetic data at all?

Three reasons, all of which matter on the day:

1. **We don't have the real dataset yet.** The platform is domain-agnostic; the vertical
   slice must run end-to-end *before* the true problem is revealed. Synthetic data lets
   the whole pipeline (retrieval → ML → agent → UI) work today.
2. **It's PII-free by construction.** Synthetic records replicate the *statistical shape*
   of a real dataset without carrying any real person's information — the standard privacy
   argument for synthetic data. That means we can demo, log, and share freely.
3. **It's the industry direction.** Synthetic data is no longer experimental; analysts
   expect it to overtake real data for AI training, and today's frontier models are
   trained overwhelmingly on generated content. Knowing how to make good synthetic data is
   a core skill, not a hack.

> **Golden rule:** synthetic data is only useful if it is *realistic where it matters* and
> *labelled where you need to learn or evaluate*. The rest of this guide is how to get both.

---

## 2. The SOTA recipe (what "good" looks like)

The state-of-the-art pattern for **label-consistent** synthetic data is a **hybrid**
generator. We use exactly this, in three layers:

### Layer 1 — Schema-first, seeded structure

Define the *shape of the world* as strict, typed models first
([`adapter/schema.py`](../../backend/src/app/adapter/schema.py)): enums for every categorical
(priority, channel, region, tier…), foreign keys between entities, and a designated **ML
target** field. Then draw all the *feature-bearing* fields from a single seeded
`random.Random`. A fixed `seed` makes the entire structural world **reproducible** — rerun
and you get byte-identical data, which is priceless for debugging and for fair before/after
comparisons.

*Why seeded + structured draws?* Because the label has to be *learnable*: if features were
noise, no model could predict the target and the "trustworthy ML" story collapses.

### Layer 2 — LLM-fabricated content (with a templated fallback)

The *text* — ticket titles/descriptions and knowledge-base documents — is written by the
model gateway (`app.core.llm.complete`), requested **by role** (a cheap model for bulk
tickets, a stronger one for KB docs) and parsed defensively as JSON. This is the modern
"strong teacher model writes the examples" approach, and it grounds the text in the schema
so the content stays on-topic. Crucially, if the LLM is unavailable or returns malformed
JSON, the generator **falls back to deterministic templates** — so it *always* returns
schema-valid data and offline runs never break.

### Layer 3 — Inject a learnable signal for the ML target

The target (`resolution_hours`) is **computed from the features** via
[`adapter/ml_spec.py`](../../backend/src/app/adapter/ml_spec.py) plus a small amount of seeded
Gaussian noise. So higher priority resolves faster *on average*, but not deterministically
— exactly the "signal + noise" a real supervised model must learn (and exactly what
conformal prediction needs a calibration split to quantify). The generator's own test
asserts this: urgent tickets resolve faster than low-priority ones on average.

**Techniques you'll hear about, and where we stand:**

| Technique | What it means | Us |
|---|---|---|
| **Schema-first generation** | Types/enums/FKs defined before any data | ✅ `schema.py` |
| **Seeded / structured draws** | Reproducible feature sampling from a fixed seed | ✅ `GeneratorConfig.seed` |
| **LLM fabrication** | A teacher model writes realistic text | ✅ gateway, by role |
| **Templated fallback** | Deterministic text when no LLM | ✅ never breaks offline |
| **Persona-conditioned** | Text/records conditioned on a persona/tier | ◑ tiers & specialties bias records; extend prompts per-persona for more |
| **Distributional realism** | Class frequencies & values look plausible | ✅ weighted priority/tier draws |
| **Learnable signal** | Target is a real function of features | ✅ `ml_spec.latent_*` + noise |
| **Difficulty / distractors** | Near-miss docs to stress retrieval eval | ◑ multi-category corpus gives natural distractors; add gold Q→doc pairs for scored eval |
| **PII-free by construction** | No real identities, ever | ✅ `.example` emails, fictional orgs |

(◑ = present and usable; the note says how to push it further if you have time.)

---

## 3. What to include for best quality

When you sit down on the day, tune
[`GeneratorConfig`](../../backend/src/app/adapter/generator.py) so the dataset has all of
these. This is the checklist that separates a demo that *looks* real from one that *is*
useful:

- **Coverage** — every category/enum value appears at least once. Our generator
  **guarantees category coverage**: the first pass round-robins every category so no class
  is ever missing, even for a small `num_requests`.
- **Class balance** — don't let one category or priority dominate unless you *want* that
  imbalance (imbalance is realistic, but know it's there). Check the counts (see §5).
- **Edge cases** — reopened tickets, SLA-tight deadlines, very short/very long text. These
  are where models and retrieval break; include a few on purpose.
- **Referential integrity** — every `customer_id` / `assigned_agent_id` on a request must
  point at a real record. A dangling foreign key silently corrupts training and joins.
- **Temporal consistency** — `resolved_at` must be ≥ `created_at`; timestamps must respect
  causality. Our generator derives `resolved_at = created_at + resolution_hours`, so this
  holds by construction.
- **Labelled ground truth** — enough *resolved* requests to train **and** hold out a
  calibration/eval split. `resolved_fraction` controls this; `metadata.num_labelled` reports
  it.
- **For retrieval eval specifically** — you want **gold (question → relevant-document)
  pairs** plus **distractors** (documents that are topically close but wrong). The
  multi-category corpus already supplies natural distractors (a billing question should
  *not* retrieve the login runbook); to score retrieval, add a few hand-written gold pairs
  and measure hit-rate / MRR against them.

---

## 4. Quality checks (run these before you trust the data)

Never seed the stores from data you haven't checked. The generator ships a **pure,
offline quality gate** — [`assess_quality(dataset)`](../../backend/src/app/adapter/generator.py)
— that returns a `DatasetQualityReport`:

```python
from app.adapter.generator import GeneratorConfig, generate_synthetic, assess_quality

dataset = await generate_synthetic(GeneratorConfig(num_requests=200, seed=7))
report = assess_quality(dataset)
assert report.ok           # referential integrity + coverage + labels + temporal + PII-free
print(report.category_counts)   # eyeball class balance
print(report.num_labelled)      # enough rows to train + calibrate?
```

`report.ok` is `True` only when **all** hard checks pass:

- `referential_integrity` — every FK resolves,
- `category_coverage` — every category present,
- `has_labels` — at least one ML-labelled row,
- `temporal_consistency` — `resolved_at ≥ created_at` everywhere,
- `pii_free` — emails are `.example`, orgs are fictional.

If `ok` is `False`, fix the config (or the seed) before ingesting — a red gate here saves
you from a mysteriously bad model or a broken graph later.

**Other cheap checks worth a glance:** distribution sanity (do priority/category counts
look plausible?), diversity (are LLM titles varied, or did the model collapse to one
phrasing? — *mode collapse* is the classic synthetic-data failure), and a quick read of a
handful of records to make sure the text is coherent and on-topic.

---

## 5. How it maps to our code (the day-of cheat sheet)

| You want to… | Do this |
|---|---|
| Generate a world | `await generate_synthetic(GeneratorConfig(...))` |
| Make it reproducible | set `seed=<int>` |
| Run with no LLM / offline | `use_llm=False` (pure templated, still valid) |
| Control size | `num_customers`, `num_agents`, `num_requests`, `num_documents` |
| Control label supply | `resolved_fraction` (share of ML-labelled rows) |
| Tune label noise | `noise_scale` (std-dev of Gaussian noise on the target) |
| Check quality | `assess_quality(dataset)` → `DatasetQualityReport` |
| Seed retrieval | the `documents` feed ingestion; the seed corpus lives in `adapter/corpus/*.md` |
| Add a hand-written doc | drop a new `*.md` (with frontmatter) into `adapter/corpus/` — no code change |

**How the documents reach retrieval.** `SyntheticDataset.documents` (LLM-written) and the
hand-authored `adapter/corpus/*.md` files are ingested by the retrieval pipeline
([`retrieval/pipeline.py`](../../backend/src/app/retrieval/pipeline.py)), which chunks them
**structure-aware** (heading-scoped chunks with overlap and a section prefix), validates
each chunk for poisoning, deduplicates (exact **and** near-duplicate), and writes them into
the embedded vector store (vectors) and Neo4j (graph, via LightRAG). Re-ingesting the same corpus is
**idempotent** — it won't create duplicates — so you can regenerate and re-run freely.
Writing varied, well-structured documents here directly improves retrieval quality
downstream.

---

## 6. Common newbie mistakes (and the fix)

- **"The model won't learn anything."** → Your target isn't a function of the features.
  Make sure the label comes from `ml_spec`, not from `random()`.
- **"Retrieval returns the wrong doc every time."** → Your corpus has no distractors *or*
  every doc is near-identical. Add topically distinct documents across categories.
- **"My results aren't reproducible."** → You forgot `seed`. With `seed=None` every run is
  different (fine for stress-testing, bad for debugging).
- **"Every ticket reads the same."** → Mode collapse. Raise `llm_temperature`, or vary the
  category hints, or check that the LLM (not just the template) is actually being used
  (`metadata.llm_used`).
- **"There aren't enough labelled rows to calibrate."** → Raise `resolved_fraction` or
  `num_requests`; conformal prediction needs a real calibration split.

---

## Sources

- [Best Chunking Strategies for RAG (and LLMs)](https://www.firecrawl.dev/blog/best-chunking-strategies-rag) — structure-aware/recursive chunking at ~400–512 tokens with 10–20% overlap; contextual retrieval.
- [The Chunking Paradigm: Recursive Semantic for RAG Optimization (ICNLSP 2025)](https://aclanthology.org/2025.icnlsp-1.15.pdf) — recursive-semantic chunking outperforms naive splitters.
- [Synthetic Data: Benefits and Techniques for LLM Fine-Tuning in 2025 (Label Your Data)](https://labelyourdata.com/articles/llm-fine-tuning/synthetic-data) — teacher-model generation, grounding in real data, distributional realism.
- [The Definitive Guide to Synthetic Data Generation for LLMs in 2025 (Phinity)](https://phinity.ai/blog/synthetic-data-llms-definitive-guide-2025) — quality control, diversity, avoiding mode collapse, evaluation.
- [Synthetic Data for ML: Uses, Risks, and Best Practices (CleverX)](https://cleverx.com/blog/synthetic-data-for-ml-the-game-changer-in-training-for-2025/) — privacy/PII-free rationale; blend synthetic with real; adoption outlook.
- [Awesome-LLM-Synthetic-Data (reading list)](https://github.com/wasiahmad/Awesome-LLM-Synthetic-Data) — survey of LLM-based synthetic-data methods.
