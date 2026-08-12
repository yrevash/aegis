# hackathon.md — Mission Context

> Read this first. It defines *what we are building, why, and how we are judged.* Every implementation decision must trace back to something here. If a request conflicts with this file, stop and ask.

---

## 1. The event

- **What:** TAIF S2 — Mumbai Regional Finals. A high-stakes on-site hackathon among top teams.
- **Format:** ~25 hours of on-site build spread across **two days**, with a working system + pitch deck + demo video as deliverables.
- **Problem statement:** revealed **on the day**, and it is **100% blind** — no theme, no domain, no track known in advance.
- **Therefore the prime directive:** we do **not** build a solution ahead of time. We build a **domain-agnostic agentic platform** ("a weapon") that can be aimed at *any* problem within the first ~2 hours on the day. The mentor examples (ITSM, ServiceNow, SLA tickets) were *illustrations of the thinking style judges want*, not hints about the problem. Design for generality.

---

## 2. The evaluation rubric (official)

1. **Solution quality and innovation**
2. **Code quality, maintainability and security**
3. **Documentation quality**
4. **Jury observations across both days**
5. **AI-assisted insights from code, documentation and presentations**

### What this rubric implies (encode these as first-class goals)

- **You are judged by two evaluators at once: a human jury AND an AI reader.** The AI parses the *code, docs, and presentations*. So repo structure, naming, docstrings, README, and commit hygiene are literally scored. Clean, consistent, machine-readable structure is a requirement, not a nicety.
- **Checkpoint judging.** The jury visits 2–3 times across the two days to observe and guide. Progress must be *visible at every visit*, and their guidance must be visibly acted on between visits.
- **Tokens are visible.** The jury can see token usage / cost. Efficiency is **measured**, not claimed — so caching + small-model routing must be real and surfaced on a dashboard.
- **The back half of the rubric is where points hide.** Most teams neglect security, maintainability, and documentation. We treat them as *demoable artifacts* built continuously. Innovation gets attention; engineering discipline wins.

---

## 3. Hard constraints (non-negotiable environment facts)

- **Machine:** 16 GB RAM Windows laptop. **No Docker.**
- **Models:** API-only (Azure fleet, see `backend.md`). No local model weights, no GPU.
- **Infra is fully local or API — nothing else cloud.** Local Postgres+pgvector, local Neo4j, local Redis, local Arize Phoenix. The only remote calls are the model APIs.
- **Consequence:** never introduce a dependency that needs Docker, a GPU, or a heavy local model. If a tool seems to require any of those, stop and ask for an alternative.

---

## 4. Winning strategy

1. **Weapon, not solution.** Build a reusable scaffold; only a thin domain adapter changes on the day.
2. **The rubric is the architecture.** Map every build decision to a rubric axis.
3. **SOTA at every layer, telling one coherent story.** The moat is not any single component (competitors can match a component) — it's coherence: *cheap-to-scale + trustworthy + secure + auditable* as one narrative.
4. **The trust stack is the differentiator:** conformal prediction (guaranteed uncertainty) → human gate → SHAP (explanation) → guardrails (safety) → OpenTelemetry traces + audit log (auditability). The winning sentence: *"every autonomous action is uncertainty-bounded, explainable, guarded, and fully traced."*
5. **Actions, not answers.** The agent must *do things* (execute tools/workflows), not just retrieve and chat. This is the separator from teams that build a "smart search box."

---

## 5. The domain adapter (what changes on the day)

Everything in `frontend.md`, `backend.md`, and `security.md` is the **stable core** — built ahead, untouched on the day. Only **five thin pieces** are domain-specific:

1. **Data schema + synthetic generator** — the shape of the world.
2. **Tool definitions** — what actions the agent can take.
3. **System prompts + personas** — who the agent is, who it serves.
4. **ML features + target** — what the ML spine predicts.
5. **Domain corpus** — what gets ingested into the graph.

Keep these isolated in clearly-named config/adapter modules so that on the day, only these change. Do not let domain logic leak into the core.

---

## 6. Deliverables checklist

- Working end-to-end system (the vertical slice must work by end of Day 1).
- Live dashboard (clear, enterprise-grade, projector-legible).
- Pitch deck + demo video (cater to the jury's language: business value, before/after, why-not-solved-today).
- **Architecture diagram + system design** (already drafted: `architecture_v2.mermaid`, `request_flow.mermaid`, `system_design.md`).
- **One-page threat model** mapping the app to OWASP LLM + Agentic Top 10 (see `security.md`).
- **README** that states the architecture in the first screen.
- **2–3 ADRs** (Architecture Decision Records) — "why this, not that" for the big choices.
- **Eval + token/cost dashboard** showing measurable quality and efficiency.

---

## 7. The money-shot demo (design toward this single screen)

An item/query arrives → the agent **streams its reasoning live** while the **knowledge graph animates** → it calls an **action tool**, but the action is high-risk so it **pauses at a human-approval gate** → beside it a **SHAP panel** explains the ML score with its **conformal uncertainty interval** → the **token/eval dashboard** ticks up showing cache-hit rate, small-model share, cost per query, and a live quality score. Every element is a rubric axis being scored in real time.

---

## 8. The one unvalidated assumption (spike this before building)

The whole agent design assumes **tool/function-calling passes cleanly through the Azure model gateway**. This is unverified. **Before building the agent loop, run a single test call with a `tools` parameter and confirm a `tool_calls` block comes back.** If it does not, the agent design pivots to a ReAct / structured-output fallback. Do not build against the happy path until this is confirmed. See `how_to_approach.md`.

---

## 9. Definition of success

Not "we built an AI thing," but: **"we built something cheap enough to scale, measurable enough to trust, secure enough to buy — and it takes real actions, explainably."** If a decision doesn't serve that sentence or a rubric axis, don't do it.
