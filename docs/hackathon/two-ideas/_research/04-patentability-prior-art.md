# Patentability and prior art — comparative research

> Lane: patentability / freedom-to-operate / subject-matter eligibility.
> Research date: 29 August 2026. All patent records cited below were opened or machine-queried
> in this session unless explicitly marked otherwise.
>
> **Tooling caveat, stated up front because it bounds three claims in this document.**
> Google Patents (`patents.google.com`, including its `/xhr/query` search endpoint),
> Espacenet, Justia Patents, PatentsView and FreePatentsOnline all began returning
> `HTTP 403/503` bot-blocks partway through this research and did not recover over ~60 minutes
> of paced retries. The PS-17-side corpus counts in §PS-17.1 were captured **before** the block
> and are real. The equivalent numeric counts for the PS-04 side could **not** be captured;
> where I say a space is crowded without a number, I say so as a qualitative judgement and
> label it. USPTO's image server (`image-ppubs.uspto.gov`) and the EPO publication server
> (`data.epo.org`) stayed reachable, which is how the Capital One claim text below was
> obtained (scanned PDF → OCR) and how EP 4 364 078 A1 was verified.

---

## Executive answer

- **PS-17's presumed crown jewel is already occupied at the storage layer.** Capital One holds
  **US 11,935,046 B2**, *"Immutable database for processing retroactive and historical
  transactions using bitemporal analysis"* (granted 19 Mar 2024). Claim 1 is quoted verbatim in
  §PS-17.2. It covers: maintain events on a timeline → receive a *correcting event* → duplicate
  the prior sequence to a second timeline → substitute the correction → **replay the subsequent
  events** → verify → **promote the second timeline to current while forbidding deletion of the
  first**. That is bitemporal retroactive recomputation with immutable supersession, claimed
  generically. Anyone pitching "we replay history against a corrected version" as their novelty
  is pitching Capital One's claim back at them.
- **It is a family of six, not four.** The same provisional (63/157,455, *"System to Ensure Cloud
  Resource Compliance Before Provisioning (CloudPRE)"*, 5 Mar 2021) spawned six applications all
  filed 1 Nov 2021 — serials listed in §PS-17.2.
- **The hypothesis from the other lane survives, but only just, and only above the storage layer.**
  Capital One's claim recomputes a **data state** (its own dependent claims 2, 8, 10 define that
  state as *an account balance*). It contains no normative verdict, no rule versioning by
  effective date, no notion that an action was already executed in the outside world, and
  therefore no irreversibility. The defensible residue for PS-17 is the **action layer**:
  re-adjudication of *derived normative conclusions* under effective-dated rule versions, coupled
  with **irreversibility triage** on already-emitted external actions (a served contractual notice
  cannot be un-served) and hash-derived idempotency. Draft claim in §PS-17.5.
- **"Provenance-directed selective re-evaluation" is dead as a standalone novelty.** Not because
  of a patent — because of forty years of publications that teach exactly it: de Kleer's
  assumption-based truth maintenance system (1986), incremental view maintenance (Gupta et al.
  1993), provenance semirings / how-provenance (Green et al. 2007), differential dataflow
  (McSherry et al. 2013). §102/§103 prior art does not care that these are papers. It is
  patentable only as a *narrowing limitation* inside a larger claim, never as the point of novelty.
- **The CLM patent space is genuinely thin; the credit-risk space is not.** Icertis: 4 US records
  under the assignee facet. Sirion: 4. `"contract lifecycle management"` full-text, US: **83**.
  `"contract amendment" "effective date" recalculat*`, US: **0**. By contrast credit early-warning
  and covenant monitoring sit inside CPC **G06Q 40/03** (credit/loan processing), one of the most
  heavily worked corners of fintech, alongside every bank, bureau and rating agency.
- **§101 is where PS-04 dies.** *Electric Power Group v. Alstom*, 830 F.3d 1350 (Fed. Cir. 2016)
  holds that collecting data from disparate sources, analysing it, and displaying the result is an
  abstract idea. That is a one-sentence description of PS-04's journey. Worse, the 2019 PEG names
  *"fundamental economic principles or practices (including hedging, insurance, mitigating risk)"*
  as an enumerated abstract-idea grouping. Covenant breach forecasting **is** mitigating risk.
- **EPO kills PS-04 cleanly too.** Guidelines G-II 3.3.2 (post-G 1/19): *"Calculated numerical data
  reflecting the physical state or behaviour of a system or process existing only as a model in a
  computer usually cannot contribute to the technical character of the invention."* PS-04's
  modelled system is not even physical — it is a borrower's balance sheet.
- **India §3(k) is the harshest of the three, and it is the jurisdiction that matters for a
  TCS-affiliated jury.** The IPO's 2013 CRI Guidelines say it in one line: *"if in substance the
  claims relate to business method even with the help of technology, they are not considered
  patentable."* The 2017 Guidelines repeat the substance-over-form test for all claim formats.
  PS-04 hits **two** limbs of §3(k) at once (business method **and** mathematical method).
- **Verdict: PS-17 = 6/10, PS-04 = 3/10.** PS-17 wins by 3 points. Its win is narrower than it
  looks before you read Capital One's claim, and much wider than PS-04's after you read
  *Electric Power Group*.

---

## PS-17: Contract Obligation, SLA & Commercial Leakage Monitor

### PS-17.1 — Prior-art landscape

Machine-run queries against Google Patents full text, captured 29 Aug 2026 before the block.
Totals are Google's own `total_num_results` for the stated query and country filter; treat them
as order-of-magnitude, not as a census.

| Query (Google Patents full-text) | Filter | Total |
| --- | --- | --- |
| `assignee=Icertis` | — | **4** |
| `assignee=SirionLabs` | — | **4** |
| `"contract lifecycle management"` | US | **83** |
| `"contract compliance" "obligation" monitoring` | US, granted patents | **43** |
| `"contract obligation" "machine learning"` | US | **9** |
| `"service level agreement" "service credit"` | US | **6** |
| `"service level agreement" violation prediction` (unquoted, relevance-ranked) | US | **4,290** |
| `"bi-temporal" versioning` | US | **81** |
| `"contract amendment" "effective date" recalculat*` | US | **0** |

Read that table as three separate stories.

**(a) The CLM vendors have barely filed.** Icertis and Sirion — the two most-cited pure-play CLM
vendors — have single-digit US patent families each. Verified records:

| Number | Assignee | Title | Priority | Grant |
| --- | --- | --- | --- | --- |
| US 11,151,501 B2 | Icertis, Inc. | Risk prediction based on automated analysis of documents | 2019-02-19 | 2021-10-19 |
| US 12,020,130 B2 | Icertis, Inc. | Automated training and selection of models for document analysis | 2018-12-24 | 2024-06-25 |
| US 10,409,805 B1 | Icertis, Inc. | Clause discovery for validation of documents | 2018-04-10 | 2019-09-10 |
| US 10,162,850 B1 | Icertis, Inc. | Clause discovery for validation of documents | 2018-04-10 | 2018-12-25 |
| US 10,936,974 B2 | Icertis, Inc. | Automated training and selection of models for document analysis | 2018-12-24 | 2021-03-02 |
| US 11,593,440 B1 | Icertis, Inc. | Representing documents using document keys | 2021-11-30 | 2023-02-28 |
| DE 11 2020 000 860 T5 | Icertis, Inc. | Risk forecast based on automated document analysis | 2019-02-19 | pending |
| US 11,482,027 B2 | Sirionlabs Pte. Ltd. | Automated extraction of performance segments and metadata values associated with a document | 2019-01-11 | 2022-10-25 |
| US 2020/0226510 A1 | Sirionlabs | Method and system for determining risk score for a contract document | 2019-01-11 | pending |
| EP 3 908 997 A1 | Sirion Labs Private Ltd. | Method and system for configuring a workflow | 2019-01-11 | pending |
| US 2014/0129276 A1 | Sirion Labs | Method and system for supplier management | 2012-11-07 | — |

Every one of these is about **extracting attributes or clauses from a document**, or scoring a
document's risk. Not one is about *time-versioned adjudication of obligations against operational
evidence*. That is the shape of the gap.

