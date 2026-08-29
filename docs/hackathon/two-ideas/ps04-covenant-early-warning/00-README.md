# PS-04 — AI-Powered Dynamic Covenant Monitoring & Early Warning

**Second priority.** 58.9 / 100 on the weighted scorecard. Wins one lane of eight — business
impact, 8.5 vs 6.5 — and it wins it decisively.

**Take this instead of PS-17 if** the team is smaller than three engineers. PS-04 degrades
gracefully into a competent dashboard; PS-17's floor is a table plus a log line. See
`../00-decision/recommendation.md`, Condition A.

## The one-sentence framing

> A covenant breach is not a surprise. It is the last event in a chain of signals the bank
> already had. We built the system that reads the chain — and that knows the difference between a
> borrower who is failing and a borrower who is hiding.

## The thesis, and the trap it must dodge

PS-04 is a **regulation written as a problem statement**. EBA GL/2020/06 §267 tells banks covenant
adherence "should be utilised as early warning tools"; §269 requires EWIs "supported by an
appropriate IT and data infrastructure." The remediation window closed 30 June 2024. RBI's
Stressed Assets Directions landed 28 Nov 2025. The buyer has a name, a budget line, and a
supervisor who will ask about this whether or not they buy anything.

**The trap:** on synthetic data, a supervised breach classifier is circular. You write the
generator, recover it with a gradient-boosted model, then SHAP the generator. Any AUC you report
measures your own simulator, and one question from a CTO destroys it.

**Therefore the entire build is organised around making the arithmetic the contribution rather
than the accuracy number.** Every idea in this folder is chosen to route around the circularity.
Do not pitch accuracy. Pitch defensibility, consequence-ranking, and the gaming detector.

## What wins, in priority order

1. **The gaming detector.** Detect the borrower who is *hiding* rather than the one who is
   *failing*. Pure arithmetic, needs no labels, immune to the circularity attack.
2. **Consequence ranking, not probability ranking.** ~63% of covenant violations are waived
   without altering major terms. Ranking by P(breach) hands the credit committee a queue of names
   who will be waived on the nod.
3. **The Evidence Dossier as the product.** The score is a field on the dossier, not the
   deliverable. An unexplained model output is not a good-faith belief under UCC §1-309.
4. **Horizon honesty.** Three separately calibrated models with reliability diagrams *in the
   product*, and a stated true lead time net of reporting lag.

## Files

| File | Contents |
| --- | --- |
| `01-pitch-spine.md` | The seven named sub-problems and the objection map |
| `02-architecture.md` | Point-in-time correctness, the model choice, the stack on bare Windows |
| `03-experience.md` | Screens, what to avoid, the storyboard |
| `04-differentiation.md` | White space, the circularity problem, why patentability is weak |
| `05-business-case.md` | The regulatory forcing function — PS-04's strongest asset |
| `06-governance.md` | SR 26-2, the AI Act, autonomy, and why to skip crypto |
| `07-build-plan.md` | Seven days |
