# Sub-problem decomposition and pitch narrative — comparative research

> Lane: how each problem statement breaks into named, defensible sub-problems; which of those a
> typical team will miss; the demo arc; and the CTO objection map.
> Clean-room: written from the two briefs plus external sources only.

---

## Executive answer

- **PS-17 decomposes better, and it is not close: 9/10 vs 7/10.** The reason is structural, not
  aesthetic — PS-17's sub-problems are each *visible on a screen*, and PS-04's hardest sub-problems
  are statistical and invisible. A jury scoring "can the solution be seen" rewards the former.
- **PS-17's decomposition is handed to you by the brief.** Section 04's seven capability bullets map
  almost one-to-one onto seven sub-problems. Naming them back as "the seven hard problems we found"
  is simultaneously an original-looking pitch spine and a literal rubric compliance checklist.
- **The national-finale inject is one sentence hiding three separate failures**, and most teams will
  only see the first. (1) Re-evaluate under the new threshold. (2) Realise the amendment's *effective
  date can precede its signature date*, so the correct answer is retroactive, not prospective — the
  opposite of what every durable-execution framework does by default (Temporal's `patched()`
  explicitly *keeps in-flight executions on the old code* [8]). (3) Reconcile the revised conclusion
  with **actions already taken** — the credit note posted, the notice served. Part 3 is the graded
  moment and the pitch's "oh no" beat.
- **The single sharpest line for PS-17:** *"Re-evaluating the past is the easy half. Reconciling the
  past with what you already did about it is the half that decides whether you get sued."* This has
  a legal spine — courts decline retroactive effect where it "prejudices intervening rights" [11].
- **The incumbent gap in PS-17 is demonstrable, not asserted.** Icertis's own amendment
  documentation covers creating, inheriting and diffing amendment attributes and is **silent on
  retroactive effect and on re-evaluating pre-amendment events** [1]. Agiloft's December 2025
  "enterprise-grade obligation management" launch — the market frontier — is described as
  extract → assign → deadline → remind → escalate [12]. Nobody publicly claims decision re-derivation.
- **PS-04's two best sub-problems are genuinely world-class** — *The Three Clocks* (test date vs.
  certificate delivery date vs. observation date; get this wrong and your backtest has label leakage
  and your "90-day warning" is 45 days of reporting lag) and *Breach ≠ Loss* (~63% of covenant
  violations are waived without changing major terms [14]). But they are hard to photograph.
- **PS-04 has a pitch trap the team must dodge deliberately.** The brief asks for a breach probability.
  Probabilities cannot be validated on synthetic data, and the public bar is brutal: Moody's EDF-X
  flags only **43% of eventual private-firm defaults 12 months out, and 51% one month out**, with a
  44% combined error rate at a 17% watchlist rate [6]. "Our model is better" is an unwinnable frame
  in front of CTOs who can look that up. The team would have to reframe to "ours is *actionable*",
  which is a subtler and slower pitch.
- **PS-17's backend depth is legible in one visual** (a time slider over a bitemporal ledger).
  PS-04's backend depth is a point-in-time feature store, which is real engineering and looks like
  nothing.
- **Both need an autonomy matrix.** PS-17's is handed to you — the brief *itself* removes legal
  interpretation, contractual notice and material settlement from the machine. PS-04's must be
  argued from outside the brief (UCC §1-309 good-faith standard on insecurity/acceleration [15]).

---

## PS-17: Contract Obligation, SLA & Commercial Leakage Monitor

### The framing number

WorldCC/IACCM's 2014 research put average contract value erosion at **9.2% of contract value**.
The 2023 re-run with Deloitte, across **more than 1,200 organizations**, found it now stands at
**8.6%, with the best performers at a little over 3% and the worst above 20%** [10]. A decade of
CLM investment moved the number **0.6 percentage points**. That is the opening slide: the market
bought software and the leak did not close. Something in the decomposition is wrong.

### The seven sub-problems

Each is named for a slide title. Each has: the hard truth, the failure mode, the mechanism.

---

#### SP-17.1 — **Bitemporal Truth**

**Hard truth.** Every fact in this system has two dates: when it was true in the world, and when you
found out. An invoice correction that lands in September restates July. A ticket reopened on the 20th
changes the outage duration on the 3rd.

**Failure mode.** You store one timestamp and you `UPDATE` in place. Now two things are broken:
the audit trail says the system always knew the right answer (a lie), and the breach report you sent
the customer in July **cannot be reproduced**. The brief's phrase "without losing earlier evidence"
is not a nicety — it is the whole data model.

**Mechanism — the bitemporal ledger.** Nothing is ever updated. Every fact carries
`(valid_from, valid_to, recorded_at, superseded_at)` — Snodgrass's valid-time / transaction-time
split, standardised as application-time and system-time period tables in SQL:2011 [7]. Fowler's
framing is the one to put on the slide: *"what did we think Sally's salary history was on
February 25?"* — record history is **append-only; we never delete what we previously thought** [2].
The public API is a single call: `as_of(decision_time, knowledge_time)`. The demo is a two-axis time
slider.

*Windows/no-Docker note: this is hand-rollable on SQLite or embedded Postgres; no temporal DB needed.*

---

#### SP-17.2 — **The Amendment Inject**

*This is the graded moment. It deserves its own decomposition — see the dedicated section below.*

**Hard truth.** An amendment is not a new document. It is a **time-indexed patch to the rulebook**,
and the events it governs have already happened and already been acted on.

**Failure mode.** Three, and teams typically see only the first:
1. *Overwrite.* Re-run everything under the new threshold; the past silently changes; the prior
   conclusion is gone.
2. *Prospective-only.* Apply the amendment from its signature date. But an amendment's **effective
   date can precede its signature date** — retroactive amendment (`nunc pro tunc`, effective
   *ab initio*) is ordinary commercial practice [11]. Prospective-only is the industry-standard
   engineering default and it is the wrong answer here. Temporal's versioning docs are explicit:
   patching "applies a code change to new Workflow Executions **while avoiding disruptive changes to
   in-progress Workflow Executions**" [8]. The correct behaviour for PS-17 is the inverse.
3. *No reconciliation.* You re-evaluate correctly and never ask what you already **did** about the
   old conclusion — the credit note posted, the notice served, the escalation opened.

**Mechanism — Effective-Version Resolver + Decision Re-derivation with Action Reconciliation.**
Three named parts (see the dedicated section).

---

#### SP-17.3 — **The Evidence Fog**

**Hard truth.** Systems do not disagree by being empty. They disagree by being confidently different.
Ticketing says the outage was 47 minutes; monitoring says 63; the invoice already credited for 30.

**Failure mode.** Last-writer-wins. Whichever feed loaded last silently becomes truth and the breach
determination becomes a coin flip on ingestion order. The brief asks you to "represent competing
interpretations… without hiding uncertainty" — a single `duration_minutes` column cannot do that.

**Mechanism — Claim / counter-claim reconciliation.** Evidence is stored as **claims by a source**,
never as facts. A `Fact` is a *derived* object with (a) a resolution policy — usually the contract
itself names the system of record — (b) a confidence, and (c) an explicit `CONTESTED` state that
**blocks automated action** and routes to a human. On screen: three coloured bars of different length
over the same incident, and a red lock on the action button.

---

#### SP-17.4 — **The Silent Entitlement** (and the value-and-deadline scheduler)