**(b) The adjacent art that actually matters is not from CLM vendors.**

| Number | Assignee | Title | Why it matters to PS-17 |
| --- | --- | --- | --- |
| US 2021/0192650 A1 | Clause, Inc. (acquired by DocuSign) | System and method for managing data state across linked electronic resources | Contract state driven by external data — the closest conceptual neighbour to "obligations bound to operational evidence" |
| US 2020/0104296 A1 | Clause, Inc. | System and method for a hybrid contract execution environment | Executable contract terms against real-world events |
| US 2024/0202663 A1 | DocuSign, Inc. | System and method for forming, storing, managing, and executing contracts | Priority 2016-06-30 |
| US 2022/0108411 A1 | DocuSign, Inc. | System for an electronic document with state variable integration to external environment | Priority 2016-03-31 |
| US 8,732,047 B2 | SciQuest, Inc. | System and method for contract execution against expressive contracts | 2008 priority; machine-checkable contract terms |
| US 11,055,703 B2 | Hitachi, Ltd. | Smart contract lifecycle management | — |
| US 11,941,374 B2 | NB Ventures, Inc. (GEP) | Machine learning driven rules engine for dynamic data-driven enterprise applications | Procurement rules engine |
| US 9,965,735 B2 | Energica Advisory Services | System and method for IT sourcing management and governance covering multi-vendor… | Multi-vendor SLA governance |
| US 10,289,973 B2 | Ericsson | System and method for analytics-driven SLA management and insight generation… | SLA analytics, telco framing |
| US 9,141,927 B2 | IBM | Determining costs for workflows | SLA cost/credit adjacency |
| US 11,762,921 B2 | Eigen Technologies Ltd | Training and applying structured data extraction models | Document extraction |
| US 11,301,619 B2 | Zensar Technologies | System and method for transforming a contract into a digital contract | — |
| US 11,379,735 B2 / US 11,922,325 B2 | Legislate Technologies / Textmine | Automated document generation (and search) | — |

**(c) The temporal-database art is a separate, older, and more dangerous cluster.**

| Number | Assignee | Title |
| --- | --- | --- |
| US 8,219,522 B2 | Asserted Versioning, LLC | Management of temporal data by means of a canonical schema |
| US 8,713,073 B2 | Asserted Versioning, LLC | Management of temporal data by means of a canonical schema (continuation) |
| US 8,965,889 B2 | Oracle International | Bi-temporal user profiles for information brokering in collaboration systems |
| US 9,330,119 B2 / US 11,468,098 B2 | Oracle International | Knowledge-intensive data (management/processing) system for business process and case management |
| US 10,789,239 B2 | AlphaPoint | Finite state machine distributed ledger |

Add the standards and literature layer, which is prior art of exactly equal force: **SQL:2011
temporal features** (Kulkarni & Michels, *ACM SIGMOD Record* 41(3), 2012, DOI
`10.1145/2380776.2380786`) standardised system-time and application-time period tables — i.e.
bitemporal tables — into the SQL standard fourteen years ago.

### PS-17.2 — THE BLOCKER: Capital One's bitemporal retroactive recomputation family

**US 11,935,046 B2** — *"Immutable Database for Processing Retroactive and Historical Transactions
Using Bitemporal Analysis"*
Assignee: **Capital One Services, LLC**, McLean, VA. Inventors: Philip Austin Kedy, Kenneth J.
Schneider, Aaron Zhang. Appl. No. **17/516,438**, filed 1 Nov 2021. Pre-grant pub.
**US 2022/0284424 A1** (8 Sep 2022). **Granted 19 Mar 2024.** Provisional **63/157,455**
(5 Mar 2021). Examiner: Paul S. Schwarzenberg. Firm: Sterne, Kessler, Goldstein & Fox.
CPC: G06Q 20/389; G06F 16/215, 16/219, 16/2308, 16/2358, 16/2365, 16/2379, 16/9024;
G06Q 20/08; H04L 41/0806, 41/0816, 41/0856, 41/0866; H04L 67/34; H04L 69/02, 69/08;
G06Q 40/00; H04L 63/20.

Retrieved as the USPTO scanned grant PDF and OCR'd, because every text-based patent source was
blocked. Source: `https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11935046`.

> **1.** A computer-implemented method by an immutable database in a data processing system for
> tracking a data state of the data processing system, the method comprising:
>
> maintaining a plurality of historical events processed by the data processing system on a first
> time-ordered sequence, wherein the plurality of historical events includes a historical event, a
> first temporal sequence of historical events that occurs prior to the historical event, and a
> second temporal sequence of historical events that occurs subsequent to the historical event, and
> wherein each historical event is associated with a corresponding change in the data state;
>
> designating the first time-ordered sequence as a current time-ordered sequence for tracking the
> data state;
>
> receiving a correcting event configured to correct an error caused by the historical event; and
>
> correcting the historical event in the plurality of historical events based on the correcting
> event, the correcting comprising:
>
> &nbsp;&nbsp;duplicating the first temporal sequence of historical events to a second time-ordered
> sequence to form an alternate sequence of events in the second time-ordered sequence;
>
> &nbsp;&nbsp;replacing the historical event with the correcting event in the second time-ordered
> sequence;
>
> &nbsp;&nbsp;replaying, in the second time-ordered sequence, the second temporal sequence of
> historical events using the correcting event to form a second alternate sequence of events
> including an alternate event;
>
> &nbsp;&nbsp;tracking a change in the data state based on the alternate event;
>
> &nbsp;&nbsp;performing a verification of the change in the data state as being accurate; and
>
> &nbsp;&nbsp;promoting the second time-ordered sequence as the current time-ordered sequence for
> tracking a future data state based on the verification, wherein the second time-ordered sequence
> replaces the first time-ordered sequence, and wherein promoting the second time-ordered sequence
> further comprises:
>
> &nbsp;&nbsp;&nbsp;&nbsp;preventing deletion of the first time-ordered sequence from the immutable
> database subsequent to promoting of the second time-ordered sequence.

