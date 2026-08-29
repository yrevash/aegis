# PS-04 — experience and visuals

Scored 6.0 against PS-17's 8.5. The problem is not that PS-04 is hard to visualise — it is that
everything it naturally produces is **already shipped by the incumbents**, and two of its headline
charts are actively contested.

## What to avoid, and why

| Avoid | Why |
| --- | --- |
| **Fan charts** | The **Bernanke Review of Bank of England forecasting (April 2024)** recommended the Bank *stop* publishing them and de-emphasise a central forecast in favour of scenarios; the Bank accepted all recommendations. Putting one on a hero slide in front of a finance-literate panel in 2026 is a mild own-goal |
| **SHAP driver waterfall** | Reads to a technical judge as *"you called `shap.plots.waterfall()`"* — because that is literally the default plot's shape |
| **Portfolio risk heatmap** | nCino Continuous Credit Monitoring, Abrigo Portfolio Risk and Moody's Early Warning System all ship it |
| **A single confident probability** | "72% chance of breach in 60 days" on synthetic data is **unfalsifiable inside five minutes** |

The only screen that answers *"why should I believe that number"* is a **reliability diagram /
backtest** — the right engineering answer, and a deliberately unglamorous demo beat. Show it
anyway; honesty is the pitch.

## What to build instead

### The hero: the threshold notch histogram
A histogram of distance-to-covenant across the portfolio, with a **visible hole just below zero**
and an arrow labelled *"these did not disappear — they were moved."*

Cheap to build, genuinely novel in this domain, and it makes an invisible statistical insight
(SP-3) into a single legible image. **This is PS-04's best available visual moment.**

### The split-screen ranking
Two portfolio rankings side by side — **P(breach) order vs. consequence order** — visibly
disagreeing at the top.

That single screen is the whole argument that you understand credit rather than classification
(SP-4). Second-best moment in the build.

### The leakage reveal
Two backtest curves side by side: the leaky model's spectacular AUC, and the point-in-time-correct
model's honest one. Watch the number collapse.

This is the deliberate mitigation for PS-04's invisible-depth problem (see `02-architecture.md`).
It converts the hardest engineering in the build into something a jury can see.

### Quantile dotplots instead of bare percentages
Fernandes et al. (CHI 2018) found quantile dotplots let people reach **97% of optimal payoff** in
decisions under uncertainty. Use them wherever you would otherwise print a naked probability. This
is the single cheapest upgrade to PS-04's visual credibility — it lifts the lane from ~6.0 to
~7.5.

### The Evidence Dossier
The primary artefact (SP-6). The score is a *field* on it. Show the dossier, not the score.

## Screen inventory

| # | Screen | What it proves |
| --- | --- | --- |
| 1 | Portfolio watchlist, ranked by **consequence** | SP-4 — you understand waivers |
| 2 | **Split-screen ranking comparison** ★ | The credit-vs-classification argument |
| 3 | **Threshold notch histogram** ★ hero | SP-3 — borrowers manage the number |
| 4 | Borrower detail: covenant AST, contractual vs GAAP wedge | SP-2 — the contract is the model |
| 5 | **Evidence Dossier** | SP-6 — a reason to act that survives a lawyer |
| 6 | Calibration / reliability + **leakage reveal** ★ | SP-1 and honest scoring |
| 7 | Signal trust lattice with action ceilings | SP-7 |
| 8 | Case queue with escalation state, owner, TAT clock | SP-8 — EBA ¶270/275/276 |

**Library call:** ECharts for everything conventional; quantile dotplots are a custom series but
cheap. No bespoke 2-D plane needed — which is precisely why PS-04's build risk is lower and its
ceiling is too.

## Storyboard

| Time | What the jury sees |
| --- | --- |
| 0:00 | **Pre-empt the killer question immediately.** "We generated this data, so we will not show you an AUC. Here is what we will show you." |
| 0:30 | Threshold notch histogram. "Borrowers know where their covenant is. The breaches did not disappear — they were moved." |
| 1:15 | Split-screen ranking. P(breach) vs consequence. They disagree at the top. "63% of violations are waived without changing terms. Ranking by probability hands the committee a queue of names who get waived on the nod." |
| 2:15 | Borrower detail: covenant AST, contractual vs GAAP wedge. "Leverage is whatever the agreement says it is. 3,595 of 3,939 loan packages carry at least one non-GAAP add-back." |
| 3:00 | Evidence Dossier. Clause, citation, line-item trail, counterfactual, recommended action, authority level. "The score is a field on this. This is the product." |
| 3:45 | Leakage reveal. Two curves. The spectacular one is the wrong one. |
| 4:15 | Alert budget dial + case queue with TAT clocks. "The scaling limit here is not CPU. It is attention." |
