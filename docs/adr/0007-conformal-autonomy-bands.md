# ADR 0007 — Graded conformal autonomy bands (autonomous / defer / abstain)

- **Status:** **Superseded** (band-based routing was removed from the live agent
  by founder decision — see note below). The Python band *engine* has since been
  **deleted** from the backend; only the `AutonomyBand` wire-enum survives
  (`app/schemas.py`, backing `MLExplanation.band` for the frontend).
- **Date:** 2026-08-05
- **Deciders:** Team
- **Related:** ADR 0004 (conformal prediction /
  MAPIE), `app/agent/graph.py`, `app/agent/deps.py` (`classify_autonomy`,
  `AgentConfig`), `app/adapter/personas.py` (per-persona policy).

> **Superseding note (honesty alignment).** By founder decision, **ML never
> gates**: the human-in-the-loop gate is driven **solely by the tool risk tier**
> (`AgentConfig.gate_min_risk`) — a proposed action at or above that tier routes
> to the human approval inbox (the money-shot). ML/conformal output is a
> **solution signal** that informs the plan and is shown as supporting evidence;
> a low-confidence or failed prediction **never** defers, abstains, or terminates
> a run. Accordingly `AgentConfig.autonomy_bands_enabled` defaults to **`False`**
> and the graph no longer routes on the graded conformal bands. The three-band
> engine described below (`classify_autonomy`, `assess_uncertainty`, the
> `uncertainty_*` / `abstain_*` thresholds) has since been **removed from the
> backend entirely** — only the `AutonomyBand` enum remains as a wire type. The
> rest of this ADR is preserved for historical context and describes the
> *superseded* design.

## Context

ADR 0004 gave us calibrated conformal intervals, but the graph consumed them as a
**binary brake**: `gated = high_risk OR uncertain`, and the ML step ran *only* when the
planner had already proposed a tool call. So ML acted downstream of the decision (a
safety interlock), never upstream informing it, and the conformal signal collapsed to a
single yes/no with no principled "the model is too degenerate to act at all" state. The
platform's differentiator sentence — "predict-then-act, uncertainty-bounded,
explainable" — was really "act (LLM), then maybe-brake (ML)."

The SOTA framing is conformal prediction as a **distribution-free trigger for graded
deferral and selective abstention**: act when the prediction set is a confident
singleton, defer to a human when it is wide/non-singleton, and abstain when it is
degenerate/empty — with the symbolic layer escalating on high neural uncertainty
*regardless* of the default risk tier.

## Decision

Move ML **before** planning (an `ml_predict` node on the `retrieve → plan` edge) so the
planner reasons *with* the calibrated prediction + top SHAP drivers (predict-then-plan),
and replace the binary gate with a three-band **conformal autonomy policy**
(`classify_autonomy`, on by default via `AgentConfig.autonomy_bands_enabled`):

| Conformal signal | Band | Behaviour |
|---|---|---|
| Tight interval / singleton set / high confidence, risk within persona ceiling | **autonomous** | act |
| Wide interval / non-singleton set, **or** HIGH-risk (D5) | **defer** | route to the human approval inbox (ADR 0005) |
| Degenerate / no-coverage / empty set (at/below the abstain thresholds) | **abstain** | do not act; emit `abstained`, return an "insufficient confidence" answer |

- **D5 posture:** a HIGH-risk tool is *never* autonomous regardless of confidence — it
  always defers to the live gate (preserving the money-shot), and the SLA sweeper
  auto-**rejects** it on timeout (fail-safe).
- **Per-persona policy** (`max_autonomous_risk`, `min_confidence`, `max_rel_width`,
  abstain thresholds) is read from the adapter, so a `client` persona defers earlier
  than an `operations_lead` — the founder's day-of dial. Policy is **domain** data in
  the adapter; the band-classification *engine* is domain-free core.
- MAPIE's classification prediction-**set size** (previously discarded) is surfaced as
  the non-singleton signal, and the top-k signed SHAP features are injected into the
  planner + final prompt so the answer explains itself from the model's actual drivers.

## Consequences

- **+** ML becomes a first-class **solver** that shapes the plan, not just a brake — the
  differentiator sentence is now literally true end-to-end.
- **+** "Abstain" gives an honest third outcome for genuinely un-actionable predictions
  (no acting, no wasting a human's time), and the graded defer keeps the dramatic gate.
- **+** The gate threshold stays **defensible** (ADR 0004's coverage guarantee) while
  becoming *graded* and *persona-tunable* without touching core.
- **+** Bands and thresholds are unit-tested exhaustively
  (`tests/agent/test_autonomy_bands.py`) and driven end-to-end
  (`tests/integration/test_ml_abstain.py`).
- **−** Predict-before-plan adds one inference on the hot path for action queries;
  mitigated because the spine is local CPU XGBoost (sub-millisecond) and pure Q&A skips
  it (no ML subject → additive no-op).
- **−** "Abstain" is a new terminal SSE state the frontend and eval must handle
  (coordinated via the P0 event contract).
- **Note:** the default abstain thresholds are chosen to *preserve the money-shot* — a
  confident HIGH-risk action is confident enough not to abstain, so it defers to the
  live gate; only a degenerate prediction abstains (Open Decision D5).

## Alternatives considered

- **Keep the binary `high_risk OR uncertain` gate.** Simplest and already shipped, but
  it is a brake, not a solver: no predict-then-plan, no abstain, no per-persona grading
  — it under-sells the conformal spine.
- **A hand-tuned confidence threshold for deferral** (e.g. defer if p < 0.7). Exactly
  the arbitrary magic number ADR 0004 rejected; conformal set-size / interval-width give
  a distribution-free trigger instead.
- **Always defer every action to a human.** Maximally safe but destroys the "bounded
  *autonomy*" story and floods the approver; the autonomous band (within a persona
  ceiling) is the point.
