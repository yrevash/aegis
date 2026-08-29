# Explainability, observability, autonomy & cryptographic provenance — comparative research

> Lane 08. Clean-room evaluation of PS-17 (Contract Obligation, SLA & Commercial Leakage Monitor)
> against PS-04 (AI-Powered Dynamic Covenant Monitoring & Early Warning), on four sub-lanes:
> **A** explainability and audit, **B** OpenTelemetry observability over agent loops,
> **C** the human–AI autonomy model, **D** cryptographic/blockchain provenance.
> Every non-obvious claim is sourced. Unverifiable claims are marked.

---

## Executive answer

1. **PS-17 wins this lane decisively: 35/40 vs 21/40.** It wins all four sub-lanes, and wins B (observability) and D (crypto) by margins large enough to change the overall build decision on their own.

2. **The single sharpest regulatory finding is for PS-04, and it is a double-edged one. Do not cite SR 11-7 — it no longer exists.** On **17 April 2026** the Fed, OCC and FDIC jointly issued revised interagency model-risk guidance, carried as **SR 26-2** (Federal Reserve) and **OCC Bulletin 2026-13** ("Model Risk Management: Revised Guidance"). SR 26-2 *supersedes and replaces* SR 11-7 (2011) and SR 21-8; OCC Bulletin 2026-13 rescinds **OCC Bulletin 2011-12** [S1][S1b]. Four months before this hackathon. **Footnote 3 explicitly puts generative and agentic AI *outside* scope**: "Generative AI and agentic AI models are novel and rapidly evolving. As such, they are not within the scope of this guidance… the principles described in this guidance apply to traditional statistical and quantitative models and non-generative, non-agentic AI models" [S1, p.3]. A team that says "we're SR 11-7 compliant" is quoting a withdrawn document *and* claiming compliance with a regime that has disclaimed their system.

3. **A second SR 26-2 finding is an architecture argument, not a compliance one — and it cuts across both problem statements.** "Model" is defined to *exclude* "simple arithmetic calculations… as well as deterministic rule-based processes and software where there are no statistical, economic, or financial theories underpinning their design or use" [S1, p.3]. Combined with footnote 3, this yields a three-zone architecture that is worked through in the cross-cutting section below. **The headline conclusion: the carve-out does not remove governance burden, it *relocates* it — from model validation to the point where text becomes rules.** Both problem statements have that point, and in both it is an LLM extracting rules from a contract.

4. **The EU AI Act asymmetry is decision-relevant and should be stated explicitly.** **PS-04 has a named Annex III high-risk classification; PS-17 has none.** Annex III point 5(b) covers "AI systems intended to be used to evaluate the creditworthiness of natural persons or establish their credit score" [S31] — with the important nuance that PS-04 is *commercial* lending, so on a literal reading its core corporate-borrower use case sits outside 5(b). PS-17 (contract/SLA operations) has no Annex III entry at all, and no prudential regulator writes rules for service credits. **Net: PS-04 has the far heavier and more nameable regulatory stack; PS-17 has to build to Art. 12/14 by choice rather than obligation.** This is PS-04's single strongest card in this lane, and it is the main thing that could narrow the verdict.

4. **For PS-04 the regulator has already written the product spec — which is both the opportunity and the problem.** EBA/GL/2020/06 §§266–274 is, almost line for line, PS-04's requirements document: monitor adherence to covenants and use it "as early warning tools," track "net debt/EBITDA, interest coverage ratio, debt service coverage ratio (DSCR)," maintain EWIs with "defined trigger levels" and "escalation procedures," and a **watch list** [S2, §§267, 269–272]. §274 then enumerates 9+ deterioration signals that map one-to-one onto the brief's signal list. This is superb grounding — and it means *every serious team will find it*. It is table stakes, not differentiation.

5. **PS-17's brief hands you a first-class research contribution as a requirement.** "Maintain versioned state with source-level provenance and explicit separation of recorded fact, AI inference, user input, automated action and human decision" (PS-17 §04). Almost no team will implement this as a **typed model with a runtime consequence**. The consequence is the differentiator: an **authority rule that demotes an action's autonomy level when its transitive evidence closure contains an unverified AI inference.** Provenance stops being a display artefact and becomes a control-plane input. Section A3 designs it, and maps it 1:1 onto **W3C PROV-O** [S3] so it exports as a standard rather than a homegrown schema.

6. **Observability is where PS-17 pulls furthest ahead.** OTel's GenAI conventions describe *agent* structure — `invoke_agent`, `create_agent`, `execute_tool`, `chat` spans [S4]. PS-17 is genuinely agentic (retrieve → reconcile → hypothesise → re-evaluate), so the trace tree is deep, branching, and *meaningful*. PS-04 is a numeric pipeline with a narrative LLM bolted on: its GenAI trace is three chat spans over a pandas job. **PS-17 also has the only trace demo that can end on a highlighted source document.**

7. **Caveat the whole of B: the GenAI conventions are not stable.** They were split out of the main semconv repo into `open-telemetry/semantic-conventions-genai`, **created 2026-05-05**, which as of today has **zero tagged releases** (verified via GitHub API) while the main semantic-conventions repo is on v1.44.0 (2026-08-04). The agent-spans document is stamped **"Development"** [S4]. Plan to dual-emit `gen_ai.*` and OpenInference `openinference.span.kind` attributes [S5].

8. **Windows/no-Docker verdict for the trace backend, verified against release artefacts:** **Jaeger v2.20.0 ships `jaeger-2.20.0-windows-amd64.zip`** (native, no Docker) [S6]. **Arize Phoenix ships a pure-Python `py3-none-any` wheel (v20.4.0)** — `pip install arize-phoenix`, SQLite-backed, UI on localhost:6006 [S7]. **Langfuse is out**: its own v3 self-hosting docs require six services (web, worker, PostgreSQL, ClickHouse, Redis, S3/MinIO) and recommend 4 cores / 16 GiB [S8]. Grafana Tempo runs on Windows only unofficially [S9]. **Recommendation: Phoenix as primary (it renders LLM/agent spans natively), Jaeger as the "this is standard OTel, not a vendor toy" backstop.**

9. **On autonomy, do not invent a scale.** Use **Sheridan & Verplank's 10-level scale as reproduced verbatim in Parasuraman, Sheridan & Wickens (2000), Table I** [S10], applied per-stage across their four-stage model (information acquisition / information analysis / decision selection / action implementation). Collapse it into a 5-level ladder **and state that the ladder deliberately truncates at Sheridan level 7** — levels 8–10 ("informs the human only if asked," "only if it, the computer, decides to," "ignoring the human") are excluded by design. That truncation *is* the slide. It also has a direct legal hook: **EU AI Act Art. 14(3)** requires oversight measures "proportionate to risks, autonomy level, and context" [S11] — the Act itself asks you to name your autonomy level.

10. **Crypto provenance is load-bearing for PS-17 and close to decorative for PS-04 — say so honestly.** A hash chain proves tamper-evidence *against the log's operator*, which only has economic function when there is a second party who does not trust the first. PS-17 is a **two-party dispute** (customer vs supplier, over which SLA version was effective when). PS-04 is **one-party** (the bank's own model, own auditor). The amendment inject is the exact moment the chain pays off. **Build it for PS-17; for PS-04, don't pitch it.** Smart contracts as executable SLA terms have real substance for PS-17 (and the UK Law Commission concluded in 2021 that English law already accommodates hybrid natural-language-plus-code contracts [S12]); covenant thresholds on-chain for PS-04 die on the oracle problem and nobody wants borrower leverage ratios on any ledger.

---

# Cross-cutting: what SR 26-2's scope carve-outs actually imply for the architecture

This section applies to both problem statements and is the most load-bearing analysis in the report. It is worked through rather than asserted, because the naive reading of the carve-out ("put the judgement in deterministic code and escape governance") is wrong and a bank CTO will say so.

### The three zones SR 26-2 creates

Reading the definition of "model" (p.3) together with footnote 3 (p.3) [S1], any system built for either brief splits into three governance zones:

| Zone | What is in it | SR 26-2 status | Consequence |
| --- | --- | --- | --- |
| **Z1 — Deterministic** | Arithmetic and versioned rule evaluation with no statistical/economic theory underpinning it: ratio computation, covenant headroom tests, SLA uptime → credit-percentage tables, date/deadline arithmetic | **Not a "model."** Explicitly excluded: "simple arithmetic calculations… as well as deterministic rule-based processes and software" | No model validation, no model inventory, no independent validation cycle. **But the *rules* become the governed artefact.** |
| **Z2 — Statistical / non-generative AI** | The 30/60/90 breach forecaster; any scoring, ranking or anomaly model | **Squarely in scope.** "The principles described in this guidance apply to traditional statistical and quantitative models and **non-generative, non-agentic AI models**" | Full burden: conceptual soundness incl. "interpretability measures," developmental testing, outcomes analysis, ongoing monitoring, effective challenge, model inventory, documentation [S1] |
| **Z3 — Generative / agentic** | The LLM extractor, the reasoning loop, the narrative composer | **Out of scope.** "Generative AI and agentic AI models… are not within the scope of this guidance" — but "a banking organization's risk management and governance practices should guide the determination of appropriate governance and controls for any tools, processes, or systems not covered" | **Ungoverned by MRM, not unregulated.** EU AI Act Art. 12/14/26 [S13][S11][S14], EBA §54(b)/(d) [S2] and RBI FREE-AI [S34] all still bite. You must supply the control set yourself. |

### The trap: scope arbitrage

The naive optimisation is to push judgement out of Z2 into Z1 (to escape validation) or into Z3 (to escape scope). **Both are governance holes and both are detectable.**

- **Z2 → Z1 arbitrage** is pushing a fitted relationship into a "rule." If your covenant engine contains a threshold that was *tuned* rather than *contracted*, it is not a deterministic rule; there is a statistical theory underpinning it, and it is a model. The test to state on stage: **every constant in Z1 must be traceable to a contract clause or a policy document, never to a fit.**
- **Z3 → escape arbitrage** is the more seductive one and the more dangerous. If the credit officer or contract manager actually *reads the narrative* and acts on it, the narrative is functionally part of the decision, regardless of what footnote 3 says about scope. Moving the reasoning into prose does not make it not-a-decision; it makes it an undocumented one.

### The rule that closes the hole — and it is cheap and demonstrable

> **The generative layer may restate, never originate.**

Concretely: **Z3 output must be provably non-additive over Z1+Z2 output.** Enforce it with a mechanical invariant rather than a policy:

- **The "no new numbers" check.** Every numeral, threshold, date, percentage and monetary amount appearing in generated narrative must match a value already present in the computed decision record. Parse the narrative, extract numerics, set-difference against the record. Any orphan numeral is a hard failure: the narrative is rejected and regenerated, and the failure is emitted as a span attribute (`aegis.narrative.orphan_numerals`).
- **Every claim carries an assertion id.** Narrative sentences link to the `Assertion` they restate; unlinked assertive sentences are flagged.
- **The narrative is never in the evidence closure of an action.** Under the authority rule (PS-17 §A3), narrative text is presentation, so it can never contribute to `authorised_level`.

This is perhaps 60 lines of code, it is visible on screen (show it rejecting a hallucinated figure), and it converts "we kept the LLM in a safe role" from a claim into an enforced invariant. **It is also the single cheapest credibility win in this report.**

### PS-04: does "statistical model predicts, LLM narrates" hold?

**Yes, and it is the right architecture — but it is a burden *shift*, not a burden *reduction*, and the pitch must say so.**

What holds:
- Confining the risk figure to Z2 means the number that drives the decision is produced by an artefact the agencies *do* govern, with a defined validation vocabulary you can borrow wholesale. That is defensible and easy to explain.
- It makes the explainability obligation tractable: what must be explainable is the **Z2 forecast** (drivers, calibration, counterfactual) and the **Z1 covenant test** (which is self-explaining — threshold, actual, headroom, rule version). Neither requires explaining an LLM.
- It answers the "who is it explainable *to*?" question with three distinct audiences and three distinct artefacts — see below.

What it does *not* do:
- **It does not reduce governance work; it concentrates it.** Z2 now carries the full SR 26-2 load: conceptual soundness with interpretability measures, developmental and out-of-sample testing, outcomes analysis, ongoing monitoring, effective challenge by independent experts, an inventory entry [S1]. For a 7-day build the honest deliverable is the *harness* — a validation notebook, a reliability diagram, a backtest, an inventory record with a trace link — not a completed validation.
- **It does not make Z3 safe by itself.** See the arbitrage trap above. The "no new numbers" invariant is what makes it safe.
- **It relocates risk onto covenant extraction.** Z1's covenant thresholds come from credit agreements via an LLM. A mis-extracted threshold is a silent, systematic error that no amount of Z2 validation will catch, because the model is being asked the wrong question. **This is why covenant-definition extraction sits at autonomy level A2 with mandatory human confirmation against the source clause** (PS-04 §C).