Dependent claims worth knowing (OCR'd verbatim in substance):

- **cl. 2 / 10** — the data state is *an account balance of a user financial account*; the event is
  a financial event; the change is a correction/adjustment to the balance.
- **cl. 3 / 11** — the plurality of events represents **all** events for that data state and the
  database prevents events from being overwritten.
- **cl. 5 / 13** — designate the second sequence as current and the first as *historical*.
- **cl. 6–8 / 14–15** — the same downstream event produces a *different* change on the alternate
  sequence than on the original (i.e. the recomputation actually changes the answer).
- **cl. 9** = system claim; **cl. 16** = CRM claim, both mirroring claim 1.

**The family — six applications, all filed 1 Nov 2021 off provisional 63/157,455** (from the
CROSS-REFERENCE section of the '046 specification, col. 1):

| Appl. Ser. No. | Title |
| --- | --- |
| 17/516,329 | Managing Pre-Provisioning and Post-Provisioning of Resources Using Bitemporal Analysis |
| 17/516,338 | Resource Compliance System Using Bitemporal Analysis |
| 17/516,340 | Managing Pre-Provisioning of Resources Using Bitemporal Analysis |
| 17/516,345 | Immutable Database for Bitemporal Analysis |
| 17/516,436 | Architecture of Immutable Database for Bitemporal Analysis |
| **17/516,438** | **Immutable Database for Processing Retroactive and Historical Transactions Using Bitemporal Analysis → US 11,935,046 B2** |

> **Open item, flagged rather than guessed.** I could not map the other five serial numbers to
> granted patent numbers or read their claims: every full-text patent search route was blocked
> (see the tooling caveat) and USPTO's Patent Center / assignment / ODP APIs returned
> `405/401/404` without credentials. **Do not put a freedom-to-operate assertion about those five
> on a slide until a proper search has been run.** The titles alone tell you what to expect:
> 17/516,345 and 17/516,436 are the ones most likely to claim the immutable-store architecture
> itself; 17/516,329/338/340 read on the *policy pre-provisioning* use case (test a proposed
> policy on an alternate timeline before deploying it), which is the "what-if branch" idea.

#### Testing the hypothesis against the actual claim language

The hypothesis put to me was: *a defensible claim must sit above storage-level bitemporal
recomputation — re-adjudication of derived normative conclusions under versioned effective-dated
rules, plus irreversibility triage of already-executed downstream actions.*

**That hypothesis holds.** Reading claim 1 element by element, here is what is and is not there:

| Element of the hypothesis | Present in US 11,935,046 cl. 1? |
| --- | --- |
| Immutable append-only history | **Yes** — "preventing deletion of the first time-ordered sequence" |
| Branch, substitute, replay forward | **Yes** — "duplicating… replacing… replaying" |
| Promote the corrected branch to current | **Yes** — explicitly |
| Correction arrives late, after downstream processing | **Yes** — "correcting event configured to correct an error caused by the historical event" |
| Recomputed thing is a **derived normative verdict** (breach / no-breach) rather than a data value | **No.** The claim recomputes "a data state"; claims 2/8/10 anchor that to an account balance. A verdict produced by evaluating evidence against a *rule* is nowhere in the claim. |
| The rule itself is **versioned by effective date**, and re-evaluation selects the rule version whose validity interval contains the *evidence's* valid time | **No.** There is one timeline of *events*. There is no second-class citizen called a rule, no effective-dated rule version, and no matching of event valid-time to rule valid-time. This is the single sharpest distinction available. |
| Only the **affected subset** is re-evaluated, identified from per-conclusion input lineage | **No.** Claim 1 duplicates the *whole* prior sequence and replays the *whole* subsequent sequence. It is a full replay, not a lineage-directed partial one. |
| An action was already **executed in the outside world** | **No.** Nothing leaves the database. |
| **Irreversibility classification** of executed actions, and differential handling (auto-reverse / compensate / quarantine for human approval) | **No, and this is the widest gap.** The only "gate" in claim 1 is "performing a verification of the change in the data state as being accurate" before promotion — an internal check, not a triage of external side effects. |
| Idempotency key preventing duplicate downstream emission | **No.** |

**Conclusion.** The crown jewel is *half* blocked. "We keep a bitemporal ledger and replay history
when an amendment lands" is Capital One's claim and must not be pitched as novel. What remains
unclaimed by '046 — and, on the evidence I could gather, unclaimed generally — is the pairing of
**effective-dated rule versioning** with **re-adjudication of normative verdicts** and
**irreversibility-typed disposition of already-emitted external actions**. That is the claim to
draft, and it is drafted in §PS-17.5.

One honest caution: gaps in an *independent claim* are not freedom to operate. The '046
specification is 38 pages and its disclosure is broader than its claims; the five sibling
applications may already claim into the space; and any of them could still spawn continuations.
Capital One is a prolific and aggressive filer. **Retain counsel before asserting anything.**

### PS-17.3 — Where the white space is

Four candidate mechanisms, each assessed against what was actually found.

**M1. Effective-dated re-adjudication with irreversibility triage.** *(strongest)*
On a late-arriving amendment, re-evaluate only the exceptions whose input closure contains the
superseded rule version and whose evidence valid-time falls inside the new version's validity
interval; append (never overwrite) superseding verdicts; then, for each verdict that changed,
dispose of the already-emitted actions according to a typed effect class — reversible actions get
an automatic reversal under the original idempotency key, compensable actions get a compensating
action bound to the same key, irreversible actions (a served notice, a paid credit) are
**quarantined into a human-approval queue and further automation is blocked**.
*Novelty:* good. `"contract amendment" "effective date" recalculat*` returns **0** US hits. The
Sagas / compensating-transaction literature (Garcia-Molina & Salem, SIGMOD 1987, DOI
`10.1145/38713.38742`) teaches compensation, and Capital One teaches replay, but neither teaches
*classifying a downstream action by reversibility and letting that class decide whether the
machine may act or must stop and ask a human*. That coupling is where the invention is.
*This is the one to file.*

**M2. Hash-chained provenance where the digest covers the decision's input closure.**
*Novelty: weak on its own.* Hash-chaining a log is Haber & Stornetta, *"How to time-stamp a
digital document"*, J. Cryptology 3(2), 1991 (DOI `10.1007/BF00196791`); Merkle-tree
append-only audit logs are RFC 6962 (Certificate Transparency, 2013). Hashing the *inputs* so a
replay is verifiable is the ordinary construction of a verifiable-computation transcript.
*Salvage:* it is a fine **dependent** claim — "wherein the digest is computed over the canonical
serialisation of the input closure, whereby re-execution of the deterministic adjudication engine
over the input closure is verifiable against the digest" — because it converts "auditability"
from a marketing word into a checkable property. Do not make it the point of novelty.

**M3. Typed provenance (recorded fact / AI inference / human decision) with an authority rule that
refuses to act on a disallowed mix.**
*Novelty: weak on its own, moderate as a limitation.* W3C **PROV-DM / PROV-O** (W3C
Recommendation, 2013) already types provenance into Entities, Activities and Agents and
distinguishes derivation from attribution. Typing an assertion by origin is prior art. What is
*not* obviously prior art is the **refusal rule**: the engine declines to emit an external action
when the input closure of the verdict contains an element typed as machine inference *and* the
remedy quantum exceeds a configured authority threshold. That is a concrete gate on a concrete
quantity, and it maps directly onto the brief's requirement that "material commercial settlement
decisions remain human-owned". Claim it as a dependent of M1.

**M4. Provenance-directed selective re-evaluation** (per-conclusion input lineage → re-evaluate
only the affected subset).
*Novelty: essentially none as a standalone mechanism.* This is a rediscovery of:
- **Assumption-based truth maintenance** — de Kleer, *"An assumption-based TMS"*, Artificial
  Intelligence 28(2), 1986 (DOI `10.1016/0004-3702(86)90080-9`), building on Doyle, *"A truth
  maintenance system"*, AI 12(3), 1979 (DOI `10.1016/0004-3702(79)90008-0`). An ATMS records,
  for every derived belief, the set of assumptions it rests on, precisely so that retracting an
  assumption invalidates only the dependent beliefs.
- **Incremental view maintenance** — Gupta, Mumick & Subrahmanian, *"Maintaining views
  incrementally"*, SIGMOD 1993 (DOI `10.1145/170035.170066`).
- **How-provenance** — Green, Karvounarakis & Tannen, *"Provenance semirings"*, PODS 2007 (DOI
  `10.1145/1265530.1265535`), which gives the algebra for exactly "which inputs produced this
  output, and what happens if one changes".
- **Differential dataflow** — McSherry, Murray, Isaacs & Isard, CIDR 2013.

I could not run a patent-database query for claims on this (blocked), so I cannot tell you
whether anyone *holds* such a claim; I can tell you that the published art is fatal to it as an
independent claim regardless. **Use it as a dependent limitation** that gives M1 a computational
bound — "wherein the re-adjudication set is identified by traversing an inverted index from rule
version identifiers to verdict identifiers, and is bounded independently of the total number of
verdicts in the store". That framing has a second benefit: a *bounded recomputation* is a
technical effect, which matters enormously in §PS-17.4.

### PS-17.4 — Subject-matter eligibility

**United States — Alice/Mayo, 35 U.S.C. §101, and the 2019 PEG (84 FR 50, 7 Jan 2019, docket
PTO-P-2018-0053).**

Step 2A Prong One: the claim will be found to recite an abstract idea. The PEG's grouping (b),
*"certain methods of organizing human activity"*, expressly includes *"commercial or legal
interactions"* — a contract obligation dispute is the paradigm case. Do not fight this.

Step 2A Prong Two is where PS-17 lives or dies: is the exception *integrated into a practical
application*? The PEG's listed considerations include *"an improvement in the functioning of a
computer, or an improvement to other technology or technical field"*.

The argument for M1: the claim does not merely decide who owes what. It (i) bounds the
recomputation set by lineage rather than rescoring the corpus, (ii) derives an idempotency key by
a one-way function over the input closure so that an unchanged verdict is *structurally incapable*
of emitting a duplicate external action, and (iii) makes the system's authority to act a function
of a typed effect class. Those are assertions about the behaviour of a distributed system under
retroactive change, not about contract law.

The argument against, which the team must be able to answer on stage:
- ***Electric Power Group, LLC v. Alstom S.A.***, 830 F.3d 1350 (Fed. Cir. 2016) — collecting
  information from disparate sources, analysing it, and displaying results, even in real time and
  even limited to a particular field, is abstract, and generic computer implementation is not
  "significantly more". PS-17's marketing description walks straight into this.
- ***Content Extraction & Transmission LLC v. Wells Fargo Bank***, 776 F.3d 1343 (Fed. Cir. 2014)
  — "collecting data, recognizing certain data within the collected data set, and storing that
  recognized data" is abstract. **This is obligation extraction from contracts, described by the
  Federal Circuit, held ineligible.** Never make extraction the point of novelty.

Practical drafting consequence, and this is the most useful thing in this section: **the claim's
opening words drive art-unit assignment, and art-unit assignment drives your §101 fate.** A claim
that opens *"A computer-implemented method of maintaining consistency between machine-adjudicated
conclusions and externally executed actions in a distributed system…"* with CPC in G06F 16/2308
(transaction/concurrency) reads as a database-consistency invention. The same disclosure opening
*"A method of monitoring contract compliance…"* lands in a business-methods art unit (TC 3600,
AU 3690s) where §101 rejection rates are far higher. Same invention, different odds.

*Honest estimate:* M1 as drafted has a real but not comfortable chance — call it a coin flip that
turns on examiner and art unit. M2 alone: no. M3 alone: no. M4 alone: no (§103, not just §101).

**Europe — Art. 52(2)/(3) EPC, COMVIK (T 641/00), G 1/19.**

Under COMVIK only features contributing to technical character count for inventive step;
everything else is folded into the problem statement as a non-technical constraint. Contract
semantics, obligations, service credits and settlement authority are all non-technical constraints
and will be handed to the skilled person for free. What remains for PS-17 is an append-only store
with lineage-bounded selective replay and hash-derived idempotent emission. EPO Guidelines G-II 3.3
allows a mathematical/algorithmic feature to contribute technical character through *"adaptation to
the internal functioning of the computer system or network"* — an idempotency key that structurally
prevents duplicate external emissions under concurrent retroactive correction is at least arguably
that. Then it faces §103-equivalent art: SQL:2011 temporal tables, the Asserted Versioning
patents, and now '046. **Verdict: arguable, probably narrow, expensive.**

**India — §3(k), Patents Act 1970.**

§3(k) excludes *"a mathematical method or business method or a computer programme per se or
algorithms"*. The IPO's **2017 CRI Guidelines** state the operative test:

> "If, in substance, claims in any form such as method/process, apparatus/system/device, computer
> program product/ computer readable medium belong to the said excluded categories, they would not
> be patentable."

and the **2013 CRI Guidelines** put the business-method point bluntly:

> "if in substance the claims relate to business method even with the help of technology, they are
> not considered patentable."

The counterweight is ***Ferid Allani v. Union of India***, W.P.(C) 7/2014, Delhi High Court,
12 Dec 2019 — a CRI is not barred by §3(k) if it demonstrates a *technical effect* or *technical
contribution*. The IPO's own annexure of CRI case law lists Ferid Allani alongside *Microsoft
Technology Licensing v. Controller of Patents* (2023, 2023:DHC:3342), *OpenTV Inc. v. Controller*
(C.A.(COMM.IPD-PAT) 14/2021) and *Raytheon Co. v. Controller General of Patents* (2023) — the line
of authority on which any Indian CRI prosecution now runs.

The 2013 Guidelines' own examples of technical effect are hardware-flavoured — *"higher speed,
reduced hard-disk access time, more economical use of memory"*. M1 with the M4 dependent can be
argued as reduced computation (bounded re-evaluation) and prevented duplicate I/O. It is a real
argument. It is not a strong one.

**Practical reality for a TCS-affiliated event.** Two things a jury of Indian CTOs will already
know, so say them first rather than being caught:
1. Indian examiners reject CRIs on §3(k) routinely, and "we will patent this" is a claim they hear
   at every hackathon and discount. What earns credit is showing you know *which* limb you are
   fighting and *what* the substance-over-form test is.
2. The correct move for a 7-day build is not "we will file a patent" — it is **file a provisional
   specification** to lock the priority date (§9 of the Patents Act gives twelve months to file
   the complete specification), keep the US as the primary jurisdiction, and defensively publish
   the rest. Saying that on stage is a stronger patentability answer than any claim chart.

### PS-17.5 — The defensible claim

Drafted to sit **above** US 11,935,046: the recomputed object is a normative verdict, the rules are
effective-dated, and the machine's authority to act is a function of the irreversibility of what it
already did.

> **Claim 1.** A computer-implemented method of maintaining consistency between machine-adjudicated
> conclusions and externally executed actions, the method comprising:
>
> **(a)** storing, in an append-only store, a plurality of obligation-rule versions, each
> obligation-rule version having a valid-time interval defining a contractual period over which the
> rule version governs and a transaction-time interval defining an interval during which the rule
> version was known to the system, wherein a first obligation-rule version and a second
> obligation-rule version have overlapping valid-time intervals and disjoint transaction-time
> intervals;
>
> **(b)** storing a plurality of evidence records, each evidence record having an evidence valid
> time and an evidence transaction time;
>
> **(c)** producing, by a deterministic adjudication engine, an adjudication record for an evidence
> record by evaluating the evidence record against the obligation-rule version whose valid-time
> interval contains the evidence valid time and whose transaction-time interval is current, the
> adjudication record comprising a normative verdict, a computed remedy quantum, and an
> input-closure identifier identifying the set of obligation-rule version identifiers and evidence
> record identifiers consumed in producing the normative verdict;
>
> **(d)** emitting an external action in dependence on the adjudication record and recording an
> action record comprising (i) an action-effect class selected from a set consisting of a reversible
> class, a compensable class and an irreversible class, and (ii) an idempotency key derived by
> applying a one-way function to the input-closure identifier and an action-type identifier;
>
> **(e)** receiving, after said emitting, an amendment record that supersedes the first
> obligation-rule version with the second obligation-rule version, the valid-time interval of the
> second obligation-rule version beginning before the transaction time of the amendment record;
>
> **(f)** identifying a re-adjudication set consisting of those adjudication records whose
> input-closure identifier identifies the first obligation-rule version and whose evidence valid
> time lies within the valid-time interval of the second obligation-rule version, and excluding from
> re-evaluation every adjudication record not in the re-adjudication set;
>
> **(g)** re-evaluating each adjudication record of the re-adjudication set against the second
> obligation-rule version to produce a superseding adjudication record, and appending each
> superseding adjudication record to the append-only store without deleting or overwriting the
> adjudication record it supersedes;
>
> **(h)** for each superseding adjudication record whose normative verdict or computed remedy
> quantum differs from that of the adjudication record it supersedes, selecting a disposition of the
> action record bound to the superseded adjudication record as a function of the action-effect class
> of that action record, wherein
> &nbsp;&nbsp;(i) for the reversible class, automatically emitting a reversing action under the
> idempotency key of the action record such that a repeated emission under said idempotency key
> produces no further external effect,
> &nbsp;&nbsp;(ii) for the compensable class, emitting a compensating action bound to said
> idempotency key and to the superseding adjudication record, and
> &nbsp;&nbsp;(iii) for the irreversible class, suppressing automatic emission of any action,
> enqueuing the superseding adjudication record to a human-approval queue, and blocking further
> automated emission bound to the superseded adjudication record until an approval record is
> received; and
>
> **(i)** recording, for each superseding adjudication record, a replay artifact comprising the
> input-closure identifier and a cryptographic digest computed over a canonical serialization of the
> input closure, whereby a re-execution of the deterministic adjudication engine over the input
> closure is verifiable against the digest.

> **Claim 2.** The method of claim 1, wherein each replay artifact further comprises a chaining
> digest computed over the cryptographic digest of the replay artifact and the chaining digest of an
> immediately preceding replay artifact in the append-only store, whereby modification of any
> recorded input closure invalidates every subsequent chaining digest.

> **Claim 3.** The method of claim 1, wherein each element identified by the input-closure
> identifier is associated with a provenance class selected from a set consisting of a recorded-fact
> class, a machine-inference class and a human-decision class, and wherein step (h) further comprises
> suppressing automatic emission of the reversing action and of the compensating action, and
> enqueuing the superseding adjudication record to the human-approval queue, when the input closure
> of the superseding adjudication record contains an element of the machine-inference class and the
> computed remedy quantum exceeds a configured authority threshold.

> **Claim 4.** The method of claim 1, wherein identifying the re-adjudication set in step (f)
> comprises traversing an index mapping obligation-rule version identifiers to adjudication record
> identifiers, whereby a cardinality of the re-adjudication set is bounded by a number of
> adjudication records whose input closure identifies the first obligation-rule version and is
> independent of a total number of adjudication records in the append-only store.

**Why these four.** Claim 1's point of novelty is (f)+(h) together — lineage-bounded
re-adjudication of *verdicts* plus irreversibility-conditioned authority — which is exactly the
territory US 11,935,046 does not occupy. Claim 4 supplies the computational-efficiency limitation
that gives the §101 Prong-Two and the EPO technical-character arguments something concrete to
stand on. Claim 3 encodes the brief's own governance requirement as a machine-checkable refusal
rule. Claim 2 is the cryptographic-provenance angle, correctly demoted to a dependent.

### PS-17.6 — Trade secret / defensive publication alternative

- **Patent (provisional first):** M1 only. One provisional, drafted as a data-consistency
  invention.
- **Defensive publication:** M2 (hash-chained input-closure digests) and M4 (lineage-directed
  selective re-evaluation). Both are too close to published art to survive prosecution and too
  visible in the product to keep secret — publishing them (timestamped repo, a technical note,
  or an IP.com-style disclosure) costs nothing and forecloses a competitor's later filing.
- **Trade secret:** the extraction prompts, the obligation taxonomy, the effect-class assignment
  heuristics, and the calibration of the authority thresholds. These are invisible at runtime,
  which is the only test that matters for choosing secrecy over disclosure — and *Content
  Extraction* tells you the extraction pipeline was never patentable anyway.

---

## PS-04: AI-Powered Dynamic Covenant Monitoring & Early Warning

### PS-04.1 — Prior-art landscape

> **Measurement caveat.** The numeric corpus counts I ran for PS-17 were captured before Google
> Patents blocked this environment; the equivalent covenant-side counts were not. The statements
> below about crowding are therefore **qualitative judgements supported by named records**, not
> machine counts. Treat any number a slide wants here as unverified until a proper search is run.

What is verifiable is the *shape* of the space, and it is bad news for PS-04.

| Number | Assignee / applicant | Title | Note |
| --- | --- | --- | --- |
| **EP 4 364 078 A1** | **Brex Inc.** | Automatic adjustment of limits based on machine learning forecasting | Verified in full at the EPO publication server. Filed 15 Jun 2022 (PCT/US2022/033679, WO 2023/278160), pub. 8 May 2024. Priority US 63/217,182 (30 Jun 2021) + US 17/560,114. CPC **G06Q 40/03; G06N 20/00; G06N 5/01; G06N 3/08**. This is ML forecasting driving an automatic credit-limit change. |
| US 2023/0342845 A1 | — | Systems and methods for data exploration analysis based covenant categorization and recommendation thereof | Covenant categorisation/recommendation. Could not open the record (blocked); number and title taken from an indexed search result. |
| US 2003/0083916 A1 | — | System for monitoring contractual compliance | 2003 priority — the space is over twenty years old |
| US 2003/0065613 A1 | — | Software for financial institution monitoring and management and for assessing risk for a financial institution | 2003 |
| US 2011/0270779 A1 / US 2015/0026035 A1 | — | Data analytics model(s) for loan treatment | ML-driven loan treatment prediction |
| US 2007/0203826 A1 | — | Fraud early warning system and method | "early warning" as a claimed term, 2007 |
| US 8,489,500 B2 | Federal National Mortgage Association (Fannie Mae) | Method and system for compliance hosting | — |
| US 2016/0196605 A1 | — | System and method to search and verify borrower information using banking and investment account data… | Borrower monitoring from account data |
| CN 112085310 A | — | AI management system for monitoring enterprise compliance credit | CN family |
| CN 118350927 A | — | Financial credit management and control system based on big data, artificial intelligence and blockchain — includes a post-loan early-warning model | CN family; the closest single description of PS-04's whole pitch |

**Reading.** CPC **G06Q 40/03** ("credit/loan processing… e.g. credit approval, risk assessment")
is the classification into which every one of PS-04's ideas falls, and it is a class that has been
worked continuously since the 1990s by banks, bureaux and rating agencies. Note that CN 118350927 A
describes, in its own abstract, a post-loan early-warning model producing automatic identification,
quantitative evaluation and active notification of customer risk — that is PS-04's entire journey,
already published. And EP 4 364 078 A1 shows a fintech already prosecuting ML-forecasting-drives-a-
credit-decision claims through the EPO.

The specific rating-agency/core-banking assignee sweeps requested (Moody's, S&P Global, FIS,
Fiserv, Oracle Financial Services, JPMorgan, Bank of America, Capital One) **could not be run** —
the assignee-facet queries are exactly what the block killed. **This is an open research item**, and
given Capital One's demonstrated appetite for filing (six applications off one provisional, §PS-17.2)
it should not be assumed empty.

### PS-04.2 — Where the white space is

**N1. Arithmetic-constrained conformal multi-horizon breach forecasting.** *(best available)*
Do not classify "breach / no breach" directly. Parse the covenant into its **expression tree**
(leaves = component quantities such as EBITDA, total debt, interest expense; internal nodes =
the covenant's own arithmetic; a comparison node = threshold and direction). Forecast the *leaves*,
attach split-conformal predictive intervals per leaf and per horizon, propagate those through the
covenant's own arithmetic to get a predictive distribution of the test statistic, and read the
breach probability off the comparison node. Driver attribution then falls out **additively by
construction** — hold one leaf at its last observed value, re-evaluate, and the delta in the test
statistic is that leaf's attribution — instead of being bolted on afterwards by SHAP.
*Novelty:* moderate. Conformal prediction itself is textbook (Vovk, Gammerman & Shafer,
*Algorithmic Learning in a Random World*, Springer 2005, DOI `10.1007/b106715`; Angelopoulos &
Bates, arXiv:2107.07511). What is not textbook is **binding the uncertainty propagation to the
covenant's own algebra** so that the attribution reconciles exactly to the covenant arithmetic.
That is a specific technical means. It is also the only PS-04 mechanism I would put in front of a
patent attorney.

**N2. Calibration-gated alerting.** Refuse to emit a breach probability when the measured empirical
coverage of the predictive intervals over a rolling validation window falls outside tolerance of
the nominal coverage; emit a *calibration-failure* signal instead. *Novelty:* thin, but it is a
concrete refusal condition on a measurable quantity, which is exactly the kind of limitation that
survives prosecution as a dependent claim.

**N3. Extraction-confidence-gated evidence request.** When the covenant definition element was
extracted from a credit agreement with confidence below a threshold, suppress the forecast and
emit an evidence request naming the element. *Novelty:* thin. *Content Extraction* (see §PS-17.4)
makes the extraction half unpatentable outright; the gating is a dependent at best.

**N4. Bitemporal restatement handling for financials.** A borrower restates a prior quarter;
covenant tests already run against the old figures must be re-run against the restated ones with
the old results preserved. *Novelty: blocked.* This is **US 11,935,046** almost exactly — a
correcting event replayed forward over a financial data state, with claims 2/8/10 anchored to
account balances. PS-04's version is *closer* to Capital One's claim than PS-17's is, because
PS-04 really is recomputing a data value rather than a normative verdict. **Do not pitch this.**

### PS-04.3 — Subject-matter eligibility

**United States.** This is the sharpest finding in the lane.

The 2019 PEG's abstract-idea groupings catch PS-04 twice:
- Grouping (a), *mathematical concepts* — "mathematical relationships, mathematical formulas or
  equations, mathematical calculations". Conformal intervals, ratio arithmetic, probability of
  breach.
- Grouping (b), *certain methods of organizing human activity* — expressly including *"fundamental
  economic principles or practices (including hedging, insurance, **mitigating risk**)"*.
  Predicting a covenant breach so a bank can intervene early **is** mitigating risk. This is not
  an analogy; it is the enumerated example.

And *Electric Power Group* — collect from disparate sources, analyse, display, even in real time,
even field-limited — describes PS-04's six-step journey line for line. The court there also held
that specifying *what* information is desirable to gather in a particular field does not save the
claim.

Prong Two has almost nothing to grip. There is no improvement to a computer, no particular
machine, no transformation of an article. The 2024 AI eligibility guidance update (**89 FR 58128**,
17 Jul 2024, docket PTO-P-2024-0026, with Examples 47–49) is explicit that AI claims must show an
improvement to *the technology*, not merely a more accurate prediction within a business field.
More accurate covenant forecasting is a better business answer, not a better computer.

*Honest verdict: N1 has a poor chance; N2, N3, N4 have essentially none. If PS-04 wins the
hackathon it will not be on patentability.*

**Europe.** Worse, and cleanly so. EPO Guidelines G-II 3.3.2 (rewritten after **G 1/19**,
Enlarged Board, 10 Mar 2021, which confirmed COMVIK applies to computer-implemented simulations):

> "Calculated numerical data reflecting the physical state or behaviour of a system or process
> existing only as a model in a computer usually cannot contribute to the technical character of
> the invention, even if it adequately reflects the behaviour of the real system or process."

and

> "For establishing a technical effect, it is not decisive whether the simulated system or process
> is technical or whether the simulation reflects technical principles underlying the simulated
> system and how accurately it does so."

PS-04 simulates a borrower's financial trajectory. The simulated system is not merely
non-physical, it is *economic*. The Guidelines allow a purely numerical simulation to contribute
technical character only through adaptation to the internal functioning of the computer, or
through an intended technical use of the output. Neither is present: the intended use of PS-04's
output is a relationship manager picking up the phone. **Expect refusal under Art. 52(2)(a) and
(c) EPC.**

**India.** PS-04 hits **two** limbs of §3(k) simultaneously — *mathematical method* and *business
method*. Against the 2013 CRI Guidelines' "in substance… business method even with the help of
technology" and the 2017 Guidelines' substance-over-form test, a credit-risk forecasting engine
is about as clear a §3(k) case as exists. *Ferid Allani* does not help much here, because the
technical-effect examples the IPO itself gives are computational (speed, memory, disk access) and
N1's effect is *forecast accuracy*, which is not a technical effect at all. **Assume refusal.**

**Practical reality for a TCS-affiliated event.** A room of Indian CTOs, several of whom will have
prosecuted CRIs, will not believe a covenant-forecasting patentability claim. If PS-04 is chosen,
the honest and credit-earning line is: *"the model is not the IP; the calibrated-refusal contract
and the covenant-algebra attribution are what we would file, and we expect §3(k) trouble in
India — so our protection strategy here is trade secret plus speed, not patent."* Claiming
otherwise in front of that jury is a self-inflicted wound.

### PS-04.4 — The defensible claim

> **Claim 1.** A computer-implemented method for generating a calibrated covenant-breach forecast,
> the method comprising:
>
> **(a)** parsing a machine-readable covenant definition into an expression tree comprising a
> plurality of leaf nodes each corresponding to a component financial quantity, one or more internal
> nodes each corresponding to an arithmetic operator, and a comparison node defining a threshold
> value and a direction of breach;
>
> **(b)** generating, for each leaf node and for each of a plurality of forecast horizons, a point
> prediction of the component financial quantity from a time series of observed values of that
> component financial quantity;
>
> **(c)** computing, for each leaf node and each forecast horizon, a set of non-conformity scores
> over a calibration set of historical observations of that component financial quantity at that
> forecast horizon, and deriving from the set of non-conformity scores a predictive interval having
> a specified marginal coverage level;
>
> **(d)** propagating the predictive intervals of the leaf nodes through the internal nodes of the
> expression tree to obtain, for each forecast horizon, a predictive distribution of a covenant test
> statistic;
>
> **(e)** evaluating the predictive distribution against the comparison node to obtain a breach
> probability for each forecast horizon;
>
> **(f)** computing a driver attribution by, for each leaf node, re-evaluating the expression tree
> with that leaf node held at a last observed value of its component financial quantity while
> remaining leaf nodes take their point predictions, and assigning to that leaf node an attribution
> equal to a resulting change in the covenant test statistic, whereby the attributions reconcile to
> a total movement of the covenant test statistic by construction of the expression tree; and
>
> **(g)** gating emission of an alert comprising the breach probability and the driver attribution
> on a measured empirical coverage of the predictive intervals over a rolling validation window
> lying within a tolerance of the specified marginal coverage level, and otherwise emitting a
> calibration-failure signal in place of the breach probability.

> **Claim 2.** The method of claim 1, wherein the non-conformity scores of step (c) are partitioned
> by a taxonomy comprising at least a component-financial-quantity identifier and a forecast
> horizon, such that the predictive interval derived for a given leaf node and forecast horizon is
> conditioned on that partition.

> **Claim 3.** The method of claim 1, wherein the propagating of step (d) comprises sampling from
> the predictive intervals of the leaf nodes according to a dependence structure estimated from a
> historical residual covariance among the component financial quantities, and the breach
> probability of step (e) is a sampled fraction of the propagated samples satisfying the direction
> of breach at the threshold value.

> **Claim 4.** The method of claim 1, wherein at least one leaf node is bound to a covenant
> definition element extracted from a credit agreement document with an associated extraction
> confidence, and the method further comprises suppressing emission of the alert and emitting an
> evidence request identifying the covenant definition element when the extraction confidence is
> below a configured threshold.

**Be honest on stage about this one.** Steps (a)–(f) are, under Alice Step 2A Prong One, a
mathematical method applied to a fundamental economic practice. Step (g) is the only element that
does something other than compute-and-display, and it does it by refusing. If the team presents
this claim, it should present it *with* the §101 analysis — "here is our strongest claim and here
is why we rate its US odds low and its EPO/India odds near zero" — which reads as sophistication.
Presenting it as a slam dunk reads as naivety.

### PS-04.5 — Trade secret / defensive publication alternative

Patenting is weak here, so name the alternative plainly:
- **Trade secret is the primary instrument.** The calibration sets, the industry-conditioned
  non-conformity taxonomy, the residual covariance structure, the horizon-specific feature
  windows and the intervention playbook mapping are all invisible from outside the product and
  all constitute the actual moat. Nothing about them can be reverse-engineered from an alert.
- **Defensive publication for the covenant-algebra attribution (N1(f)).** It is the most
  imitable idea and the least likely to survive prosecution; publishing it timestamped denies a
  competitor the ability to claim it later.
- **The real commercial protection for PS-04 is data and distribution, not IP** — proprietary
  calibration corpora and core-banking integration depth. Say so; it is a more credible answer
  than a patent claim a CTO jury will discount.

---

## Head-to-head verdict for this lane

| | PS-17 | PS-04 |
| --- | --- | --- |
| Prior-art density in the core space | Thin. CLM vendors have single-digit portfolios; 83 US full-text hits for "contract lifecycle management"; **0** hits for amendment-triggered effective-date recalculation. | Dense and old. CPC G06Q 40/03 has been worked since the 1990s by every bank, bureau and rating agency; a 2003 priority already claims "monitoring contractual compliance"; CN 118350927 A publishes the whole PS-04 journey. |
| Is the headline mechanism blocked? | **Partly.** US 11,935,046 B2 (Capital One) owns bitemporal branch-replay-promote at the storage layer. The *action layer* above it is open. | **Yes for N4**, which is nearer to '046 than PS-17's version. N1 is open but modest. |
| §101 (US) | Coin flip for the action-layer claim, if drafted as a consistency invention and not as contract monitoring. | Poor. Two PEG groupings plus *Electric Power Group* squarely on the facts. |
| EPO | Arguable via "adaptation to internal functioning" (idempotency, bounded recomputation). Narrow. | Refusal expected. G-II 3.3.2's "model existing only in a computer" passage is written for this case. |
| India §3(k) | Weak but arguable on a computational-efficiency technical effect. | Two limbs hit at once. Assume refusal. |
| Is there a *specific technical means*, not a business outcome? | **Yes** — irreversibility-typed disposition of already-emitted actions under lineage-bounded re-adjudication. | Partially — covenant-algebra-constrained conformal propagation. The rest is prediction-and-display. |
| **Score** | **6 / 10** | **3 / 10** |

**Winner: PS-17, by 3 points.**

The margin is not "contracts are more novel than credit". It is structural: **PS-17's hard part is
a state-and-consistency problem, and consistency problems produce technical means. PS-04's hard
part is a prediction problem, and prediction problems produce numbers.** Patent law in all three
jurisdictions rewards the former and punishes the latter. The 2019 PEG hands you an enumerated
abstract-idea grouping that names risk mitigation; the EPO Guidelines hand you a paragraph written
to exclude simulations of non-physical systems; §3(k) hands you two exclusions at once. PS-04
loses on the law, not on the art.

PS-17 scores 6 rather than 8 solely because of Capital One. Before reading US 11,935,046 the
obvious pitch — "bitemporal re-evaluation when an amendment lands" — looked like a 9. It is a 2.
What is left is the narrower, more interesting, and genuinely unclaimed thing above it.

**What would change this verdict:**
1. **If the five Capital One siblings claim the action layer.** If 17/516,345 or 17/516,436 reach
   irreversibility triage or effect-class-conditioned emission, PS-17 drops to 3/10 and the two
   are level. **This is the single highest-value follow-up and it is unresolved.**
2. **If a rating-agency or core-banking assignee sweep comes back thin.** The Moody's / S&P / FIS /
   Fiserv / Oracle FS / JPMorgan / BofA / Capital One queries could not be run. If that space is
   emptier than I judge, PS-04 rises to perhaps 5 — still short, because §101 and §3(k) do not
   care how empty the art is.
3. **If the team is willing to claim at the systems layer only.** A claim that never mentions
   contracts or covenants, and speaks only of effect-classed external actions under retroactive
   rule correction, improves PS-17's US odds materially and would push it to 7.
4. **If the jury reads "patentability" as "did they show they understand it".** Both problems then
   score higher, and PS-04's honest §101 self-assessment becomes an asset rather than a liability.

### Where this lands in the sub-problem decomposition

For the pitch's "N hard sub-problems, one named solution each" spine, this lane contributes for
PS-17:

| Sub-problem | Named mechanism | IP posture |
| --- | --- | --- |
| A late amendment invalidates conclusions already reached | **Effective-dated re-adjudication** (rule versions with valid-time/transaction-time intervals) | **File.** Novel above US 11,935,046. |
| Re-running everything is wrong and slow | **Lineage-bounded re-adjudication set** | Dependent claim only — ATMS/IVM/provenance-semiring art is fatal standalone. |
| Some things we already did cannot be undone | **Irreversibility triage** (reversible / compensable / irreversible → auto-reverse / compensate / quarantine) | **File. This is the point of novelty.** |
| The machine must not act twice, or act beyond its authority | **Input-closure-derived idempotency keys** + **typed-provenance authority gate** | Dependent claims. |
| A reviewer must be able to prove what the system knew | **Chained digest over the decision's input closure** | Defensive publication; dependent claim. |

---

## Risks and open questions

1. **[TOP RISK — unresolved] The five Capital One sibling applications.** Serials 17/516,329,
   17/516,338, 17/516,340, 17/516,345, 17/516,436, all filed 1 Nov 2021. I have their titles from
   the '046 specification's cross-reference paragraph but **could not retrieve their claims or map
   them to granted patent numbers**: Google Patents, Espacenet, Justia, FreePatentsOnline and
   PatentsView all blocked this environment, and USPTO Patent Center / assignment / ODP APIs
   require credentials. **No freedom-to-operate statement about PS-17's mechanism should go on a
   slide until these five are read.** Resolve by running each serial through USPTO Patent Public
   Search or Google Patents from an unblocked network, or by ordering a professional search.
2. **Assignee sweeps not run for PS-04.** Moody's, S&P Global, FIS, Fiserv, Oracle Financial
   Services, JPMorgan, Bank of America and Capital One were all requested and none could be
   executed. My PS-04 crowding judgement rests on named records and CPC structure, not counts.
3. **PS-04 records not opened.** US 2023/0342845 A1, US 2003/0083916 A1, US 2003/0065613 A1,
   US 2011/0270779 A1, US 2015/0026035 A1, US 2007/0203826 A1, US 8,489,500 B2,
   US 2016/0196605 A1, CN 112085310 A and CN 118350927 A come from indexed search results that
   linked to live Google Patents pages; the numbers and titles are as indexed, but I could not
   open the records to confirm assignee, claims or legal status. **Verify before citing on a
   slide.** EP 4 364 078 A1 (Brex Inc.) is the one PS-04 record I verified in full.
4. **Google Patents totals are approximate.** `total_num_results` is a search-engine estimate over
   full text, not a curated family count. Two of my queries (`"service level agreement" violation
   prediction`, unquoted) are relevance-ranked rather than strict-phrase and the 4,290 figure
   should be read as "this is a busy area", nothing more.
5. **§101 estimates are judgement, not prediction.** Outcomes at the USPTO turn heavily on art
   unit and examiner. Nothing here is legal advice and none of these claims has been reviewed by
   counsel.
6. **The 2025 Indian CRI guidelines are draft.** The operative documents I verified on ipindia.gov.in
   are the 2013 and 2017 CRI Guidelines. A revised set was put out for public consultation in 2025;
   I could not locate the official draft PDF on ipindia.gov.in (the guidelines index serves
   opaque UUID filenames and my enumeration timed out), so I have relied on the 2013/2017 primary
   texts and not on the draft. Commentary on the draft consistently reports "technical effect" as
   the gateway concept, which is continuous with *Ferid Allani*, so the analysis above is unlikely
   to change — but confirm the current text before filing.
7. **Claim drafting here is illustrative.** The claims in §PS-17.5 and §PS-04.4 are written to
   demonstrate that the team knows what a claim is and where the point of novelty sits. They are
   not filing-ready and should not be described as such.

---

## Sources

Every URL below was opened or machine-queried in this session unless marked.

**Patent records — verified in full**

1. US 11,935,046 B2 — grant PDF (scanned; OCR'd for claim text):
   https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11935046
2. EP 4 364 078 A1 (Brex Inc.) — EPO publication server:
   https://data.epo.org/publication-server/rest/v1.2/patents/EP4364078NWA1/document.html

**Patent records — retrieved via Google Patents machine query before the block** (numbers,
assignees, titles and dates as returned by `patents.google.com/xhr/query`; individual pages could
not subsequently be opened)

3. Icertis: US 11,151,501 B2; US 12,020,130 B2; US 10,409,805 B1; US 10,162,850 B1;
   US 10,936,974 B2; US 11,593,440 B1; DE 11 2020 000 860 T5
4. Sirion: US 11,482,027 B2; US 2020/0226510 A1; EP 3 908 997 A1; US 2014/0129276 A1
5. Clause/DocuSign: US 2021/0192650 A1; US 2020/0104296 A1; US 2024/0202663 A1; US 2022/0108411 A1
6. Other CLM/SLA adjacency: US 8,732,047 B2 (SciQuest); US 11,055,703 B2 (Hitachi);
   US 11,941,374 B2 and US 12,488,310 B2 (NB Ventures/GEP); US 9,965,735 B2 (Energica);
   US 10,289,973 B2 (Ericsson); US 9,141,927 B2 and US 2008/0262981 A1 (IBM);
   US 11,762,921 B2 (Eigen); US 11,301,619 B2 (Zensar); US 11,379,735 B2 / US 11,922,325 B2
   (Legislate/Textmine)
7. Temporal-database art: US 8,219,522 B2 and US 8,713,073 B2 (Asserted Versioning);
   US 8,965,889 B2, US 9,330,119 B2, US 11,468,098 B2 (Oracle); US 10,789,239 B2 (AlphaPoint)

**Patent records — number and title from indexed search results, records NOT opened
(mark as unverified)**

8. US 2023/0342845 A1; US 2003/0083916 A1; US 2003/0065613 A1; US 2011/0270779 A1;
   US 2015/0026035 A1; US 2007/0203826 A1; US 8,489,500 B2 (Fannie Mae); US 2016/0196605 A1;
   CN 112085310 A; CN 118350927 A — all indexed at `patents.google.com/patent/<number>/en`,
   which returned HTTP 503 for the duration of this research.

**US eligibility law and guidance**

9. 2019 Revised Patent Subject Matter Eligibility Guidance, 84 FR 50 (7 Jan 2019), docket
   PTO-P-2018-0053: https://www.govinfo.gov/content/pkg/FR-2019-01-07/html/2018-28282.htm
10. October 2019 Update: Subject Matter Eligibility:
    https://www.uspto.gov/sites/default/files/documents/peg_oct_2019_update.pdf
11. 2024 Guidance Update on Patent Subject Matter Eligibility, Including on Artificial
    Intelligence, 89 FR 58128 (17 Jul 2024), docket PTO-P-2024-0026:
    https://www.govinfo.gov/content/pkg/FR-2024-07-17/html/2024-15377.htm
12. USPTO AI SME Examples 47–49:
    https://www.uspto.gov/sites/default/files/documents/2024-AI-SMEUpdateExamples47-49.pdf
13. MPEP § 2106: https://www.uspto.gov/web/offices/pac/mpep/s2106.html
14. *Electric Power Group, LLC v. Alstom S.A.*, 830 F.3d 1350 (Fed. Cir. 2016) — slip opinion:
    https://www.cafc.uscourts.gov/opinions-orders/15-1778.opinion.7-28-2016.1.pdf
15. *Content Extraction & Transmission LLC v. Wells Fargo Bank*, 776 F.3d 1343 (Fed. Cir. 2014):
    https://www.bitlaw.com/source/cases/patent/Content-Extraction.html
16. *Alice Corp. v. CLS Bank Int'l*, 573 U.S. 208 (2014) — U.S. Reports:
    https://tile.loc.gov/storage-services/service/ll/usrep/usrep573/usrep573208/usrep573208.pdf

**European eligibility law and guidance**

17. EPO Guidelines for Examination, G-II 3.3.2 "Simulation, design or modelling" (2025 edition) —
    quoted verbatim above:
    https://www.epo.org/en/legal/guidelines-epc/2025/g_ii_3_3_2.html
18. G 1/19 (Enlarged Board of Appeal, 10 Mar 2021) — decision page:
    https://www.epo.org/en/boards-of-appeal/decisions/g190001ex1
19. Mewburn Ellis, "EPO confirms that the COMVIK approach… is applicable to computer simulations
    (G1/19)":
    https://www.mewburn.com/forward/epo-confirms-that-the-comvik-approach-to-assessing-patent-eligibility-is-applicable-to-computer-simulations-g1-19

**Indian eligibility law and guidance**

20. Guidelines for Examination of Computer Related Inventions (CRIs), Office of the CGPDTM, **2017**
    — quoted verbatim above:
    https://ipindia.gov.in/storage/uploads/docs-operator/60dda8e5-510a-4e7d-918a-53c0d49266ca.pdf
21. Guidelines for Examination of Computer Related Inventions (CRIs), Office of the CGPDTM, **2013**
    — quoted verbatim above (business-method substance test, technical-effect examples):
    https://ipindia.gov.in/storage/uploads/docs-operator/125154d2-4490-41aa-9a8c-0ee97219692f.pdf
22. IPO Annexure II — List of case laws related to CRIs (lists *Ferid Allani*, *Microsoft
    Technology Licensing*, *OpenTV*, *Raytheon*, *Priya Randolph*, *Ericsson v. Lava*):
    https://ipindia.gov.in/storage/uploads/docs-operator/144d1bd8-7995-41f8-b38d-3d5a98e489ab.pdf
23. IPO patents guidelines index (source of 20–22):
    https://ipindia.gov.in/resource/patents-resources-guidelines
24. *Ferid Allani v. Union of India*, W.P.(C) 7/2014, Delhi High Court, 12 Dec 2019 — commentary
    (judgment text itself not opened in this session):
    https://sflc.in/say-no-to-software-patents-ii-the-judgements-that-shaped-technical-effect/
25. Draft CRI Guidelines 2025 — commentary only; official draft PDF **not located** on
    ipindia.gov.in in this session:
    https://corporate.cyrilamarchandblogs.com/2025/04/draft-guidelines-for-examination-of-computer-related-inventions-2025/

**Publication prior art relied on in the novelty assessment** (all bibliographic records confirmed
via the Crossref API in this session)

26. de Kleer, J., "An assumption-based TMS", *Artificial Intelligence* 28(2), 1986 —
    DOI `10.1016/0004-3702(86)90080-9`
27. Doyle, J., "A truth maintenance system", *Artificial Intelligence* 12(3), 1979 —
    DOI `10.1016/0004-3702(79)90008-0`
28. Green, T.J., Karvounarakis, G., Tannen, V., "Provenance semirings", PODS 2007 —
    DOI `10.1145/1265530.1265535`; open PDF: https://web.cs.ucdavis.edu/~green/papers/pods07.pdf
29. Gupta, A., Mumick, I.S., Subrahmanian, V.S., "Maintaining views incrementally", SIGMOD 1993 —
    DOI `10.1145/170035.170066`
30. Garcia-Molina, H., Salem, K., "Sagas", SIGMOD 1987 — DOI `10.1145/38713.38742`;
    open PDF: https://www.cs.cornell.edu/andru/cs711/2002fa/reading/sagas.pdf
31. McSherry, F., Murray, D., Isaacs, R., Isard, M., "Differential dataflow", CIDR 2013:
    https://www.microsoft.com/en-us/research/publication/differential-dataflow/
32. Kulkarni, K., Michels, J.-E., "Temporal features in SQL:2011", *ACM SIGMOD Record* 41(3), 2012 —
    DOI `10.1145/2380776.2380786`
33. Haber, S., Stornetta, W.S., "How to time-stamp a digital document", *Journal of Cryptology*
    3(2), 1991 — DOI `10.1007/BF00196791`
34. RFC 6962, "Certificate Transparency": https://www.rfc-editor.org/rfc/rfc6962
35. W3C PROV-DM: The PROV Data Model (W3C Recommendation): https://www.w3.org/TR/prov-dm/
36. Vovk, V., Gammerman, A., Shafer, G., *Algorithmic Learning in a Random World*, Springer 2005 —
    DOI `10.1007/b106715`
37. Angelopoulos, A.N., Bates, S., "A Gentle Introduction to Conformal Prediction and
    Distribution-Free Uncertainty Quantification", arXiv:2107.07511:
    https://arxiv.org/abs/2107.07511

**Sources consulted but unusable (recorded so the next researcher does not repeat the attempt)**

38. `patents.google.com` (search XHR and patent pages) — HTTP 503 bot-block from ~30 min into
    this session onward; did not recover over ~60 min of paced retries.
39. `worldwide.espacenet.com`, `patents.justia.com`, `www.freepatentsonline.com`,
    `www.patentguru.com`, `uspto.report` — HTTP 403.
40. `api.patentsview.org` (retired) and `search.patentsview.org` (API key required);
    `api.uspto.gov` ODP (HTTP 401 Unauthorized); `patentcenter.uspto.gov` retrieval API
    (HTTP 405); `assignment-api.uspto.gov` (empty responses); `ppubs.uspto.gov` search endpoints
    (WAF 404); `patentscope.wipo.int` (result count rendered client-side, not retrievable
    without a JSF session).
