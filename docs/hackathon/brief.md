# Mission context — what we are building, why, and how we are judged

> Read this first. Every implementation decision must trace back to something here. If a request conflicts with this file, stop and ask.

---

## 1. The event

- **What:** TAIF S2 — a high-stakes on-site hackathon among top teams.
- **Format:** ~25 hours of on-site build spread across **two days**, with a working system + pitch deck + demo video as deliverables.
- **Problem statement:** revealed **on the day**, and it is **100% blind** — no theme, no domain, no track known in advance.
- **Therefore the prime directive:** we do **not** build a solution ahead of time. We build a **domain-agnostic agentic platform** ("a weapon") that can be aimed at *any* problem within the first ~2 hours on the day. The mentor examples (ITSM, ServiceNow, SLA tickets) were *illustrations of the thinking style judges want*, not hints about the problem. Design for generality.

---

## 2. How the jury scores

Six weighted areas (weights sum to 100%). Performance levels: **5 = Outstanding (100%) ·
4 = Excellent (80%) · 3 = Good (60%) · 2 = Fair (40%) · 1 = Poor (20%)**. Points earned =
sub-weight × performance level. **Every hour of work should move one of these levers.**

| Weight | Area | What the jury judges | How Aegis serves it |
|---|---|---|---|
| **25%** | **Working Technical Solution Prototype** | prototype level, business value, **demo readiness** | The real, enforced, working system + the live "show your work" console. This is why *nothing is showoff* — every SOTA piece must actually run (guardrails, real graph, the embedded vector store, Redis memory, conformal ML). |
| **20%** | **Solution Hypothesis** | innovation, feasibility, alignment to the problem | The modular, importable, enterprise-grade agentic platform — a domain weapon retargetable in ~2 hours. |
| **15%** | **Solution Articulation & Presentation** | clarity of explanation, visuals/demo, teamwork | The console visuals, the projector Present mode, a clear narrative. |
| **15%** | **Problem Statement Understanding** | clarity + supporting research/evidence | Retrieval with citations and provenance; the problem framing in the demo. |
| **15%** | **Business Impact Assessment** | value proposition, key metrics | Cost savings, quality/eval numbers — **measured, not claimed**. |
| **10%** | **Roadmap for Production Scaling** | milestones, dependencies | The enterprise infra itself: the embedded vector store (installs with zero server binaries, which is what lets Aegis land on a locked-down enterprise box), Redis semantic cache, RLS multi-tenant governance, OTel/Phoenix observability, `AEGIS_MODE`. Keep an explicit milestones/dependencies story ready. |

### What this implies (encode these as first-class goals)

- **You are judged by two evaluators at once: a human jury AND an AI reader.** The AI parses the *code, docs, and presentations*. So repo structure, naming, docstrings, README, and commit hygiene are literally scored. Clean, consistent, machine-readable structure is a requirement, not a nicety.
- **Checkpoint judging.** The jury visits 2–3 times across the two days to observe and guide. Progress must be *visible at every visit*, and their guidance must be visibly acted on between visits.
- **Tokens are visible.** The jury can see token usage and cost. Efficiency is **measured**, not claimed — so caching and small-model routing must be real and surfaced on a dashboard.
- **The back half of the scoring is where points hide.** Most teams neglect security, maintainability, and documentation. We treat them as *demoable artifacts* built continuously. Innovation gets attention; engineering discipline wins.
- **Alignment of current work:** the "make-it-real / SOTA enterprise" program maps almost entirely to the top three levers — a **working prototype (25%)**, an **innovative feasible hypothesis (20%)**, and a **production-scaling roadmap (10%)**. The blind-domain adapter rehearsal de-risks the prototype and the hypothesis on the day.

---

## 3. Hard constraints (non-negotiable environment facts)