**Who must it be explainable to?** Three audiences, three artefacts — worth a slide because most teams will conflate them:

| Audience | What they need | Artefact |
| --- | --- | --- |
| **Relationship manager / credit officer** (acts on it today) | Why this borrower, why now, what to do | Counterfactual [S41] + ranked drivers + the deterministic headroom table |
| **Model validator / internal audit** (challenges it quarterly) | Conceptual soundness, limits, performance drift, effective challenge trail | Reliability diagram + Brier score + backtest + interventional-vs-observational SHAP comparison + model inventory entry [S1] |
| **External auditor / regulator** (asks a year later) | Replay: what was known, what was computed, under which covenant version | The decision record + OTel trace + covenant version + immutable event store [S2 §54(b)] |

### PS-17: is the deterministic carve-out a real advantage, or does it relocate the burden?

**Both — but the advantage is smaller than it first appears, and the relocation is where the interesting engineering is.**

**Why the advantage is smaller than it looks.** SR 26-2 applies to *banking organizations*, and its stated applicability is those "with over $30 billion in total assets" [S1]. PS-17 is an enterprise-wide commercial-operations problem, not a bank credit process. **PS-17 was never in scope of model risk management in the first place, so it cannot claim relief from it.** Any pitch that says "our SLA engine is exempt under SR 26-2" is claiming an exemption from a regime that never applied, and a bank CTO on the panel will notice immediately. Do not make that claim.

**The real, defensible version of the point.** The carve-out is evidence of what supervisors consider *low-risk by construction*, and that reasoning transfers to whatever assurance regime does bind PS-17 — the customer's internal audit, procurement security review, and contractual audit rights. The transferable principle:

> **A conclusion computed by a versioned deterministic rule over cited evidence is auditable by inspection. A conclusion produced by a model is auditable only by validation. Prefer the first wherever the domain permits — and in SLA/obligation evaluation, the domain almost always permits it.**

That is genuinely true of PS-17 and genuinely differentiating: service-credit calculation, breach determination against a threshold, notice-period arithmetic and renewal-date computation are all *contractually specified deterministic functions*. There is no legitimate reason for a model to produce any of them. **So PS-17's defensible architecture is: LLM does extraction-with-citation only (Z3); everything downstream of the extracted obligation is Z1.** No Z2 at all — PS-17 arguably has no statistical model anywhere, which is a cleaner story than PS-04 can tell.

**Where the burden goes instead — and this is the good part.** Once rules replace models, **the rules become the governed artefact**, and rule governance is exactly:

