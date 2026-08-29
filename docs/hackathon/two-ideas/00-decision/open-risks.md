# Open risks and verify-before-slide list

Nothing in this list is a reason to change the recommendation. Every item is a reason to check
something before it reaches a slide or a Q&A answer. Sorted by consequence.

## Blocking risks

**OR-1 — Five Capital One sibling patents are unread. Owner: whoever writes the patent slide.**
US 11,935,046 B2 (granted 19 Mar 2024) claims bitemporal retroactive recomputation with immutable
supersession. Claim 1 was recovered verbatim by OCR'ing the scanned grant PDF from
`image-ppubs.uspto.gov` — every text-based patent source hard-blocked the research environment.
The family is **six**, all filed 1 Nov 2021 off provisional 63/157,455 ("CloudPRE"): serials
17/516,329 / 338 / 340 / 345 / 436 / 438. Only '438 (→ '046) has been read.
*Consequence:* if 17/516,345 or 17/516,436 reach the **action layer**, PS-17's patentability drops
from 6/10 to 3/10.
*Action:* read all five before any freedom-to-operate assertion is made. Until then the honest
line is "our claim sits above the storage layer; we have read the parent and are clearing the
family" — never "this is unpatented".
*Already settled:* "we replay history when an amendment lands" is **unpitchable** as novelty. The
defensible residue is re-adjudication of derived *normative verdicts* under effective-dated rule
versions, plus **irreversibility triage** on already-executed external actions.

**OR-2 — `SR 11-7` no longer exists.** Superseded 17 Apr 2026 by **SR 26-2 / OCC Bulletin
2026-13**, which also retires SR 21-8 and rescinds OCC Bulletin 2011-12. Verified independently
from both the Fed PDF and the OCC. Every other team will cite SR 11-7; knowing this is a free
credibility win, and citing the dead guidance is an unforced error.
Footnote 3, verbatim: *"Generative AI and agentic AI models… are not within the scope of this
guidance… the principles described in this guidance apply to traditional statistical and
quantitative models and non-generative, non-agentic AI models."*
*Nuance that must not be dropped:* the carve-out for deterministic rule-based processes
**relocates** burden rather than removing it, and escaping scope by pushing judgement into prose
is detectable **scope arbitrage**. PS-17 must **not** claim SR 26-2 exemption — that guidance
applies to banking organizations and never reached it.

**OR-3 — PG18 partial-unique-index question.** Unknown whether PostgreSQL 18 permits a *partial*
unique index carrying `WITHOUT OVERLAPS`. The two-table design (append-only assertion log +
constrained current-belief projection) sidesteps it. *Action:* spike on day 1, before the schema
hardens.

## Claims to drop entirely

Do not put these on a slide. They will not survive a Q&A with a prepared judge.

- **"60–80% of SLA credits go unclaimed."** A licensing consultancy's self-reported ~30
  engagements. Not research.
- **TM Forum's telecom leakage percentages.** Mutually inconsistent across their own publications.
- **Any loss-avoided-per-month-of-earlier-detection elasticity for PS-04.** No published figure
  exists. If a rival team shows one, they invented it. The honest substitute is to make the system
  measure its own realised lead time.
- **Any AUC figure for PS-04 presented as evidence of quality.** It measures your simulator.

## Numbers to correct before use

- **Contract value leakage: use 8.6%, not 9%.** WorldCC + Deloitte, *The ROI of Contracting
  Excellence* (June 2023, n>1,200), updating IACCM's 2014 9.2%. Best performers ~3%, worst >20%.
  The full PDF was read. Note it publishes its own ROI curve with an explicit "do not rely on this
  without further validation" caveat.
- **Do not lead PS-17 with SLA credits.** Most demo-able pool, smallest money: ~$84k/yr on $60m
  covered spend (0.14%). Lead with the obligation/leakage pool. The credit *forfeiture* mechanic is
  still worth citing — Google's SLA ("Customer will forfeit" after 60 days), AWS's (end of second
  billing cycle or "disqualified") — because it motivates the entitlement clock.

## Sources blocked in-session — verify before quoting

Marked unverified in the research rather than quietly used:

| Source | Status |
| --- | --- |
| Both McKinsey papers | 403 / timeout |
| RBI Financial Stability Report Dec 2025 | CAPTCHA |
| TM Forum figures | 403 |
| All of `tcs.com` | 403 — Infosys FY26 20-F used as verifiable sector proxy |
| Google Patents / Espacenet / Justia / FPO / PatentsView | hard-blocked; USPTO credentialed APIs 401/405 |
| Dichev & Skinner, Roberts & Sufi, Dahl et al. | search-summary only — verify before slide |
| RBI primary notification, Beneish threshold | search-summary only |
| One AUC temporal-leakage statistic | 403 — do not slide |

## Method caveat

The explainability lane exhausted its web-search budget mid-research, so its patentability
suggestion (*provenance-typed authority demotion*) is engineering judgement, **not** a
freedom-to-operate search. Only `_research/04` did prior-art work. Treat any patent claim not
sourced from that document as unverified.
