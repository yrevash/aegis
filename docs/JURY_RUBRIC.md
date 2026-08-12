# Jury Scoring Rubric — what the build is judged on

The TAIF hackathon jury scores on **6 weighted areas** (weights sum to 100%).
Performance levels: **5 = Outstanding (100%) · 4 = Excellent (80%) · 3 = Good (60%) ·
2 = Fair (40%) · 1 = Poor (20%)**. Points earned = sub-weight × performance level.

Every hour of work should move one of these levers.

| Weight | Area | What the jury judges | How Aegis serves it |
|---|---|---|---|
| **25%** | **Working Technical Solution Prototype** | prototype level, business value, **demo readiness** | The real, enforced, working system + the live "show your work" console. This is why *nothing is showoff* — every SOTA piece must actually run (guardrails, real graph, Qdrant, Redis memory, conformal ML). |
| **20%** | **Solution Hypothesis** | innovation, feasibility, alignment to the problem | The modular, importable, enterprise-grade agentic platform — a domain weapon retargetable in ~2 hours. |
| **15%** | **Solution Articulation & Presentation** | clarity of explanation, visuals/demo, teamwork | The console visuals, the projector Present mode, a clear narrative. |
| **15%** | **Problem Statement Understanding** | clarity + supporting research/evidence | Retrieval with citations + provenance; the problem-framing in the demo. |
| **15%** | **Business Impact Assessment** | value proposition, key metrics | Cost-savings, quality/eval numbers — **measured, not claimed**. |
| **10%** | **Roadmap for Production Scaling** | milestones, dependencies | The enterprise infra itself: Qdrant vector DB, Redis semantic cache, RLS multi-tenant governance, OTel/Phoenix observability, AEGIS_MODE. Keep an explicit milestones/dependencies story ready. |

**Alignment of current work:** the "make-it-real / SOTA enterprise" program
(`docs/superpowers/plans/2026-08-12-make-it-real-sota.md`) maps almost entirely to
the top three levers — a **real working prototype (25%)**, an **innovative feasible
hypothesis (20%)**, and a **production-scaling roadmap (10%)**. The blind-domain
adapter rehearsal de-risks the prototype + hypothesis on the day.
