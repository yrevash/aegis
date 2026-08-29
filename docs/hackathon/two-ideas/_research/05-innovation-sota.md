# Innovation, uniqueness and state-of-the-art white space — comparative research

*Lane 05. Clean-room evaluation of PS-17 (Contract Obligation, SLA & Commercial Leakage Monitor)
against PS-04 (AI-Powered Dynamic Covenant Monitoring & Early Warning). Every non-obvious claim
carries a source; unverified items are labelled.*

---

## Executive answer

- **PS-17 has the more accessible and more defensible white space: 8.5 / 10 vs PS-04 at 6.5 / 10.**
  The gap is not about which domain is more interesting. It is that PS-17's brief hands you a
  *provable* mechanism — retroactive re-adjudication of legal conclusions under versioned,
  effective-dated rules — and then the National Finale inject tests exactly that mechanism. PS-04's
  brief asks for a prediction whose ground truth you have to invent.

- **The single most dangerous fact about PS-04**: on synthetic data, a supervised breach classifier
  is circular. You write the data generator, then "discover" the generator with XGBoost, then SHAP
  the generator. Any AUC you report measures your own simulator. A CTO jury needs one question to
  destroy it. Every PS-04 white-space idea below is chosen to route around this, by making the
  *arithmetic* the contribution rather than the accuracy number.

