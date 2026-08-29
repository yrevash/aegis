# PS-04 — seven-day build plan

**29 Aug → 4 Sep 2026.** PS-04 is the choice when the team is smaller than three engineers — it
degrades gracefully, and it has no single-screen gate the way PS-17 does.

## Day 0 — before anything

| Owner | Task |
| --- | --- |
| BE | Install PostgreSQL 18 + pgmq + DBOS on the **actual Windows demo box** |
| Data | **Design the synthetic generator carefully — and write down its DGP** |
| Data | Verify Dichev & Skinner, Jha, Roberts & Sufi and the Beneish threshold against primary papers (see `../00-decision/open-risks.md`) |
| Pitch | Pull EBA/GL/2020/06 §§267–277 and the RBI Nov-2025 directions verbatim |

**The generator is the highest-risk artefact in a PS-04 build.** It must include, by construction:
borrowers who *manage the number* (bunching just above threshold), borrowers who breach and are
**waived**, restated financials arriving late, and at least one out-of-distribution borrower the
model should refuse to score confidently. Without these, SP-3, SP-4 and the A1 fallback have
nothing to show.

**Write the DGP diagram on day 0.** You will show it on stage in the first 30 seconds.

## Days 1–2 — the arithmetic foundation

- **BE:** point-in-time feature store keyed by **knowledge date**. Labels stamped at *delivery*
  date. This is the invisible-but-essential piece; build it first because everything else depends
  on it being right.
- **BE:** covenant AST — parse definitions into an executable formula over named line items, with
  add-backs, caps, time restrictions. Compute contractual **and** GAAP ratios.
- **FE:** screens 1 and 4 (watchlist, borrower detail with the wedge).

> The glass-box covenant-arithmetic engine is the **foundational dependency**. The cheap
> high-impact features (counterfactual headroom, the wedge, the gaming detector) all layer on it
> and are worthless without it. Plan the dependency graph, not the ranking table.

## Day 3 — the label-free centrepiece

- **BE:** **the gaming detector** — ratio bunching above threshold, accrual-quality decay, reported
  revenue vs bank-observed cash collections. No labels needed; immune to the circularity attack.
- **FE:** **the threshold notch histogram** (the hero). Cheap to build, and it is PS-04's best
  visual moment.
- **BE:** reporting-clock-as-signal (EBA ¶274(o)) — half a day, and nobody else builds it.

## Day 4 — the model, honestly

- **BE:** discrete-time hazard model (person-period + regularised logistic regression, horizon
  indicators). **Not Cox. Not DeepHit.**
- **BE:** three separately calibrated horizons; reliability diagram + Brier score; conformal
  intervals.
- **BE:** the **leaky-vs-correct backtest pair** — this is the demo artefact that makes the
  invisible depth visible.
- **FE:** screen 6 (calibration + leakage reveal).

## Day 5 — consequence, not probability

- **BE:** `P(not waived | breach)` model from waiver history, syndicate structure, relationship
  tenure, headroom trajectory.
- **BE:** consequence ranking = P(breach) × P(not waived) × exposure × urgency.
- **FE:** **the split-screen ranking** — P(breach) order vs consequence order, disagreeing at the
  top. Second-best visual moment in the build.
- **BE:** alert budget / Positive Signal Rate dial; persistence filter (k-of-n).

## Day 6 — governance and the dossier

- **BE:** **the Evidence Dossier** — clause with citation and effective version, computed value
  with line-item trail, signals with source/date/trust tier, counterfactual, recommended action
  with authority level. *The score is a field on this.*
- **BE:** signal trust lattice with action ceilings; entity-resolution confidence, human-confirmable.
- **BE:** case objects — state, assignee, TAT clock, decision record, closure reason (EBA
  ¶270/275/276).
- **BE:** the **"no new numbers" check** on generated narrative — and a deliberately tampered
  prompt that makes it fail on stage.
- **FE:** screens 5, 7, 8; quantile dotplots wherever a bare probability would otherwise appear.

## Day 7 — freeze and rehearse

- **Morning:** feature freeze. Seed-data determinism only.
- **Rehearse the storyboard four times**, especially the **first 30 seconds** — the pre-emption of
  the circularity question is the most important half-minute of the pitch.
- **Pre-flight:** cold-boot the demo box; confirm no step needs network.
- **Rehearse Q&A**, especially objections 1 and 2 in `01-pitch-spine.md`.

## Cut order under time pressure

Last thing standing on the left:

`covenant AST + point-in-time store`
← `gaming detector`
← `threshold notch histogram`
← `consequence ranking + split-screen`
← `Evidence Dossier`
← `calibration + leakage reveal`
← `trust lattice`
← `case objects / TAT`
← `quantile dotplots`
← `OTel traces`

## Definition of done

The demo is ready when a judge can, unassisted:

1. See the DGP acknowledged in the first 30 seconds, before they ask.
2. See the threshold notch and understand immediately why it matters.
3. See P(breach) ranking and consequence ranking disagree at the top.
4. Open a dossier and trace a number to the clause that defines it.
5. Watch the leaky backtest's spectacular number collapse to the honest one.
6. Turn the alert-budget dial and watch the watchlist resize.
