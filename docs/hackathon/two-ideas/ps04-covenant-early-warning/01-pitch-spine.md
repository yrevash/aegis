# PS-04 — pitch spine

*"This is not one problem. It is seven."* Each sub-problem is named for a slide title.

Two of these — **The Three Clocks** and **Breach ≠ Loss** — are genuinely world-class ideas, and
sharper than anything in PS-17's decomposition. PS-04 loses the overall comparison because its
hardness is **hard to photograph**, not because it is easy.

## The seven sub-problems

### SP-1 — The Three Clocks
**Hard truth.** A covenant has three dates and they are never the same day: the **test date**
(fiscal quarter end), the **delivery date** (compliance certificate, commonly 45 days after
quarter end, 60 for some, 90 for annuals), and the **observation date** (today). *"Predict a
breach 90 days in advance" is ambiguous until you say which clock you are counting to.*

**Failure mode — the one that kills the project silently.** Label leakage. You build features from
Q2 financials to predict a "Q2 breach" that was only *knowable* on day 45 of Q3. Your backtest is
spectacular; production is useless. The subtler version: you announce a 60-day warning, and 45 of
those days are reporting lag — you have not predicted anything, you have described the calendar.

**Mechanism.** Point-in-time feature store keyed by **knowledge date**. Every feature carries the
date it *became knowable*, not the date it describes. The label is the covenant test result
stamped at its *delivery* date. Every horizon is measured observation-date → test-date, and the UI
states the **true lead time = predicted test date − today − reporting lag**, in words, beside
every score.

**The bonus that proves domain understanding:** EBA ¶274(o) lists *"the late delivery of a
certificate of adherence, a waiver request or a breach with respect to the covenants"* as a
deterioration signal. **The reporting clock is itself a signal.** A borrower who has always filed
on day 40 and files on day 44 has told you something. Nobody builds this.

### SP-2 — The Contract Is The Model
**Hard truth.** Leverage is whatever the credit agreement *defines* leverage to be. Covenant
EBITDA is a negotiated formula, not a GAAP quantity. In a Federal Reserve Bank of St. Louis study
of **3,939 loan packages with EBITDA-based covenants, all but 344 contained at least one non-GAAP
add-back; the modal count was 2; ~43% of definitions had three or more.**

**Failure mode.** You compute Net Debt/EBITDA from the financials; the borrower computes it from
the contract; you disagree by tens of percent. The RM checks two alerts against the compliance
certificate, finds you wrong, and stops opening your emails in week two. **Adoption failure, not
model failure.**

**Mechanism — Covenant-as-Executable-Formula.** Parse the covenant definition into an **AST over
named financial line items**, carrying its add-back set, caps, time restrictions and
pro-forma/run-rate rules. Compute **both** the contractual and the GAAP ratio, and render the
**wedge between them as a first-class risk signal** — add-back intensity is itself predictive:
each additional add-back category raises probability of 60-day delinquency within 3 years by
**4.2pp against a 1.3% base**.

### SP-3 — The Kink at the Threshold
**Hard truth.** Borrowers know their covenant. Dichev & Skinner (JAR 2002) found an unusually
small number of firm-quarters *just below* thresholds and an unusually large number that just met
or beat them. **The observable distribution has a hole on the wrong side of the line.**

**Failure mode.** Your model learns the distribution *after* management action and systematically
under-predicts breach — and the borrowers who manage the number hardest are often the ones in most
trouble, so the model is most wrong exactly where it matters.

**Mechanism — two models, not one.** (1) An **unmanaged trajectory model**: what would the ratio
be if the borrower did nothing? (2) A **management-capacity model**: how much room is left —
equity cure availability, remaining add-back headroom under the cap, working-capital levers, cure
period length? Alert on the *unmanaged* path; use capacity as a confidence discount and a
time-buyer estimate.

**The visual is outstanding and cheap:** a histogram of distance-to-covenant with a visible notch
just below zero, and an arrow labelled *"these did not disappear, they were moved."*

### SP-4 — Breach ≠ Loss
**Hard truth.** A covenant breach is a **transfer of control rights**, not a credit loss. Roberts
& Sufi: **~63% of covenant violations are waived by creditors without altering major loan terms**;
more than 75% lead to some renegotiation.

**Failure mode.** You optimise P(breach), rank by it, and hand the credit committee a queue
dominated by borrowers who will be waived on the nod. Alert fatigue within one quarter — while the
genuinely impaired borrower who will not breach for six months sits at rank 40 and defaults.

**Mechanism — rank on expected consequence, not probability:**

```
priority = P(breach at horizon h)
         × P(not waived | breach)        ← the non-obvious factor
         × economic exposure (EAD, drawn + undrawn)
         × urgency (days until the action window closes)
```

`P(not waived)` is learnable from waiver history, syndicate structure, relationship tenure and
headroom trajectory. **Demo it as two side-by-side rankings — P(breach) order vs. consequence
order — and show they disagree at the top.** That split-screen is the whole argument that you
understand credit rather than classification.

