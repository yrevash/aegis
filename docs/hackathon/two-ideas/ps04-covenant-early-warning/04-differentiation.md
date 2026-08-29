# PS-04 — differentiation and patentability

## The commodity baseline

What the median team ships in seven days: a gradient-boosted classifier over synthetic borrower
features, SHAP, and a dashboard. Possibly an LLM writing a memo over the output.

**Uniqueness starts negative here.** Moody's Lending Suite and nCino already advertise AI covenant
monitoring with early-warning signals. Unlike PS-17 — where the incumbent frontier is
demonstrably task tracking — PS-04's incumbents already claim the thing the brief asks for.

## The strongest single idea in either problem statement

It comes from the **accounting literature, not the ML literature.**

Dichev & Skinner (JAR 2002): private-debt covenants are set tight, technical violations occur in
**~30% of loans**, and — critically — **leverage is a poor proxy for closeness to a covenant.**
Jha (2013): managers manage earnings **upward in the quarters preceding** a violation.

So the novel product is not "predict the breach". It is:

> **Detect the borrower who is *hiding* rather than the borrower who is *failing*.**

**The gaming detector:**
1. Bunching of reported ratios just above the covenant line.
2. Beneish-style accrual-quality decay.
3. Divergence between reported accrual revenue and **bank-observed cash collections** — the bank
   has this and the rating agencies do not.

**Why this is the centrepiece:** it is **pure arithmetic, needs no labels, and is therefore immune
to the circularity attack** that destroys everything else in PS-04. Nobody else in the room will
build it.

*Verify Dichev & Skinner, Jha, and the Beneish threshold against the primary papers before
quoting — see `../00-decision/open-risks.md`.*

## White space, ranked

| Idea | Novelty | Demo value | Cost | Notes |
| --- | --- | --- | --- | --- |
| **The gaming detector** | High | High | 1.5d | Label-free. The centrepiece |
| **Consequence ranking** (`P(not waived)`) | High | Very high | 1.5d | The split-screen |
| **Reporting-clock-as-signal** (EBA ¶274(o)) | High | Medium | 0.5d | Nobody builds this |
| **Covenant AST + contractual/GAAP wedge** | Medium-high | High | 2d | Add-back intensity is itself predictive |
| **Counterfactual headroom** | Medium | High | 1d | Scores 140 on (novelty × demonstrability ÷ days) — but **worthless without the glass-box arithmetic engine underneath it** |
| Quantile dotplots | Low | Medium | 0.5d | Cheapest credibility upgrade |

> **Plan the dependency graph, not the ranking table.** The cheapest high-impact features layer on
> one expensive foundational choice — the glass-box covenant-arithmetic engine — not on each other.

## The circularity problem, stated plainly

On synthetic data a supervised breach classifier is **structurally circular**: you write the
generator, recover it with XGBoost, then SHAP the generator. Any AUC measures your own simulator.
**A CTO needs one question to break it.**

Every escape route — forecast the covenant *components* and evaluate the real formula, report
conformal coverage instead of AUC — works by **abandoning the headline prediction claim the brief
actually asks for.**

This is inherent. Manage it by pre-empting it in the first 30 seconds of the demo (see
`03-experience.md`), and by making the label-free gaming detector the centrepiece.

## The public accuracy bar

Moody's EDF-X publishes: **43%** of eventual *private-firm* defaults flagged 12 months out,
**51%** at one month, **44% combined error at a 17% watchlist rate.**

*"We predict better"* is unwinnable in front of judges who can look that up. Reframe to
**actionability** — consequence ranking, the dossier, the wedge — and say explicitly that you are
not claiming to beat published benchmarks on synthetic data. That honesty scores better than the
claim would.

## Anti-slop

The four things every other team will show, all LLM-with-extra-steps: a chat box over financials,
multi-agent theatre, an LLM-written credit memo, a SHAP bar chart. PS-04 is *more* exposed to this
than PS-17, because its natural shape invites all four.

## Patentability — 3/10, and it fails on law rather than art

This is PS-04's weakest lane and the gap is not recoverable in seven days.

**US §101.** *Electric Power Group v. Alstom*, 830 F.3d 1350 (Fed. Cir. 2016) holds that
collecting data from disparate sources, analysing it, and displaying the result is an abstract
idea. **That is a one-sentence description of PS-04's own process flow.** Worse, the USPTO's 2019
PEG names *"fundamental economic principles or practices (including hedging, insurance, mitigating
risk)"* as an enumerated abstract-idea grouping. **Covenant breach forecasting *is* mitigating
risk.**

**EPO.** Guidelines G-II 3.3.2 (post-G 1/19): *"Calculated numerical data reflecting the physical
state or behaviour of a system or process existing only as a model in a computer usually cannot
contribute to the technical character of the invention."* PS-04's modelled system is not even
physical — it is a borrower's balance sheet.

**India §3(k)** — the harshest, and the jurisdiction that matters for a TCS-affiliated jury. The
IPO's 2013 CRI Guidelines: *"if in substance the claims relate to business method even with the
help of technology, they are not considered patentable."* **PS-04 hits two limbs at once —
business method *and* mathematical method.**

**Prior art density compounds it.** Credit early-warning and covenant monitoring sit inside CPC
**G06Q 40/03**, one of the most heavily worked corners of fintech, alongside every bank, bureau
and rating agency.

**The honest position for the deck:** do not claim patentability for PS-04. Claim **trade secret
and defensive publication** on the gaming detector's specific feature construction, and spend the
slide on the regulatory moat instead — which is genuinely strong (see `05-business-case.md`).
