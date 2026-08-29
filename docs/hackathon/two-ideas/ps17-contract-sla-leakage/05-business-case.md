# PS-17 — business case

This is PS-17's **weakest** lane (6.5 vs PS-04's 8.5). Lead the pitch with the engineering and
the inject; bring these numbers when asked. Do not open with them.

## The headline number — use 8.6%, not 9%

**WorldCC + Deloitte, *The ROI of Contracting Excellence* (June 2023, n>1,200):** organisations
lose **8.6%** of annual contract value to poor contract management. This updates IACCM's 2014
figure of 9.2% — the version everyone else in the room will quote. Best performers a little over
**3%**, worst more than **20%**.

Knowing it is 8.6% and knowing it was revised is a cheap credibility signal.

Caveat to hold in reserve: the same report publishes its own ROI curve (0.5%–6.25% of
revenue/spend by capability shortfall) with an explicit *"do not rely on this without further
validation"* note. Both ROI cases below sit deliberately **below** that curve.

## Do not lead with SLA credits

They are the most demo-able pool and the **smallest**: roughly **$84k/yr on $60m of covered spend
— 0.14%**.

The *forfeiture* mechanic is still worth citing, because it motivates the Entitlement Clock:

- **Google Cloud SLA** — claim within 60 days with log files, or *"Customer will forfeit"*.
- **AWS SLA** — end of the second billing cycle, or *"disqualified"*.

That is the leakage insight in two citations: the credit is *claimable, not automatic*, and the
window is short.

**Do NOT use** the widely-repeated "60–80% of SLA credits go unclaimed" figure. It traces to a
licensing consultancy's self-reported ~30 engagements, not research. Also drop TM Forum's telecom
leakage percentages — they are mutually inconsistent across TM Forum's own publications.

## The ROI model

On a **$2.0bn contracted-value book**:

| Case | Annual recoverable | Basis |
| --- | ---: | --- |
| Conservative | **$13.4m** | Sub-curve capture against the 8.6% leakage pool |
| Optimistic | **$36.4m** | Upper capture, still below the WorldCC ROI curve |

**Conservative ROI: 14.6×.**

State the assumptions out loud so a CTO attacks the assumptions rather than the conclusion. That
is the point of showing the arithmetic.

## The one genuine commercial advantage over PS-04

**PS-17 can be sold on contingency.** Recovery of leaked value funds the engagement, so there is
no budget-approval gate — you are paid from money the client did not know they had. PS-04 requires
a CRO-office budget line and a procurement cycle.

Per-enterprise, PS-17's recoverable pool is arguably **larger** and easier to collect than PS-04's
avoided-loss pool. PS-17 loses the lane on *defensibility of the numbers* and *buyer clarity*, not
on size.

## The buyer

Procurement, legal ops, revenue assurance, vendor management. Diffuse — that is the weakness. No
single ring-fenced budget line, and no regulator forcing the spend.

**Be honest about this in Q&A.** PS-17's driver is commercial, not regulatory. DORA Arts. 28–30
force contractual SLA *content and registers* for ICT third parties, but only at financial
entities, and they do not mandate obligation-evidence reconciliation. Claiming a regulatory
mandate PS-17 does not have is the fastest way to lose a finance-literate judge.

## The seam that justifies the product

CLM sells **obligation tracking** — "did we do the thing". Revenue assurance sells **leakage
recovery** — "did we collect the thing we're owed". Different discipline, different department,
different tooling.

> The money sits in the seam, and neither side's product owns it.

That sentence is the business slide.

## The three numbers for the slide

1. **8.6%** of annual contract value lost to poor contract management — WorldCC + Deloitte, 2023.
2. **$13.4m/yr** conservative recoverable on a $2.0bn book — **14.6× ROI**, assumptions stated.
3. **0 days** of budget approval — contingency-fundable from recovered value.

## Sector relevance

`tcs.com` was 403-blocked during research, so **Infosys' FY26 20-F is used as the verifiable
sector proxy**: Financial Services at **27.9%** of $20,158m revenue. Use the proxy honestly, or
substitute a TCS figure you can source yourself before the pitch.