- **The single most valuable fact about PS-17**: the finale inject ("an amendment changes an SLA
  threshold after breaches were flagged; re-evaluate each event using the correct effective
  version") is a direct test of bitemporal reasoning. The median team stores current state and
  mutates it, so they will either re-run everything and lose their prior conclusions, or leave stale
  flags standing. Nothing else in either problem statement separates competent from excellent so
  cleanly, in public, in 30 seconds of stage time.

- **Do not trust an LLM to say "breach".** Magesh et al., peer-reviewed in the *Journal of Empirical
  Legal Studies* (2025), found the two flagship commercial legal-RAG products — Lexis+ AI and
  Westlaw AI-Assisted Research — hallucinate on **17%–33%** of queries despite vendor claims of
  "hallucination-free" citations; Lexis+ AI answered 65% of 202 expert-scored queries accurately,
  Westlaw 42% ([RegLab](https://reglab.stanford.edu/publications/hallucination-free-assessing-the-reliability-of-leading-ai-legal-research-tools/),
  [JELS](https://onlinelibrary.wiley.com/doi/full/10.1111/jels.12413)). The differentiating
  architecture in PS-17 is therefore: **LLM extracts a typed norm object; a deterministic evaluator
  renders the verdict.** You can unplug the model on stage and the verdicts do not change.

- **The highest-return idea in PS-04 comes from the accounting literature, not the ML literature.**
  Dichev & Skinner (2002) show private-debt covenants are set tight, technical violations occur in
  **~30% of loans**, and — critically — *leverage is a poor proxy for closeness to a covenant*
  ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=275174)). Jha (2013) shows managers
  manage earnings **upward in the quarters preceding** a violation
  ([JAAF](https://journals.sagepub.com/doi/abs/10.1177/0148558X13505597)). So the novel product is
  not "predict the breach" — it is **"detect the borrower who is hiding rather than the borrower who
  is failing"**: bunching of reported ratios just above the covenant line, plus deteriorating accrual
  quality, plus a divergence between reported accrual revenue and bank-observed cash collections.
  Nobody else in the room will build that.

- **Prior art is closer than it looks on PS-17's crown jewel.** Bitemporality itself is 40 years old
  (Snodgrass/TSQL2, standardised in SQL:2011) and Capital One holds a granted patent family on
  bitemporal *retroactive recomputation* — US 11,935,046 B1 and siblings
  ([USPTO](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11935046),
  [Justia](https://patents.justia.com/patent/20220284422)). The claimable novelty must sit one layer
  up: re-adjudication of *derived normative conclusions* under versioned effective-dated rules, with
  an irreversibility gate on already-executed external actions. Not "a bitemporal database".

- **The cheapest high-impact ideas in both problem statements are the small exact-arithmetic
  features layered on one expensive foundational choice** — not the foundational choice itself.
  Counterfactual headroom in PS-04 scores 140 on (novelty × demonstrability ÷ days) but is
  worthless without the glass-box covenant-arithmetic engine underneath it. Plan the dependency
  graph, not the ranking table.

- **Best cross-pollination, and it runs PS-04 → PS-17:** PS-17's brief only asks you to *detect*
  breaches. Import multi-horizon forecasting with conformal bands and turn an SLA into a burn-down —
  *"you are at 99.87% with 9 days left in the month; 26 minutes of downtime budget remain;
  P(monthly breach) = 0.71."* There is a mature cloud-computing literature on SLA violation
  prediction ([arXiv:1611.10338](https://arxiv.org/abs/1611.10338),
  [arXiv:1509.01386](https://arxiv.org/pdf/1509.01386)) that no CLM vendor has ever imported into
  commercial contract monitoring. It costs about a day and it converts a rules engine into an early
  warning system.

- **Anti-slop headline**: "chat with your contracts", multi-agent theatre, LLM-written memos, and a
  SHAP bar chart are the four things every other team will show. All four are LLM-with-extra-steps.
  Called out individually in §4 of each section below.

---

## PS-17: innovation and white-space analysis

### 1. The commodity baseline — what the median competent team ships by 4 September

Be precise about this, because differentiation is defined relative to it. Seven days, small team,
Windows, no Docker. The median build is:

1. **Ingest**: PyMuPDF over ~15 synthetic contract PDFs + amendments. Chunk at ~1000 tokens.
   Embed with `sentence-transformers/all-MiniLM-L6-v2` or an API embedding. Store in Chroma or FAISS
   (both pip-installable on Windows).
2. **Extract**: one LLM call per chunk, or a long-context call per contract, with a prompt like
   *"extract every obligation as JSON: {party, obligation_text, metric, threshold, deadline,
   penalty, page}"*. Pydantic model for structured output. Page number kept as "provenance".
3. **Evidence**: synthetic CSVs (service_events, invoices, credits, notices) loaded into SQLite or
   Postgres. A pandas join per obligation.
4. **Detect**: hardcoded or LLM-generated comparison — `uptime < threshold` → breach row.
5. **Quantify**: `credit = 5% * MRC`, summed into a "Total leakage identified: $412,000" hero number.
6. **Act**: an LLM drafts a breach-notice email. A button sets `status = 'approved'`. This is called
   "human-in-the-loop".
7. **Show**: Streamlit or a Next.js dashboard — obligations table, RAG-status breach list, a
   Recharts bar chart, and a "chat with your contracts" box.
8. **Agent garnish**: LangGraph with 4 nodes (extract → monitor → detect → notify) drawn as a
   diagram on a slide. Possibly renamed "multi-agent".

**Where the median team fails on stage.** At the finale inject, they ingest the amendment, re-run
extraction, and overwrite the obligation record. Three failure modes follow, all visible:
(a) previously-flagged breaches silently disappear with no record they ever existed;
(b) events *before* the amendment's effective date are re-judged under the new threshold, which is
legally wrong; (c) they cannot answer "what did you believe on 12 August, and why do you believe
something else now?" — which is the actual question a CTO will ask.

**Incumbent baseline they will be compared against.** Enterprise CLM (Icertis, Sirion, DocuSign,
Evisort/Workday) already ships obligation extraction and renewal alerting. So "we extracted
obligations from a PDF" is not a claim; it is table stakes. WorldCC's research is the standard
business-impact anchor: organisations lose **~9.2% of annual revenue** to poor contract management,
with top performers at ~3% and laggards at 15–20%
([WorldCC](https://www.worldcc.com/resource/Stopping-the-Leak-The-value-of-contracts.html)); more
recent WorldCC work puts post-signature procurement value leakage at **~11%**
([PASA summary](https://procurementandsupply.com/procurement-contracts-leaking-11-percent-of-value-due-to-enterprise-wide-failures/)).

### 2. State of the art, with papers

**Contract NLP benchmarks — and what they say about the extraction step.**

| Benchmark | What it tests | Why it matters here |
| --- | --- | --- |
| **CUAD** — 13,000+ expert annotations over 510 commercial contracts, 41 clause types, NeurIPS 2021 D&B ([arXiv:2103.06268](https://arxiv.org/abs/2103.06268), [Atticus](https://www.atticusprojectai.org/cuad/)) | span-level clause identification | The closest public analogue to obligation extraction. Transformer performance is described by the authors as "nascent" — this is not solved. |
| **ContractNLI** | document-level NLI: entailed / contradicted / not-mentioned + evidence spans | The right task shape for "does this contract impose obligation X, and where?" |
| **MAUD** | deal-point extraction from merger agreements | Companion large-scale expert benchmark |
| **LexGLUE** ([arXiv:2110.00976](https://arxiv.org/pdf/2110.00976)) | multi-task legal NLU | General legal-language baseline |
| **LegalBench** ([repo](https://github.com/HazyResearch/legalbench), NeurIPS 2023) | legal reasoning in foundation models, *given* the context | Evaluates generation only, not retrieval |
| **LegalBench-RAG** ([arXiv:2408.10343](https://arxiv.org/abs/2408.10343)) | retrieval quality over CUAD/ContractNLI/MAUD/PrivacyQA | The one that maps to your actual pipeline: can you *find* the clause in a long document, not just reason about it once handed to you |

**LLMs on long contracts.** *ContractEval* ([arXiv:2508.03080](https://arxiv.org/abs/2508.03080))
benchmarks 4 proprietary against 15 open-source models on clause-level legal risk in CUAD. Findings
that should shape your architecture: proprietary models lead on both correctness and output quality;
**extended reasoning modes improve output quality but *reduce* correctness** (models over-complicate
simple extractions); open-source models frequently emit "no related clause" when a clause *is*
present; quantised models degrade noticeably. The authors place most models at roughly
"junior legal assistant" level. Separately, *Lost in the Middle*
([arXiv:2307.03172](https://arxiv.org/abs/2307.03172)) documents a U-shaped positional curve with
>30% accuracy degradation for information in the middle of a long context, replicated across six
model families. **Design consequence: do not dump a 60-page MSA plus three amendments into one
context window and ask for a verdict.** Retrieve narrowly, extract into a typed object, and decide
in code.

**Hallucination and grounding in legal AI — the citation that carries the pitch.** Magesh, Surani,
Dahl, Suzgun, Manning and Ho, *Hallucination-Free? Assessing the Reliability of Leading AI Legal
Research Tools*, 202 hand-scored legal queries, peer-reviewed into *JELS* 2025:
**17%–33% hallucination rates** in Lexis+ AI, Westlaw AI-Assisted Research and Ask Practical Law AI;
Lexis+ AI accurate on 65% of queries, Westlaw on 42% while hallucinating roughly twice as often as
the others. The authors explicitly rebut vendor claims that RAG "eliminates" hallucination
([RegLab](https://reglab.stanford.edu/publications/hallucination-free-assessing-the-reliability-of-leading-ai-legal-research-tools/) ·
[preprint PDF](https://dho.stanford.edu/wp-content/uploads/Legal_RAG_Hallucinations.pdf) ·
[JELS](https://onlinelibrary.wiley.com/doi/full/10.1111/jels.12413)). **This is your permission
slip for the whole architecture**: the market leaders, with far more than seven days, still cannot
make a generative legal answer trustworthy — which is why your system's verdicts are computed, not
generated.

**Deontic / normative logic as an executable layer.** This is a live and credible research line, and
it is the SOTA that nobody in the room will have read:

- Horner, Mateis, Governatori and Ciabattoni, *Toward Robust Legal Text Formalization into Defeasible
  Deontic Logic using LLMs* ([arXiv:2506.08899](https://arxiv.org/abs/2506.08899), v1 June 2025,
  v3 December 2025). A two-stage pipeline segmenting normative language into atomic snippets,
  extracting deontic rules, then refining for syntactic and semantic coherence; evaluated on the
  Australian Telecommunications Consumer Protections Code. Conclusion: *when guided effectively,
  LLMs produce formalizations that align closely with expert-crafted representations.*
- Workshop version: [CEUR Vol-4174 paper 7](https://ceur-ws.org/Vol-4174/paper7.pdf).
- *Towards Trustworthy Legal AI through LLM Agents and Formal Reasoning*
  ([arXiv:2511.21033](https://arxiv.org/pdf/2511.21033)) — LLM inside a logic-based control loop,
  typed schema for actors / actions / conditions / deontic status, with an SMT solver verifying
  soundness. *[search-result summary; PDF not opened]*
- *De Jure: Iterative LLM Self-Refinement for Structured Extraction of Regulatory Rules*
  ([arXiv:2604.02276](https://arxiv.org/html/2604.02276v1)) — notes that deontic logic is the
  dominant paradigm for making extracted rules machine-actionable. *[search-result summary; not opened]*

**Temporal reasoning — why you must not ask the model to do the dates.**
*Test of Time* ([arXiv:2406.09170](https://arxiv.org/abs/2406.09170), ICLR 2025) builds synthetic
semantic and arithmetic temporal tasks precisely because LLMs are error-prone on complex temporal
logic. *TIME* ([arXiv:2505.12891](https://arxiv.org/abs/2505.12891)) adds 38,522 QA pairs across
three levels and eleven sub-tasks. The formal machinery you want instead is 43 years old and exactly
right: Allen's interval algebra — 13 relations over time intervals — J. F. Allen, *Maintaining
Knowledge about Temporal Intervals*, CACM 26(11):832–843, 1983
([CACM](https://cacm.acm.org/research/maintaining-knowledge-about-temporal-intervals/),
[dblp](https://dblp.org/rec/journals/cacm/Allen83.html)). Effective-dating, measurement windows,
cure periods and notice periods are all interval relations. Compute them; don't prompt them.

**Bitemporal state — the substrate for the finale inject.** Valid time vs transaction time,
formalised by Jensen, Snodgrass and Soo in TSQL2
([chapter PDF](https://people.cs.aau.dk/~csj/Thesis/pdf/chapter12.pdf)), standardised into SQL:2011;
recent systematic review at [Springer JDIM](https://link.springer.com/article/10.1007/s42488-026-00162-x).
Retroactive writes — writes into the past of valid time — are the exact operation the inject demands.

**Incremental recomputation — the "targeted re-evaluation" the brief asks for by name.**
The brief says material changes "must trigger targeted re-evaluation rather than silently preserving
an outdated conclusion". The formal answer is incremental view maintenance: Budiu, Chajed, McSherry,
Ryzhyk and Tannen, *DBSP: Automatic Incremental View Maintenance for Rich Query Languages*, VLDB
2023 ([arXiv:2203.16684](https://arxiv.org/pdf/2203.16684), [Feldera VLDB PDF](https://docs.feldera.com/vldb23.pdf)) —
four operators, full relational algebra, aggregation and recursion, with a mechanically verified
proof of correctness in Lean. You will not implement DBSP in a week. But **citing it as the theory
your dependency-tracked re-adjudication queue approximates** is the difference between "we re-run
the affected rows" and "we implement targeted incremental recomputation, and here is the formalism".

**Tamper-evident audit logs.** RFC 6962 Certificate Transparency
([IETF](https://datatracker.ietf.org/doc/html/rfc6962)) — an append-only SHA-256 Merkle tree with
inclusion and consistency proofs, so a third party can verify the log never rewrote history without
holding the whole log. Lineage: Crosby & Wallach, *Efficient Data Structures for Tamper-Evident
Logging* (2009). Russ Cox's [Transparent Logs for Skeptical Clients](https://research.swtch.com/tlog)
is the readable explainer. `hashlib` on bare Windows. No chain required.

### 3. The white space — five ideas, ranked

Scored on **novelty (1–10) × demonstrability (1–10) ÷ build cost (days)**. Read the dependency
column before the score column: two of these are cheap *because* another is expensive.

| # | Idea | N | D | Days | Score | Depends on |
| --- | --- | --- | --- | --- | --- | --- |
| 17-A | Retroactive re-adjudication engine + Decision Diff | 9 | 10 | 2.0 | **45** | 17-B |
| 17-B | Deontic-temporal norm compiler (LLM extracts, solver decides) | 8 | 8 | 2.0 | **32** | — |
| 17-C | Contradiction ledger with value-of-information next-evidence ranking | 8 | 7 | 1.5 | **37** | 17-B |
| 17-D | Irreversibility-gated autonomy + idempotent action ledger | 6 | 9 | 1.0 | **54** | — |
| 17-E | Hash-chained adjudication log with deterministic replay | 6 | 8 | 1.0 | **48** | 17-B |
| 17-F | *(cross-pollinated)* Predictive SLA burn-down with conformal bands | 7 | 9 | 1.0 | **63** | — |

---

**17-A — Retroactive re-adjudication engine, with the Decision Diff as the product surface.**

*What it is.* Every conclusion — breach / no-breach / credit due / notice required — is a **derived
fact** stamped with three hashes: the evidence-version set it consumed, the *clause version* in
force at the event's service date, and the evaluator code version. Facts are never mutated. When an
amendment arrives with an effective date in the past, the engine (i) computes the affected event set
by dependency, not by re-running the corpus, (ii) re-adjudicates only that set under the correct
effective version, and (iii) emits a first-class `Reversal` record.

*What it looks like on screen.* A split-pane ledger: **"As of 12 Aug"** on the left, **"As of today"**
on the right, with red strikethroughs and green additions between them, and a per-row "why did this
change" trace opening clause v1 beside clause v2 with the changed token highlighted. On stage:

> *On 12 August we concluded Event #4471 breached the P1 restore obligation and owed a $12,400
> credit. On 20 August, Amendment A-3 — effective retroactively to 1 July — moved the P1 restore
> target from 4h to 6h. Event #4471 no longer breaches. Nine other events flip the same way, worth
> $61,200. Two of these already had notices sent to the counterparty; those are frozen and routed to
> a human retraction queue. Seventeen events dated before 1 July are correctly untouched.*

*Why nobody else will do it.* It requires deciding the data model on day 1, before you have written
any UI, and it is invisible until the inject fires. Teams optimising for a Friday demo will store
mutable current state.

*Patentability note.* Bitemporality is prior art. Capital One holds granted claims on bitemporal
retroactive recomputation with alternate temporal sequences for testing retroactive events —
US 11,935,046 (filed 1 Nov 2021, granted 19 Mar 2024; inventors Kedy, Schneider, Zhang), plus
US 11,915,236, US 11,907,943 and US 12,481,990
([11,935,046](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11935046) ·
[11,915,236](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11915236) ·
[11,907,943](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11907943) ·
[12,481,990](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12481990) ·
[app 20220284422](https://patents.justia.com/patent/20220284422) ·
[app 20220284423](https://patents.justia.com/patent/20220284423)). **Draft the claim above the
storage layer**: re-adjudication of derived *normative* conclusions under versioned effective-dated
rule objects, where the system classifies each superseded conclusion by whether an irreversible
external action was already executed against it, and routes accordingly. That combination — norm
versioning + conclusion reversal + irreversibility triage — is where the defensible claim lives.

**17-B — Deontic-temporal norm compiler: the LLM extracts, a solver decides.**

*What it is.* The LLM is forbidden from ever emitting the word "breach". It emits a typed norm:

```
Norm {
  id, source_span, contract_id, clause_version,
  modality: OBLIGATION | PROHIBITION | PERMISSION,
  bearer, counterparty,
  trigger:      { event_type: "incident", severity: "P1" },
  requirement:  { metric: "restore_time", relation: "<=", value: 4, unit: "hours" },
  window:       { basis: "rolling_calendar_month" },
  exceptions:   [ "force_majeure", "customer_caused_delay" ],
  remedy:       { type: "service_credit", basis: "pct_of_MRC", value: 5, cap: 15 },
  effective:    [2026-07-01, 2026-12-31]
}
```

A deterministic evaluator (~400 lines of Python; Allen-relation interval arithmetic over the event
log) produces the verdict. Two payoffs compound. First, **hallucination immunity where it counts**:
the failure mode Magesh et al. measured cannot produce a wrong dollar figure, because the dollar
figure is arithmetic. Second — and this is the structural reason to build it first — **norms are
diffable**, which is what makes 17-A possible at all. You cannot diff a paragraph of prose; you can
diff a struct.

*On screen.* Three panes: the contract text with the extracted span highlighted → the compiled norm
object → the evaluation trace with real numbers. Then the anti-slop moment: **kill the LLM
connection and re-run the whole evaluation.** Identical verdicts, in milliseconds.

*Novelty.* The academic line exists (Horner et al. 2025; the L4M/SMT approach in arXiv:2511.21033),
but it targets statutes and regulatory codes. **Nobody has applied defeasible deontic formalisation
to commercial SLA and service-credit monitoring**, and no CLM vendor ships it. That is a genuine
gap, and it is defensible to state out loud.

**17-C — Contradiction ledger with value-of-information ranking of the next evidence request.**

The brief asks for two things everybody will fake: "make uncertainty explicit" (they will render a
yellow badge) and "dynamically select the next useful evidence ... based on expected value" (they
will let an LLM pick).

Build instead: a **source-precedence lattice** (executed amendment > executed contract > signed SoW >
credit note > invoice > monitoring telemetry > email assertion). Two conflicting facts about the same
(obligation, period) do not resolve — they produce a `HeldAdjudication` with a named conflict and a
computed **branch valuation**:

> *Uptime for July is 99.91% per the vendor's monitoring export and 99.84% per our ticket system.
> Under the first, no credit is due. Under the second, $84,000 is due. The single cheapest evidence
> that resolves the conflict is the raw incident timeline export; expected value of that request =
> P(flip) 0.55 × $84,000 = $46,200. It is ranked first in the evidence queue.*

Expected value of information is trivial arithmetic here — P(flip) × dollar delta — and it is a
completely different answer to "next-best action" than an agent prompt. It is also the correct
answer to a jury question about how the system prioritises under partial information.

**17-D — Irreversibility-gated autonomy and an idempotent action ledger.**

Autonomy level is not a global setting; it is a **function of (reversibility class × dollar exposure
× confidence)**. Classify every permitted action: `reversible` (recompute a view), `soft-external`
(draft an email into a queue), `hard-external` (send contractual notice, issue a credit note, post to
the ledger). Each hard-external action carries an idempotency key over
`(obligation_id, period, action_type, evidence_version)` — the brief explicitly demands "preventing
duplicate requests, duplicate transactions or repeated external actions".

*On screen, and this is the best production-readiness demo in either problem statement:* start a
workflow that issues a credit note, **kill the process from the terminal mid-flight in front of the
jury**, restart, and show the ledger — one credit note, one idempotency key, a recovered
half-committed workflow, no double-send. Then map the five autonomy levels onto the reversibility
classes in a single table. This is cheap, it is visual, and almost nobody will do it.

**17-E — Hash-chained adjudication log with a deterministic replay button.**

`record_hash = SHA-256(prev_hash ‖ clause_version_hash ‖ sorted(evidence_hashes) ‖ evaluator_code_hash ‖ verdict ‖ timestamp)`.

Two properties that are genuinely load-bearing rather than decorative, which matters because the
brief warns against forcing a blockchain angle:

1. **Anti-backdating.** SLA credit disputes are adversarial. The counterparty's natural accusation
   is that you decided the breach *after* seeing the amendment. An RFC 6962-style inclusion proof
   against a published tree head settles that without disclosing the rest of your log.
2. **Replay verification.** A "Verify" button re-executes the evaluator from the logged inputs and
   checks the hash matches. This is not a metaphor for auditability; it *is* auditability.

Ships as `hashlib` + a SQLite table on bare Windows. No chain, no node, no Docker.

**17-F — Predictive SLA burn-down (imported from PS-04; see §5).** Highest score on the table,
lowest build cost, and it is the one idea that makes PS-17 feel like more than a very good rules
engine.

### 4. The anti-slop check — PS-17

| Pattern | Verdict |
| --- | --- |
| "Chat with your contracts" RAG box | **Pure commodity.** Every team will have one. The jury saw a hundred this year. If you keep it, keep it small and never mention it in the pitch. |
| Multi-agent framing (extractor agent / monitor agent / negotiator agent) | **The #1 slop pattern.** A CTO will ask what the agents actually share. If the answer is "a JSON blob in a graph state", you are finished. Have one orchestrator and a real state machine, and say so. |
| LLM-generated breach summaries and narrative memos | Acceptable **garnish**, never a claim. Generate prose *from* the computed verdict, never the verdict from prose. |
| "The agent decides the next best action" via LLM prompt | **LLM with extra steps.** Replaced by 17-C's expected-value ranking, which is defensible arithmetic. |
| Auto-sending contractual breach notices | **Worse than slop — a liability.** The brief explicitly reserves contractual notice and material settlement for humans. Sending one automatically fails the brief on stage. |
| Knowledge graph of contracts, drawn as a hairball | Slop **unless** the edges are load-bearing for re-adjudication dependency tracking. If they are, show the subgraph that fired, not the whole hairball. |
| "Confidence score" produced by asking the LLM for a confidence score | Well-known to be uncalibrated. Either omit or replace with the conflict/held state from 17-C. |

---

## PS-04: innovation and white-space analysis

### 1. The commodity baseline — what the median competent team ships by 4 September

1. **Synthetic portfolio**: a pandas generator producing 200–500 borrowers × 24–36 months of
   financials, facilities, utilisation, payment history and treasury flows.
2. **Ratios**: DSCR, net leverage, interest coverage, current ratio, computed per period.
3. **Labels**: `breach_in_30 / 60 / 90` derived from the same generator that produced the features.
4. **Model**: three LightGBM/XGBoost binary classifiers, or one multi-output model. Reported AUC in
   the 0.90s, because the model is recovering the generator.
5. **Explain**: SHAP. A waterfall per borrower, a global bar chart on the overview page.
6. **Signals**: an LLM scores "industry sentiment" over synthetic headlines that the team also
   generated with an LLM.
7. **Show**: portfolio heatmap, borrower drill-down, three gauge dials for 30/60/90, a SHAP chart,
   and an LLM-drafted "recommended intervention" paragraph.
8. **Garnish**: "multi-agent credit committee", or a "digital twin of the borrower" (a renamed
   simulator).

**Why this baseline is structurally weaker than PS-17's.** Two reasons, and both are fatal in front
of a technical panel.

*First, the circularity.* You wrote the process, then discovered it. The AUC is a measurement of
your own simulator. There is no honest answer to *"what would this score on real data?"*, and there
is no honest answer to *"how do you know the model learned deterioration rather than your noise
seed?"* SHAP does not help — it explains the generator too. This is not a fixable presentation
problem; it is a property of the problem statement combined with the synthetic-data constraint.

*Second, the incumbents are already there and they say so publicly.* Moody's Lending Suite markets
AI-driven loan monitoring that automatically tracks, requests, collects and validates covenant
documents, ingests unstructured financials without templates, and surfaces "early warning signals"
with tolerance-breach notifications
([Moody's](https://www.moodys.com/web/en/us/solutions/lending/loan-monitoring.html)). nCino carries
covenant tracking with on-demand testing; Cync and Cardo AI ship automated covenant monitoring
([Cync](https://www.cyncsoftware.com/platform/covenants)). Your uniqueness claim therefore starts in
a hole that PS-17's does not.

**Business-impact anchors that are real.** Dichev & Skinner (2002): private lenders use covenants as
trip wires, set them tightly, and **technical violations occur in roughly 30% of loans**, frequently
waived for healthy firms
([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=275174) ·
[JAR](https://onlinelibrary.wiley.com/doi/abs/10.1111/1475-679X.00083)). Nini, Smith & Sufi (2012),
*Review of Financial Studies* 25(6):1713–1761: across all US non-financial firms 1996–2008,
**10–20% of firms report a financial covenant violation in any given year**, followed immediately by
falling capex and acquisitions, sharply reduced leverage and payouts, higher CEO turnover — and
improved operating and stock performance afterwards
([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1344302) ·
[RFS](https://academic.oup.com/rfs/article-abstract/25/6/1713/1595112)). That last clause is your
value proposition in one sentence: **early creditor intervention at covenant violation demonstrably
improves outcomes, so moving detection 30–90 days earlier has measurable value.**

### 2. State of the art, with papers

**Is there published work specifically on *covenant* breach prediction, distinct from default
prediction? Largely no — and that gap is itself the finding.**

The academic literature treats covenant violation as an **explanatory or identification** device,
not a forecasting target. Nini/Smith/Sufi and the regression-discontinuity literature use the
violation threshold as a source of exogenous variation to study creditor control; Dichev & Skinner
use covenant slack to test the debt covenant hypothesis. The nearest thing to a modern ML treatment
is **CovenantAI** — Saunders, Steffen and Verhoff, *CovenantAI: New Insights into Covenant
Violations* ([SSRN 4640653](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4640653) ·
[CEPR DP20117](https://cepr.org/publications/dp20117) ·
[working paper PDF](https://static1.squarespace.com/static/60f02ca7138f3902d7a39744/t/65cf2feb9e802c4401f17658/1708077039426/CovenantAI_v15Feb2024.pdf)),
which applies ML across **>1.8 million SEC filings over 20+ years** — but note carefully what it
does: it **identifies and classifies violations that already happened** from 10-K/10-Q/8-K text,
reframing away from a binary flag toward amendments (pre- and post-violation), waivers and technical
defaults. It is an extraction and measurement paper, not a forecasting paper.

**Consequences for your pitch, and they are large.**

1. You cannot cite a benchmark for 30/60/90-day covenant breach prediction, because there isn't one.
   Say this explicitly to the jury — *"we searched for a covenant-breach forecasting benchmark and
   there is none; the literature forecasts default and measures violations"* — and it reads as
   command of the field rather than a gap in your work.
2. The *outcome space is richer than breach/no-breach*, and CovenantAI proves it empirically: waiver,
   amendment before violation, amendment after violation, technical default. **Predicting a binary
   breach label is the wrong target.** Modelling the renegotiation outcome, or better, the *ratio
   trajectory and headroom*, is both more novel and more useful.
3. The absence of covenant-specific work means the default-prediction toolkit is what everyone will
   reach for, and it is a poor fit — Dichev & Skinner's finding that **leverage is a weak proxy for
   closeness to a covenant** is the direct evidence that generic credit features miss covenant risk.

**Credit-risk ML SOTA.** The current frontier is tabular foundation models: Baesens, Goethals,
Lessmann, De Vos, Bravo, Martens, Medina-Olivares, Mues, Óskarsdóttir, vanden Broucke, Van Gestel,
Verdonck and Verbeke, *Foundation Models for Credit Risk Prediction: A Game Changer?*
([arXiv:2605.18147](https://arxiv.org/abs/2605.18147), May 2026, rev. July 2026) — benchmarked on PD
and LGD across multiple datasets, tabular foundation models generally perform best, **with the
improvement growing as dataset size shrinks**, and with minimal tuning. That last property is
directly relevant to an SME/commercial-lending portfolio and to a hackathon-sized synthetic
portfolio. Practical note for the demo: this is a *citable* frontier, but a foundation model trained
on your own synthetic generator inherits the circularity problem exactly like XGBoost does.

**Survival / hazard models — the right functional form, and nobody will use it.** Discrete-time
hazard models on a loan-month person-period panel are the standard approach for time-to-default with
time-varying covariates, scale to millions of rows, and accommodate macro drivers naturally; see the
IFRS 9 term-structure tutorial ([arXiv:2507.15441](https://arxiv.org/html/2507.15441v1),
[Springer IJDSA](https://link.springer.com/article/10.1007/s41060-026-01032-w)) and dynamic survival
models with varying coefficients ([EJOR](https://www.sciencedirect.com/science/article/abs/pii/S0377221718309548)).
Recent work explicitly incorporates data drift into survival analysis for credit
([arXiv:2601.20533](https://arxiv.org/abs/2601.20533)). **Why this matters for the pitch:** a hazard
model gives you a *term structure* — 30/60/90 fall out of one coherent model rather than three
independently-trained classifiers that can produce incoherent probabilities (P(90d) < P(30d)). The
median team will ship three classifiers and will not notice the incoherence. Pointing at it is a
free, sharp, defensible differentiator.

**Multi-horizon forecasting.** Lim, Arik, Loeff and Pfister, *Temporal Fusion Transformers for
Interpretable Multi-horizon Time Series Forecasting*
([arXiv:1912.09363](https://arxiv.org/abs/1912.09363), IJF 37(4):1748–1764, 2021) — quantile
forecasts with variable-selection and interpretable attention, reported 7% lower P50 and 9% lower
P90 quantile loss vs the then-SOTA. Zero-shot time-series foundation models (Chronos, encoder-decoder,
tokenised values; TimesFM, decoder-only with input patching) are pip-installable and would let you
forecast borrower series with no training at all — see the comparative evaluations at
[arXiv:2509.26347](https://arxiv.org/pdf/2509.26347) and
[arXiv:2412.12834](https://arxiv.org/html/2412.12834v1), and the broader architecture survey at
[arXiv:2411.05793](https://arxiv.org/pdf/2411.05793). *For a 7-day Windows build, quantile gradient
boosting or a small TFT is the safer bet; a TSFM is a stretch goal with a large download.*

**Coherence — the mechanism that makes glass-box arithmetic possible.** Wickramasuriya, Athanasopoulos
and Hyndman, *Optimal Forecast Reconciliation for Hierarchical and Grouped Time Series Through Trace
Minimization*, JASA 114(526)
([PDF](https://robjhyndman.com/papers/MinT.pdf) ·
[JASA](https://www.tandfonline.com/doi/abs/10.1080/01621459.2018.1448825)). MinT reconciles
independently-forecast components so aggregation constraints hold exactly, minimising MSE across the
hierarchy subject to unbiasedness. Financial statements *are* a hierarchy with exact identities
(revenue − COGS − opex = EBIT; current assets sum to a total). **This is how you forecast EBITDA,
interest expense, debt and cash such that the balance sheet still balances — and therefore how the
covenant ratio you compute from them is arithmetically legitimate.** Nobody else will reconcile.

**Uncertainty under drift.** Gibbs & Candès, *Adaptive Conformal Inference Under Distribution Shift*,
NeurIPS 2021 ([ACM](https://dl.acm.org/doi/10.5555/3540261.3540389) ·
[Semantic Scholar](https://www.semanticscholar.org/paper/Adaptive-Conformal-Inference-Under-Distribution-Gibbs-Cand%C3%A8s/445596c40dc421efe2354a340085b43181bea2be)):
an online wrapper around any black-box predictor that provably attains the target coverage frequency
over long horizons *irrespective of the true data-generating process* — including when it shifts.
Generalised by Angelopoulos, Bates, Fisch, Lei and Schuster, *Conformal Risk Control*
([arXiv:2208.02814](https://arxiv.org/abs/2208.02814)). Multi-step and time-series variants:
[arXiv:2010.09107](https://arxiv.org/pdf/2010.09107),
[arXiv:2409.14792](https://arxiv.org/pdf/2409.14792),
[arXiv:2508.13362](https://arxiv.org/pdf/2508.13362).

**Concept drift in credit — and the reason SHAP is a trap.** PSI is the industry-standard drift
monitor; concept drift means the learned relationship stops holding. *Fair and Explainable
Credit-Scoring under Concept Drift: Adaptive Explanation Frameworks for Evolving Populations*
([arXiv:2511.03807](https://arxiv.org/abs/2511.03807)) develops drift-aware SHAP rebaselining with
sliding-window backgrounds and per-slice reweighting **precisely because vanilla SHAP explanations
are temporally unstable under drift**. So: everyone's SHAP chart is unstable in a domain defined by
deterioration, and you can say so.

**Regulatory literature on explainability in credit.** Three anchors, all real, all quotable:

- **Fed/OCC SR 11-7**, *Guidance on Model Risk Management* (2011). A "model" explicitly includes
  machine-learning methods; requirements span development, implementation, use, independent
  validation, model inventory, and board-level governance
  ([Fed](https://www.federalreserve.gov/supervisionreg/srletters/sr1107.htm) ·
  [PDF](https://www.federalreserve.gov/boarddocs/srletters/2011/sr1107.pdf)). *"Reconstruct exactly
  what the model saw and recompute the output"* is a validation obligation — which is why PS-17's
  replay log (17-E) is load-bearing here too, not decorative.
- **EBA**, *Follow-up report on machine learning for IRB models* (4 August 2023)
  ([PDF](https://www.eba.europa.eu/sites/default/files/document_library/Publications/Reports/2023/1061483/Follow-up%20report%20on%20machine%20learning%20for%20IRB%20models.pdf);
  earlier [discussion paper](https://www.eba.europa.eu/sites/default/files/document_library/Publications/Discussions/2022/Discussion%20on%20machine%20learning%20for%20IRB%20models/1023883/Discussion%20paper%20on%20machine%20learning%20for%20IRB%20models.pdf)).
  Principles: stakeholders must understand how the model functions, **avoid unnecessary complexity
  in the modelling approach**, ensure correct interpretation; respondents note that avoiding
  excessive retraining aids traceability. **This is a regulator explicitly preferring the simpler
  interpretable construction — cite it as the justification for glass-box covenant arithmetic over a
  black-box classifier, and the "we chose the simpler model" line stops sounding like a limitation.**
- **RBI**: the revised Master Directions on Fraud Risk Management require banks/NBFCs/UCBs to
  operate Early Warning Signals and Red Flagging of Accounts frameworks integrated with core banking,
  with EWS indicators approved by the board's Risk Management Committee, extended beyond credit to
  non-credit and digital-channel transactions. *[Secondary sources only — primary RBI notification
  not opened; verify before quoting on stage:*
  [Servosys](https://www.servosys.com/rbi-fraud-risk-management-guidelines-ews/) ·
  [Pirimid](https://pirimidtech.com/what-is-ews-and-what-does-rbis-fraud-risk-management-direction-mean-for-financial-institutions/) ·
  [India Law](https://www.indialaw.in/blog/banking-and-finance/rbi-issues-revised-master-directions-on-fraud/)*]*

**LLM arithmetic over financial statements — do not let the model compute the ratio.**
FinQA (Chen et al., EMNLP 2021, [arXiv:2109.00122](https://arxiv.org/abs/2109.00122)): 8,281 QA pairs
over 2,789 earnings reports with annotated reasoning programs; the authors report that large
pre-trained models *"fall far short of expert humans"* on multi-step numerical reasoning over
financial text and tables. Later work in this line reports accuracy collapsing on longer programs
and on multivariate calculations *[secondary summaries; individual follow-up papers not opened]*.
**Design consequence, identical to PS-17's: the LLM parses the statement into typed line items; the
ratio is computed in Python.**

### 3. The white space — five ideas, ranked

| # | Idea | N | D | Days | Score | Depends on |
| --- | --- | --- | --- | --- | --- | --- |
| 04-A | Glass-box covenant arithmetic — forecast components, evaluate the formula | 7 | 9 | 2.0 | **31** | — |
| 04-B | Conformal coverage bands + a live coverage tracker under injected drift | 7 | 8 | 1.0 | **56** | 04-A |
| 04-C | Covenant-gaming detector: slack bunching + accrual quality + cash divergence | 9 | 9 | 1.5 | **54** | — |
| 04-D | Covenant-as-contract: deontic engine over the credit agreement | 8 | 8 | 1.5 | **43** | 17-B |
| 04-E | Exact counterfactual headroom — the cheapest lever that clears the test | 7 | 10 | 0.5 | **140** | 04-A |

---

**04-A — Glass-box covenant arithmetic: forecast the ratio, never the label.**

*What it is.* Do not train a classifier on `breach_in_90`. Instead:

1. Probabilistically forecast the **components** — EBITDA, total debt, interest expense, scheduled
   amortisation, cash, receivables — at 30/60/90 days, using quantile GBMs or a small TFT.
2. **Reconcile** the component forecasts so the accounting identities hold exactly (MinT-style).
3. Evaluate **the actual covenant formula from the credit agreement**, unchanged, on the reconciled
   forecast distribution.
4. Breach probability = Monte Carlo mass crossing the threshold at the **next contractual test
   date** — not at an arbitrary +90 days, because covenants test quarterly.

*Why this is the right call and not just the conservative one.* It converts the circularity problem
from fatal to survivable: the *arithmetic* is real even when the data is synthetic, so the claim you
defend is "our covenant engine is exact and our forecast is calibrated", not "our AUC is 0.94 on
data we invented". It gives you drivers in the units a credit officer actually thinks in — *"DSCR
falls to 1.08 because interest expense rises $0.4m on the SOFR reset and amortisation steps up in
Q4, not because feature_17 had a SHAP value of −0.31."* And EBA's "avoid unnecessary complexity"
principle plus SR 11-7's validation burden both point here.

*On screen.* A fan chart of the covenant ratio with the 30/60/90 markers and the threshold drawn as
a hard line, the P10–P90 band crossing it visibly at day 62 — and beside it, **the covenant formula
rendered with live numbers substituted in**, so the arithmetic is legible at a glance.

**04-B — Conformal bands with a live coverage tracker, demonstrated under an injected regime shift.**

Wrap the ratio forecast in adaptive conformal inference (Gibbs & Candès). The alert rule stops being
`PD > 0.6` — an unfalsifiable number — and becomes **"the covenant threshold lies inside the 90%
predictive interval at horizon h"**, which a credit committee can defend in minutes. Then, on stage,
**inject a regime shift the model never saw** (an interest-rate step, a sector demand shock), and
show the bands widen and empirical coverage recover toward 90% on a live tracker.

This is the direct answer to the brief's core challenge — *"distinguish meaningful deterioration from
temporary noise"* — with a stated guarantee instead of a smoothing heuristic. And a calibration plot
is a thing no other team will put on screen, precisely because it is the plot that would expose
their circularity.

**04-C — The covenant-gaming detector. The strongest originality claim in either problem statement.**

*What it is.* Three signals that no other team will build, because they come from accounting
research rather than ML tutorials:

1. **Slack bunching.** Compute, per borrower, how often the *reported* ratio lands in the narrow band
   just above the covenant threshold (say 0–5% headroom), against what that borrower's own realised
   volatility implies it should. Persistent bunching just above the line is not luck. The
   regression-discontinuity literature exploits exactly this discontinuity as an identification
   device; you are inverting it into a detector.
2. **Accrual quality.** Beneish-style manipulation signals computed from the synthetic statements —
   receivables growth outrunning sales (DSRI), rising total accruals to assets (TATA), softening
   gross margin with rising leverage. Beneish's M-score, with its −2.22 classification threshold, was
   found best-in-class against six competing fraud-detection models over US data 1982–2016 in
   Beneish & Vorst, *The Accounting Review* (2022) *[secondary sources:*
   [GMT Research](https://www.gmtresearch.com/en/accounting-ratio/beneishs-m-score/) ·
   [Cogent Business & Management](https://www.tandfonline.com/doi/full/10.1080/23311975.2025.2502542)*;
   original 1999 paper URL not opened — verify before quoting the threshold on stage]*.
3. **The bank-native cross-check nobody else has.** The bank *sees the operating account*. Reported
   accrual revenue can be managed; collections through your own accounts cannot. Flag divergence:
   *"reported revenue up 12% QoQ; observed collections through our accounts down 4%; receivable days
   implied by the statements up 19."*

*The literature backing it is unambiguous.* Jha (2013), large-sample quarterly data: managers manage
earnings **upward in the quarters preceding** a violation and downward in the violation quarter, and
do so to improve bargaining power in the subsequent renegotiation
([JAAF](https://journals.sagepub.com/doi/abs/10.1177/0148558X13505597)). Dyreng, Hillegeist &
Penalva, *Earnings Management to Avoid Debt Covenant Violations and Future Performance*, European
Accounting Review 31(2) ([EAR](https://www.tandfonline.com/doi/full/10.1080/09638180.2020.1826337) ·
[SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3687741)). Bordeman et al. (2022)
reexamine the debt covenant hypothesis in *JAR*
([JAR](https://onlinelibrary.wiley.com/doi/abs/10.1111/1475-679X.12456)). And Dichev & Skinner's
finding that leverage poorly proxies covenant closeness is the reason a generic credit model cannot
see any of this.

*The demo line.* This idea earns the single best sentence available in either problem statement:

> **"This borrower is not deteriorating. This borrower is hiding. Here are the three arithmetic
> reasons we think so, and here is the one number they cannot manage — the cash that moves through
> our own account."**

Build cost is 1–1.5 days and it is all arithmetic — no training, no labels, therefore **no
circularity**. It is the one PS-04 idea that is immune to the "you're just recovering your generator"
attack, because it is not a learned model at all.

**04-D — Covenant-as-contract: run the deontic engine on the credit agreement.**

Covenants *are* contractual obligations with effective dates, testing frequencies, cure and equity-cure
rights, headroom step-down schedules, springing triggers, and negotiated EBITDA add-back definitions.
Extract them into the **same typed norm objects as 17-B**, so a waiver or amendment retroactively
re-adjudicates past test dates — PS-17's finale inject, imported into PS-04 where no one expects it.

This is the mainline, not an edge case: Nini/Smith/Sufi put 10–20% of firms in violation in any
given year, and CovenantAI's entire contribution is that the outcome space is waivers and amendments,
not a binary flag. The median team will hardcode `dscr >= 1.25` in a YAML file and will be unable to
represent a step-down schedule, a cure period, or a covenant that springs on utilisation.

*On screen.* The covenant rendered as a **step-down schedule over time** — 1.10 through Q2, 1.20
through Q4, 1.25 thereafter — with the tested value plotted against the *then-effective* threshold,
and a waiver period shaded out. Then land a retroactive waiver and watch three historical test dates
flip from breach to cured, with the reason attached.

**04-E — Exact counterfactual headroom: the cheapest lever that clears the test.**

Replace "recommended action: contact the RM" with the minimal change set that clears the covenant at
the next test date. This is Wachter, Mittelstadt & Russell's counterfactual explanation
([arXiv:1711.00399](https://arxiv.org/abs/1711.00399), Harvard JOLT 31, 2018) — *"the smallest change
to the world that yields the desired outcome"*, argued there as the explanation form that is
intelligible to judges, lawyers and affected individuals — applied to a **deterministic formula**.
Because 04-A made the covenant exact arithmetic rather than a black box, the counterfactual is
**solvable in closed form or by 1-D root-finding, not approximated by gradient search.** That is the
twist worth stating aloud: *our counterfactuals are exact because our covenant engine is exact.*

> *To hold DSCR ≥ 1.25 at 31 December you need EBITDA +$1.9m, **or** a debt paydown of $6.2m, **or** a
> covenant reset to 1.15. Ranked by (probability of clearing × cost to the bank), the cheapest lever
> we actually control is freezing $4m of the revolver, which lifts projected DSCR to 1.27 with 84%
> confidence and costs us $61k in foregone margin.*

Half a day on top of 04-A, and it is the most demonstrable single feature in either problem
statement. Score 140 — but only because 04-A paid for it.

### 4. The anti-slop check — PS-04

| Pattern | Verdict |
| --- | --- |
| LLM sentiment over synthetic news headlines the team also generated | **A closed loop with zero information content.** You are measuring your own prompt. If you need news signals, generate them from an *independent* latent state variable that the borrower model does not see, and say so — otherwise cut it. |
| SHAP bar chart as "explainability" | **The most predictable slide in the competition.** Worse: on synthetic data SHAP explains your generator, and under drift SHAP is documented as temporally unstable ([arXiv:2511.03807](https://arxiv.org/abs/2511.03807)). Replace with 04-A's arithmetic drivers and 04-E's counterfactuals. |
| Three independent binary classifiers for 30/60/90 | Not slop exactly, but **technically incoherent** — nothing constrains P(90d) ≥ P(30d), and it will be visibly wrong on some borrower. A hazard model or 04-A's Monte Carlo gives a coherent term structure for free. |
| LLM-drafted credit memo / "AI credit analyst" | **Garnish.** Generate from computed numbers. Never let the LLM produce the number. |
| "Multi-agent credit committee" (risk agent, RM agent, arbiter agent) | **Slop.** The panel has seen this exact framing all year. |
| "Digital twin of the borrower" | Usually a renamed Monte Carlo simulator. If you use the phrase, be ready to say precisely what is twinned and what is calibrated against what. |
| Any headline AUC / accuracy number | **The circularity trap.** Reporting 0.94 AUC invites the one question you cannot answer. Report *calibration* and *conformal coverage* instead — those are honest properties of your method rather than of your generator. |
| "Agentic RAG over the credit agreement" | Commodity. See PS-17. |

---

## Cross-pollination — mechanisms that transfer, and which direction pays

**The two problem statements are the same problem viewed from opposite ends**, and saying so is
itself an insight worth a slide: a covenant is an SLA denominated in accounting ratios rather than
minutes, and an SLA is a covenant denominated in service metrics. Both are effective-dated normative
constraints tested on a schedule against evidence that arrives late and gets corrected.

**PS-04 → PS-17 (the strongest transfer, and it is cheap).** *Predictive SLA burn-down.* PS-17's
brief only asks you to **detect** breaches — it never asks you to forecast one. So forecasting one is
free white space. A monthly-uptime SLA is a monotone accumulator, which makes this trivially cheap:
you know minutes consumed, minutes remaining in the window, and the historical incident arrival rate.

> *Vendor is at 99.87% with 9 days left in the July window. 26 minutes of downtime budget remain.
> P(monthly breach) = 0.71 with 90% conformal coverage. Expected credit exposure $84k. This is the
> first month we have ever told you before the month ended.*

There is a mature cloud-computing literature on exactly this — *SLA Violation Prediction in Cloud
Computing: A Machine Learning Perspective* ([arXiv:1611.10338](https://arxiv.org/abs/1611.10338)),
which also documents the extreme class imbalance (violations ~0.2% of events) that makes naive
accuracy meaningless, and *Predicting SLA Violations in Real Time using Online Machine Learning*
([arXiv:1509.01386](https://arxiv.org/pdf/1509.01386)). **No CLM vendor has imported it into
commercial contract monitoring.** One day of work; converts a rules engine into an early-warning
system; scores highest on the PS-17 table.

**PS-17 → PS-04 (the strongest structural transfer).** *Effective-dated re-adjudication and the norm
compiler* (04-D above). Covenant waivers, amendments and step-downs are retroactive rule changes over
past test dates. This is the PS-17 finale inject wearing a suit, and PS-04 teams will not have
thought about it at all.

**PS-04 → PS-17 (second).** *Expected-loss ranking of the work queue.* Credit risk has a mature
notion of prioritising by exposure × probability; PS-17's "next best action" is the same problem
wearing different clothes, and 17-C's value-of-information ranking is the import.

**PS-17 → PS-04 (second).** *The hash-chained replay log.* Under SR 11-7 the ability to reconstruct
exactly what a model saw and recompute its output is a validation requirement, not a nicety — so the
same mechanism is load-bearing in both, and building it once serves whichever problem statement wins.

**PS-04 → PS-17 (third, cheap).** *Conformal coverage as the uncertainty representation.* PS-17's
brief demands "make uncertainty explicit". Everyone will render a yellow badge. A stated coverage
guarantee on a predicted SLA breach is a different order of answer.

**The reciprocal insight worth building for either.** *The gaming detector generalises.* 04-C's logic
— detect the counterparty steering a reported number to sit just inside a contractual threshold —
transfers directly to PS-17: a vendor whose self-reported uptime lands at 99.90% or 99.91% in
fourteen consecutive months, and never at 99.89%, is not operating a well-tuned platform. It is
rounding. That is the same bunching statistic with a different denominator, and it is the sharpest
single idea in this document.

---

## Head-to-head verdict for this lane

### PS-17 — **8.5 / 10** on accessible, defensible white space

**Why it scores high.**

- The **problem statement itself hands you the mechanism**. The finale inject is a direct test of
  retroactive re-adjudication, and the brief's own language ("targeted re-evaluation rather than
  silently preserving an outdated conclusion", "represent late, corrected or conflicting versions
  without losing earlier evidence") names bitemporality and incremental view maintenance without
  using the words. You are not inventing a differentiator; you are reading it off the page and
  building it properly while others skim past.
- **Everything defensible is deterministic**, so it is demonstrable, reproducible on stage, and
  immune to the failure mode the Stanford study documents in market-leading legal AI.
- The **patentable claim is crisp** — norm versioning + conclusion reversal + irreversibility triage
  — even though the storage layer beneath it is crowded prior art.
- **Explainability is native, not bolted on.** "Why did this change?" is answered by a diff of two
  structs and a clause version, which is a better artefact than any attribution method.
- The **decision-diff UI is genuinely novel as a product surface**. Nobody ships "here is what we
  believed yesterday, here is what we believe now, and here is the clause that changed our mind."

**Why it isn't a 10.**

- **Extraction is where the demo can visibly fail.** ContractEval places most models at junior-legal-
  assistant level; CUAD performance is characterised as nascent. Ideas 17-A and 17-B both sit on top
  of extraction quality, so a bad parse is a load-bearing failure, live, on stage.
- **CLM is a crowded incumbent field.** Obligation extraction is table stakes, so your uniqueness has
  to be entirely in the temporal/adjudication layer, and you must say so in the first 60 seconds.
- The best ideas are **invisible until the inject fires**. You must engineer the demo so the reversal
  moment happens, rather than hoping the jury asks.

### PS-04 — **6.5 / 10**

**Why white space exists.**

- **There is genuinely no covenant-breach forecasting literature or benchmark** — the field measures
  violations (CovenantAI) or uses them for identification (Nini/Smith/Sufi). Naming that gap is
  itself a credible research contribution in the pitch.
- **04-C has no analogue anywhere in the vendor market**, and it is arithmetic, so it cannot be
  attacked as circular.
- **04-E is the highest-demonstrability-per-day feature in this entire document.**

**Why it scores 2 points lower.**

- **The circularity problem is structural, not fixable.** Synthetic data + supervised prediction =
  you measured your own generator. Every route around it (04-A, 04-C) works by *abandoning the
  headline prediction claim* — which is exactly the claim the problem statement asks you to make.
  The brief asks for 30/60/90-day breach probability; the honest version of that is a forecast you
  cannot validate.
- **Incumbents already advertise the headline.** Moody's Lending Suite markets AI loan monitoring
  with automated covenant document collection, validation, testing, pattern-detected early warning
  signals and tolerance-breach notification. nCino and Cync ship covenant monitoring. Your novelty
  claim starts negative.
- **Visual sameness.** Every risk dashboard looks like every other risk dashboard — heatmap, drill-
  down, gauge, driver bar chart. PS-17's split-pane decision diff has no equivalent in this genre.
- **The default toolkit is a poor fit and everyone will use it anyway** — and Dichev & Skinner
  already told us why (leverage poorly proxies covenant closeness), so you'd be differentiating
  against a crowd making a known error, which is good, but the jury has to follow an accounting
  argument to see it.

### Margin, and what would change the verdict

**PS-17 wins by ~2 points on this lane.** Three things would move it:

1. **If the team is willing to lead with an accounting-forensics claim rather than an ML claim,
   PS-04 rises to ~8.** "We detect the borrower who is hiding, not the borrower who is failing" is a
   better single sentence than anything in PS-17, and 04-C + 04-E + 04-A together are a coherent,
   circularity-proof, arithmetically defensible product. This requires the team to *not* put an AUC
   number on a slide, which takes discipline.
2. **If structured norm extraction from contract text proves unreliable in the first 48 hours,
   PS-17 drops to ~6**, because 17-A and 17-B both collapse onto it. Mitigation: hand-author the
   gold norm objects for the demo contracts, keep the extractor as a live-parse tab with a visible
   confidence-and-review gate, and be explicit that human review of extracted norms is the intended
   product design and not a hackathon shortcut — the brief reserves legal interpretation for humans
   anyway, so this is *aligned* with the problem statement, not a dodge.
3. **If the jury weights "business impact with real numbers" heaviest, the gap narrows.** PS-04's
   impact chain is short and citable (Nini/Smith/Sufi: intervention at violation improves operating
   and stock performance; violations affect 10–20% of firms annually). PS-17's is a single, softer,
   consultancy-sourced number (WorldCC 9.2%/11%).

---

## Risks and open questions

1. **Prior-art risk on PS-17's crown jewel is real and specific.** Capital One's bitemporal patent
   family (11,935,046 / 11,915,236 / 11,907,943 / 12,481,990) covers retroactive recomputation with
   alternate temporal sequences. *Open question:* do the independent claims reach *rule versioning*
   and *derived-conclusion reversal*, or only transaction/event replay? **Someone should read claim 1
   of 11,935,046 in full before the patentability slide is written** — the Google Patents page
   returned HTTP 503 during this research and only the abstract-level summary was obtained.
2. **RBI EWS citation is secondary-sourced.** Every RBI claim above came from vendor and law-firm
   analyses, not the primary Master Direction on rbi.org.in. Verify before it appears on a slide in
   front of an Indian CTO panel.
3. **Beneish's original 1999 paper URL was not opened**, and the −2.22 threshold and the Beneish &
   Vorst (2022) *Accounting Review* head-to-head result are reported here from secondary sources.
   The mechanism (accrual-quality indices) is safe to build on; the specific threshold number should
   be verified before it is quoted.
4. **A few arXiv identifiers appeared only in search results and were not opened** — 2511.21033,
   2604.02276, and the FinQA follow-up work reporting accuracy collapse on multivariate calculations.
   Two future-dated IDs *were* verified by direct fetch (2605.18147 Baesens et al.; 2506.08899 Horner
   et al.), so the index is trustworthy, but do not put an unopened paper on a slide.
5. **Open question with real consequences: how much of the 7 days does extraction eat?** Both PS-17's
   17-B and PS-04's 04-D depend on getting typed norms out of prose. If it takes 3 days, neither
   problem statement's best ideas ship. Timebox it to day 2 and fall back to hand-authored norms.
6. **The "no covenant-breach forecasting benchmark exists" claim is a negative result** from a
   focused but not exhaustive search. It is stated as an absence in the *published, findable*
   literature. Phrase it that way on stage — *"we could not find one, and we looked"* — rather than
   as a proof of non-existence.
7. **Conformal coverage on synthetic data is a partial defence, not a complete one.** Adaptive
   conformal inference guarantees long-run coverage irrespective of the data-generating process, so
   the guarantee is honest — but the *distribution being covered* is still yours. Be ready for the
   sharp version of the question: "your intervals are valid for your simulator; why should I believe
   the simulator?" The answer is 04-A and 04-C — the covenant arithmetic and the manipulation signals
   do not depend on the simulator being right.
8. **The out-of-the-box crypto angle must stay load-bearing.** 17-E is justified because SLA credit
   disputes are adversarial and backdating is the natural accusation. If the pitch drifts toward
   "and we put it on a blockchain", it becomes exactly the forced angle the brief warns against.

---

## Sources

Every URL below was surfaced during this research. Items marked **[not opened]** appeared in search
results and were not individually fetched; items marked **[fetched]** were retrieved and read.

**Legal AI reliability and grounding**

1. Stanford RegLab — *Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools* — https://reglab.stanford.edu/publications/hallucination-free-assessing-the-reliability-of-leading-ai-legal-research-tools/
2. Magesh et al., *Journal of Empirical Legal Studies* (2025) — https://onlinelibrary.wiley.com/doi/full/10.1111/jels.12413 **[not opened]**
3. Preprint PDF (*Legal_RAG_Hallucinations*) — https://dho.stanford.edu/wp-content/uploads/Legal_RAG_Hallucinations.pdf **[not opened]**

**Contract / legal NLP benchmarks**

4. CUAD — https://arxiv.org/abs/2103.06268 · project page https://www.atticusprojectai.org/cuad/
5. LexGLUE — https://arxiv.org/pdf/2110.00976 **[not opened]**
6. LegalBench — https://github.com/HazyResearch/legalbench · ACM https://dl.acm.org/doi/10.5555/3666122.3668037 **[not opened]**
7. LegalBench-RAG — https://arxiv.org/abs/2408.10343 · https://arxiv.org/html/2408.10343v1 · repo https://github.com/zeroentropy-ai/legalbenchrag **[not opened]**
8. ContractEval — https://arxiv.org/abs/2508.03080 **[fetched]**

**Deontic / formal legal reasoning**

9. Horner, Mateis, Governatori, Ciabattoni — *Toward Robust Legal Text Formalization into Defeasible Deontic Logic using LLMs* — https://arxiv.org/abs/2506.08899 **[fetched]**
10. Workshop version — https://ceur-ws.org/Vol-4174/paper7.pdf **[not opened]**
11. *Towards Trustworthy Legal AI through LLM Agents and Formal Reasoning* — https://arxiv.org/pdf/2511.21033 **[not opened]**
12. *De Jure: Iterative LLM Self-Refinement for Structured Extraction of Regulatory Rules* — https://arxiv.org/html/2604.02276v1 **[not opened]**
13. LN2FR 2022 workshop proceedings (JURIX) — https://arxiv.org/pdf/2305.12203 **[not opened]**

**Long context and temporal reasoning**

14. *Lost in the Middle* — https://arxiv.org/abs/2307.03172 **[not opened]**
15. *Test of Time* (ICLR 2025) — https://arxiv.org/abs/2406.09170 · https://openreview.net/pdf?id=44CoQe6VCq **[not opened]**
16. *TIME* multi-level temporal benchmark — https://arxiv.org/abs/2505.12891 **[not opened]**
17. Allen (1983), *Maintaining Knowledge about Temporal Intervals*, CACM 26(11) — https://cacm.acm.org/research/maintaining-knowledge-about-temporal-intervals/ · https://dblp.org/rec/journals/cacm/Allen83.html **[not opened]**

**Bitemporal state, incremental computation, tamper-evident logs**

18. Jensen, Snodgrass & Soo — TSQL2 data model — https://people.cs.aau.dk/~csj/Thesis/pdf/chapter12.pdf **[not opened]**
19. Bitemporal databases: PRISMA-guided systematic review — https://link.springer.com/article/10.1007/s42488-026-00162-x **[not opened]**
20. US 11,935,046 — *Immutable database for processing retroactive and historical transactions using bitemporal analysis* (Capital One Services LLC) — https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11935046 **[abstract-level only; Google Patents returned HTTP 503]**
21. US 11,915,236 — https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11915236 **[not opened]**
22. US 11,907,943 — https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11907943 **[not opened]**
23. US 12,481,990 — https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12481990 **[not opened]**
24. App. 20220284422 / 20220284423 — https://patents.justia.com/patent/20220284422 · https://patents.justia.com/patent/20220284423 **[not opened]**
25. Budiu et al., *DBSP* (VLDB 2023) — https://arxiv.org/pdf/2203.16684 · https://docs.feldera.com/vldb23.pdf **[not opened]**
26. *Recent Increments in Incremental View Maintenance* — https://arxiv.org/pdf/2404.17679 **[not opened]**
27. RFC 6962 Certificate Transparency — https://datatracker.ietf.org/doc/html/rfc6962 **[not opened]**
28. Cox, *Transparent Logs for Skeptical Clients* — https://research.swtch.com/tlog **[not opened]**

**SLA prediction and contract-value leakage**

29. *SLA Violation Prediction in Cloud Computing: A Machine Learning Perspective* — https://arxiv.org/abs/1611.10338 **[not opened]**
30. *Predicting SLA Violations in Real Time using Online Machine Learning* — https://arxiv.org/pdf/1509.01386 **[not opened]**
31. WorldCC, *Stopping the Leak: The value of contracts* — https://www.worldcc.com/resource/Stopping-the-Leak-The-value-of-contracts.html **[not opened]**
32. PASA summary of WorldCC ~11% post-signature leakage — https://procurementandsupply.com/procurement-contracts-leaking-11-percent-of-value-due-to-enterprise-wide-failures/ **[not opened]**

**Covenants: economics and measurement**

33. Dichev & Skinner (2002), *Large-Sample Evidence on the Debt Covenant Hypothesis*, JAR 40(4):1091–1123 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=275174 · https://onlinelibrary.wiley.com/doi/abs/10.1111/1475-679X.00083 **[not opened]**
34. Nini, Smith & Sufi (2012), *Creditor Control Rights, Corporate Governance, and Firm Value*, RFS 25(6):1713–1761 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1344302 · https://academic.oup.com/rfs/article-abstract/25/6/1713/1595112 **[not opened]**
35. Saunders, Steffen & Verhoff, *CovenantAI — New Insights into Covenant Violations* — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4640653 · https://cepr.org/publications/dp20117 · working PDF https://static1.squarespace.com/static/60f02ca7138f3902d7a39744/t/65cf2feb9e802c4401f17658/1708077039426/CovenantAI_v15Feb2024.pdf **[conference PDF fetch failed — encoded stream; details from search summaries]**
36. Jha (2013), *Earnings Management Around Debt-Covenant Violations*, JAAF — https://journals.sagepub.com/doi/abs/10.1177/0148558X13505597 **[not opened]**
37. Dyreng, Hillegeist & Penalva, *Earnings Management to Avoid Debt Covenant Violations and Future Performance*, EAR 31(2) — https://www.tandfonline.com/doi/full/10.1080/09638180.2020.1826337 · https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3687741 **[not opened]**
38. Bordeman et al. (2022), *Do Borrowers Intentionally Avoid Covenant Violations?*, JAR — https://onlinelibrary.wiley.com/doi/abs/10.1111/1475-679X.12456 **[not opened]**
39. Kim, *Debt Covenant Slack and Real Earnings Management* — https://web-docs.stern.nyu.edu/old_web/emplibrary/DebtCovenantSlackandReal...pdf **[not opened]**

**Earnings-manipulation detection**

40. Beneish M-score overview (GMT Research) — https://www.gmtresearch.com/en/accounting-ratio/beneishs-m-score/ **[not opened; secondary]**
41. *Earnings manipulation and cash holdings: a Beneish M-score analysis in G7 nations*, Cogent Business & Management (2025) — https://www.tandfonline.com/doi/full/10.1080/23311975.2025.2502542 **[not opened]**

**Credit-risk ML, survival, forecasting, uncertainty, drift**

42. Baesens et al., *Foundation Models for Credit Risk Prediction: A Game Changer?* — https://arxiv.org/abs/2605.18147 **[fetched]**
43. *Approaches for modelling the term-structure of default risk under IFRS 9: a tutorial using discrete-time survival analysis* — https://arxiv.org/html/2507.15441v1 · https://link.springer.com/article/10.1007/s41060-026-01032-w **[not opened]**
44. *Incorporating data drift to perform survival analysis on credit risk* — https://arxiv.org/abs/2601.20533 **[not opened]**
45. *Dynamic survival models with varying coefficients for credit risks*, EJOR — https://www.sciencedirect.com/science/article/abs/pii/S0377221718309548 **[not opened]**
46. Lim, Arik, Loeff & Pfister, *Temporal Fusion Transformers*, IJF 37(4) — https://arxiv.org/abs/1912.09363 · https://www.sciencedirect.com/science/article/pii/S0169207021000637 **[not opened]**
47. Wickramasuriya, Athanasopoulos & Hyndman, *Optimal Forecast Reconciliation … Trace Minimization*, JASA 114(526) — https://robjhyndman.com/papers/MinT.pdf · https://www.tandfonline.com/doi/abs/10.1080/01621459.2018.1448825 **[not opened]**
48. Gibbs & Candès, *Adaptive Conformal Inference Under Distribution Shift*, NeurIPS 2021 — https://dl.acm.org/doi/10.5555/3540261.3540389 · https://www.semanticscholar.org/paper/Adaptive-Conformal-Inference-Under-Distribution-Gibbs-Cand%C3%A8s/445596c40dc421efe2354a340085b43181bea2be **[not opened]**
49. Angelopoulos et al., *Conformal Risk Control* — https://arxiv.org/abs/2208.02814 **[not opened]**
50. *Conformal Prediction for Time Series* — https://arxiv.org/pdf/2010.09107 **[not opened]**
51. *Adaptive Conformal Inference for Multi-Step Ahead Time-Series Forecasting Online* — https://arxiv.org/pdf/2409.14792 **[not opened]**
52. *Optimization-based Online Conformal Prediction for Multi-step Forecasting* — https://arxiv.org/pdf/2508.13362 **[not opened]**
53. *Fair and Explainable Credit-Scoring under Concept Drift* — https://arxiv.org/abs/2511.03807 **[not opened]**
54. *A two-stage model for dealing with temporal degradation of credit scoring* — https://arxiv.org/pdf/1406.7775 **[not opened]**
55. Time-series foundation model evaluations (Chronos / TimesFM) — https://arxiv.org/pdf/2509.26347 · https://arxiv.org/html/2412.12834v1 **[not opened]**
56. *A Comprehensive Survey of Deep Learning for Time Series Forecasting* — https://arxiv.org/pdf/2411.05793 **[not opened]**

**Explainability, counterfactuals, decision-change explanation**

57. Wachter, Mittelstadt & Russell, *Counterfactual Explanations without Opening the Black Box*, Harvard JOLT 31 (2018) — https://arxiv.org/abs/1711.00399 · https://jolt.law.harvard.edu/assets/articlePDFs/v31/Counterfactual-Explanations-without-Opening-the-Black-Box-Sandra-Wachter-et-al.pdf **[not opened]**
58. *Delta-XAI: A Unified Framework for Explaining Prediction Changes in Online Time Series Monitoring* — https://arxiv.org/html/2511.23036v2 **[not opened]**
59. *Delta-Audit: Explaining What Changes When Models Change* — https://arxiv.org/html/2508.19589 **[not opened]**
60. *Contrastive Explanations for Model Interpretability* — https://arxiv.org/pdf/2103.01378 **[not opened]**

**Financial numerical reasoning**

61. Chen et al., *FinQA: A Dataset of Numerical Reasoning over Financial Data*, EMNLP 2021 — https://arxiv.org/abs/2109.00122 **[fetched]**
62. *FinanceQA* — https://arxiv.org/pdf/2501.18062 **[not opened]**
63. *Evaluating LLMs' Mathematical Reasoning in Financial Document Question Answering* — https://arxiv.org/html/2402.11194v3 **[not opened]**

**Regulation and governance**

64. Fed/OCC SR 11-7 — https://www.federalreserve.gov/supervisionreg/srletters/sr1107.htm · https://www.federalreserve.gov/boarddocs/srletters/2011/sr1107.pdf **[not opened]**
65. EBA, *Follow-up report on machine learning for IRB models* (Aug 2023) — https://www.eba.europa.eu/sites/default/files/document_library/Publications/Reports/2023/1061483/Follow-up%20report%20on%20machine%20learning%20for%20IRB%20models.pdf **[not opened]**
66. EBA, *Discussion paper on machine learning for IRB models* — https://www.eba.europa.eu/sites/default/files/document_library/Publications/Discussions/2022/Discussion%20on%20machine%20learning%20for%20IRB%20models/1023883/Discussion%20paper%20on%20machine%20learning%20for%20IRB%20models.pdf **[not opened]**
67. RBI Fraud Risk Management Directions / EWS — **secondary sources only, primary RBI notification NOT located**: https://www.servosys.com/rbi-fraud-risk-management-guidelines-ews/ · https://pirimidtech.com/what-is-ews-and-what-does-rbis-fraud-risk-management-direction-mean-for-financial-institutions/ · https://www.indialaw.in/blog/banking-and-finance/rbi-issues-revised-master-directions-on-fraud/ **[not opened]**

**Observability**

68. OpenTelemetry, *Inside the LLM Call: GenAI Observability with OpenTelemetry* — https://opentelemetry.io/blog/2026/genai-observability/ **[not opened]**
69. MLflow, OpenTelemetry GenAI semantic conventions — https://mlflow.org/docs/latest/genai/tracing/opentelemetry/genai-semconv/ **[not opened]**

**Incumbent vendor positioning (baseline to differentiate against)**

70. Moody's Lending Suite — AI-powered loan monitoring — https://www.moodys.com/web/en/us/solutions/lending/loan-monitoring.html **[not opened]**
71. Cync — automated covenant monitoring — https://www.cyncsoftware.com/platform/covenants **[not opened]**
72. Cardo AI — automated financials and covenant monitoring — https://cardoai.com/whats_new_in_product/automated-financials-and-covenant-monitoring/ **[not opened]**