**Hard truth.** The money is not lost when the SLA is breached. It is lost when nobody files the
claim inside the window. Most commercial SLAs make the credit *claimable, not automatic*, and an
unclaimed credit is waived — customers must request in writing within a stated period after the
affected billing period, and the burden of evidencing the outage sits with the claimant [17,
*verify against a primary SLA before quoting*].

**Failure mode.** You build a beautiful breach detector and recover nothing, because the claim
deadline was 30 days after the affected billing period and your alert fired on day 41. You are
"compliant" and still poorer.

**Mechanism — the Entitlement Clock.** Every detected breach instantiates a **claim object** with
its own contractual deadline, parsed from the remedy clause. The workspace does not prioritise by
severity; it prioritises by **time-to-forfeiture × recoverable value × confidence**. This is exactly
the brief's "determine the next-best evidence request or permitted action dynamically from current
state, **deadlines, dependencies, authority and expected value**" — the entitlement clock *is* the
next-best-action scheduler, and saying so out loud maps the sub-problem onto the rubric.

**Why this is the leakage insight:** CLM sells *obligation tracking* ("did we do the thing"). Revenue
assurance sells *leakage recovery* ("did we collect the thing we're owed") — and it is a different
discipline in a different department with different tooling (TM Forum's business-assurance track).
The seam between the two is where the money sits, and neither side's product owns it.

---

#### SP-17.5 — **Provenance vs. Inference**

**Hard truth.** "The SLA threshold is 99.5%" is either a quoted span in a signed PDF or a model's
guess, and a CTO must be able to tell which in one click.

**Failure mode.** An LLM paraphrases a threshold, the number is plausible, and you serve a breach
notice on a hallucinated term. This is not paranoia: Dahl et al. (Stanford RegLab, *Journal of Legal
Analysis* 2024) found legal hallucination rates on specific, verifiable questions ranging from
**58% (GPT-4) to 88% (Llama 2)**, and — more damning — that models "cannot always predict, or do not
always know, when they are producing legal hallucinations" [13].

And extraction itself is not solved. On CUAD (510 expert-annotated commercial contracts, 13,101
annotations, 41 clause categories), the best model reported is **DeBERTa-xlarge at 47.8% AUPR,
44.0% precision at 80% recall, and 17.8% precision at 90% recall** [4]. The honest design
assumption is *"extraction is wrong roughly one time in two at useful recall"* — not *"extraction works."*

**Mechanism — span-anchored extraction under a four-colour provenance model.** Every field is
tagged **RECORDED FACT / AI INFERENCE / USER INPUT / HUMAN DECISION** (the brief demands exactly this
separation, plus AUTOMATED ACTION as a fifth). Every AI inference carries `document_id` +
character offsets and renders as a click-through highlight in the source PDF. **Nothing tagged
INFERENCE crosses an action gate without a human promotion event**, and that promotion is itself a
HUMAN DECISION record.

---

#### SP-17.6 — **Exactly-Once in the Real World**

**Hard truth.** Sending a breach notice twice is worse than sending it zero times — and the failure
that makes you retry is precisely the failure that stops you knowing whether the first one landed.

**Failure mode.** Partial success. Credit note posted to billing, notice email failed, workflow
retried, customer receives two credits and one notice for the same outage. The brief calls this out
by name: "preventing duplicate requests, duplicate transactions or repeated external actions" and
"safe recovery when only part of a workflow succeeds."

**Mechanism — the Action Ledger.** A content-addressed idempotency key
(`contract_id + obligation_version_id + event_window + action_type`), a two-phase intent→commit
record, and a **compensation catalogue** that classifies every action as reversible (draft, internal
task) or irreversible (notice served, credit note posted, payment made). That classification is the
interlock for SP-17.2: **an irreversible action turns re-derivation from a computation into a human
question.**

**Pitch value:** idempotency is invisible in a demo, which is exactly why nobody builds it and
exactly why *showing* it (kill the worker mid-action; restart; watch it not double-send) wins
backend-depth points that no other team will contest.

---

#### SP-17.7 — **The Autonomy Boundary**

**Hard truth.** The brief itself removes the three highest-value decisions from the machine: *legal
interpretation, contractual notice, and material commercial settlement remain human-owned.* So the
system's job is to make the human's decision **cheap and defensible**, not to make it.

**Failure mode.** A team builds an agent that "resolves the dispute end to end" and gets marked
down for violating an explicit stated constraint while thinking it is impressing the jury.

**Mechanism — a five-level autonomy matrix bound to action types**, rendered as a live UI panel:
L1 observe (ingest, reconcile) · L2 recommend (draft a claim) · L3 act-with-approval (post a credit
note) · L4 act-and-notify (open a re-derivation case, raise monitoring frequency) · L5 autonomous
(recompute a metric, re-index evidence). Every gate crossing writes to the same append-only ledger,
so "audit replay" is a query, not a feature.

---

#### SP-17.8 (optional) — **The Sealed Notice**

Only include if it is load-bearing, and here it genuinely is: the counterparty's *first* challenge
after a retroactive amendment is **"you changed the record after the fact."** A Merkle/hash chain
over the decision ledger, with a daily signed root, makes "what we knew on 12 July" cryptographically
attestable without needing a chain runtime on a Windows box. Note honestly that bitemporal storage
and bitemporal compliance analysis already have patent prior art [9, *claim text unverified*], so
the novelty argument must live in re-derivation + action reconciliation, not in "we stored two dates."

---

### The national-finale inject, decomposed

> *"A contract amendment changes an SLA threshold after potential breaches were flagged. The system
> must re-evaluate each event using the correct effective version."*

That sentence demands **six** distinct capabilities. Teams that treat it as one will lose the moment.

| # | What the sentence actually demands | Why it is hard | Named mechanism |
|---|---|---|---|
| 1 | Know that the amendment *is* an amendment to a specific term, not a new contract | Amendments arrive as standalone PDFs referencing clauses by number, sometimes ambiguously | **Clause version graph** — each obligation term is a node; an amendment creates a child version with a resolved parent edge; ambiguous resolution is `CONTESTED`, not guessed |
| 2 | Extract the *effective date*, which is not the signature date | Retroactive/`nunc pro tunc` effective dates are ordinary [11]; extraction gets this wrong; the wrong date silently changes which events are in scope | **Effective-interval resolver** — extraction *proposes* the effective interval, a human *confirms* it, and the confirmation is a HUMAN DECISION provenance record |
| 3 | Identify exactly which past events fall inside the changed interval | Naively you re-run the whole portfolio (O(N·M·E)); that is slow, and worse, it re-touches conclusions nobody asked about | **Targeted invalidation** — each conclusion stores dependency edges to the term versions and evidence claims it consumed; changing a term re-queues exactly the conclusions whose evaluation window intersects the new effective interval |
| 4 | Re-evaluate **without destroying the prior conclusion** | The prior conclusion is evidence about your own conduct and may be the subject of a live dispute | **Append-only decision ledger** — the new conclusion supersedes; it does not overwrite. `as_of(14 July)` still returns what you believed on 14 July [2] |
| 5 | Classify the *delta*, not just recompute the answer | A CTO does not want 400 re-run rows; they want the four categories that need different handling | **Re-derivation delta classes:** `CONFIRMED` (still a breach, same quantum) · `VACATED` (no longer a breach) · `NEWLY-BREACHED` (was clean, now isn't) · `QUANTUM-CHANGED` (still a breach, different credit) |
| 6 | Reconcile each delta with **actions already committed** | *This is the graded half.* A `VACATED` conclusion under which you already served notice and posted a credit note is not a database row — it is a commercial and legal event | **Reconciliation ledger** — join the delta against the Action Ledger (SP-17.6). Reversible actions auto-retract with an audit entry. **Irreversible actions never auto-reverse**; they open a human reconciliation task showing the prior action, its idempotency key, and the `as_of` view of the world that justified it |

**The legal spine for #6.** Retroactive effect is not automatic even when the parties wrote it:
courts may decline to give retroactive effect "where the retroactive effect would result in prejudice
to intervening rights or harm parties who already took action based on existing orders" [11]. A
service credit the customer already claimed and consumed *is* an intervening right. So the correct
system behaviour is not "recompute and correct" — it is **"recompute, then surface the collision to
the humans the brief says own settlement."** That is a defensible, citable, counter-intuitive answer,
and it is the whole pitch.

### Which PS-17 sub-problems are non-obvious

**Non-obvious (this is where the win lives):**

1. **SP-17.2, part 6 — action reconciliation.** Every team will version the contract. Almost none
   will ask what happened to the credit note. *Why non-obvious:* the entire CLM market frames
   amendments as a **document-lineage** problem. Icertis's amendment documentation covers
   inheritance of parent attributes, a History tab with "Show Changes", and even amending expired
   agreements — and is **silent on retroactive effect and on the treatment of pre-amendment
   events** [1]. Agiloft's Dec-2025 "enterprise-grade obligation management" launch describes
   extract → structure → assign → deadline → remind → escalate, with dashboards for upcoming,
   past-due and high-risk items [12] — a forward-looking task system with no re-derivation story.
   The frontier of the market is task tracking; the inject asks for something behind it.
2. **SP-17.4 — The Silent Entitlement.** *Why non-obvious:* obligation management and revenue
   assurance are separate professional disciplines. CLM vendors sell "did we comply"; RA vendors
   sell "did we bill correctly". Neither owns "did we *claim* what we're owed before the window
   shut." Teams will detect breaches and never model the claim deadline, so their "leakage
   recovered" number is theoretical.
3. **SP-17.3 — contradiction, not absence.** *Why non-obvious:* "detect missing/stale data" is the
   obvious reading of the brief's bullet. "Detect *contradictory* data and refuse to pick a winner"
   is the hard one, and it is the one that produces the CONTESTED lock that makes the autonomy
   model real rather than decorative.
4. **SP-17.6 — idempotency.** *Why non-obvious:* it is invisible unless you deliberately stage a
   failure. Teams optimise for the happy path because the happy path is what gets demoed.

**Obvious (every team will have these; do not spend pitch time on them):** PDF ingestion, clause
extraction with an LLM, an obligations table, owner mapping, a breach dashboard, an email alert,
a renewal calendar.

### PS-17 — narrative arc for a 5-minute demo

The shape is **build it → break it three times → the collision → the money → the receipt.**

| Time | Beat | What is on screen | What is said |
|---|---|---|---|
| 0:00–0:30 | **The number** | One slide: 9.2% → 8.6% | "A decade of CLM spend moved contract value erosion by 0.6 points [10]. The tooling isn't wrong. The decomposition is." |
| 0:30–1:10 | **The obvious system, working** | Contract in → obligations out → SLA breach flagged → credit computed | "Every team in this room will show you this. In 2026 it is an afternoon's work, and on its own it is worth nothing. Here are the six problems underneath it." |
| 1:10–1:50 | **Break 1 — Evidence Fog** | Second feed lands, contradicts the outage duration; row turns amber; action button locks | "We don't pick a winner. We show you that your systems disagree and we refuse to act until someone decides." |
| 1:50–3:05 | **Break 2 — THE INJECT** | Amendment PDF dropped in. Effective-date field highlighted: **1 April — before the breaches.** Targeted invalidation animates: 14 events re-queued. Delta board fills: 9 CONFIRMED / 3 VACATED / 2 NEWLY-BREACHED | "The threshold moved. And it moved *backwards in time*. Note what a standard durable workflow engine does here — Temporal keeps in-flight executions on the old code [8]. That is the correct default for software and the wrong answer for contracts." |
| 3:05–3:35 | **⚡ THE MOMENT** | Freeze on one VACATED row. Expand. It shows: *Notice served 14 July · Credit note ₹8,40,000 posted 14 July · idempotency key `a4f…` · irreversible* | **"We already told this customer they breached, and we already paid them for it. The amendment says that never happened. What does your system do now?"** *(beat)* "Ours does not silently reverse it. It cannot — that credit is an intervening right, and courts decline retroactive effect that prejudices those [11]. It opens a reconciliation task, attaches the July 14 view of the world that justified the action, and routes it to the commercial owner — because this brief says settlement is human-owned." |
| 3:35–4:15 | **The money** | Entitlement Clock view, sorted by time-to-forfeiture. Two NEWLY-BREACHED events with claim windows closing in 6 days, ₹31L at risk | "Breach detection isn't recovery. An unclaimed credit is a donation. We rank by *deadline × value*, not severity." |
| 4:15–4:45 | **Break 3 — kill the worker** | Terminate the process mid-action; restart; the action does not re-fire | "Partial failure is the normal case. One notice, ever, per idempotency key." |
| 4:45–5:00 | **The receipt** | Drag the time slider back to 14 July. The whole workspace rewinds. Signed ledger root shown | "Audit replay isn't a report. It's a query. And the chain says we didn't edit the past — we recorded that we changed our mind about it." |

**The one "oh no, I hadn't thought of that" moment:** *the VACATED row with an irreversible action
attached.* A CTO's instinct on hearing "re-evaluate under the correct version" is *"fine, re-run the
rule."* The moment they see that re-running the rule creates a **commercial collision with an action
already in the world**, the problem visibly changes shape. One sentence carries it:

> **"Re-evaluating the past is the easy half. Reconciling the past with what you already did about it
> is the half that decides whether you get sued."**

### PS-17 — the judge's-objection map

| # | The question a CTO will ask | Defensible? | The answer |
|---|---|---|---|
| 1 | "Isn't this just a bitemporal database? Snodgrass did this in the 90s and it's in SQL:2011." | **Yes — fully** | Concede it instantly; conceding is the strong move. Bitemporal *storage* is 30-year-old prior art [7], and bitemporal *compliance analysis* is already patented territory [9]. The claim is at the **decision layer**: targeted re-derivation of superseded conclusions, delta classification, and reconciliation against committed irreversible external actions. Nobody stores their way to that. |
| 2 | "Why not just re-run the whole pipeline on every amendment?" | **Yes** | Blast radius and cost. Full re-run is O(N contracts × M obligations × E events) and re-touches conclusions nobody asked about — which in an audit context is itself a liability. Targeted invalidation is O(conclusions whose evaluation window ∩ the amendment's effective interval). Bring the arithmetic for a stated portfolio size. |
| 3 | "An LLM extracted that threshold. What if it's wrong?" | **Yes** | Show the numbers first — CUAD's best model is 44% precision at 80% recall [4]; legal hallucination on verifiable questions runs 58–88% [13]. Then show the design that assumes it: span anchoring, the four-colour provenance model, and the rule that nothing tagged INFERENCE crosses an action gate without a human promotion event. "We did not build a system that trusts the model." |
| 4 | "Who is liable when your system tells a customer they breached and they hadn't?" | **Yes** | Notice is never above L3 in the autonomy matrix, because the brief mandates it. And the reconciliation ledger *is* the defence file: it reproduces exactly what was known, from which source, at the moment of the decision. |
| 5 | "Amendments in the wild are scanned, badly drafted, and their effective dates are ambiguous. Then what?" | **Yes** | The effective interval is a *proposal* until a human confirms it, and that confirmation is a first-class provenance record. Ambiguity resolves to `CONTESTED`, which blocks re-derivation from auto-committing. Demo it: drop in an amendment with a vague date and let the system refuse. |
| 6 | "How does this deploy and scale — multi-tenant, thousands of contracts, continuous evidence?" | **Partially — prepare this one** | *Weakest link.* Append-only ledger partitioned by tenant; re-derivation as queued work with the idempotency key as the dedupe primitive; the sweep is the only expensive path and it is bounded by the amendment's effective interval. On bare Windows: embedded store + a single supervised worker process, horizontally splittable by tenant. **Bring one concrete number** (events/sec ingested, re-derivation latency for a 10k-event sweep) or this lands soft. |

---

## PS-04: AI-Powered Dynamic Covenant Monitoring & Early Warning

### The framing reality

This is a **regulated, mandated, and already-served** market — which cuts both ways.

Mandated: the EBA's Guidelines on loan origination and monitoring are explicit — ¶267: *"The
borrower's adherence to covenants, as well as the timely delivery of covenant compliance
certificates… should be utilised as early warning tools. Early detection of deviations is key…"*;
¶269–270 require EWIs with **"defined trigger levels"** and **"assigned escalation procedures,
including assigned responsibilities for the follow-up actions"**; ¶275–277 require documented
decisions, communication onward, and increased review frequency [3]. India's side is harder: the RBI
Fraud Risk Management Directions 2024 require a board-approved EWS/RFA framework, a dedicated Data
Analytics and MI Unit, and an **"appropriate Turnaround Time (TAT), preferably not more than
30 days"** for examining EWS alerts [5]. Basel's Principles for the Management of Credit Risk
(2000, updated by consultative document Feb 2025) require rating systems responsive to deterioration
and watchlists reviewed by senior management [16].

Already served, and publicly benchmarked — which is the trap. Moody's Analytics EDF-X EWS
methodology publishes its own numbers [6]:

| | 1 month before default | 12 months | 24 months | 36 months |
|---|---|---|---|---|
| Public firms flagged severe/high | 87% | 73% | 59% | 51% |
| **Private firms** (i.e. actual commercial borrowers) | **51%** | **43%** | **36%** | **32%** |

and at a 17% positive-signal rate the SEVERE watchlist "successfully signals 72% of the default
observations… while managing to limit the combined error rate to **44%**" (Type I 16%, Type II 28%) [6].

**Read that again.** The state of the art, in a commercial product, catches **43% of private-firm
defaults a year out**. Any team that walks on stage claiming an 84% breach probability with a
confident number and no calibration curve is going to be quietly disbelieved. **The framing must not
be "we predict better." It must be "we predict something different, and something actionable."**

### The seven sub-problems

---

#### SP-04.1 — **The Three Clocks**

**Hard truth.** A covenant has three dates and they are never the same day:
- the **test date** (fiscal quarter end),
- the **delivery date** (the compliance certificate — commonly 45 days after quarter end, 60 for some,
  90 for annuals [18, *pattern verified across clause samples; confirm against a named agreement*]),
- the **observation date** (today).

"Predict a breach 90 days in advance" is *ambiguous* until you say which clock you are counting to.

**Failure mode — and this is the one that kills the project silently.** Label leakage. You build
features from Q2 financials to predict a "Q2 breach" that was only *knowable* on day 45 of Q3. Your
backtest is spectacular. Production is useless. Or the subtler version: you announce a 60-day
warning, and 45 of those 60 days are the reporting lag — you have not predicted anything, you have
described the calendar.

**Mechanism — a point-in-time feature store keyed by knowledge date.** Every feature carries the
date it *became knowable*, not the date it describes. The label is the covenant test result stamped
at its **delivery** date. Every horizon is measured observation-date → test-date, and the UI states
the **true lead time = predicted test date − today − reporting lag**, in words, next to every score.

**Bonus that proves you understand the domain:** the EBA's own list of deterioration signals includes
¶274(o) — *"the late delivery of a certificate of adherence, a waiver request or a breach with
respect to the covenants"* [3]. The *reporting clock itself is a signal*. A borrower who has always
filed on day 40 and files on day 44 has told you something. Nobody builds this.

---

#### SP-04.2 — **The Contract Is The Model**

**Hard truth.** Leverage is whatever the credit agreement *defines* leverage to be. Covenant EBITDA
is a negotiated formula, not a GAAP quantity. In a Federal Reserve Bank of St. Louis study of
**3,939 loan packages with EBITDA-based covenants, all but 344 contained at least one non-GAAP
add-back; the modal count was 2; and about 43% of definitions had three or more** [4a]. S&P Global
found that issuers' **projected adjusted EBITDA at deal inception exceeded actual realised EBITDA in
the two following calendar years by about 30% on average** [4a, footnote 1 — cited via the paper;
S&P article not opened].

**Failure mode.** You compute Net Debt/EBITDA from the financials. The borrower computes it from the
contract. You disagree by tens of percent. Every alert you raise is wrong in someone's favour, the
relationship manager checks two of them against the compliance certificate, finds you wrong, and
stops opening your emails in week two. *Adoption failure, not model failure.*

**Mechanism — Covenant-as-Executable-Formula.** The covenant definition is parsed from the agreement
into an **AST over named financial line items**, carrying its add-back set, caps (often expressed as
a shared % of EBITDA), time restrictions and pro-forma/run-rate rules. The system computes **both**
the contractual ratio and the GAAP ratio and renders the **wedge between them as a first-class risk
signal** — because add-back intensity is itself predictive: each additional add-back category raises
the probability of 60-day delinquency within 3 years by **4.2 percentage points against a 1.3%
unconditional base**, and default probability by 1.6pp against a 1.1% base [4a]. Conceptually this
is Surden's "data-oriented contracting": expressing contract terms in a form a computer can make
*prima facie* compliance assessments against [19].

---

#### SP-04.3 — **The Kink at the Threshold**

**Hard truth.** Borrowers know their covenant. Dichev & Skinner (JAR 2002) found in Dealscan data an
**unusually small number of firm-quarters just below covenant thresholds and an unusually large
number that just met or beat them** — managers take action to avoid violations [14, *figure from
search summary of the paper; open the paper before quoting on stage*]. The observable distribution
has a hole on the wrong side of the line.

**Failure mode.** Your model learns the distribution *after* management action and systematically
under-predicts breach, because reality has had the breaches actively removed from it. And the
borrowers who manage the number hardest are frequently the ones in the most trouble — so the model
is most wrong exactly where it matters.

**Mechanism — two models, not one.**
1. An **unmanaged trajectory model**: what would the reported ratio be if the borrower did nothing?
2. A **management-capacity model**: how much room do they have left — equity cure availability,
   remaining add-back headroom under the cap, working-capital levers, length of the cure period?

Alert on the *unmanaged* path; use capacity as a **confidence discount and a time-buyer estimate**.
The demo visual is outstanding and cheap: a histogram of distance-to-covenant with a visible notch
just below zero, and an arrow labelled "these did not disappear, they were moved."

---

#### SP-04.4 — **Breach ≠ Loss**

**Hard truth.** A covenant breach is a **transfer of control rights**, not a credit loss. Roberts &
Sufi report that **~63% of covenant violations are waived by creditors without altering major loan
terms**, and that more than 75% of violations lead to some renegotiation [14a, *figures from search
summary; papers not opened*]. Dichev & Skinner: violations are "not necessarily associated with
financial distress" and are "often waived for healthy firms" [14].

**Failure mode.** You optimise for P(breach), rank the portfolio by it, and hand the credit committee
a queue dominated by borrowers who will be waived on the nod. Alert fatigue inside one quarter.
Meanwhile the borrower who will not breach for six months but is genuinely impaired sits at rank 40
and defaults.

**Mechanism — rank on expected consequence, not probability.**

```
priority = P(breach at horizon h)
         × P(not waived | breach)        ← the non-obvious factor
         × economic exposure (EAD, drawn + undrawn commitment)
         × urgency (days until the action window closes)
```

The `P(not waived)` model is learnable from waiver history, syndicate structure, relationship
tenure and headroom trajectory. **Demo it as two side-by-side rankings — P(breach) order vs.
consequence order — and show that they disagree at the top.** That single split-screen is the whole
argument that you understand credit rather than classification.

---

#### SP-04.5 — **The Lead-Time Tax**

**Hard truth.** Accuracy decays with horizon, and the decay is brutal and public (table above) [6].
The brief's "30, 60 and 90 days" is not three settings on one model; it is three different problems
with three different achievable accuracies.

**Failure mode.** A single confident number — "84% probability of breach in 90 days." A CTO who
knows this market disbelieves it; one who doesn't will ask why yours beats Moody's, and there is no
good answer on synthetic data.

**Mechanism — horizon-honest scoring.**
- Three **separately calibrated** models with reliability diagrams shown *in the product*, not in an
  appendix; confidence bands that visibly widen with horizon.
- A **persistence filter** — Moody's own "first sustained signal" concept: require k-of-n consecutive
  periods above trigger before escalating, which is precisely the brief's "distinguish meaningful
  deterioration from temporary noise" [6].
- An **alert budget**, expressed the way the industry expresses it: Moody's exposes a single
  user-set **Positive Signal Rate** — "the percentage of firms from your portfolio that your risk
  tolerance will yield on your watchlist" — because "it intuitively reflects how much work you are
  willing to spend" [6]. Adopting that framing lets you answer the false-positive question with a
  policy dial instead of an apology.
- A **"why now" delta** on every alert: what changed since the last score.

---

#### SP-04.6 — **The Defensible Belief**

**Hard truth.** The deliverable is not a score. It is a **reason to act that survives a lawyer.**
Under UCC §1-309, a party with an at-will acceleration/insecurity right "has power to do so only if
that party in good faith believes that the prospect of payment or performance is impaired"; and
courts have held that a lender may not terminate a facility, accelerate, or exercise remedies on an
immaterial default or without reasonable notice and opportunity to cure [15]. **An unexplained model
output is not a good-faith belief.**

**Failure mode.** The bank can use your alert for nothing except more monitoring, and the business
case collapses to zero. This is the *actual* reason bank EWS programmes underdeliver — not accuracy.

**Mechanism — the Evidence Dossier as the primary artefact.** Every alert compiles into a document:
the covenant clause with its citation and effective version; the computed value with the full
line-item trail; each contributing signal with source, date and trust tier; a counterfactual ("if
receivable days revert to 52, this alert clears"); and a recommended action mapped to an authority
level. **The score is a field on the dossier, not the product.** Wrap it in SR 11-7 hygiene — the
whole system is a "model" under Fed/OCC supervisory guidance and needs a model inventory, independent
validation, documentation and ongoing performance monitoring [20] — and ship a model card in the UI.

---

#### SP-04.7 — **Signal Trust Tiers**

**Hard truth.** The signals with the most lead time (news, industry deterioration, concentration) have
the worst entity resolution and the weakest provenance. The ones with the best provenance (payments,
utilisation, treasury flows) have the least lead time.

**Failure mode.** "Sharma Textiles Ltd" in a news feed is not "Sharma Textiles Pvt Ltd" in the loan
book. One bad match triggers an unfounded facility review — or worse, becomes part of the
"good-faith belief" record under SP-04.6.

**Mechanism — a four-tier trust lattice with an action ceiling per tier.**
internal deterministic (transactions, utilisation, DPD) > internal derived (ratios) > external
structured (ratings, filings) > external unstructured (news, sentiment). **Unstructured signals may
raise monitoring frequency and move a name onto the watch list — which is exactly what EBA ¶272 and
¶277 prescribe [3] — but can never on their own cross the intervention threshold.** Every entity
match carries a resolution confidence and is human-confirmable.

---

#### SP-04.8 (the compliance backbone) — **Escalation as State, not Notification**

**Hard truth.** An alert that is an email is not an early warning system. EBA ¶270 requires
**"assigned escalation procedures, including assigned responsibilities for the follow-up actions"**;
¶275 requires designated functions to analyse severity "without undue delay" and present to credit
decision-makers; ¶276 requires the decision to be **documented and communicated onward** [3]. RBI
requires a TAT "preferably not more than 30 days" with Risk Management Committee oversight [5].

**Failure mode.** Alerts accumulate, nobody owns them, and the bank is non-compliant as well as blind.

**Mechanism — every alert is a durable, owned, SLA'd case object** with state, assignee, deadline
clock, decision record and closure reason. This is also how PS-04 earns a long-running-state /
backend-depth story comparable to PS-17's — it is the sub-problem that turns an ML demo into a system.

### Which PS-04 sub-problems are non-obvious

**Non-obvious:**

1. **SP-04.1 — The Three Clocks.** *Why non-obvious:* every ML practitioner has heard of leakage in
   the abstract; almost nobody realises that in covenant monitoring the leakage is *structural and
   built into the contract*, because the label is only observable at certificate delivery. Vendor EWS
   products sidestep it entirely by predicting **default** (an event with a clean date) rather than
   **covenant test outcomes** (an event with three dates) — which is exactly why EDF-X ranks *names*
   and leaves the covenant arithmetic to the bank's own systems [6].
2. **SP-04.4 — Breach ≠ Loss.** *Why non-obvious:* the brief itself asks you to "predict probability
   of covenant breach," and the obvious reading is that the probability is the product. The published
   evidence says the majority of breaches are waived and are not distress events [14, 14a]. The
   business value is in *which* breaches matter, and the brief's own "rank by urgency and expected
   impact" bullet quietly concedes this — most teams will read it as "sort by probability."
3. **SP-04.2 — The Contract Is The Model.** *Why non-obvious:* it looks like a data-engineering
   detail and it is actually the difference between adoption and abandonment. Vendors handle it by
   making the bank key the covenant definition in by hand — which is why the definition and the
   monitoring live in different systems and drift apart.
4. **SP-04.3 — The Kink at the Threshold.** *Why non-obvious:* it inverts the usual ML instinct.
   The data is not merely noisy; it has been *adversarially curated by the subject of the prediction*.

**Obvious (every team will have these):** ingest financials, compute the standard ratio set, a
gradient-boosted classifier, SHAP driver bars, a portfolio heat map, a "recommended intervention"
LLM paragraph, a news sentiment gauge.

### PS-04 — narrative arc for a 5-minute demo

The shape is **the regulator says you must → here's the version everyone builds → here's why the
clock breaks it → here's the version that can actually be acted on.**

| Time | Beat | On screen | Said |
|---|---|---|---|
| 0:00–0:25 | **Mandate, not idea** | EBA ¶267 and RBI 30-day TAT on one slide | "This isn't a product idea. EBA says covenant adherence *shall* be used as an early warning tool [3]; RBI gives you 30 days to examine every alert [5]. The question is only whether the system is worth acting on." |
| 0:25–1:00 | **The obvious system** | Financials → ratios → "84% chance of breach in 60 days", SHAP bars | "This is the demo you will see four more times today." *(pause)* "Here is the bar it has to clear." — flip to the Moody's table: **43% of private-firm defaults caught 12 months out** [6]. "So why is ours 84% sure?" |
| 1:00–2:00 | **⚡ Reveal 1 — The Three Clocks** | Timeline graphic: test date · +45d certificate delivery · today. The "60-day warning" bar shrinks live to 15 days | **"Forty-five of those sixty days were the reporting lag. We didn't predict anything — we read the calendar."** Then show the point-in-time store: same borrower, honest clock, honest lead time. |
| 2:00–2:45 | **Reveal 2 — The Contract Is The Model** | Toggle "GAAP EBITDA" → "Agreement EBITDA (7 add-backs, 12.5% shared cap)". Ratio moves 3.9× → 3.1×; the breach evaporates | "Leverage is whatever the agreement says it is. 43% of covenant EBITDA definitions carry three or more non-GAAP add-backs [4a]. If you compute the GAAP number, the RM checks you against the compliance certificate once and never opens your email again." |
| 2:45–3:20 | **Reveal 3 — The Kink** | Histogram of distance-to-covenant with the visible hole just below zero | "The breaches didn't not happen. They were moved. We predict the unmanaged path and score how much room they have left." |
| 3:20–4:10 | **Reveal 4 — Breach ≠ Loss** | Split screen: ranking by P(breach) vs ranking by expected consequence. Different names at the top. "63% of violations are waived without changing major terms" on the slide | "A covenant breach is a transfer of control rights, not a loss. Rank by probability and you hand the committee a queue of borrowers who'll be waived on the nod." |
| 4:10–4:45 | **The dossier** | Open one alert: clause + citation, computed value with line-item trail, each signal with source/date/trust tier, counterfactual, recommended action + authority level | "Under §1-309 the bank may act only if it *in good faith believes* performance is impaired [15]. A score is not a belief. This is." |
| 4:45–5:00 | **The case** | Alert as a stateful case with owner, 30-day RBI clock, decision record | "EBA ¶270 wants assigned escalation with named responsibility. An email is not that." |

**The one "oh no" moment:** **The Three Clocks.** It is the right choice because it is an *engineering*
failure a CTO recognises from their own systems — point-in-time correctness and label leakage — dressed
in domain clothes they don't have. The bar visibly shrinking from 60 days to 15 on screen is the beat.
*(Fallback if the room is credit-heavy rather than engineering-heavy: lead the reveal with Breach ≠
Loss and the two disagreeing rankings.)*

### PS-04 — the judge's-objection map

| # | The question a CTO will ask | Defensible? | The answer |
|---|---|---|---|
| 1 | "You trained on synthetic data. How do you know any of this predicts anything?" | **Only if pre-empted** | *Hardest question; must be answered before it's asked.* You cannot validate discrimination on synthetic data — so **do not claim AUC.** Claim the two things synthetic data *can* demonstrate: **point-in-time correctness** (the walk-forward harness that respects knowledge dates, and the leakage test that fails loudly when a feature is used before it was knowable) and **calibration machinery** (reliability diagrams, and the PSR dial). Ship the backtest harness as a deliverable, not the model. |
| 2 | "Why is this better than Moody's, S&P or SAS?" | **Yes** | Different job, and say so with their own numbers. EDF-X is a **portfolio triage** tool calibrated on a user-chosen positive-signal rate — it ranks *names* by default risk [6]. This is **covenant-clause-level**, agreement-definition-aware, and produces the action dossier. "We are not competing with the ranker. We are the thing the ranker hands off to." |
| 3 | "What's the false positive cost, and what's your alert budget?" | **Yes, if SP-04.5 is built** | Use the industry's own framing: "Tell us your watchlist capacity in analyst-hours and we set the trigger — that's the PSR [6]." At 17% PSR, the published SEVERE benchmark carries a 44% combined error rate; we show ours on the same axes rather than hiding it. |
| 4 | "Is this a 'model' under SR 11-7 (or your regulator's equivalent)? How does it get validated and monitored?" | **Yes** | Yes, unambiguously — SR 11-7 covers any quantitative method transforming input into estimates or decisions, including ML [20]. Model inventory entry, model card, independent-validation hooks, ongoing performance monitoring, and champion/challenger are in the build, not the roadmap. |
| 5 | "The RM acts on your alert, cuts the line, and the borrower sues. What's our position?" | **Yes** | The dossier is the position: a documented, contemporaneous, evidence-backed belief that performance is impaired [15]. And the autonomy matrix never recommends acceleration — the recommended actions top out at information requests, increased monitoring frequency and watch-list placement, exactly the EBA ¶272/¶277 escalation ladder [3]. |
| 6 | *(likely bonus)* "Your news signal flagged the wrong company." | **Yes** | Trust tiers with an action ceiling: unstructured external signals can raise monitoring frequency but can never on their own cross the intervention threshold, and every entity match carries a resolution confidence that a human confirms. |

---

## Head-to-head verdict for this lane

### PS-17 — **9/10** for decomposability into a compelling pitch

| Criterion | Assessment |
|---|---|
| Do the sub-problems have sharp, non-generic names? | Yes — Bitemporal Truth, The Amendment Inject, Evidence Fog, Silent Entitlement, Exactly-Once in the Real World. Each is a slide title, not a layer name. |
| Is each sub-problem *visible*? | **Yes, all seven.** A two-axis time slider, an amber CONTESTED lock, a delta board, a countdown-sorted claim queue, a killed worker that doesn't double-send. This is the decisive advantage. |
| Is there a counter-intuitive reveal? | **Yes, and it is handed to you by the organisers.** The inject's third failure (action reconciliation) is genuinely non-obvious, legally grounded [11], and lands in 30 seconds. |
| Does the decomposition map to the rubric? | **Almost one-to-one** with the brief's seven Section-04 bullets. The decomposition *is* the scoring sheet. |
| Is the incumbent gap demonstrable? | Yes — Icertis's amendment docs are silent on retroactivity [1]; Agiloft's Dec-2025 frontier launch is a task tracker [12]. |
| Weakness | Production-scale answer is the softest link; the extraction layer is commodity and must be framed as *not* the contribution. |

### PS-04 — **7/10**

| Criterion | Assessment |
|---|---|
| Do the sub-problems have sharp names? | Yes — Three Clocks, The Contract Is The Model, The Kink at the Threshold, Breach ≠ Loss, The Lead-Time Tax. Intellectually these are the equal of PS-17's, arguably better. |
| Is each sub-problem *visible*? | **Mixed, and this is the deduction.** The Kink (histogram) and Breach ≠ Loss (split ranking) demo beautifully. The Three Clocks demos adequately. But the core engineering — a point-in-time feature store — photographs as nothing, and "we didn't leak the label" is a claim, not a picture. |
| Is there a counter-intuitive reveal? | Yes (Three Clocks), but it requires ~40 seconds of setup before it lands, versus PS-17's inject which lands on contact. |
| Does the decomposition map to the rubric? | Partially. The brief's journey is linear (intake → monitor → forecast → explain → prioritise → intervene) and reads as a pipeline, which pulls teams toward a pipeline pitch. The good decomposition has to be argued *against* the brief's own structure. |
| Is the incumbent gap demonstrable? | Yes but *carefully* — the gap is "clause-level and actionable" vs "name-level ranking," which requires the audience to grant a distinction. PS-17's gap is a thing the incumbent visibly does not do. |
| Weakness | **The headline deliverable is a probability, and probabilities are unfalsifiable on synthetic data in a 5-minute demo.** The team spends pitch time defending rather than demonstrating. The public benchmark bar is high and citable, so any accuracy claim invites a losing comparison. |

### Winner: **PS-17, by 2 points.**

The deciding argument is not that PS-17's problems are harder — PS-04's Three Clocks is the single
sharpest idea in this document. It is that **PS-17's hardness is photographable and PS-04's is not**,
and that **PS-17's counter-intuitive moment is supplied, graded, and timed by the organisers** while
PS-04's must be constructed and defended by the team. In a 5-minute CTO demo, a supplied inject that
you visibly handle better than expected is worth more than a superior insight that needs setup.

Secondary argument: PS-17 lets you be *honest about AI's limits* and score points for it (span
anchoring, the four-colour provenance model, human-owned settlement), whereas PS-04 requires you to
be confident about a prediction you cannot validate. The first is a much better posture in front of
engineers.

### What would change the verdict

- **PS-04 → 8.5 and near-parity** if the team has a genuine quant who will build and *show* a
  walk-forward, knowledge-date-respecting backtest harness with live reliability diagrams and a
  deliberately-failing leakage test. That turns "trust our number" into "watch our machinery catch
  us cheating," which is the same rhetorical move that makes PS-17 strong.
- **PS-17 → ~7.5 and near-parity** if the inject is graded narrowly as *"did you version the
  contract and re-run"* rather than *"what did you do about the conclusions you already acted on."*
  If the grader's rubric stops at re-evaluation, PS-17's decisive advantage becomes a nice-to-have.
  **Mitigation: build both, but make the reconciliation the demo's centre of gravity so that the
  narrow reading is satisfied in passing.**
- **PS-04 → 8** if the team can source (not synthesise) even a small real covenant-agreement corpus
  for the SP-04.2 formula parsing, since that sub-problem is verifiable without any prediction claim.
- **Either → down 1** if the team lets the extraction/ingestion layer occupy more than 45 seconds of
  the pitch. Both briefs make it a prerequisite, and every competing team will have it.

---

## Risks and open questions

1. **PS-17 pitch risk: the amendment inject may be demoed by everyone.** It is in the published
   brief, so every finalist will have *something*. The differentiator has to be the third failure
   (action reconciliation), not the fact of re-evaluation. Assume at least one rival team versions
   the contract correctly; assume almost none reconciles committed actions.
2. **PS-17 data risk: synthetic amendments must be genuinely nasty** — a retroactive effective date,
   an ambiguous clause reference, and one amendment that supersedes another amendment. If the demo
   amendment is clean, the mechanism looks like overkill.
3. **PS-04 evaluation risk (unresolved):** it is unclear whether the jury will treat an unvalidatable
   probability as a fatal flaw or ignore it. This is the single largest uncertainty in the comparison.
   If the jury is credit-risk-literate they will press it; if they are generalist CTOs they may not.
4. **Unverified figures flagged in-line** and repeated here — **do not put these on a slide without
   opening the primary source first:** Dichev & Skinner's 30%-of-loans violation rate and the
   threshold-distribution kink [14]; Roberts & Sufi's 63% waiver rate [14a]; Dahl et al.'s 58–88%
   legal hallucination range [13]; the "60–80% of eligible SLA credits go unclaimed" claim, which
   appears only in vendor marketing and **should not be used at all** [17]; TM Forum's revenue
   leakage percentages, which are **mutually inconsistent across the sources surfaced** (0.52%, 1.9%,
   2.92% all appear) and should be dropped in favour of the WorldCC figure, which is verified [10].
5. **Patent prior art is not fully checked in this lane.** US11907943 ("Resource compliance system
   using bitemporal analysis") surfaced but its claims could not be retrieved [9]. Whoever owns the
   patentability lane must clear it before the novelty claim is made on stage.
6. **The claim-window mechanic for SP-17.4 needs one primary citation.** The pattern (written claim
   within N days; unclaimed credit waived) is consistent across cloud and telecom SLAs, but the
   attempt to fetch a named provider's SLA returned truncated content. Pull one named SLA — a cloud
   provider's compute SLA is the easiest — and quote the clause verbatim on the slide.
7. **Windows/no-Docker constraint is not a constraint for either decomposition.** Every mechanism
   named here is a data-model and control-flow idea implementable on an embedded store plus a
   supervised worker process. No mechanism in this document requires a container.

---

## Sources

**Opened and read directly:**

1. Icertis Contract Intelligence Help 8.2 — *Amendments*. https://iciwikiapac.icertis.com/ICIHelp8.2/index.php?title=Amendments — covers amendment creation, attribute inheritance from parent, History tab / "Show Changes", and amending executed/terminated/expired agreements. **Silent on retroactive effect and on treatment of pre-amendment events.**
2. Martin Fowler, *Bitemporal History*. https://martinfowler.com/articles/bitemporal-history.html — actual/valid time vs. record/transaction time; append-only record history; retroactive correction; event sourcing as an alternative implementation.
3. European Banking Authority, *Final Report — Guidelines on loan origination and monitoring* (EBA/GL/2020/06), applicable 30 June 2021. https://www.eba.europa.eu/sites/default/files/document_library/Publications/Guidelines/2020/Guidelines%20on%20loan%20origination%20and%20monitoring/884283/EBA%20GL%202020%2006%20Final%20Report%20on%20GL%20on%20loan%20origination%20and%20monitoring.pdf — §8.4 ¶266–268 (monitoring of covenants), §8.5 ¶269–274 (EWIs and watch lists, incl. ¶274(o) late certificate delivery as a signal), §8.5.1 ¶275–277 (follow-up and escalation). *Text extracted locally from the fetched PDF.*
4. Hendrycks, Burns, Chen, Ball, *CUAD: An Expert-Annotated NLP Dataset for Legal Contract Review* (NeurIPS 2021 Datasets & Benchmarks). https://arxiv.org/html/2103.06268v2 — 510 contracts, 13,101 annotations, 41 categories; DeBERTa-xlarge 47.8% AUPR, 44.0% P@80%R, 17.8% P@90%R.
   - 4a. Faria-e-Castro, Gopalan, Pal, Sánchez & Yerramilli, *EBITDA Add-backs in Debt Contracting: A Step Too Far?* (Federal Reserve Bank of St. Louis / WUSTL / Univ. of Houston, 30 Nov 2021). https://www.bauer.uh.edu/yerramilli/EBITDA_Addbacks.pdf — 3,939 loan packages; all but 344 have ≥1 non-GAAP add-back; modal 2; ~43% have ≥3; each additional add-back +4.2pp on 3-yr 60-day delinquency (base 1.3%) and +1.6pp on default (base 1.1%). Footnote 1 cites S&P Global finding projected adjusted EBITDA exceeded realised EBITDA by ~30% on average over the two following years (S&P article itself **not opened**). *Text extracted locally.*
5. Reserve Bank of India, *(Fraud Risk Management in Commercial Banks (including RRBs) and AIFIs) Directions, 2024*, RBI/2024-25/118, 15 July 2024. https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12702 — ¶3.1.1 EWS/RFA framework; ¶3.1.2–3.1.4 RMC oversight and TAT "preferably not more than 30 days"; ¶3.2 EWS integrated with CBS; ¶3.3.2 dedicated Data Analytics and MI Unit; ¶3.5 six-month implementation.
6. Moody's Analytics, *EDF-X Early Warning System — Model Methodology*, October 2022. https://dkf1ato8y5dsg.cloudfront.net/uploads/52/504/edfx-early-warning-system.pdf — Positive Signal Rate as the user-facing calibration dial; Table 9 (SEVERE watchlist: 17% PSR, 72% hit rate, 44% combined error = 16% Type I + 28% Type II); Table 15 (public firms 87/73/59/51% at 1/12/24/36 months; **private firms 51/43/36/32%**); "first sustained signal" persistence concept. *Text extracted locally.*
7. Search-verified: temporal features in SQL:2011; valid-time / transaction-time terminology from Snodgrass, standardised in ISO SQL:2011. Representative reference: Jensen, Snodgrass & Soo, *The TSQL2 Data Model*. https://people.cs.aau.dk/~csj/Thesis/pdf/chapter12.pdf
8. Temporal, *Versioning — Python SDK*. https://docs.temporal.io/develop/python/workflows/versioning — non-determinism on replay; `patched()` "applies a code change to new Workflow Executions **while avoiding disruptive changes to in-progress Workflow Executions**."
9. US 11,907,943 B2, *Resource compliance system using bitemporal analysis*. https://patents.google.com/patent/US11907943B2/en — **claims could not be retrieved (503 / unreadable OCR PDF); treat as unverified prior art that must be cleared.**
10. WorldCC / Deloitte Legal, *The ROI of Contracting Excellence*, June 2023. https://passle-net.s3.amazonaws.com/Passle/5d1eec76989b6e0f3cff1041/MediaLibrary/Document/2023-08-04-13-34-26-203-ROI-of-contracting-excellence.pdf — 2014 IACCM research: 9.2% average value erosion; 2023 re-run across **more than 1,200 organizations**: **8.6%**, best performers "a little over 3%", worst above 20%. *Text extracted locally.* (The worldcc.com-hosted copy returns HTTP 403.)
11. Retroactive amendment / `nunc pro tunc` doctrine — search-verified across: https://en.wikipedia.org/wiki/Nunc_pro_tunc · https://aaronhall.com/retroactive-amendment-clauses-legal-effect-limits/ · https://www.contractcodex.com/contracts/commercial-contract/amendment-form-and-effect — amendments may be effective *ab initio*; **courts may decline retroactive effect where it "would result in prejudice to intervening rights or harm parties who already took action based on existing orders."** *(Secondary sources; get a primary case cite before using the legal claim on a slide.)*
12. Agiloft, *Agiloft Launches Enterprise-Grade Obligation Management*, 8 Dec 2025. https://www.prnewswire.com/news-releases/agiloft-launches-enterprise-grade-obligation-management-pioneering-the-ai-native-era-of-contract-lifecycle-management-302635020.html — **search-surfaced, not opened directly.** Described capability: extract obligations to structured data, assign to owners, deadlines, real-time progress, automated reminders, escalation of overdue tasks, prebuilt dashboards. No re-derivation or retroactivity claim.

**Located via search, NOT opened — verify before quoting:**

13. Dahl, Magesh, Suzgun & Ho, *Large Legal Fictions: Profiling Legal Hallucinations in Large Language Models*, *Journal of Legal Analysis* 16(1):64–93 (2024). https://academic.oup.com/jla/article/16/1/64/7699227 · https://arxiv.org/abs/2401.01301 — reported hallucination rates on specific verifiable questions about federal cases: GPT-4 58%, GPT-3.5 69%, Llama 2 88%; models often fail to know when they are hallucinating. **Figures from search summary.**
14. Dichev & Skinner, *Large-Sample Evidence on the Debt Covenant Hypothesis*, *Journal of Accounting Research* 40(4):1091–1123 (2002). https://onlinelibrary.wiley.com/doi/abs/10.1111/1475-679X.00083 — ~30% of loans have at least one violation; unusually few firm-quarters just below thresholds and unusually many just at/above; violations often waived for healthy firms and not necessarily distress-associated. **Figures from search summary.**
    - 14a. Roberts & Sufi, *Control Rights and Capital Structure* / *Renegotiation of financial contracts*. https://web-docs.stern.nyu.edu/salomon/docs/conferences/roberts_sufi.pdf · https://www.nber.org/system/files/working_papers/w20484/w20484.pdf — ~63% of covenant violations waived without altering major loan terms; >75% of violations lead to renegotiation; ~90% of long-term debt contracts renegotiated before maturity. **Figures from search summary.**
15. Lender liability / good faith on acceleration — UCC §1-309 (power to accelerate only where the party "in good faith believes that the prospect of payment or performance is impaired"); courts limiting termination/acceleration on immaterial default without notice and cure opportunity. https://www.quinnemanuel.com/the-firm/publications/client-alert-lender-liability-at-forty-thinking-through-implied-covenant-claims/ · https://nationalaglawcenter.org/wp-content/uploads/assets/bibarticles/mcleroy_liability.pdf — **search summary; get the statutory text and one case cite.**
16. Basel Committee on Banking Supervision, *Principles for the Management of Credit Risk* (Oct 2000). https://www.bis.org/publ/bcbs75.pdf — four areas incl. credit administration, measurement and monitoring; rating systems responsive to deterioration; watchlists reviewed by senior management. Consultative update: BCBS d591, published 5 Feb 2025, consultation closed — https://www.bis.org/bcbs/publ/d591.htm (**opened**; confirms it is a limited update to the 2000 Principles removing obsolete/superseded parts).
17. SLA service-credit claim windows and unclaimed credits — the *mechanic* (written claim within a stated window after the affected billing period; unclaimed credit waived; claimant bears the evidentiary burden) is consistent across sources, but every quantified claim found ("60–80% of eligible credits unclaimed") appears **only in vendor marketing**. https://aaronhall.com/service-credit-clauses-sla-breach-remedies/ · https://alertping.com/blog/sla-service-credits — **do not use the percentages. Replace with a verbatim clause from one named provider SLA before the pitch.** (An attempt to fetch https://cloud.google.com/compute/sla returned truncated content.)
18. Covenant compliance certificate delivery deadlines (45 days quarterly / 60 / 90 annual; 30–60 day cure periods; equity cure rights). https://www.lawinsider.com/clause/covenant-compliance-certificate · https://www.hklaw.com/en/insights/publications/2017/05/time-to-check-the-financial-covenants-a-brief-summ — **clause-sample pattern, not a single authority; confirm against a named credit agreement.**
19. Harry Surden, *Computable Contracts*, 46 UC Davis Law Review 629 (2012). https://lawreview.law.ucdavis.edu/sites/g/files/dgvnsk15026/files/media/documents/46-2_Surden.pdf — "data-oriented contracting"; expressing terms so computers can make prima-facie compliance assessments. **Search summary; paper not opened.**
20. Federal Reserve SR 11-7 / OCC 2011-12, *Supervisory Guidance on Model Risk Management* (4 Apr 2011) — model inventory, independent validation proportionate to materiality, documentation, ongoing performance monitoring; "model" defined broadly enough to include ML. **Search summary; primary Fed page not opened.**

**Attempted and blocked (recorded for completeness):** worldcc.com ROI PDF and "Stopping the Leak" (403) · worldcc.foundation "10 Pitfalls" asset (403) · sirion.ai obligations page (403) · openriskmanual.org (login wall) · patents.google.com US11907943 (503) · USPTO image PDF for 11907943 (unreadable scan) · cloud.google.com/compute/sla (truncated) · tmforum.org Revenue Assurance Survey 2017/18 PDF (403).