- rule **versioning** and **effective-dating** (which version governed this event, at this time?),
- rule **change control** (who approved v2, on what authority, when did it take effect, retroactively or prospectively?),
- rule **provenance** (which clause in which amendment produced this rule?),
- **re-evaluation on change** (what must be recomputed when a rule's effectivity changes?).

**That list is PS-17's brief, almost verbatim** — "represent late, corrected or conflicting versions without losing earlier evidence," and the National Finale inject itself. So the honest framing is not "we escaped governance" but:

> **"We converted model risk into configuration risk — and configuration risk is the thing this problem statement already requires us to solve. The amendment inject is a configuration-risk test, and we pass it with a versioned rule graph and automatic re-evaluation, not with a revalidated model."**

That is a stronger and more original claim than the exemption claim, and it makes the inject the proof rather than an obstacle.

### The unifying finding

**In both problem statements, the deterministic carve-out relocates risk to the same place: the point where text becomes rules.** For PS-04 that is covenant-definition extraction from the credit agreement; for PS-17 it is obligation and SLA-threshold extraction from the contract and its amendments. In both, an LLM reads a document and emits something that will subsequently be treated as authoritative and deterministic.

**Therefore the highest-value control in either system is the same one:** grounded, citation-verified extraction, typed as `AIInference`, gated at autonomy level **A2** with human confirmation against the highlighted source span, and incapable of authorising an action until confirmed. Everything else in this report — the provenance type system, the authority rule, the span tree that terminates on a source document, the hash chain over the evidence closure — exists to make *that one gate* trustworthy and inspectable.

PS-17 has more of this work, does it more visibly, and has an inject built specifically to stress it. **That is why it wins the lane.**

---

# PS-17: Contract Obligation, SLA & Commercial Leakage Monitor

## A. Explainability and audit

### A1. What "explainable" concretely means here, and what the regulator requires

PS-17 sits in **enterprise commercial operations**, not a regulated credit decision. **There is no EU AI Act Annex III entry for contract or SLA management** — the asymmetry against PS-04 set out in the head-to-head — and no prudential regulator writes rules for service credits. SR 26-2 / OCC Bulletin 2026-13 do not reach it either, since they apply to banking organizations [S1][S1b]; as the cross-cutting section argues, **PS-17 must not claim exemption from a regime that never applied to it.** **This is a genuine weakness of PS-17 in this sub-lane and should be conceded on stage rather than hidden.**

But that reframes rather than removes the requirement. For PS-17, "explainable" means something narrower and far more demonstrable:

> **A conclusion is explainable iff a reviewer can reach, in one click, (a) the exact source bytes it rests on, (b) the exact rule version applied, (c) the exact classification of every intermediate step as fact / inference / input / action / decision, and (d) proof that none of these were altered afterwards.**

That is an **evidence-chain** and **audit-replay** requirement, and it is exactly what the brief asks for: "reviewers can reconstruct what the system knew, why it acted and what changed afterward" (PS-17 §04).

**The applicable standard is not a financial regulation; it is a provenance standard.** **W3C PROV-O** (W3C Recommendation, 30 April 2013) [S3] defines the three starting-point classes — `prov:Entity` ("a physical, digital, conceptual, or other kind of thing with some fixed aspects"), `prov:Activity` ("something that occurs over a period of time and acts upon or with entities"), `prov:Agent` ("something that bears some form of responsibility for an activity taking place") — and the properties `wasGeneratedBy`, `wasDerivedFrom`, `wasAttributedTo`, `used`, `wasInformedBy`, `wasAssociatedWith`, `actedOnBehalfOf`, plus qualified terms `Revision`, `PrimarySource`, `Attribution`, `Association`, `Delegation`, `Plan`, `Role` [S3].

Two secondary hooks are worth one line each on a slide, because a CTO jury will recognise them:

- **EU AI Act Art. 12(1)**: "High-risk AI systems shall technically allow for the automatic recording of events (logs) over the lifetime of the system," with logging that provides traceability appropriate to purpose [S13]. PS-17 is almost certainly *not* high-risk under Annex III, but Art. 12 is the cleanest available articulation of "your system must log its own reasoning."
- **EU AI Act Art. 26(6)**: deployers must keep those logs for **"at least six months"** [S14]. Use it as the retention parameter rather than inventing one.

Positioning line: *"We are not in Annex III. We built to Art. 12 anyway, because our customer's auditor will ask the same question the regulator asks."*

### A2. Technique level: grounded citation, and the honest failure rate

PS-17's core extraction task is **clause → typed obligation, with a source span**. The two numbers that matter, and that no competing team will have:

**Extraction is not solved.** **CUAD** (Hendrycks, Burns, Chen, Ball; NeurIPS 2021) contains **510 contracts, 13,000+ expert annotations, 41 clause categories** [S15]. Best reported model, DeBERTa-xlarge: **47.8% AUPR** and **44.0% Precision @ 80% Recall** — against BERT-base at 8.2% [S15]. So a well-resourced benchmark effort gets under half precision at high recall on exactly PS-17's task. **This is the strongest possible argument for the provenance type system and the human gate: extraction is unreliable, therefore every extracted obligation must be typed as an inference, carry its source span, and be incapable of authorising an action on its own.** Turning a weakness into the architectural thesis is a very strong pitch move.

**Grounding does not fix it either.** The Stanford RegLab work is the citation to use:

- Dahl, Magesh, Suzgun & Ho, **"Large Legal Fictions: Profiling Legal Hallucinations in Large Language Models," *Journal of Legal Analysis* 16(1), 2024, pp. 64–93** — hallucination rates of **58%–88%** on verifiable legal questions across tested 2023 models, and models "struggle to predict their own hallucinations" [S16].
- Magesh, Surani, Dahl, Suzgun, Manning & Ho, **"Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools"** (2024) — the *retrieval-augmented commercial* tools (Lexis+ AI, Westlaw AI-Assisted Research, Ask Practical Law AI) **each hallucinate between 17% and 33% of the time**, and vendor "hallucination-free" claims are "overstated" [S17].

That second one is the killer slide for PS-17. **RAG reduces hallucination; it does not eliminate it. 17–33% residual, in shipped legal products, measured by Stanford.** Therefore citation-to-source is necessary but insufficient, and the system must additionally (i) verify each citation mechanically (does the cited span actually contain the asserted threshold?) and (ii) refuse to auto-act on unverified inferences.

**Verified-citation metric to put on the dashboard.** Borrow from **ALCE** (Gao, Yen, Yu & Chen, EMNLP 2023), the first benchmark for automatic LLM citation evaluation, which scores fluency, correctness and **citation quality**; even the best models "lack complete citation support 50% of the time" on ELI5 [S18]. Ship a live **citation-support rate** tile: % of asserted obligations whose cited span, re-read, entails the assertion under a separate verifier pass. A number that moves on stage beats a static architecture diagram.

### A3. The provenance type system — designed, and honestly assessed

The brief demands "explicit separation of recorded fact, AI inference, user input, automated action and human decision." Here is that as a first-class typed model.

#### The types

```
Assertion (abstract)
  id                : ULID
  valid_time        : [from, to)        -- when it is true of the world
  transaction_time  : [from, to)        -- when the system believed it   (bitemporal)
  derived_from      : [AssertionId]     -- transitive evidence edges
  supersedes        : AssertionId?
  status            : live | retracted | superseded
  trace_id, span_id : OTel linkage      -- see B

├─ RecordedFact
│    source        : SourceRef{ doc_id, sha256, page, char_span, retrieved_at, system_of_record }
│    -- an invoice line, an SLA measurement, a signed amendment PDF. Never produced by a model.
│
├─ AIInference
│    model_id, model_version, prompt_hash, temperature
│    evidence       : [AssertionId]     -- what it read
│    confidence     : float
│    verified_by    : VerificationRef?  -- NULL until mechanically or humanly checked
│
├─ UserInput
│    actor, role, entered_at
│    -- a human-supplied datum that is NOT a ruling ("the outage began 03:00")
│
├─ AutomatedAction
│    action_type, idempotency_key, attempted_at, outcome, external_ref
│    -- an effect the system caused in the world. Irreversible ones are never auto-authorised.
│
└─ HumanDecision
     actor, role, authority_scope, decided_at, rationale, decision
     -- an authorised ruling. The only type that can terminate a legal-interpretation question.
```

#### Mapping to PROV-O (so it is a standard, not a schema you invented)

| Aegis type | PROV-O |
| --- | --- |
| `Assertion` | `prov:Entity` |
| the derivation step that produced it | `prov:Activity` |
| `derived_from` | `prov:wasDerivedFrom` |
| `RecordedFact.source` | `prov:hadPrimarySource` (qualified `prov:PrimarySource`) |
| `AIInference` | `prov:wasAttributedTo` a `prov:SoftwareAgent`, which `prov:actedOnBehalfOf` an org |
| `HumanDecision`, `UserInput` | `prov:wasAttributedTo` a `prov:Person` |
| `AutomatedAction` | `prov:Activity` with `prov:wasAssociatedWith` + `prov:Plan` (the rule version) |
| `supersedes` | qualified `prov:Revision` |
| a case's whole graph | `prov:Bundle` |

Consequence: `GET /case/{id}/provenance.jsonld` emits **conformant PROV-O JSON-LD**. On stage: *"this is not our audit format; it is the W3C's, and it has been a Recommendation since 2013."* [S3]

#### The authority rule — what the type system *enables*

This is the part that makes it engineering rather than metadata.

```
authorised_level(action) =
    min(
      declared_level(action),                       -- the ladder level from section C
      demote_if(evidence_closure(action) contains
                  any AIInference a with a.verified_by = NULL),
      demote_if(evidence_closure(action) contains
                  any Assertion with status != live),
      block_if(effective_rule_version(action) is ambiguous over the evidence window),
      block_if(action is irreversible and no HumanDecision is in the closure)
    )
```

In words: **an unverified AI inference anywhere in the transitive evidence closure costs the action one autonomy level; a retracted assertion costs another; an ambiguous rule effectivity blocks entirely; and no irreversible action is ever authorised without a `HumanDecision` in its closure.** Call the mechanism **provenance-typed authority**.

#### What the amendment inject does to it — this is the demo

The National Finale inject: *"A contract amendment changes an SLA threshold after potential breaches were flagged. The system must re-evaluate each event using the correct effective version."*

Under this model the amendment is a single new `RecordedFact` with a `valid_time` that starts in the past. Ingesting it:

1. Changes `effective_rule_version(t)` for a window of event times.
2. Every `AIInference` and derived conclusion whose closure depends on the old version flips `status → retracted` (not deleted — the brief forbids losing earlier evidence).
3. `authorised_level` is recomputed. Pending actions that were at "act after veto window" collapse to "recommend only."
4. On screen: rows go amber, the auto-send affordances grey out, a re-evaluation queue populates, and the audit log gets a new signed entry.

**No other part of either problem statement produces a change that is simultaneously this correct, this visible, and this hard to fake.**

#### Honest differentiation assessment

- **Not novel:** typed provenance itself. PROV-O has been a W3C Recommendation since 2013 [S3]; database provenance and event sourcing are decades old. Bitemporal state is standard practice.
- **Not novel:** hash-chained audit logs. See D — this is crowded territory.
- **Plausibly novel and claimable:** the **conjunction** — a typed provenance closure that is *evaluated at runtime as an input to an authority/permission decision*, keyed to versioned rule effectivity, such that a change in rule effectivity automatically demotes the autonomy level of pending actions. The claim should be drafted around *provenance-type-conditioned action authorisation*, **not** around Merkle trees.
- **Open risk:** I could not run a prior-art search (the session's web-search budget was exhausted; see Risks). Treat the novelty assessment as an engineering judgement, not a freedom-to-operate opinion.

**Verdict on A for PS-17: strong on demonstrability and on brief-alignment, weak on binding regulation. 9/10.**

---

## B. Observability — OpenTelemetry over the agent loop

### B1. State of the standard (verified today)

- The GenAI semantic conventions **moved out of** `open-telemetry/semantic-conventions` into a dedicated repo, `open-telemetry/semantic-conventions-genai`. The old `opentelemetry.io/docs/specs/semconv/gen-ai/` page now says only "GenAI semantic conventions have moved," and every `gen_ai.*` entry in the old attribute registry is marked **Deprecated** at that location [S19].
- The new repo was **created 2026-05-05**, was last pushed **2026-08-27**, and has **zero tagged releases** (verified via GitHub API today). The main semantic-conventions repo is at **v1.44.0 (2026-08-04)**.
- The agent-spans document carries **Document Status: Development** [S4].

**Implication for the pitch:** do not claim "OTel-compliant." Claim **"instrumented to the OpenTelemetry GenAI conventions as they stand on `main`, dual-emitted with OpenInference for backend compatibility, because the GenAI conventions have no 1.0 yet."** Precision here reads as seniority.

### B2. The span vocabulary you actually get

From the GenAI agent-spans document [S4]:

| Span | Required attributes | Notable conditional/recommended |
| --- | --- | --- |
| `create_agent {gen_ai.agent.name}` | `gen_ai.operation.name=create_agent`, `gen_ai.provider.name` | `gen_ai.agent.id`, `gen_ai.agent.version`, `gen_ai.agent.description`, `gen_ai.request.model`, `error.type` |
| `invoke_agent {gen_ai.agent.name}` | `gen_ai.operation.name=invoke_agent`, `gen_ai.provider.name` | `gen_ai.conversation.id`, **`gen_ai.data_source.id`**, `gen_ai.output.type`, `gen_ai.request.model`, `gen_ai.response.finish_reasons`, `gen_ai.usage.*` |
| `execute_tool` | — | tool name / call id |
| `chat` | model call attributes | `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.usage.input_tokens` |

Registered `gen_ai.operation.name` values include `chat`, `create_agent`, `embeddings`, `execute_tool`, `generate_content`, `invoke_agent`, `invoke_workflow`, `retrieval`, `text_completion` [S19].

**OpenInference** [S5] (Arize, Apache-2.0, consumable by any OTel backend) requires `openinference.span.kind` on every span, with kinds `LLM`, `CHAIN`, `RETRIEVER`, `TOOL`, `EMBEDDING`, `AGENT`. `RETRIEVER` is the one that matters most here: it is the span type that carries **retrieved documents as structured attributes**, which is precisely where PS-17's source provenance lives. The two conventions are expected to converge as the GenAI spec stabilises [S5]. **OpenLLMetry / Traceloop** is the third option — standard OTel instrumentations for LLM providers and vector DBs plus a Traceloop SDK, Apache-2.0, and its semantic conventions were upstreamed into OpenTelemetry [S20].

### B3. The PS-17 span tree

```
case.evaluate                                     [root]
│  case.id, contract.id, event.id
│  aegis.rule.version.effective_at, aegis.as_of
│
├─ invoke_agent aegis.obligation_monitor
│  │  gen_ai.operation.name=invoke_agent, gen_ai.agent.name, gen_ai.conversation.id
│  │
│  ├─ execute_tool resolve_effective_version
│  │     contract.amendment.id, effective_from, prior_version, resolved_version
│  │
│  ├─ RETRIEVER  evidence.select                 [openinference.span.kind=RETRIEVER]
│  │     gen_ai.data_source.id
│  │     aegis.assertion.ids[]
│  │     span events, one per document:
│  │        doc.id | doc.sha256 | page | char_span | retrieved_at
│  │
│  ├─ chat  (obligation extraction)
│  │     gen_ai.request.model, gen_ai.response.model, gen_ai.usage.*
│  │     aegis.assertion.emitted = AIInference:<id>
│  │     aegis.confidence, aegis.prompt_hash
│  │
│  ├─ execute_tool verify_citation
│  │     aegis.citation.entailed = true|false     <- the ALCE-style check
│  │
│  ├─ execute_tool compute_service_credit         [deterministic]
│  │     rule.id, rule.version, inputs_hash, result_amount
│  │
│  └─ execute_tool authority.check
│        aegis.autonomy.requested = A4
│        aegis.autonomy.granted   = A2
│        aegis.autonomy.demotion_reason = unverified_inference:<assertion_id>
│
└─ audit.append
      aegis.log.leaf_hash, aegis.log.tree_size, aegis.log.root_hash
```

### B4. The click-path a judge follows — "conclusion → spans → source bytes → proof"

1. Judge clicks a flagged breach on screen.
2. The UI holds `assertion_id`; the assertion row holds `trace_id` + `span_id`.
3. Deep-link opens **Phoenix** (or Jaeger) on that exact span, showing the reasoning subtree: what was retrieved, what the model said, what the deterministic credit calculator computed, and what the authority check granted.
4. The `RETRIEVER` span's per-document events carry `doc.sha256` + `char_span`; a second click opens the PDF at that page with the span highlighted.
5. A third click hits `GET /proof/{decision_id}` (section D) and shows a green inclusion proof: *this decision was made on exactly these inputs.*

**Then the judge triggers the amendment inject and repeats.** Two traces side by side over the same event, with `aegis.rule.version.effective_at` differing and `authority.check` visibly demoting. That is the strongest five seconds available in either problem statement.

### B5. Windows-friendly local backend — verified

| Backend | Bare Windows, no Docker? | Evidence | Verdict |
| --- | --- | --- | --- |
| **Arize Phoenix** | **Yes** | `arize-phoenix` 20.4.0 publishes a pure-Python `arize_phoenix-20.4.0-py3-none-any.whl`; runs in-process via `px.launch_app()`, SQLite-backed, UI on `localhost:6006`, OTLP/gRPC 4317 and OTLP/HTTP at `/v1/traces` [S7] | **Primary.** Renders LLM/retriever/tool spans natively; shows prompts, retrieved documents and token usage as first-class UI, which generic tracers do not. |
| **Jaeger** | **Yes** | v2.20.0 release assets include **`jaeger-2.20.0-windows-amd64.zip`** (verified via GitHub API); official Windows-service deployment doc via `nssm` [S6] | **Secondary/backstop.** Use it to prove "this is standard OTel, not a vendor format." |
| **Grafana Tempo** | Unofficial | Maintainers: "You should be able to run this on windows, but many don't run it this way so it may have some unknown issues"; needs the HTTP port changed off 80 [S9] | Avoid. |
| **Langfuse** | **No** | v3 self-hosting requires six services — langfuse-web, langfuse-worker, PostgreSQL, ClickHouse, Redis/Valkey, S3/MinIO — with a recommended 4 cores / 16 GiB / ~100 GiB [S8] | Out under the no-Docker constraint. |

**Recommendation: Phoenix in-process + Jaeger .exe on the side.** Both are `pip install` / unzip. Two backends off one OTLP exporter costs ~10 lines and buys a demo-failure fallback.

**Verdict on B for PS-17: 9/10.**

---

## C. The human–AI autonomy model

### C1. Frameworks — pick, don't invent

| Framework | What it gives you | Citation |
| --- | --- | --- |
| **Sheridan & Verplank (1978) 10 levels**, reproduced verbatim as Table I of Parasuraman, Sheridan & Wickens (2000) | The canonical ordinal scale of decision/action autonomy | [S10] |
| **Parasuraman, Sheridan & Wickens (2000)** four-stage model | *Types* as well as levels: automation applies independently to **information acquisition, information analysis, decision selection, action implementation** | [S10] |
| **SAE J3016** | Rhetorical analogue only ("Level 3 for contracts"). Use the *shape*, do not quote level definitions — see Risks | [S21] |
| **DeepMind, Morris et al., "Levels of AGI" (2023), Table 2** | Six *interaction-paradigm* levels: No AI / Tool / Consultant / Collaborator / Expert / Agent, each with "example risks introduced" | [S22] |
| **Feng, McDonald & Zhang, "Levels of Autonomy for AI Agents" (arXiv 2506.12469, Jun 2025)** | Five levels named by the *user's* role: Operator, Collaborator, Consultant, Approver, Observer; treats autonomy as a design choice independent of capability | [S23] |
| **NIST AI RMF 1.0 (AI 100-1, Jan 2023)** | Governance framing: `MAP 3.5` "Processes for human oversight are defined, assessed, and documented"; `MEASURE 2.9` "The AI model is explained, validated, and documented…"; `MAP 2.2` documents knowledge limits "and how system output may be utilized and overseen by humans"; Appendix C: "Human-AI configurations can span from fully autonomous to fully manual" | [S24] |
| **EU AI Act Art. 14** | The legal hook. 14(3): oversight measures proportionate to "risks, **autonomy level**, and context"; 14(4)(d) the human can "disregard, override or reverse the output"; 14(4)(e) a **'stop' button** bringing the system "to a halt in a safe state" | [S11] |

**The verbatim Sheridan scale** [S10, Table I], which belongs on the slide as an image:

> **HIGH** 10. The computer decides everything, acts autonomously, ignoring the human.
> 9. informs the human only if it, the computer, decides to
> 8. informs the human only if asked, or
> 7. executes automatically, then necessarily informs the human, and
> 6. allows the human a restricted time to veto before automatic execution, or
> 5. executes that suggestion if the human approves, or
> 4. suggests one alternative
> 3. narrows the selection down to a few, or
> 2. The computer offers a complete set of decision/action alternatives, or
> **LOW** 1. The computer offers no assistance: human must take all decisions and actions.

### C2. The 5-level ladder (shared spine, per-problem mapping)

Defined as a **collapse of Sheridan 1–7**, with 8–10 excluded by construction.

| Level | Name | Sheridan | Meaning |
| --- | --- | --- | --- |
| **A0** | **Observe** | 1 | System surfaces raw evidence. No computed conclusion. |
| **A1** | **Analyse** | 2–3 | System computes and narrows; presents a *set* of candidate conclusions with evidence. Human selects. |
| **A2** | **Recommend** | 4 | System asserts one conclusion with confidence, drivers and a counterfactual. Human decides. |
| **A3** | **Act on approval** | 5 | System executes only on an explicit `HumanDecision`, which is recorded as a typed assertion. |
| **A4** | **Act with veto window, then notify** | 6–7 | System executes after a bounded veto window and necessarily informs. **Reserved for reversible, idempotent, low-materiality actions.** |
| **H** | **Human-owned** | — | Off the ladder. The system may prepare, but may never execute. |

> **The truncation is the argument.** Sheridan 8 ("informs the human only if asked"), 9 ("only if it, the computer, decides to") and 10 ("ignoring the human") are structurally unavailable: every action at every level emits a typed `AutomatedAction` assertion and an OTel span. There is no code path that acts silently. Say this out loud — it converts an absence into a designed control.

### C3. PS-17 mapping (every step of the brief's process flow)

| # | Action | Level | Justification |
| --- | --- | --- | --- |
| 1 | Contract / amendment ingested, normalised, versioned | **A4** | Reversible, idempotent, internal. Notifies on ingest. |
| 2a | Extract obligation — *informational* (reporting, contact details) | **A4** | Low materiality; wrong extraction costs nothing irreversible. |
| 2b | Extract obligation — *material* (credit-bearing, notice-bearing, termination-triggering) | **A2** | CUAD: 44% precision @ 80% recall [S15]. An unverified `AIInference` cannot found a credit claim. |
| 3 | Map owners | **A4** | Internal routing; reversible. |
| 4 | Monitor evidence (SLA records, invoices, service events, treasury of credits) | **A4** | Pure ingestion. |
| 5a | Detect exception / flag *potential* breach | **A2** | A flag is an internal hypothesis, not a legal interpretation — so A2 is permitted. It must never be A3+: asserting a breach *is* interpretation. |
| 5b | Detect contradiction / staleness / duplication and raise uncertainty | **A4** | Raising doubt is always safe. See the asymmetry rule below. |
| 6a | Compute service credit (deterministic function of rule version × measured evidence) | **A4** *as a computation* | Arithmetic over a versioned rule; fully replayable. |
| 6b | Assert the credit as *owed* | **A2** | Material commercial quantum. |
| 7 | Prepare action — draft notice, draft credit memo | **A4** *to produce an inert artefact*; **A3** to attach it to a case for approval | Drafting has no external effect. |
| 8 | Commercial review | **A0/A2** (system supports) | Human forum. |
| **—** | **Send contractual notice** | **H** | Brief: "contractual notice … remain[s] human-owned." |
| **—** | **Agree material commercial settlement** | **H** | Brief: "material commercial settlement decisions remain human-owned." |
| **—** | **Resolve a legal-interpretation question** | **H** | Brief: "Legal interpretation … remain[s] human-owned." |
| 9a | **Re-evaluate on amendment (the inject)** | **A4** | **Critical and non-obvious — see below.** |
| 9b | Retract / reopen a conclusion invalidated by new evidence | **A4** | Same asymmetry. |
| 9c | Outcome tracked, deadlines advanced | **A4** | Internal state. |

#### The asymmetry rule — the sharpest single idea in this lane

> **Autonomy to *retract* is safe. Autonomy to *assert* is not.**

Retraction, reopening, raising uncertainty, and demoting an action's own authority all move the system toward *more* human involvement. They can therefore run at A4 — fully autonomous, notify after — without any risk of the system over-reaching. Assertion, quantification and external action move toward *less* human involvement and are capped at A2/A3/H.

This resolves the inject cleanly and defensibly: the amendment triggers **fully autonomous** re-evaluation (A4) because re-evaluation only ever *withdraws* conclusions and *lowers* authority. Nothing is auto-asserted under the new threshold; every affected case returns to A2 for a human. **A CTO will recognise this as a real safety invariant rather than a policy table.**

### C4. Is the human-owned boundary an advantage or a limitation?

**Advantage, clearly — but only if you build the boundary as a mechanism instead of a disclaimer.**

- *Limitation view:* the system cannot close the loop, so business impact is capped at "prepared, not executed," and a jury may read that as a half-built product.
- *Advantage view, which is stronger:* the brief hands you a **principled, externally-specified gate**. You did not choose where to stop for convenience — the problem statement did. That lets you (i) build the gate as an enforced type-system property (`AutomatedAction` of type `notice_send` is unconstructible without a `HumanDecision` with `authority_scope ⊇ notice`), (ii) prove it live by attempting the action and watching it refuse, and (iii) claim EU AI Act Art. 14(4)(d)/(e) conformance shape — override, and a stop that lands in a safe state [S11].
- **The demo move:** put a **"Send notice"** button on screen. A judge presses it. The system refuses, and shows *why* — the authority check span, the missing `HumanDecision`, the unverified inference in the closure. **A system that visibly refuses to act is a far better trust demo than a system that acts.** Almost no team will do this.

**Verdict on C for PS-17: 9/10.**

---

## D. Cryptographic / blockchain provenance

### D1. What actually runs on bare Windows without Docker (verified against release artefacts)

| Technology | Runs on bare Windows? | Evidence |
| --- | --- | --- |
| **Hand-rolled RFC 9162 Merkle log** in Python over SQLite | **Yes** | Pure stdlib `hashlib`; `cryptography` 50.0.1 ships `cp311-abi3` Windows wheels for Ed25519 signing |
| **`pymerkle` 6.1.0** | **Yes** | Pure-Python `py3-none-any` wheel [S25] |
| **RFC 3161 timestamping** (`rfc3161ng` 2.1.3) | **Yes, but** | Pure-Python wheel; **requires network access to a TSA at demo time** — pre-fetch a token as fallback |
| **Jaeger / Phoenix** (for the linkage, see B) | Yes | [S6][S7] |
| **immudb** | **Partly** | v1.10.0 (Oct 2025) publishes `immudb-v1.10.0-windows-amd64.exe`; **v1.11.0 and v1.11.1 (Apr/Jun 2026) publish *no* release binaries at all** (verified via GitHub API). Licence is **Business Source License 1.1** — source-available, not open source [S26] |
| **Trillian** | **No** | Requires MySQL/MariaDB for the storage layer, Go 1.25+, a multi-process personality + log-server + signer topology, and is **in maintenance mode** [S27] |
| **AWS QLDB** | **Discontinued — design reference only** | `https://aws.amazon.com/qldb/` now **301-redirects to `aws.amazon.com/rds/aurora/`**, and `docs.aws.amazon.com/qldb/...` returns **404** (both verified today). Cite QLDB's journal→digest→Merkle-proof model as *design inspiration*, never as a dependency |
| **Anvil (Foundry)** | **Yes, via the precompiled zip** | Foundry **v1.8.1 (2026-08-28)** publishes **`foundry_v1.8.1_win32_amd64.zip`** (verified via GitHub API). **Important nuance:** the `foundryup` installer "requires Git Bash or WSL. PowerShell and Command Prompt are not supported" [S28] — so **download and unzip the release, do not run foundryup** |
| **py-evm / eth-tester** (in-process EVM) | **Yes** | `py-evm` 0.12.1b1 and `eth-tester` 0.14.0b1 are pure-Python `py3-none-any` wheels, MIT; `web3` 7.16.0 likewise [S25] |
| **Hardhat** | Yes (npm/Node) | Standard Node install; heavier than Anvil |
| **A real chain node** (geth, besu, a validator) | **No** | Out under the constraints. Do not attempt. |

**Standards to build to, so the log is a specification rather than a bespoke chain:**

- **RFC 9162, "Certificate Transparency Version 2.0"** (Dec 2021, Experimental, **obsoletes RFC 6962**) — defines the Merkle Tree Hash with domain separation (`MTH({d[0]}) = HASH(0x00 || d[0])`, `MTH(D_n) = HASH(0x01 || MTH(D[0:k]) || MTH(D[k:n]))`), **Merkle inclusion proofs** ("the shortest list of additional nodes in the Merkle Tree required to compute the Merkle Tree Hash for that tree"), **Merkle consistency proofs** which "prove the append-only property of the tree," and the **Signed Tree Head** [S29].
- **RFC 3161, "Internet X.509 PKI Time-Stamp Protocol"** (Aug 2001, Standards Track) — a TSA attests that "a particular datum existed at a specific point in time"; the `TimeStampToken` binds `messageImprint` (the hash), `genTime`, a serial number, a policy identifier and the TSA's signature [S30].

Using the RFC 9162 leaf/node prefixes rather than a naive `sha256(prev || payload)` chain is worth doing: it gives you real inclusion *and consistency* proofs, and lets you say "we implemented the Certificate Transparency Merkle structure" instead of "we made a hash chain."

### D2. Where is it genuinely load-bearing? (honest evaluation)

The uncomfortable truth first: **in a single-party system, a hash chain the operator both writes and verifies proves nothing to a sceptic.** It becomes load-bearing only under one of three conditions:

1. **Two parties who do not fully trust each other need the same record.**
2. **An external witness co-signs the tree head**, so rewriting requires collusion.
3. **The dispute is with your own past self** — an auditor asking "prove this decision was made on exactly these inputs, and that they were not edited afterwards."

**PS-17 satisfies (1) by construction, and (3) is its explicit brief requirement.** Customer and supplier are adversarial about whether a breach occurred, which SLA version governed the event, and what credit is owed. That is textbook territory for an append-only, inclusion-provable log.

**The concrete construction.** The Merkle leaf is not "a log line" — it is a canonicalised **`DecisionRecord`**:

```json
{
  "decision_id": "...",
  "action": "assert_breach | compute_credit | prepare_notice",
  "evidence_closure": [ {"assertion_id": "...", "type": "RecordedFact", "content_sha256": "..."},
                        {"assertion_id": "...", "type": "AIInference",  "content_sha256": "..."} ],
  "rule_id": "SLA-UPTIME", "rule_version": "v1", "rule_effective_from": "2026-01-01",
  "model_id": "...", "model_version": "...", "prompt_hash": "...",
  "autonomy_level_requested": "A4", "autonomy_level_granted": "A2",
  "actor": "system|user:...", "decided_at": "..."
}
```

`leaf = SHA256(0x00 || canonical_json(DecisionRecord))`. **Because the leaf covers the evidence set *and* the rule version *and* the model version, an auditor can replay the decision and verify it was made on exactly the stated inputs** — which is the requirement, restated.

**The amendment inject is the moment it pays off, and it is a genuinely elegant fit.** The amendment retroactively changes the SLA threshold. Without a chain, "we re-evaluated everything correctly" is an assertion. With one:

- **Inclusion proof** on a pre-amendment decision: this conclusion *was* computed under `rule_version=v1`, and that record has not changed.
- **Consistency proof** between `STH@t₁` (pre-amendment) and `STH@t₂` (post-amendment): the tree at t₁ is a **prefix** of the tree at t₂. Nothing was rewritten; the v1 conclusions were *retracted by appending*, never edited away.

That is the difference between "we handle amendments" and "we can prove we handled amendments," and it is exactly the distinction a CTO jury is trained to hear. **Load-bearing: yes, for PS-17.**

**PS-04, evaluated the same way: mostly decorative.** The bank owns the model, the data, the log and the auditor. Condition (1) is absent. Condition (3) applies — SR 26-2's outcomes-analysis and ongoing-monitoring expectations [S1] do want reproducible decisions — but an ordinary immutable, versioned event store satisfies that at a fraction of the cost, and no PS-04 stakeholder is adversarial to the log's operator. **Honest recommendation for PS-04: build the immutable event store (which you need anyway), skip the Merkle proofs, and do not put crypto on a slide.** Saying that out loud to a jury — *"we considered a hash-chained ledger here and concluded it would be decoration"* — scores better than shipping it.

### D3. Smart contracts as executable terms

**PS-17 — genuine substance.** An SLA service-credit schedule *is* a deterministic function: measured uptime → credit percentage. Encoding it as `SLATerms.creditBps(uint256 uptimeBps) → uint256`, deployed at a version-pinned address, is a faithful and *publicly verifiable* representation of the commercial term. The amendment deploys `SLATerms_v2` at a new address; the effective-version resolver selects the address by event date; the number on screen is *read back from the chain*, not computed by the app. The classic "smart contract" pun finally has a referent.

Legal backing worth one line: the **Law Commission of England and Wales, *Smart legal contracts: Advice to Government* (25 November 2021)**, concluded that "the current legal framework in England and Wales is clearly able to facilitate and support the use of smart legal contracts" without statutory reform, and that existing principles apply "in much the same way as they do to traditional contracts" [S12]. So a hybrid natural-language-plus-code SLA is not a novelty item — it is a recognised instrument.

**Honest caveat to state on stage:** running it on Anvil, an ephemeral dev chain, makes it a *demonstration of the representation*, not a production settlement rail. Frame it as *"the commercial term compiled to a versioned, independently-executable artefact"* — the value is determinism and version-pinning, not decentralisation. Overclaiming here is the fastest way to lose a CTO jury.

**PS-04 — don't.** Covenant thresholds as on-chain conditions fails on two counts. First, the **oracle problem** eats the entire value: the inputs (borrower financials, utilisation, treasury flows) are private, off-chain and unverifiable by the chain, so the on-chain condition is only as trustworthy as the party that posted the input — which returns you to trusting the bank, which you already did. Second, no bank will put borrower leverage ratios anywhere near a ledger, even a private one. **Recommendation: omit entirely.**

### D4. The 1-day and 2-day versions (so it can be cut)

**Day 1 — the load-bearing minimum. Ship this or ship nothing.**

- Append-only Merkle log in SQLite, RFC 9162 hashing (`0x00` leaf / `0x01` node prefixes) [S29]. ~150 lines; hand-roll it rather than depending on `pymerkle`, both for the claim story and to remove a dependency.
- Leaf = canonical `DecisionRecord` (D2) covering **evidence closure + rule version + model version**.
- Ed25519 Signed Tree Head every *N* appends, via `cryptography` (Windows wheels available).
- Two endpoints: `GET /proof/{decision_id}` → inclusion proof; `POST /verify` → recompute.
- One UI panel: green **"Verified — decided on exactly these inputs"** badge, plus a **"Tamper"** button that mutates a stored evidence row and turns it red with the failing node highlighted.
- Effort: ~1 developer-day. Buys the entire "auditor can replay" claim.

**Day 2 — the additions that make the inject land.**

- **Consistency proofs** across tree heads. Show `STH@t₁` (pre-amendment) and `STH@t₂` (post) and prove prefix-ness: *nothing was rewritten, only appended.* **This is the highest-value single item on day 2** — it is the proof that the inject was handled honestly.
- **A second witness process** that independently co-signs tree heads. Converts "trust the operator" into "collude with two." Cheap, and it is the correct answer to the sharpest question a CTO will ask.
- **`SLATerms_v1` / `SLATerms_v2` on Anvil** (from `foundry_v1.8.1_win32_amd64.zip`, unzipped — *not* foundryup [S28]), with `py-evm`/`eth-tester` as the in-process fallback if Anvil misbehaves on the demo machine [S25]. Read the credit number back from the chain.
- **RFC 3161 timestamp** on each STH from a public TSA [S30] — **optional, network-dependent**; pre-fetch a token before the demo.

**Cut order (last thing standing on the left):** RFC 3161 → smart contract → second witness → consistency proofs → **[keep] inclusion proofs + Merkle log**.

**Verdict on D for PS-17: 8/10.**

---

# PS-04: AI-Powered Dynamic Covenant Monitoring & Early Warning

## A. Explainability and audit

### A1. What the regulator actually requires — the binding stack

This is PS-04's strongest sub-lane by a wide margin, and the citations below are the ones to put on a slide.

#### (i) United States — SR 26-2 / OCC Bulletin 2026-13, **not** SR 11-7

**The headline finding of this entire report.** SR letter **26-2**, dated **17 April 2026**, transmits interagency (Fed / OCC / FDIC) **"Revised Guidance on Model Risk Management,"** which "supersedes and replaces SR letter 11-7, *Guidance on Model Risk Management* (issued April 4, 2011) and SR letter 21-8" [S1, cover letter]. Applicability: "most relevant to banking organizations with over $30 billion in total assets" [S1]. The OCC carries the same guidance as **OCC Bulletin 2026-13, "Model Risk Management: Revised Guidance," 17 April 2026**, which rescinds **OCC Bulletin 2011-12** [S1b]. Both verified independently against the issuing agencies' own pages.

**Cite the pair, not SR 11-7.** Quoting SR 11-7 in front of a bank CTO in September 2026 is a withdrawn-authority error, and it is exactly the kind of detail that separates a team that read the source from a team that read a blog post.

What it says that matters here:

- **Scope carve-out (footnote 3, p.3), verbatim:** *"Generative AI and agentic AI models are novel and rapidly evolving. As such, they are not within the scope of this guidance. Nonetheless, a banking organization's risk management and governance practices should guide the determination of appropriate governance and controls for any tools, processes, or systems not covered in this document. However, the principles described in this guidance apply to traditional statistical and quantitative models and non-generative, non-agentic AI models."* [S1]
- **"Model" definition excludes deterministic logic (p.3):** *"The term 'model' in this guidance excludes simple arithmetic calculations, such as those found within spreadsheets, as well as deterministic rule-based processes and software where there are no statistical, economic, or financial theories underpinning their design or use."* [S1]
- **Conceptual soundness names interpretability (p.8):** *"While evaluating theoretical construction may be important for some models, other assessments—such as interpretability measures or benchmarking to other models—may be more practical for other models."* [S1]
- **"Effective challenge" (p.5):** "critical analysis conducted by objective experts who evaluate model risk and effect appropriate changes throughout the model lifecycle" [S1].
- **Outcomes analysis (p.9):** compares model outputs to real-world outcomes; "When a model's design relies substantially on expert judgment, quantitative outcomes analysis helps to evaluate the quality of that judgment" [S1].
- **Model inventory and documentation (p.11):** an effective inventory carries "sufficient information to understand model risks… at the individual and aggregate levels"; documentation supports "the tracking of recommendations, responses, and exceptions" [S1].

**How to use it.** Two moves, both strong:

1. **The gap move.** Don't claim compliance — claim you closed a gap the agencies explicitly left open. *"The current US model-risk guidance is four months old and its footnote 3 puts agentic AI out of scope. Here is the governance we built for the class the regulators haven't reached yet: the autonomy ladder, the effective-challenge queue, the outcomes-analysis harness, the model inventory entry with an OTel trace link."*
2. **The scope-reduction move.** Architect so the **deterministic covenant engine** provably falls outside the "model" definition, leaving only the 30/60/90 forecaster inside it. That is a design decision with a regulatory payoff, which is exactly the sort of thing a CTO jury rewards.

#### (ii) Europe — EBA Guidelines on loan origination and monitoring (EBA/GL/2020/06), applicable from 30 June 2021

**§54 (verbatim, abridged to the operative list):** *"When using automated models for creditworthiness assessment and credit decision-making, institutions should understand the models used, and their methodology, input data, assumptions, limitations and outputs, and should have in place: (a) internal policies and procedures detecting and preventing bias and ensuring the quality of the input data; **(b) measures to ensure the traceability, auditability, and robustness and resilience of the inputs and outputs;** (c) internal policies and procedures ensuring that the quality of the model output is regularly assessed… including backtesting the performance of the model; (d) control mechanisms, model overrides and escalation procedures within the regular credit decision-making framework, including qualitative approaches, qualitative risk assessment tools (including expert judgement and critical analysis) and quantitative limits."* [S2]

**§55:** documentation must cover methodology, assumptions, data inputs, bias detection, **and** "the use of model outputs in the decision-making process and the monitoring of these automated decisions on the overall quality of the portfolio" [S2].

§54(b) — **"traceability, auditability"** — is your direct authority for the whole of section B. §54(d) — **"model overrides and escalation procedures"** — is your direct authority for section C.

**And then EBA writes PS-04's spec outright.** §267: *"institutions should monitor borrowers' adherence to the covenants agreed in the credit agreements. The borrower's adherence to covenants, as well as the timely delivery of covenant compliance certificates… should be utilised as **early warning tools**. Early detection of deviations is key… The ongoing monitoring of financial covenants should include all relevant ratios specified in the covenants (e.g. net debt/EBITDA, interest coverage ratio, debt service coverage ratio (DSCR))."* [S2]

§269–272 then require quantitative **and qualitative** EWIs "supported by an appropriate IT and data infrastructure," with "defined trigger levels," "assigned escalation procedures, including assigned responsibilities for the follow-up actions," selection of exposures for "special monitoring — a **watch list**," and watch-list reports "regularly reviewed by the head of the risk management function… and the management body" [S2]. §274 lists the deterioration signals — negative macroeconomic events affecting an industry or borrower; "a significant increase in debt levels or significant increases in debt service ratios"; "a significant drop in turnover or, in general, in recurring cash flow (including the loss of a major contract/client/tenant)"; margin narrowing; earnings deviation from forecast; rating decline; "a worsening in financing conditions"; business slowdown [S2].

Every one of those maps onto PS-04's "Early-warning intelligence" bullet list. **Use §274 as your synthetic-signal taxonomy — the regulator's list, not yours.** And §273: when actions involve contacting the borrower, institutions "should have regard to their individual circumstances" — which is your authority for keeping borrower contact at level H.

#### (iii) EU AI Act — creditworthiness is Annex III high-risk

**Annex III, point 5(b), verbatim:** *"AI systems intended to be used to evaluate the creditworthiness of natural persons or establish their credit score, with the exception of AI systems used for the purpose of detecting financial fraud."* [S31] Annex III is the list referred to by **Article 6(2)**.

**Important nuance most teams will get wrong:** Annex III 5(b) is scoped to **natural persons**. PS-04 is *commercial* lending — corporate borrowers and facilities. **On a literal reading, PS-04's core use case sits outside Annex III 5(b).** Saying this precisely, and then saying *"we built to the Annex III control set anyway, because sole-trader and personal-guarantee exposures fall inside it and because our customers' auditors will not draw the line as finely as the Act does,"* is a much better answer than the reflexive "credit scoring is high-risk, therefore we're high-risk."

The relevant obligations if you do land in scope:

- **Art. 12(1):** "High-risk AI systems shall technically allow for the automatic recording of events (logs) over the lifetime of the system," with traceability appropriate to purpose, covering risk situations, substantial modifications, post-market monitoring and operation [S13].
- **Art. 14(1), (3), (4):** effective human oversight, proportionate to "risks, **autonomy level**, and context"; the overseer must understand capacities and limitations, "be aware of… automation bias," "correctly interpret the… output," "decide… not to use… or to otherwise disregard, override or reverse the output," and "interrupt the system through a 'stop' button or a similar procedure that allows the system to come to a halt in a safe state" [S11].
- **Art. 26(2):** deployers assign oversight "to natural persons who have the necessary competence, training and authority." **Art. 26(6):** deployers keep logs "for at least six months, unless provided otherwise." **Art. 26(5):** for financial institutions already subject to internal-governance requirements under EU financial services law, "the monitoring obligation shall be deemed to be fulfilled by complying with the rules on internal governance arrangements" [S14]. That last one is a nice, non-obvious detail: **banks get a partial equivalence route.**
- **Art. 86:** an affected person subject to a decision based on an Annex III high-risk system's output, producing legal or similarly significant effects, "shall have the right to obtain from the deployer clear and meaningful explanations of the role of the AI system in the decision-making procedure and the main elements of the decision taken" [S32].

Art. 86 is your **counterfactual explanation** requirement in all but name — see A2.

> **Date caution.** A secondary source states standalone Annex III obligations apply from **2 December 2027** [S33]. I could **not** verify this against the consolidated Article 113 text or against the 2025–26 "Digital Omnibus" amendment process. **Treat the date as unconfirmed and do not put a specific date on a slide.**

#### (iv) India — RBI FREE-AI

The RBI's **FREE-AI Committee Report ("Framework for Responsible and Ethical Enablement of Artificial Intelligence")** was released **13 August 2025**, produced by a committee chaired by **Prof. Pushpak Bhattacharyya (IIT Bombay)**. It sets out **7 guiding principles ("Sutras")** — including "understanding by design," accountability, and fairness and equity — and **26 recommendations** across **6 pillars: Infrastructure, Policy, Capacity, Governance, Protection, Assurance**. It applies to all RBI-regulated entities (scheduled commercial banks, cooperative banks, NBFCs, PSOs, fintechs) and calls for board-approved AI policies, lifecycle governance covering model approval, testing, deployment and change control, **independent validation and periodic audits**, vendor/third-party safeguards, and incident reporting [S34][S35].

> **Sourcing caveat.** I could not open `rbi.org.in` directly in this session. The above is reconstructed from KPMG's hosted copy of the report and from Dvara Research's and Chambers' summaries [S34][S35][S36]. **Verify against the RBI original before it goes on a slide.** For an Indian CTO jury this is nonetheless the single most resonant governance citation available — "understanding by design" is a gift of a phrase for an explainability slide.

#### (v) IFRS 9 — forward-looking provisioning

The link that turns PS-04 from an alerting tool into a P&L instrument. IFRS 9 replaced IAS 39's incurred-loss model with **expected credit loss (ECL)**; the **significant increase in credit risk (SICR)** assessment (§5.5.4) is explicitly **forward-looking**, using "reasonable and supportable information that is available without undue cost or effort at the reporting date," and triggers a move from 12-month to **lifetime** ECL. The 30-days-past-due threshold is a rebuttable presumption and "the latest point at which lifetime ECL should be recognised" (IFRS 9.5.5.11; B5.5.19–20) [S37].

**Why this matters for the explainability lane specifically:** an early-warning system that fires *before* 30 DPD is producing exactly the "reasonable and supportable forward-looking information" that SICR requires. **The explanation of a PS-04 alert is therefore not decoration — it is audit evidence for a provisioning judgement.** That is the strongest business framing available for PS-04's explainability, and it links straight to SR 26-2's outcomes-analysis expectation [S1].

> **Sourcing caveat.** IFRS 9's own text is behind the IFRS Foundation paywall; paragraph references above come from PwC's *In depth* guide [S37]. Mark as secondary.

### A2. Technique level: SHAP/LIME, their documented failure modes, and the better answers

**Everyone will show a SHAP bar chart. The differentiation is knowing why it is wrong.** Four citations, in ascending order of sharpness:

1. **Correlated features — the core problem for financial ratios.** Chen, Janizek, Lundberg & Lee, **"True to the Model or True to the Data?"** (arXiv:2006.16234) shows Shapley attribution splits into two incompatible variants depending on how you condition: the **observational/conditional** expectation "spreads importance among correlated features that may not be explicitly used by the machine learning model," while the **interventional** variant "is true to the model in the sense that it gives importance to features explicitly used by the model" [S38].
   **Why this is fatal-by-default for PS-04:** leverage, DSCR, interest coverage, utilisation and EBITDA margin are *arithmetically* correlated — they share EBITDA and debt in numerator and denominator. Under observational SHAP, credit will be smeared across the whole ratio family, and your "primary driver" will be an artefact of the ratio algebra. Under interventional SHAP you get model-truth but must evaluate the model off-manifold at ratio combinations that cannot exist.
   **The pitch line:** *"We use interventional SHAP with a stated background distribution, because our features are algebraically dependent and observational Shapley values would attribute risk to ratios the model never used. Here is the same borrower explained both ways — the answers differ."* Showing the two side by side is a 20-second slide almost no team will attempt, and it directly answers SR 26-2's conceptual-soundness expectation of "interpretability measures" [S1].
2. **Shapley values are not a feature-importance measure.** Kumar, Venkatasubramanian, Scheidegger & Friedler, **"Problems with Shapley-value-based explanations as feature importance measures," ICML 2020 (PMLR v119, pp. 5491–5500)** — mathematical problems arise when Shapley values are used for feature importance, and the mitigations "necessarily induce further complexity, such as the need for causal reasoning" [S39].
3. **Post-hoc explanations can be adversarially faked.** Slack, Hilgard, Jia, Singh & Lakkaraju, **"Fooling LIME and SHAP: Adversarial Attacks on Post hoc Explanation Methods," AIES 2020 (arXiv:1911.02508)** — a scaffolding technique lets an adversary hide a biased classifier's behaviour so that "extremely biased (racist) classifiers… easily fool popular explanation techniques such as LIME and SHAP into generating innocuous explanations" [S40].
   **Governance consequence to state:** perturbation-based explanations are not a control against a malicious or negligent model developer. They must sit *alongside* deterministic calculations and outcomes analysis, which is precisely the architecture split in point (3) below.
4. **Counterfactual explanation is the legally-shaped answer.** Wachter, Mittelstadt & Russell, **"Counterfactual Explanations Without Opening the Black Box: Automated Decisions and the GDPR," *Harvard Journal of Law & Technology* 31(2), 2018 (arXiv:1711.00399)** — a counterfactual describes "the smallest change to the world that can be made to obtain a desirable outcome," conveying dependency on external facts "without conveying the internal state or logic of an algorithm" [S41].
   **For PS-04 the counterfactual is also operationally the right object.** "P(breach at 90d) = 0.62. It falls below 0.30 if DSCR recovers to 1.35× **or** if revolver utilisation drops below 74% **or** if the top-customer concentration falls below 31%." That is simultaneously (a) an explanation, (b) an intervention plan for the relationship manager, and (c) the "clear and meaningful explanation… of the main elements of the decision" that AI Act Art. 86 asks for [S32]. **A counterfactual is the only explanation format that is also a recommendation.** It should be the primary explanation surface, with SHAP demoted to a secondary panel.

**Calibration as a *form of* explanation — the most under-used move available.** A probability is only an explanation if it means what it says. Guo, Pleiss, Sun & Weinberger, **"On Calibration of Modern Neural Networks," ICML 2017 (arXiv:1706.04599)**, showed that "modern neural networks, unlike those from a decade ago, are poorly calibrated," with depth, width, weight decay and batch normalisation all affecting calibration, and that **temperature scaling** — "a single-parameter variant of Platt Scaling" — is "surprisingly effective at calibrating predictions" [S42].

For PS-04 this converts directly into a stage moment: ship a **reliability diagram** and a **Brier score** for each of the 30/60/90 horizons on the synthetic portfolio, and say *"when this system says 30%, it is right 30% of the time — here is the curve."* That is a far stronger trust claim than any SHAP chart, it satisfies SR 26-2's outcomes-analysis expectation [S1] and EBA §54(c)'s backtesting requirement [S2], and it is cheap on synthetic data where ground truth is known by construction.

### A3. Does the provenance type system apply to PS-04?

Partly — and it is a *weaker* fit, which is the honest read.

PS-04's evidence is mostly **structured numeric time series** (balances, utilisation, payment dates, treasury flows) plus synthetic news. The five-way separation still applies (a ratio computed from a filed statement is a `RecordedFact`-derived deterministic value; a 90-day breach probability is an `AIInference`; an RM's note is `UserInput`; a watch-list placement is an `AutomatedAction`; a stage migration is a `HumanDecision`) — but:

- Provenance for a number is a *lineage graph*, which is already solved by any competent data-engineering stack and looks like plumbing on screen.
- Provenance for a **highlighted clause in a scanned PDF** is visceral. PS-17 has the visual; PS-04 does not.
- The authority rule still works, but its most interesting trigger — *unverified AI inference in the closure* — is less discriminating when nearly every conclusion depends on the same forecasting model.

**One place it is genuinely strong for PS-04:** *covenant definition extraction*. Thresholds, testing frequency, cure periods and exceptions are extracted from credit agreements — the same document problem as PS-17, at 5% of the volume. A wrong threshold silently poisons every downstream test. So **covenant-definition provenance with a source span and a mandatory human confirmation** is the one place PS-04 should implement the full type system. Make that the sub-problem: *"Sub-problem 2 — the covenant is only as right as the clause it came from."*

**Verdict on A for PS-04: 7/10.** Best-in-class regulatory grounding; weaker demonstrability; the headline techniques are commoditised and (as shown) fragile.

---

## B. Observability for PS-04

### B1. The structural problem

**Most of PS-04's compute is not an LLM.** Ratio calculation, covenant testing, feature engineering, gradient-boosted or survival-model forecasting, ranking — these are pandas/scikit-learn/lightgbm. LLM calls appear in exactly three places: covenant-definition extraction from the credit agreement, synthetic news/industry signal classification, and narrative composition of the alert.

So an **OpenTelemetry GenAI** trace of PS-04 is thin: a handful of `chat` spans hanging off a numeric pipeline. You can absolutely instrument the numeric pipeline with plain OTel spans — and you should — but then you are showing a *distributed-tracing* view, which is respectable engineering and completely unsurprising to a CTO jury. The `gen_ai.*` conventions [S4][S19] add little.

### B2. The span tree anyway

```
portfolio.cycle                                   [root]  as_of_date, portfolio.id, n_borrowers
└─ borrower.evaluate                              borrower.id, facility.ids[]
   ├─ execute_tool ratios.compute                 [deterministic — NOT a "model" per SR 26-2 p.3]
   │     statement.id, statement.sha256, period_end
   │     dscr, net_debt_ebitda, icr, current_ratio
   ├─ execute_tool covenant.test                  [deterministic]
   │     covenant.id, covenant.version, threshold, actual, headroom_pct, test_date, result
   ├─ model.predict                               [custom span, no gen_ai equivalent]
   │     model.id, model.version, horizon={30,60,90}, p_breach
   │     calibration.brier, calibration.bin, calibration.n_train
   │  └─ explain.shap
   │        shap.background = interventional | observational   <- state it in the span
   │        shap.top_features[], shap.values[]
   │  └─ explain.counterfactual
   │        cf.feature, cf.current, cf.target, cf.delta_p
   ├─ RETRIEVER news.signals                       [openinference.span.kind=RETRIEVER]
   │     doc.id, doc.sha256, published_at, sentiment, sector
   ├─ invoke_agent aegis.narrative                 gen_ai.agent.name, then chat spans
   └─ execute_tool authority.check
         aegis.autonomy.requested / granted / demotion_reason
```

Two attributes here genuinely earn their place and are worth naming on stage: **`covenant.version`** on the deterministic test span (so the trace itself records which covenant definition governed), and **`shap.background`** (so the trace records which Shapley variant produced the explanation — closing the loop on the failure mode in A2). Those two turn a generic trace into a model-risk artefact, which is exactly EBA §54(b)'s "traceability, auditability" [S2].

### B3. Backend

Identical to PS-17: **Phoenix** (pure-Python wheel [S7]) + **Jaeger v2.20.0 Windows zip** [S6]. But note the mismatch: Phoenix's UI is optimised for LLM/retriever/agent spans; a PS-04 trace is mostly non-GenAI spans, which Phoenix renders as generic. **For PS-04, Jaeger is arguably the better primary** — which itself tells you the GenAI observability story is not where PS-04's strength lies.

### B4. Which problem makes an OTel trace view more impressive on stage — and why

**PS-17, unambiguously.** Four reasons, in order of force:

1. **Depth and branching are real.** PS-17's agent genuinely selects the next evidence, retries, reconciles contradictions, and re-evaluates. The tree is deep because the problem is, not because you instrumented aggressively. PS-04's tree is wide and shallow — the same six spans, N times.
2. **The trace terminates on a document, not a number.** The click-path in B4 above ends with a highlighted clause in a source PDF. PS-04's ends at a cell in a table.
3. **The inject produces a second, visibly different trace over the same input.** Two traces side by side, differing in `rule.version.effective_at` and in `authority.check` outcome, is the clearest possible visual proof of "material changes trigger targeted re-evaluation." PS-04 has no equivalent event.
4. **PS-17's spans carry the authority decision.** `aegis.autonomy.requested=A4 / granted=A2 / demotion_reason=unverified_inference` inside a trace is a genuinely novel thing to show a CTO: *the trace explains not just what the system did, but why it was not permitted to do more.*

**Verdict on B: PS-17 9/10, PS-04 5/10.**

---

## C. The autonomy model for PS-04

Same ladder (A0–A4 + H), same Sheridan spine [S10], same truncation argument.

| Action (brief's process flow) | Level | Justification |
| --- | --- | --- |
| Borrower & covenant intake — load financials, facilities | **A4** | Reversible ingestion. |
| **Extract covenant definition, threshold, testing frequency, exceptions** | **A2** | A wrong threshold poisons everything downstream, silently. Requires explicit human confirmation with the source clause visible. **This is PS-04's one true provenance gate.** |
| Compute financial ratios | **A4** | Deterministic arithmetic; outside SR 26-2's "model" definition [S1]. |
| Deterministic covenant test (headroom vs threshold at test date) | **A4** | Same. Fully replayable; no judgement. |
| Signal monitoring — payments, utilisation, treasury, news ingest | **A4** | Ingestion. |
| **Score risk / forecast breach probability at 30/60/90** | **A1–A2** | A2 when calibrated and within validated range; **A1 when out-of-distribution** (new sector, thin history) — present a *range* of hypotheses rather than a point estimate. EBA §54(d) requires "control mechanisms, model overrides and escalation procedures" [S2]. |
| Identify primary drivers (SHAP / counterfactual) | **A2** | Explanation is a recommendation, per A2 above. |
| **Rank portfolio by urgency and expected impact** | **A2** | A ranking is a recommendation with consequences for attention allocation; it must be reviewable and overridable. |
| Raise alert / place on **watch list** | **A4** | Internal, reversible, and *explicitly required* by EBA §§270–272 with defined trigger levels and escalation [S2]. Safe under the asymmetry rule: it raises human involvement. |
| **Recommend intervention** (covenant waiver discussion, collateral top-up, facility restructure) | **A2** | System proposes; credit officer decides. |
| **Contact borrower** | **H** | Human-owned. EBA §273: contact "should have regard to their individual circumstances," commensurate to information requirements [S2]. An automated deterioration email to a corporate borrower is a relationship and potentially a legal event. |
| **Reclassify facility / migrate risk grade / IFRS 9 stage transfer** | **H** (A3 at most, and only for internal grades) | A Stage 1→2 migration changes 12-month ECL to lifetime ECL [S37] — a provisioning and financial-reporting judgement. Never automatic. |
| Escalate to head of risk / management body | **A4** | Required by EBA §272 [S2]; raises involvement, so safe. |
| Maintain auditable early-warning history | **A4** | Logging. |

Note the shape: **PS-04's ladder is bimodal.** Everything is either A4 (deterministic and reversible) or A2/H (judgement). There is very little at A3, because there are few *reversible external actions* in credit risk — you either compute internally or you touch a customer relationship or the balance sheet.

### C5. Which problem has the more interesting autonomy story?

**PS-17, and by a clear margin — but the reasoning is worth spelling out because the naive read goes the other way.**

The naive read: PS-04 is higher-stakes (regulated credit decisions, Annex III adjacency, EU AI Act Art. 14 directly applicable if in scope), therefore the autonomy story is richer. That is wrong for three reasons.

1. **PS-17's boundary is externally imposed, which makes it credible.** The brief says three times that legal interpretation, contractual notice and material commercial settlement are human-owned. You did not choose the line; you were given it, and you can be *tested* against it live. PS-04's boundaries are ones you chose, and a sceptical CTO will read chosen boundaries as marketing.
2. **PS-17 has a mechanism, not a policy.** The asymmetry rule (*autonomy to retract is safe; autonomy to assert is not*) plus provenance-typed authority demotion means the level is **computed at runtime from the evidence graph**, not stamped on a design document. PS-04's levels are largely static per action type. **A ladder whose rungs move is a far more interesting engineering artefact than a ladder that is a table.**
3. **PS-17 has the refusal demo.** Press "Send notice," watch the system decline and explain the decline through its own trace. PS-04's equivalent — "we won't auto-migrate the IFRS 9 stage" — is a sentence, not a demonstration.

**Is PS-17's human-owned boundary an advantage or a limitation?** **Advantage, provided you build it as an enforced type-level property rather than a UI gate.** The limitation is real and should be pre-empted: the system cannot close the loop, so quantified impact is "leakage identified and prepared for recovery," not "recovered." Pre-empt it with the honest framing: *the constraint is the customer's, not ours; here is the constraint enforced in the type system; here is the audit trail proving it held; and here is the cycle-time reduction on the human step, which is where the value actually is.*

**Verdict on C: PS-17 9/10, PS-04 6/10.**

---

## D. Cryptographic provenance for PS-04

Covered comparatively in PS-17 §D2. In summary:

- **Load-bearing? No.** Single-party setting. The bank owns the model, the data, the log, and hires the auditor. Tamper-evidence against yourself is satisfied by an immutable, versioned event store, which PS-04 needs regardless.
- **The one honest use:** binding a **decision record** (evidence set + covenant version + model version + calibration snapshot) so an SR 26-2 outcomes-analysis review [S1] or an EBA §54(c) backtest [S2] can replay a historical alert exactly. Worth building as an event store. **Not worth building as a Merkle tree, and not worth a slide.**
- **Smart contracts: no.** Covenant thresholds as on-chain conditions fail on the oracle problem — the inputs are private, off-chain and unverifiable by the chain — and no bank will place borrower financials on a ledger.
- **Best move: say so.** *"We evaluated a hash-chained ledger for covenant decisions and concluded it would be decoration; the adversary model doesn't support it. Here is the immutable event store that does the actual work."* Restraint reads as judgement to a CTO panel, and it inoculates you against the "why no blockchain?" question.

**Verdict on D: PS-17 8/10, PS-04 3/10.**

---

# Head-to-head verdict for this lane

| Sub-lane | PS-17 | PS-04 | Margin | One-line reason |
| --- | --- | --- | --- | --- |
| **A. Explainability & audit** | **9** | 7 | PS-17 +2 | PS-04 has the better *regulation*; PS-17 has the better *demonstration* — clause-span provenance you can see, and a type system the brief demands. PS-04's headline techniques (SHAP) are commoditised and documented-fragile on exactly its feature set. |
| **B. Observability (OTel)** | **9** | 5 | PS-17 +4 | PS-17 is genuinely agentic, so `invoke_agent`/`execute_tool`/`RETRIEVER` spans carry real structure and terminate on a source document. PS-04 is a numeric pipeline with three chat spans; its trace is respectable and unsurprising. |
| **C. Autonomy model** | **9** | 6 | PS-17 +3 | PS-17's boundary is externally imposed (credible), computed at runtime from the provenance graph (a mechanism, not a policy), and demonstrable by refusal. PS-04's is a static table. |
| **D. Crypto provenance** | **8** | 3 | PS-17 +5 | PS-17 is a two-party dispute where an append-only log has real economic function, and the amendment inject is the moment consistency proofs pay off. PS-04 is single-party; the honest answer there is "don't." |
| **Lane total** | **35 / 40** | **21 / 40** | **PS-17 by 14** | |

**PS-17 wins this lane decisively, and wins it on the two sub-lanes the jury rubric weights most visually** (observability and out-of-the-box factor). B and D alone are a 9-point gap.

### The one asymmetry that runs the other way — state it explicitly

**PS-04 has a named high-risk classification. PS-17 has none.** This is the clearest single point in PS-04's favour anywhere in this lane, and burying it would be dishonest.

| | PS-04 | PS-17 |
| --- | --- | --- |
| **EU AI Act** | **Named in Annex III point 5(b)**, the list referred to by Art. 6(2): "AI systems intended to be used to evaluate the creditworthiness of natural persons or establish their credit score" [S31]. Pulls in Art. 12 record-keeping, Art. 14 human oversight, Art. 26 deployer duties, **Art. 86 right to explanation** [S13][S11][S14][S32]. *Nuance: PS-04 is commercial lending, so on a literal reading the corporate-borrower core sits outside "natural persons."* | **No Annex III entry exists** for contract, SLA or commercial-operations AI. Art. 12/14 are adopted **by choice**, not obligation. |
| **US prudential** | **In scope** of SR 26-2 / OCC Bulletin 2026-13 for the Z2 forecaster [S1][S1b] | **Out of scope entirely** — not a banking organization process. Cannot claim exemption from a regime that never applied. |
| **Sectoral** | EBA/GL/2020/06 §§54–55 and §§266–274 [S2]; IFRS 9 SICR [S37]; RBI FREE-AI [S34] | None binding. Nearest analogues are internal audit and contractual audit rights. |
| **Explanation duty** | Art. 86 creates an individual **right to "clear and meaningful explanations"** [S32] | No equivalent right; the duty is owed to a counterparty and an auditor, not a data subject. |

**What this means for the verdict.** PS-04's explainability work is *compelled*, which makes it easy to justify and hard to dismiss — "we built this because Art. 86 requires it" is unanswerable. PS-17's is *elective*, which is rhetorically weaker but demonstrably stronger, because election let the team design for inspection rather than for compliance minimums.

**It moves sub-lane A but not the lane.** It is already priced into A (PS-04 scores 7 largely on the strength of this stack, against a problem whose techniques are otherwise commoditised). It does not touch B, C or D, where PS-17's margins total 12 points. **A jury weighting regulatory defensibility heavily could close A to 9–9; PS-17 would still win 35–31.**

### What would change the verdict

1. **If the jury weights regulatory defensibility over demonstrability.** PS-04's citation stack (SR 26-2 four months old; EBA §§54–55 and §§266–274; AI Act Annex III/Art. 12/14/86; IFRS 9 SICR; RBI FREE-AI) is far heavier than PS-17's. If the panel is composed of bank CTOs rather than product CTOs, A and C narrow to roughly even and the total closes to about 31–27.
2. **If clause extraction fails on the demo corpus.** PS-17's entire provenance chain points at extracted obligations. CUAD's 44% precision @ 80% recall [S15] is a real warning. If the team cannot get reliable extraction on its own synthetic corpus, the chain points at nothing and A drops from 9 to ~5, taking the lane to a near-tie. **Mitigation: author the synthetic contracts so the obligation grammar is known, and report extraction accuracy honestly as a metric rather than hiding it.**
3. **If no local trace backend runs on the demo machine.** B collapses for both. Mitigation is cheap and should be done on day 1: verify `pip install arize-phoenix` and the Jaeger Windows zip on the actual hardware before committing the demo narrative to a trace view.
4. **If a prior-art search finds close art on provenance-conditioned authorisation.** The novelty claim in A3 is the lane's main patentability contribution. **I could not run that search** (see Risks). If it is anticipated, D and A both weaken and the "out-of-the-box" score drops.
5. **If PS-04 adopts the two moves in this report that no other team will have** — the SR 26-2 agentic carve-out gap argument, and the interventional-vs-observational SHAP comparison with a live reliability diagram — A rises to 8–9 and C to 7. That is a 3-point swing and it is achievable in a day. **Even if PS-17 is chosen, port those two moves; they are cheap and they are differentiating.**

---

# Risks and open questions

1. **WebSearch budget exhausted mid-research.** This session hit its 200-call web-search limit during section C. All subsequent evidence was gathered by direct WebFetch and by authenticated API calls (GitHub, PyPI, Crossref, Semantic Scholar, arXiv). **Consequence: no prior-art / patent search was performed.** The patentability assessment in A3 is an engineering judgement, not a freedom-to-operate opinion. Hash-chained audit logs in particular are crowded territory and should be assumed anticipated.
2. **The GenAI semantic conventions are unstable.** `semantic-conventions-genai` was created 2026-05-05 and has **zero releases**; the agent-spans doc is marked *Development* [S4]. Attribute names may change before the demo. **Mitigation: dual-emit `gen_ai.*` and `openinference.*` [S5], and pin the exact commit you instrumented against.**
3. **RBI FREE-AI sourcing is secondary.** `rbi.org.in` was not reachable in this session. Details (13 Aug 2025, 7 Sutras, 26 recommendations, 6 pillars, Bhattacharyya chair) come from KPMG's hosted PDF and from Dvara/Chambers summaries [S34][S35][S36]. **Verify against the RBI original before it appears on a slide** — a wrong recommendation count in front of an Indian CTO jury is worse than omitting it.
4. **EU AI Act application dates are genuinely uncertain.** The "2 December 2027" figure for standalone Annex III obligations is from a vendor blog [S33] and I could not verify it against Article 113 as amended. The 2025–26 "Digital Omnibus" process may have moved it. **Do not put a date on a slide.**
5. **IFRS 9 paragraph references are secondary** (PwC *In depth* [S37]); the standard itself is paywalled.
6. **SAE J3016 content is unverified.** Both `sae.org` pages returned only navigation chrome. Use J3016 as a *rhetorical analogue* ("everyone understands Level 3 for cars") and do not quote its level definitions.
7. **SEC Rule 17a-4(f) could not be retrieved** (`sec.gov` returned 403 to both WebFetch and curl; `ecfr.gov` redirected to an unblock interstitial). If a WORM/audit-trail-alternative citation is wanted for the tamper-evident-log argument, it must be verified separately. The EU AI Act Art. 12 / 26(6) citations [S13][S14] cover the same ground and *were* verified.
8. **The Indian Companies (Accounts) Rules audit-trail/edit-log requirement was not verified.** It would be a strong India-specific hook for PS-17's tamper-evident log (accounting software must maintain a non-disableable edit log), but no primary source could be opened. **Do not cite it without verification.**
9. **immudb has a licence and a release problem.** Business Source License 1.1 is source-available, not open source — check whether the hackathon rules care. And v1.11.0/v1.11.1 (Apr/Jun 2026) publish **no binaries at all**, so a Windows deployment is pinned to v1.10.0 (Oct 2025). **Recommendation: don't depend on it; hand-roll the RFC 9162 log.**
10. **Anvil on Windows is verified-to-exist, not verified-to-run.** `foundry_v1.8.1_win32_amd64.zip` exists in the v1.8.1 release [S28], but was not executed on a Windows machine in this session. **Test it on day 1, and keep `py-evm`/`eth-tester` (pure-Python wheels [S25]) as the in-process fallback.**
11. **The Phoenix + Jaeger dual-export path was not run.** Both artefacts were verified to exist and to be Windows-installable; the combination was not tested end-to-end.
12. **Open design question:** should `verified_by` on an `AIInference` be satisfiable *mechanically* (a second model checks entailment of the cited span) or only by a human? Mechanical verification scales and is demoable; it also creates a circularity that a sharp CTO will attack ("who verifies the verifier?"). **Suggested answer:** mechanical verification permits A2; only a `HumanDecision` permits A3+. Worth deciding before the pitch, because it will be asked.
13. **The FDIC issuance number for the revised model-risk guidance was not located.** SR 26-2 and OCC Bulletin 2026-13 were both verified directly [S1][S1b]; the FDIC is named as a joint issuer in the guidance itself but its FIL number was not retrieved. **Cite the Fed and OCC references only.**
14. **The "no new numbers" invariant (cross-cutting section) is a design proposal, not an implemented or benchmarked control.** It is cheap and mechanically checkable, but numeral extraction from generated prose has its own false-positive modes (rounding, currency formatting, ordinals, restated ranges). Budget half a day and test it against the actual narrative templates.
15. **Unquantified:** no business-impact numbers appear in this report. That is another lane's remit, but note that the autonomy ladder directly constrains it — an A2-capped system claims *cycle-time* and *detection* benefits, not *recovery* benefits.

---

# Sources

All URLs below were opened in this session unless marked otherwise.

**Regulatory and standards**

1. **[S1]** Board of Governors of the Federal Reserve System / OCC / FDIC, **SR 26-2, "Revised Guidance on Model Risk Management," 17 April 2026** (supersedes SR letter 11-7 of 4 April 2011 and SR letter 21-8 of 9 April 2021). **Full attachment PDF read, pp. 1–12**, including the p.3 definition of "model" and footnote 3 on generative/agentic AI, verbatim. https://www.federalreserve.gov/supervisionreg/srletters/SR2602.pdf — letter page: https://www.federalreserve.gov/supervisionreg/srletters/sr2602.htm
2. **[S1b]** Office of the Comptroller of the Currency, **OCC Bulletin 2026-13, "Model Risk Management: Revised Guidance," 17 April 2026** — the OCC issuance of the same interagency guidance; **rescinds OCC Bulletin 2011-12**, "Sound Practices for Model Risk Management: Supervisory Guidance on Model Risk Management." Verified independently. https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html
2. **[S2]** European Banking Authority, **Guidelines on loan origination and monitoring, EBA/GL/2020/06** (applicable 30 June 2021). Full text extracted; §§54–55, 257–274 read verbatim. https://www.bde.es/f/webbde/INF/MenuHorizontal/Normativa/guias/EBA-GL-2020-06-EN.pdf — official EBA copy: https://www.eba.europa.eu/sites/default/files/document_library/Publications/Guidelines/2020/Guidelines%20on%20loan%20origination%20and%20monitoring/884283/EBA%20GL%202020%2006%20Final%20Report%20on%20GL%20on%20loan%20origination%20and%20monitoring.pdf
3. **[S3]** W3C, **PROV-O: The PROV Ontology**, W3C Recommendation, 30 April 2013. https://www.w3.org/TR/prov-o/
4. **[S11]** EU AI Act **Article 14 — Human oversight**. https://artificialintelligenceact.eu/article/14/
5. **[S13]** EU AI Act **Article 12 — Record-keeping**. https://artificialintelligenceact.eu/article/12/
6. **[S14]** EU AI Act **Article 26 — Obligations of deployers of high-risk AI systems** (26(2), 26(5), 26(6)). https://artificialintelligenceact.eu/article/26/
7. **[S31]** EU AI Act **Annex III — High-Risk AI Systems Referred to in Article 6(2)**, point 5(b). https://artificialintelligenceact.eu/annex/3/
8. **[S32]** EU AI Act **Article 86 — Right to explanation of individual decision-making**. https://artificialintelligenceact.eu/article/86/
9. **[S33]** Openlayer, "EU AI Act Credit Scoring High-Risk System Guide" — **secondary; the 2 December 2027 date is UNVERIFIED against primary text.** https://www.openlayer.com/blog/credit-scoring-eu-ai-act-compliance-guide
10. **[S24]** NIST, **AI Risk Management Framework (AI RMF 1.0), NIST AI 100-1**, January 2023, DOI 10.6028/NIST.AI.100-1. Full text extracted; MAP 2.2, MAP 3.5, MEASURE 2.9, Appendix C read. https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf
11. **[S34]** KPMG India, hosted copy of the **RBI FREE-AI Committee Report** — **secondary source; primary rbi.org.in not reachable this session.** https://assets.kpmg.com/content/dam/kpmgsites/in/pdf/2025/08/rbi-free-ai-committee-report-on-framework-for-responsible-and-ethical-enablement-of-artificial-intelligence.pdf.coredownload.inline.pdf
12. **[S35]** Dvara Research, "Summary of the RBI FREE AI Committee Report" — secondary. https://dvararesearch.com/summary-of-the-rbi-free-ai-committee-report/
13. **[S36]** Chambers and Partners, "RBI Committee Report on Responsible AI in the Financial Sector (FREE-AI Framework)" — secondary. https://chambers.com/articles/rbi-committee-report-on-responsible-ai-in-the-financial-sector-free-ai-framework
14. **[S37]** PwC, ***In depth*: IFRS 9 impairment — significant increase in credit risk** (references IFRS 9 §5.5.4, §5.5.11, B5.5.19–20) — **secondary; IFRS 9 primary text is paywalled.** https://www.pwc.com/hu/hu/szolgaltatasok/ifrs/ifrs_9/ifrs9_kiadvanyok/ifrs_9_impairment_significant_increase_in_credit_risk.pdf
15. **[S12]** Law Commission of England and Wales, **Smart legal contracts: Advice to Government**, 25 November 2021. https://www.lawcom.gov.uk/project/smart-contracts/
16. **[S21]** SAE International, **J3016** (Taxonomy and Definitions for Terms Related to Driving Automation Systems). **Page returned navigation chrome only — level definitions UNVERIFIED.** https://www.sae.org/standards/content/j3016_202104/
17. **[S29]** IETF, **RFC 9162, "Certificate Transparency Version 2.0,"** December 2021 (Experimental; obsoletes RFC 6962). https://www.rfc-editor.org/rfc/rfc9162.html
18. **[S30]** IETF, **RFC 3161, "Internet X.509 Public Key Infrastructure Time-Stamp Protocol (TSP),"** August 2001 (Standards Track). https://www.rfc-editor.org/rfc/rfc3161.html

**Papers**

19. **[S10]** Parasuraman, R., Sheridan, T. B., & Wickens, C. D., **"A model for types and levels of human interaction with automation,"** *IEEE Transactions on Systems, Man, and Cybernetics — Part A: Systems and Humans*, **30(3), May 2000, pp. 286–297**, DOI 10.1109/3468.844354 (bibliographic detail confirmed via Crossref; 4,230 citations per Semantic Scholar). **Table I reproduces the Sheridan & Verplank (1978) 10-level scale**, read verbatim from p. 287. Open-access PDF: http://www.cs.uml.edu/~holly/91.549/readings/sheridan-autonomy.pdf — DOI: https://doi.org/10.1109/3468.844354
20. **[S22]** Morris, M. R., Sohl-Dickstein, J., Fiedel, N., Warkentin, T., Dafoe, A., Faust, A., Farabet, C., & Legg, S., **"Levels of AGI for Operationalizing Progress on the Path to AGI,"** arXiv:2311.02462. **Table 2, "Levels of Autonomy," read verbatim.** https://arxiv.org/abs/2311.02462
21. **[S23]** Feng, K. J. K., McDonald, D. W., & Zhang, A. X., **"Levels of Autonomy for AI Agents,"** arXiv:2506.12469, June 2025 (rev. July 2025). https://arxiv.org/abs/2506.12469
22. **[S15]** Hendrycks, D., Burns, C., Chen, A., & Ball, S., **"CUAD: An Expert-Annotated NLP Dataset for Legal Contract Review,"** NeurIPS 2021 (Datasets & Benchmarks). **510 contracts, 13,000+ annotations, 41 label categories; DeBERTa-xlarge 47.8% AUPR, 44.0% Precision @ 80% Recall.** https://arxiv.org/abs/2103.06268
23. **[S16]** Dahl, M., Magesh, V., Suzgun, M., & Ho, D. E., **"Large Legal Fictions: Profiling Legal Hallucinations in Large Language Models,"** *Journal of Legal Analysis* **16(1), 2024, pp. 64–93**. https://academic.oup.com/jla/article/16/1/64/7699227 — Stanford RegLab: https://reglab.stanford.edu/publications/hlarge-legal-fictions-profiling-legal-hallucinations-in-large-language-models/ — Stanford Law summary: https://law.stanford.edu/2024/01/11/hallucinating-law-legal-mistakes-with-large-language-models-are-pervasive/
24. **[S17]** Magesh, V., Surani, F., Dahl, M., Suzgun, M., Manning, C. D., & Ho, D. E., **"Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools,"** Stanford RegLab / HAI, 2024. **Lexis+ AI, Westlaw AI-Assisted Research and Ask Practical Law AI each hallucinate 17%–33% of the time.** https://reglab.stanford.edu/publications/hallucination-free-assessing-the-reliability-of-leading-ai-legal-research-tools/
25. **[S18]** Gao, T., Yen, H., Yu, J., & Chen, D., **"Enabling Large Language Models to Generate Text with Citations" (ALCE),** EMNLP 2023, arXiv:2305.14627. https://arxiv.org/abs/2305.14627
26. **[S38]** Chen, H., Janizek, J. D., Lundberg, S., & Lee, S.-I., **"True to the Model or True to the Data?"** arXiv:2006.16234. https://arxiv.org/abs/2006.16234
27. **[S39]** Kumar, I. E., Venkatasubramanian, S., Scheidegger, C., & Friedler, S., **"Problems with Shapley-value-based explanations as feature importance measures,"** ICML 2020, PMLR v119, pp. 5491–5500. https://proceedings.mlr.press/v119/kumar20e.html
28. **[S40]** Slack, D., Hilgard, S., Jia, E., Singh, S., & Lakkaraju, H., **"Fooling LIME and SHAP: Adversarial Attacks on Post hoc Explanation Methods,"** AIES 2020, arXiv:1911.02508. https://dl.acm.org/doi/10.1145/3375627.3375830
29. **[S41]** Wachter, S., Mittelstadt, B., & Russell, C., **"Counterfactual Explanations Without Opening the Black Box: Automated Decisions and the GDPR,"** *Harvard Journal of Law & Technology* **31(2), 2018**, arXiv:1711.00399. https://jolt.law.harvard.edu/assets/articlePDFs/v31/Counterfactual-Explanations-without-Opening-the-Black-Box-Sandra-Wachter-et-al.pdf
30. **[S42]** Guo, C., Pleiss, G., Sun, Y., & Weinberger, K. Q., **"On Calibration of Modern Neural Networks,"** ICML 2017, arXiv:1706.04599. https://arxiv.org/abs/1706.04599

**Observability**

31. **[S4]** OpenTelemetry, **GenAI agent spans** (`create_agent`, `invoke_agent`, `execute_tool`), document status **Development**. https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md — repo: https://github.com/open-telemetry/semantic-conventions-genai (created 2026-05-05; **zero releases**, verified via GitHub API)
32. **[S19]** OpenTelemetry, **"Moved: Generative AI semantic conventions"** and the deprecated `gen_ai.*` attribute registry (registered operation names and provider values). https://opentelemetry.io/docs/specs/semconv/gen-ai/ and https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/
33. OpenTelemetry blog, **"Inside the LLM Call: GenAI Observability with OpenTelemetry"** (2026). https://opentelemetry.io/blog/2026/genai-observability/
34. **[S5]** Arize AI, **OpenInference semantic conventions** (`openinference.span.kind` ∈ LLM, CHAIN, RETRIEVER, TOOL, EMBEDDING, AGENT; Apache-2.0). https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md — and https://arize.com/docs/phoenix/tracing/concepts-tracing/otel-openinference/semantic-conventions
35. **[S20]** Traceloop, **OpenLLMetry** (Apache-2.0; semantic conventions upstreamed into OpenTelemetry). https://github.com/traceloop/openllmetry
36. **[S7]** **Arize Phoenix** — `arize-phoenix` 20.4.0 publishes a pure-Python `py3-none-any` wheel (verified via PyPI JSON API). https://pypi.org/project/arize-phoenix/ — https://github.com/Arize-ai/phoenix
37. **[S6]** **Jaeger** — v2.20.0 (2026-07-20) release assets include `jaeger-2.20.0-windows-amd64.zip` (verified via GitHub API). https://github.com/jaegertracing/jaeger/releases — official Windows service deployment via `nssm`: https://www.jaegertracing.io/docs/2.dev/deployment/windows/
38. **[S9]** **Grafana Tempo on Windows** — maintainer discussion; unofficial, port-80 caveat. https://github.com/grafana/tempo/discussions/3390
39. **[S8]** **Langfuse self-hosting** — v3 requires langfuse-web, langfuse-worker, PostgreSQL, ClickHouse, Redis/Valkey and S3/MinIO. https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse

**Cryptographic provenance tooling**

40. **[S27]** **Trillian** — requires MySQL/MariaDB storage layer, Go 1.25+, multi-process personality/server/signer topology; **in maintenance mode**. https://github.com/google/trillian
41. **[S26]** **immudb** — Business Source License 1.1; `immudb-v1.10.0-windows-amd64.exe` present in the v1.10.0 (2025-10-21) release; **v1.11.0 and v1.11.1 publish no binaries** (verified via GitHub API). https://github.com/codenotary/immudb — https://github.com/codenotary/immudb/releases
42. **Amazon QLDB** — **discontinued.** `https://aws.amazon.com/qldb/` **301-redirects to `https://aws.amazon.com/rds/aurora/`** and `https://docs.aws.amazon.com/qldb/` returns **404** (both verified today by HTTP status check). *The exact end-of-support date could not be retrieved and is marked* **[UNVERIFIED]**. Use QLDB's journal→digest→Merkle-proof model as a design reference only.
43. **[S28]** **Foundry (Anvil)** — v1.8.1 (2026-08-28) release includes `foundry_v1.8.1_win32_amd64.zip` (verified via GitHub API); installation docs state foundryup "requires Git Bash or WSL. PowerShell and Command Prompt are not supported." https://github.com/foundry-rs/foundry/releases — https://getfoundry.sh/getting-started/installation
44. **[S25]** **Pure-Python wheels verified via the PyPI JSON API** (all `py3-none-any`, therefore Windows-installable without a compiler): `py-evm` 0.12.1b1 (MIT), `eth-tester` 0.14.0b1, `web3` 7.16.0, `pymerkle` 6.1.0, `rfc3161ng` 2.1.3, `opentelemetry-sdk` 1.44.0, `openinference-instrumentation` 0.1.59. `cryptography` 50.0.1 ships `cp311-abi3` binary wheels. https://pypi.org/

**Bibliographic lookups (metadata only)**

45. Crossref REST API — confirmed Parasuraman/Sheridan/Wickens (2000) venue, volume, pages, DOI. https://api.crossref.org/works
46. Semantic Scholar Graph API — confirmed citation count and located the open-access PDF for [S10]. https://api.semanticscholar.org/graph/v1/
47. arXiv API — used to locate and confirm [S23]. https://export.arxiv.org/api/query