- **Machine:** 16 GB RAM Windows laptop. **No Docker.**
- **Models:** API-only (Azure fleet, see [`../architecture/backend.md`](../architecture/backend.md)). No local model weights, no GPU.
- **Infra is fully local or API — nothing else cloud.** Local Postgres, local Neo4j, local Redis, local Arize Phoenix, and an **embedded** vector store that needs no server binary at all (ADR 0009). The only remote calls are the model APIs.
- **Consequence:** never introduce a dependency that needs Docker, a GPU, a server binary, or a heavy local model. If a tool seems to require any of those, stop and ask for an alternative.

---

## 4. Winning strategy

1. **Weapon, not solution.** Build a reusable scaffold; only a thin domain adapter changes on the day.
2. **The scoring is the architecture.** Map every build decision to a scoring area.
3. **SOTA at every layer, telling one coherent story.** The moat is not any single component (competitors can match a component) — it's coherence: *cheap-to-scale + trustworthy + secure + auditable* as one narrative.
4. **The trust stack is the differentiator:** conformal prediction (guaranteed uncertainty) → human gate → SHAP (explanation) → guardrails (safety) → OpenTelemetry traces + audit log (auditability). The winning sentence: *"every autonomous action is uncertainty-bounded, explainable, guarded, and fully traced."*
5. **Actions, not answers.** The agent must *do things* (execute tools/workflows), not just retrieve and chat. This is the separator from teams that build a "smart search box."

---

## 5. The domain adapter (what changes on the day)

Everything else is the **stable core** — built ahead, untouched on the day. Only **five thin pieces** are domain-specific:

1. **Data schema + synthetic generator** — the shape of the world.
2. **Tool definitions** — what actions the agent can take.
3. **System prompts + personas** — who the agent is, who it serves.
4. **ML features + target** — what the ML spine predicts.
5. **Domain corpus** — what gets ingested into the graph.

Keep these isolated in clearly-named config/adapter modules so that on the day, only these change. Do not let domain logic leak into the core. The seam is documented in [`../learn/50-run-and-extend.md`](../learn/50-run-and-extend.md).

---

## 6. Deliverables checklist

- Working end-to-end system (the vertical slice must work by end of Day 1).
- Live dashboard (clear, enterprise-grade, projector-legible).
- Pitch deck + demo video (cater to the jury's language: business value, before/after, why-not-solved-today).
- **Architecture diagram + system design** — see [`../learn/10-architecture.md`](../learn/10-architecture.md) and the mermaid diagrams throughout [`../teaching/`](../teaching/README.md).
- **One-page threat model** mapping the app to OWASP LLM + Agentic Top 10 — [`../security/threat-model.md`](../security/threat-model.md).
- **README** that states the architecture in the first screen.
- **ADRs** — "why this, not that" for the big choices. Nine live in [`../adr/`](../adr/).
- **Eval + token/cost dashboard** showing measurable quality and efficiency.

---

## 7. The money-shot demo (design toward this single screen)

An item/query arrives → the agent **streams its reasoning live** while the **knowledge graph animates** → it calls an **action tool**, but the action is high-risk so it **pauses at a human-approval gate** → beside it a **SHAP panel** explains the ML score with its **conformal uncertainty interval** → the **token/eval dashboard** ticks up showing cache-hit rate, small-model share, cost per query, and a live quality score. Every element is a scoring area being judged in real time.

---

## 8. The one unvalidated assumption — **resolved**

The agent design assumed **tool/function-calling passes cleanly through the Azure model gateway**. It was de-risked by `spikes/tool_calling_spike.py` before the agent loop was built, and it passed — so the LangGraph plan→gate→act→reflect graph was built against the happy path, not the ReAct/structured-output fallback. Keep the spike: it is the first thing to re-run against an unfamiliar gateway.

---

## 9. Definition of success

Not "we built an AI thing," but: **"we built something cheap enough to scale, measurable enough to trust, secure enough to buy — and it takes real actions, explainably."** If a decision doesn't serve that sentence or a scoring area, don't do it.
