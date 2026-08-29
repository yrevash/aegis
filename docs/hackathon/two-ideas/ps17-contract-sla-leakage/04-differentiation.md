# PS-17 — differentiation, prior art and the patent claim

## The commodity baseline

What the median competent team will ship in seven days: RAG over contract PDFs, an LLM extracting
obligations, a table of breaches, a chat box. Naming this sharply is what lets you differentiate
from it.

**Anti-slop list — the four things every other team will show, all of them LLM-with-extra-steps:**
"chat with your contracts", multi-agent theatre, LLM-written memos, a SHAP bar chart.

## The differentiating architecture

> **The LLM extracts a typed norm object. A deterministic evaluator renders the verdict.**

You can **unplug the model on stage and the verdicts do not change.** That is the whole
differentiator in one demonstrable gesture.

The justification is empirical, not aesthetic. Magesh et al., peer-reviewed in the *Journal of
Empirical Legal Studies* (2025), found the two flagship commercial legal-RAG products — Lexis+ AI
and Westlaw AI-Assisted Research — hallucinate on **17%–33%** of queries despite vendor claims of
"hallucination-free" citations; Lexis+ AI answered 65% of 202 expert-scored queries accurately,
Westlaw 42%. Dahl et al. (Stanford RegLab, *Journal of Legal Analysis* 2024) found legal
hallucination rates of **58% (GPT-4) to 88% (Llama 2)** on specific verifiable questions — and,
more damning, that models "cannot always predict, or do not always know, when they are producing
legal hallucinations."

*Verify both citations against the primary PDFs before they go on a slide — see
`../00-decision/open-risks.md`.*

## White space, ranked

Everything defensible here is **deterministic**, therefore reproducible on stage and immune to
generative failure.

| Idea | Novelty | Demo value | Cost |
| --- | --- | --- | --- |
| **Irreversibility triage** on already-executed actions | High | Very high — it is the "oh no" beat | 1.5d |
| **The conclusion diff** (derived conclusions, not documents) | High | Very high | 1.5d |
| **Bitemporal as-of plane** | Medium-high (unshipped, not unclaimed) | Very high | 2.5d |
| **Contested-evidence gate** blocking automated action | Medium | High | 1d |
| **SLA burn-down with conformal bands** (imported from PS-04) | Medium | High | 1d |

## The cross-pollination play

PS-17's brief only asks you to **detect** breaches. Import multi-horizon forecasting with
conformal bands and turn an SLA into a burn-down:

> *"You are at 99.87% with 9 days left in the month. 26 minutes of downtime budget remain.
> P(monthly breach) = 0.71."*

There is a mature cloud-computing literature on SLA violation prediction (arXiv:1611.10338,
arXiv:1509.01386) that **no CLM vendor has ever imported into commercial contract monitoring**. It
costs about a day and it converts a rules engine into an early-warning system.

Strategic bonus: it lets you answer *"why not the other problem statement?"* by having already
absorbed its best idea.

## Prior art — read this before writing the patent slide

### What is blocked

**US 11,935,046 B2** (Capital One), *"Immutable database for processing retroactive and historical
transactions using bitemporal analysis"*, granted 19 Mar 2024, appl. 17/516,438.

Claim 1 covers: maintain events on a timeline → receive a **correcting event** → duplicate the
prior sequence to a second timeline → substitute the correction → **replay the subsequent
events** → verify → **promote the second timeline to current while forbidding deletion of the
first**.

> **"We replay history against a corrected version" is unpitchable.** Anyone pitching that as
> their novelty is pitching Capital One's claim back at them.

The family is **six**, not four — all filed 1 Nov 2021 off provisional 63/157,455 ("CloudPRE"):
serials 17/516,329 / 338 / 340 / 345 / 436 / 438. **Only '438 has been read.** See
`../00-decision/open-risks.md` OR-1 — this is the top open risk in the entire pack.

### What is also dead

**Provenance-directed selective re-evaluation** is dead as a standalone novelty — killed by
papers, not patents: de Kleer's ATMS (1986), Doyle (1979), incremental view maintenance (Gupta et
al. 1993), provenance semirings (Green et al. 2007), differential dataflow (2013). All
Crossref-verified. §102/§103 does not care that these are papers.

Use it as a **narrowing limitation** only — where it usefully supplies the bounded recomputation
that gives §101 Prong Two and EPO technical character something to stand on.

### What survives

Capital One's claim recomputes a **data state** — its dependent claims 2, 8 and 10 define that
state as *an account balance*. It contains:

- no normative verdict,
- no rule versioning by effective date matched against evidence valid-time,
- no lineage-bounded subset (it replays **everything**),
- no externally executed action, and therefore
- **no irreversibility.**

The defensible residue is the **action layer**:

> Re-adjudication of *derived normative conclusions* under effective-dated rule versions, coupled
> with **irreversibility triage** on already-emitted external actions — reversible → auto-reverse
> under the idempotency key; compensable → compensate; irreversible → quarantine to human
> approval — with hash-derived idempotency keys.

### The space is thin, which helps

- Icertis: 4 US records under the assignee facet. Sirion: 4.
- `"contract lifecycle management"` full-text, US: **83**.
- `"contract amendment" "effective date" recalculat*`, US: **0**.

Contrast credit risk, which sits in CPC **G06Q 40/03** — one of the most heavily worked corners of
fintech.

## Eligibility

PS-17 survives where PS-04 dies, because its contribution is a **state-management and
action-reconciliation mechanism**, not an analysis-and-display pipeline.

- **US §101** — the bounded-recomputation and idempotency limitations give Prong Two something
  concrete. Contrast PS-04, which is described line-for-line by *Electric Power Group v. Alstom*,
  830 F.3d 1350 (Fed. Cir. 2016), and hits the 2019 PEG's enumerated "mitigating risk" grouping.
- **EPO** — the technical effect is bounded recomputation and duplicate-action prevention, not a
  business outcome.
- **India §3(k)** — the harshest test and the one that matters for a TCS-affiliated jury. The 2013
  CRI Guidelines: *"if in substance the claims relate to business method even with the help of
  technology, they are not considered patentable."* PS-17's mechanism is a technical process over
  system state. PS-04 hits **two** limbs at once — business method *and* mathematical method.

## The honest line for the slide

Do not claim "this is unpatented". Claim:

> *"Storage-layer bitemporal recomputation is occupied — Capital One holds it. Our claim sits a
> layer up: re-adjudicating normative conclusions under effective-dated rules, with irreversibility
> triage on actions already taken. We have read the parent claim and are clearing the family."*

That answer is both more defensible and more impressive than an overclaim, and it survives a judge
who looks it up.
