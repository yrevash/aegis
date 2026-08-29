# Two Ideas — PS-17 vs PS-04 decision pack

AI Friday · National Finals · Season 2 · build window **29 Aug → 4 Sep 2026**.

This folder exists to answer one question — **which problem statement do we build?** — and
to leave us with a ready plan for whichever one wins, so no research time is wasted on the
one we drop.

## The verdict

**PS-17 first (83.8 / 100), PS-04 second (58.9 / 100).** PS-17 wins seven of eight weighted lanes
and the decomposition tie-breaker. PS-04 wins business impact, decisively.

This **reverses the team's going-in preference**, and it reverses the specific belief that PS-17
was weak on the front end — PS-17 wins visuals 8.5 to 6.0. Full reasoning in
`00-decision/recommendation.md`.

Two conditions flip the order, both about resourcing: **fewer than three engineers**, or **the
bitemporal plane not rendering real data by end of day 4**.

| | Problem statement | Domain | Score |
| --- | --- | --- | ---: |
| **PS-17** | Contract Obligation, SLA & Commercial Leakage Monitor | Enterprise operations · decision intelligence | **83.8** |
| **PS-04** | AI-Powered Dynamic Covenant Monitoring & Early Warning | Commercial banking · credit risk | 58.9 |

## How to read this pack

1. **`00-decision/`** — start here. The weighted scorecard and the recommended priority
   order. This is the document the team argues over.
2. **`ps17-contract-sla-leakage/`** and **`ps04-covenant-early-warning/`** — one build-ready
   folder each. Sub-problem decomposition, architecture, screens, innovation claims, business
   case, production answer, 7-day plan, demo script. Whichever we pick, that folder becomes
   the working plan.
3. **`_research/`** — the raw lane-by-lane research the decision was built from, with sources.
   Go here when someone challenges a claim in the decision doc.
4. **`_briefs/`** — the two problem statements verbatim, plus the shared context the research
   agents worked from. The source of truth for what was actually asked.

## Ground rules this pack was built under

- The research was run **clean-room**: the agents were given only the two briefs and were
  explicitly forbidden from reading this repository or considering any pre-existing codebase.
  The decision is on the merits of the problem statements alone.
- **Every non-obvious claim carries a source.** Unsourced claims are labelled
  `[UNVERIFIED — no source found]`. If you find one that is neither sourced nor labelled,
  that is a bug in this pack — fix it before it reaches a slide.
- Hard constraints assumed throughout: **Windows demo machine, no Docker**, a 7-day build
  window, synthetic data only, and a live demo judged by CTOs.
