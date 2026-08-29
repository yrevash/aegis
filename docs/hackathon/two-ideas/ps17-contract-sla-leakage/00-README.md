# PS-17 — Contract Obligation, SLA & Commercial Leakage Monitor

**Recommended build.** 83.8 / 100 on the weighted scorecard, winning seven of eight lanes.
See `../00-decision/recommendation.md` for why, and `../00-decision/open-risks.md` for what to
verify before it reaches a slide.

## The one-sentence framing

> A contract is a set of promises with dates on them. The money is not lost when a promise is
> broken — it is lost when nobody notices in time, or when the rulebook changes after you already
> acted. We built the system that gets both right.

## The thesis

PS-17 is a distributed-systems problem wearing a legal-tech costume. The organisers wrote the
hardest requirement into the brief — *represent late, corrected and conflicting versions without
losing earlier evidence* — and then supplied a live, graded test of it in the National Finale
inject. Everything in this folder is organised around that.

## What wins, in priority order

1. **The amendment inject, answered completely.** Not "we re-ran it" — re-adjudication under the
   correct effective version, bounded to exactly the affected events, reconciled against actions
   already taken.
2. **The conclusion diff.** Every CLM vendor redlines documents. Nobody diffs derived conclusions.
3. **Irreversibility triage.** A served notice cannot be un-served. This is the patentable residue
   and the "oh no" beat of the pitch simultaneously.
4. **Deterministic verdicts.** The LLM extracts a typed norm object; a deterministic evaluator
   renders the verdict. You can unplug the model on stage and the verdicts do not change.

## Files

| File | Contents |
| --- | --- |
| `01-pitch-spine.md` | The seven named sub-problems, the inject decomposed, the objection map |
| `02-architecture.md` | Data model, durable execution, the stack that runs on bare Windows |
| `03-experience.md` | Screen inventory, the signature visual, the five-minute storyboard |
| `04-differentiation.md` | Innovation white space, prior art, the drafted patent claim |
| `05-business-case.md` | The leakage pool, the ROI model, the buyer |
| `06-governance.md` | Provenance types, OTel spans, the autonomy ladder, the hash chain |
| `07-build-plan.md` | Seven days, with the day-4 gate |

## The two gates

- **Three or more engineers**, or switch to PS-04. PS-17 is higher ceiling, lower floor.
- **The bitemporal plane renders real data by end of day 4**, or cut to the fallback in
  `03-experience.md`.