### SP-5 — The Lead-Time Tax
**Hard truth.** Accuracy decays with horizon, brutally and publicly. Moody's EDF-X: **43%** of
eventual private-firm defaults flagged at 12 months, **51%** at one month, 44% combined error at a
17% watchlist rate. The brief's "30, 60 and 90 days" is not three settings on one model — it is
three different problems with three different achievable accuracies.

**Failure mode.** A single confident number: *"84% probability of breach in 90 days."* A CTO who
knows this market disbelieves it; one who doesn't asks why yours beats Moody's, and **there is no
good answer on synthetic data.**

**Mechanism — horizon-honest scoring.** Three **separately calibrated** models with reliability
diagrams shown *in the product*, not an appendix; bands that visibly widen with horizon. A
**persistence filter** (k-of-n consecutive periods above trigger — Moody's "first sustained
signal"), which is precisely the brief's "distinguish meaningful deterioration from temporary
noise." An **alert budget** expressed as Moody's does — a single user-set **Positive Signal Rate**,
"the percentage of firms from your portfolio that your risk tolerance will yield on your
watchlist." That framing answers the false-positive question with a policy dial instead of an
apology. And a **"why now" delta** on every alert.

### SP-6 — The Defensible Belief
**Hard truth.** The deliverable is not a score. It is **a reason to act that survives a lawyer.**
Under UCC §1-309 a party with an at-will acceleration/insecurity right "has power to do so only if
that party in good faith believes that the prospect of payment or performance is impaired."
**An unexplained model output is not a good-faith belief.**

**Failure mode.** The bank can use your alert for nothing except more monitoring, and the business
case collapses to zero. This is the *actual* reason bank EWS programmes underdeliver — not
accuracy.

**Mechanism — the Evidence Dossier as the primary artefact.** Every alert compiles into a
document: the covenant clause with citation and effective version; the computed value with full
line-item trail; each contributing signal with source, date and trust tier; a counterfactual
("if receivable days revert to 52, this alert clears"); and a recommended action mapped to an
authority level. **The score is a field on the dossier, not the product.**

### SP-7 — Signal Trust Tiers
**Hard truth.** The signals with the most lead time (news, industry, concentration) have the worst
entity resolution and weakest provenance. The ones with the best provenance (payments,
utilisation, treasury) have the least lead time.

**Failure mode.** "Sharma Textiles Ltd" in a news feed is not "Sharma Textiles Pvt Ltd" in the
loan book. One bad match triggers an unfounded facility review — or worse, becomes part of the
good-faith belief record under SP-6.

**Mechanism — a four-tier trust lattice with an action ceiling per tier:** internal deterministic
(transactions, utilisation, DPD) > internal derived (ratios) > external structured (ratings,
filings) > external unstructured (news, sentiment). **Unstructured signals may raise monitoring
frequency and move a name onto the watch list — exactly what EBA ¶272 and ¶277 prescribe — but can
never on their own cross the intervention threshold.** Every entity match carries a resolution
confidence and is human-confirmable.

### SP-8 — Escalation as State, not Notification
*The compliance backbone.* EBA ¶270 requires "assigned escalation procedures, including assigned
responsibilities for the follow-up actions"; ¶275 requires designated functions to analyse
severity "without undue delay"; ¶276 requires the decision documented and communicated onward. RBI
requires a TAT "preferably not more than 30 days" with Risk Management Committee oversight.

**An alert that is an email is not an early warning system.** Every alert is a durable, owned,
SLA'd **case object** with state, assignee, deadline clock, decision record and closure reason.
This is also how PS-04 earns a long-running-state story.

## Objection map

| # | Question | Rating | Answer |
| --- | --- | --- | --- |
| 1 | **"Your labels come from your own generator. What does your AUC mean?"** | **Only if pre-empted** | The killer question. Pre-empt it: open by saying you do not report AUC, show the DGP diagram, show calibration and base rates, and make the gaming detector — which needs no labels — the centrepiece. If this lands unprepared, the pitch is over. |
| 2 | "Why is yours better than Moody's EDF-X?" | **Partially** | It is not, on accuracy, and say so. Reframe to actionability: consequence ranking, the dossier, the wedge. Never claim to beat published benchmarks on synthetic data. |
| 3 | "How many alerts per RM per day?" | **Defensible** | Positive Signal Rate as a policy dial; precision@budget rather than AUC; the capacity-constrained knapsack. |
| 4 | "Can a lender act on this?" | **Defensible** | UCC §1-309 good-faith standard; the Evidence Dossier is built to be the record. |
| 5 | "Is this a model under supervisory guidance?" | **Defensible** | **SR 26-2 / OCC 2026-13**, not SR 11-7 — see `06-governance.md`. Statistical model predicts; LLM narrates over computed numbers only. |
