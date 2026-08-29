# Recommendation

## Priority order

1. **PS-17 — Contract Obligation, SLA & Commercial Leakage Monitor** (83.8 / 100)
2. **PS-04 — AI-Powered Dynamic Covenant Monitoring & Early Warning** (58.9 / 100)

This reverses the team's going-in preference. The reasoning is below, including the two
conditions under which the order should flip back.

## The argument in one paragraph

PS-17 is a distributed-systems problem wearing a legal-tech costume; PS-04 is a forecasting
problem wearing a banking costume. The organisers wrote PS-17's hardest requirement into the
brief — *represent late, corrected and conflicting versions without losing earlier evidence* —
and then supplied a live, graded test of it in the National Finale inject. That inject is the
textbook motivating case for bitemporal modelling: an action has already been taken on a belief
that is later retroactively corrected. You do not have to invent your differentiator, argue for
its relevance, or prove it on data you generated yourself. You have to build it and drag a
slider. PS-04, by contrast, asks for a prediction whose ground truth you must invent, on
synthetic data you wrote the generator for, against a public accuracy bar you cannot beat and a
patentability position that fails on law rather than art.

## The five findings that decide it

**1. The finale inject is a gift, and it is graded.**
*(`_research/01`, `_research/02`, `_research/05`)*
One sentence hides three failures, and most teams see only the first: re-evaluate under the new
threshold; realise the effective date can *precede* signature, so the correct behaviour is
retroactive rather than prospective; and reconcile the revised conclusion with **actions already
taken**. The third is the graded moment. Note that prospective-only is the *industry-standard
engineering default* — Temporal's `patched()` explicitly keeps in-flight executions on the old
code. Saying that out loud to a CTO panel demonstrates you know the default and know why it is
wrong here.

**2. PS-04's core claim is structurally circular, and one question breaks it.**
*(`_research/05`, `_research/02`)*
On synthetic data you write the generator, recover it with a gradient-boosted model, then SHAP
the generator. Any AUC you report measures your own simulator. Every escape route — forecast the
covenant *components* and evaluate the real formula, report conformal coverage instead of AUC —
works by abandoning the headline prediction claim the brief actually asks for. The public bar is
also brutal: Moody's EDF-X publishes 43% of eventual private-firm defaults flagged at 12 months,
51% at one month, 44% combined error at a 17% watchlist rate. "Our model is better" is
unwinnable in front of judges who can look that up.

**3. The frontend prior is inverted.**
*(`_research/03`)*
PS-17's supposed weakness is its greatest visual strength. A retroactive amendment is a **state
transition** — the only thing in either brief that can legitimately animate, and Heer &
Robertson (InfoVis 2007) justify the animation perceptually rather than decoratively. The
unclaimed visual is the **conclusion diff**: every CLM vendor redlines *documents*; nobody diffs
*derived conclusions*. Meanwhile two of PS-04's headline visuals are contested or commodity —
the Bernanke Review (Apr 2024) got the Bank of England to retire fan charts, and the driver
waterfall reads to a technical judge as a default `shap.plots.waterfall()` call.

**4. PS-17 has a scaling story; PS-04 does not.**
*(`_research/07`)*
PS-17's steady state is deliberately unimpressive — ~17 evaluations/sec. Its *retroactive*
fan-out spans four orders of magnitude: a single-obligation amendment reopens ~4,400 events, a
master term inherited by 200 SOWs reopens 18.25M. Bounding that needs genuine technique. PS-04's
entire nightly inference is under one second of CPU; its real limit is human alert fatigue, which
is a good finding but not a systems answer.

**5. Patentability: PS-17 wins narrowly on art; PS-04 loses decisively on law.**
*(`_research/04`)*
The claimable residue in PS-17 is real but must sit *above* the storage layer (see Open Risk 1).
PS-04 fails §101 under *Electric Power Group* and the 2019 PEG's enumerated "mitigating risk"
grouping, fails EPO G-II 3.3.2's "model existing only in a computer" test, and hits **two** limbs
of India's §3(k) at once — business method *and* mathematical method. For a TCS-affiliated jury,
the Indian position is the one that matters.

## What PS-04 genuinely wins, and what to take from it

**Business impact, 8.5 vs 6.5.** PS-04 is a regulation written as a problem statement. EBA
GL/2020/06 §267 tells banks covenant adherence "should be utilised as early warning tools";
§269 requires EWIs "supported by an appropriate IT and data infrastructure"; the remediation
window closed 30 June 2024. Add RBI's Stressed Assets Directions of 28 Nov 2025. Its buyer has a
name and a ring-fenced budget. PS-17 has no equivalent forcing function — its case is commercial,
not regulatory.

**Take this across:** if PS-17 is chosen, import PS-04's forward-looking framing as a one-day
feature. An SLA burn-down with conformal bands — *"you are at 99.87% with 9 days left in the
month; 26 minutes of downtime budget remain; P(monthly breach) = 0.71"* — rests on a mature
cloud-computing SLA-violation-prediction literature that no CLM vendor has imported. It converts
a rules engine into an early-warning system for about a day of work, and it lets you answer "why
not the *other* problem statement?" by having already absorbed its best idea.

## The two conditions that flip the order

Both are resourcing questions, and only the team can answer them.

**Condition A — fewer than three engineers.** Stated independently by two lanes. PS-17 is
**higher ceiling, lower floor**: its floor is a table plus a log line, which loses. PS-04 degrades
gracefully into a competent dashboard, which places. Below three engineers, take PS-04.

**Condition B — the bitemporal plane is not working by end of day 4.** PS-17's visual case
concentrates almost entirely in one bespoke `visx` screen with no library that does it for you
(~2.5 days). This is a hard gate, not a soft risk: if that screen is not rendering real data by
end of day 4, PS-17's advantage collapses to roughly parity and the safer build wins. Put the
checkpoint in the plan on day 1.

With one frontend engineer rather than two, the visuals lane narrows from 8.5–6.0 to roughly
7.0–6.5, which shrinks the overall margin without reversing it.

## Recommended decision

**Build PS-17**, provided the team can field three or more engineers and commits to the day-4
gate. Import PS-04's burn-down as a one-day feature. If the team is smaller than three, build
PS-04 and lead with the regulatory forcing function rather than model accuracy.
