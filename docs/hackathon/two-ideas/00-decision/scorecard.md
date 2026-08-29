# Weighted scorecard — PS-17 vs PS-04

**Status: complete.** All eight research lanes reported. Every score traces to a file in
`_research/`. Weights were set before any lane reported, and were not adjusted afterwards.

## Weights

Derived from what the team stated the jury decides on, with backend weighted heaviest because
it is the stated priority and because everything else is downstream of it.

| # | Criterion | Weight |
| --- | --- | ---: |
| 1 | Backend depth | 20 |
| 2 | Visuals / demonstrability | 15 |
| 3 | Innovation | 12 |
| 4 | Business impact | 12 |
| 5 | Production readiness | 11 |
| 6 | Uniqueness / originality | 10 |
| 7 | Patentability | 10 |
| 8 | Explainability & autonomy | 10 |
| | **Total** | **100** |
| — | *Out-of-the-box bonus (crypto-provenance)* | *up to +3* |

## Scores

| Criterion | Wt | PS-17 | PS-04 | Wtd 17 | Wtd 04 | Evidence |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Backend depth | 20 | **9.0** | 5.5 | 180.0 | 110.0 | `_research/02-backend-architecture.md` |
| Visuals / demonstrability | 15 | **8.5** | 6.0 | 127.5 | 90.0 | `_research/03-frontend-visuals.md` |
| Innovation | 12 | **8.5** | 6.5 | 102.0 | 78.0 | `_research/05-innovation-sota.md` |
| Business impact | 12 | 6.5 | **8.5** | 78.0 | 102.0 | `_research/06-business-impact.md` |
| Production readiness | 11 | **8.5** | 6.5 | 93.5 | 71.5 | `_research/07-production-scaling.md` |
| Uniqueness / originality | 10 | **8.5** | 5.5 | 85.0 | 55.0 | `_research/05-innovation-sota.md` |
| Patentability | 10 | **6.0** | 3.0 | 60.0 | 30.0 | `_research/04-patentability-prior-art.md` |
| Explainability & autonomy | 10 | **8.75** | 5.25 | 87.5 | 52.5 | `_research/08-explainability-autonomy-blockchain.md` |
| | | | | **813.5** | **589.0** | |
| **Normalised /100** | | | | **81.4** | **58.9** | |
| Out-of-the-box bonus | +3 | +2.4 | +0.0 | | | crypto-provenance 8/10 vs 3/10 |
| **FINAL** | | | | **83.8** | **58.9** | |

**Margin: 24.9 points.** PS-17 wins seven of eight weighted lanes and the decomposition
tie-breaker (9 vs 7). PS-04 wins one: business impact.

The result is not close and is not sensitive to reasonable re-weighting. To flip the order you
would have to weight business impact above roughly 55 of the 100 available points — i.e. decide
that the jury scores commercial case above backend, visuals, innovation, production,
uniqueness, patentability and explainability *combined*.

## Where the team's prior was wrong

Recorded before results landed, so it could be checked rather than confirmed:

| The team believed | The research found |
| --- | --- |
| PS-04 first, PS-17 second | **PS-17 first, by 24.9 points** |
| PS-17 is strong on backend | Confirmed, and more strongly than expected — 9.0 |
| PS-17 has **caveats on the front end** | **Inverted.** PS-17 wins visuals 8.5 vs 6.0 |
| PS-04 is good on front end | PS-04's visuals are legible but *generic* — the incumbents already ship all four of its natural charts |
| PS-04's backend is at "some mature level" | PS-04's hard problem is real but **invisible**, and getting it wrong makes the demo look *better* |

The frontend inversion is the single most consequential finding in this pack. The prior
compared **raw material** — covenants come with numbers, numbers come with charts; contracts
come with text. That is true and it evaluates the wrong thing. A five-minute CTO demo is won by
one legible moment where something unexpected visibly happens and is obviously hard. PS-04 is
chart-rich and moment-poor. PS-17 is chart-poor and moment-rich, and moments are the scarcer
resource.
