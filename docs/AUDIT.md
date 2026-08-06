# AUDIT.md — Adversarial Review

> Evidence-based audit of the committed platform (backend + agent loop + API +
> frontend) against `docs/hackathon.md`, `docs/backend.md`, `docs/security.md`,
> `docs/threat_model.md`, the ADRs, and the **locked** `backend/src/app/api/schemas.py`.
> Every finding below was verified by reading the cited code and/or running a check.
> Date: 2026-08-03.

## Green-claim verification (confirmed)

- `cd backend && .venv/bin/python -m pytest tests -q` → **126 passed** in ~2.0s.
- `.venv/bin/ruff check src tests` → **clean** (no output).
- `frontend`: `npm run build` (`tsc -b && vite build`) → **clean** (one 966 kB
  chunk-size warning only); `npm run lint` (oxlint) → 4 benign react-refresh
  warnings, 0 errors.

## Headline assessment

The core is genuinely strong and largely honest. **Contract fidelity is clean on
both sides** (the agent loop emits exactly the locked `StreamEvent` variants; the
frontend TS mirrors `schemas.py` field-for-field, including `type: "token"` not
`"answer_chunk"`). **Security is real, not theatre**: every model input/output is
guarded, the tool allowlist is enforced *before* side effects (and a test proves a
denied call writes no audit row and mutates nothing), retrieved content is
Spotlighted at both rerank and assembly, content is validated before graph writes,
the classifier fails closed, and `/approval` is admin-gated. The tests are
substantive (empirical conformal-coverage check, RBAC 401/403, audit round-trip),
not hollow.

The problems are concentrated in **two demo-critical gaps** where the *shipped
wiring* diverges from the money-shot narrative (the tests pass only because fakes
paper over the real behaviour), plus some **data-scoping and faked-surface** issues.
No Critical defects.

---

## HIGH

### H1 — The human gate never fires on tool risk with the real adapter+config
**Where:** `backend/src/app/agent/deps.py:74` (`gate_min_risk = RiskLevel.HIGH`);
`backend/src/app/adapter/tools.py:422,429,436` (tool risks are MEDIUM, MEDIUM, LOW —
none HIGH); decision at `backend/src/app/agent/graph.py:137-139`.

**Problem:** In production, `AgentDeps.default()` uses `AgentConfig(gate_min_risk=HIGH)`
and `_default_tool_risk` reads the registry, whose highest risk is **MEDIUM**. So
`high_risk = any(risk_at_least(r, HIGH) …)` is **always False**. The human gate can
therefore only be triggered by ML *uncertainty*, never by tool risk. The money-shot
headline — *"it calls an action tool, but the action is high-risk so it pauses at a
human-approval gate"* (`hackathon.md` §7) — does not occur with the shipped domain
adapter. Worse for the security story: the two **state-changing** tools
(`update_request_status`, `assign_request`, both MEDIUM) execute **autonomously**
whenever the ML prediction is confident — weaker bounded autonomy than
`security.md`/`threat_model.md` claim.

**Why the green suite hides it:** `test_high_risk_action_forces_gate` uses the fake
`tool_risk` in `tests/conftest.py`, which returns `RiskLevel.HIGH` when
`high_risk=True`. No real tool ever does.

**Failure scenario:** On stage the operator asks the agent to resolve/close a request
with a confident ML score. The agent executes `update_request_status` with no pause.
The signature "pause at the gate" moment never appears unless the ML happens to be
uncertain.

**Recommended fix (design-level — not applied):** Either (a) mark the consequential
tool(s) HIGH in `TOOL_REGISTRY` (resolving/closing a customer request is genuinely
high-consequence), or (b) set `AgentConfig.gate_min_risk = RiskLevel.MEDIUM` so
state-changing MEDIUM tools gate. Option (a) is the more defensible domain modelling;
(b) is a one-line change but gates *all* MEDIUM tools. Left as a recommendation
because it changes autonomy behaviour and touches the domain adapter — a team call.

### H2 — Cost dashboard will read $0.00 in production
**Where:** `backend/src/app/core/llm.py:108-114` (`_safe_cost`); consumed at
`llm.py:181`, aggregated in `graph.py:_accrue` and surfaced by
`routes.py:200-211` (`cost_per_1k_queries_usd`).

**Problem:** `_safe_cost` calls `litellm.completion_cost(...)` and returns `0.0` on
any failure. The fleet is addressed as a **custom OpenAI-compatible provider** with
bare deployment ids (`openai/genailab-maas-*`), which are **not in LiteLLM's cost
map**, so `completion_cost` will (silently) yield 0. Result: `run_finished.cost_usd`
= 0 and `/metrics.cost_per_1k_queries_usd` = 0. The rubric explicitly scores visible
tokens/cost (`hackathon.md` §2) and the money-shot wants cost "ticking up." Tokens
*are* captured (from `usage`), but cost is not.

**Why the green suite hides it:** every test injects a fake `complete` with a
literal `cost_usd` (`tests/conftest.py`), and `test_query_streams_full_sequence…`
asserts `cost_per_1k_queries_usd > 0` off that fake value.

**Failure scenario:** Live demo dashboard shows `$0.000 / 1k queries`, undercutting
the entire cost-efficiency narrative.

**Recommended fix (not applied — needs a pricing choice):** add a token-based
fallback in `_safe_cost` — when `completion_cost` returns 0/raises, estimate from
`prompt_tokens`/`completion_tokens` × a per-role $/1k rate table (a small dict keyed
by `ModelRole`, env-overridable). Keeps it honest and non-zero.

---

## MEDIUM

### M1 — TrustBar lights "Human-approved" even when the action was rejected
**Where:** `frontend/src/components/layout/TrustBar.tsx:44-48` —
`done: (s) => s.toolResults.length > 0`.

**Problem:** On the reject path the mock (and the real graph, via the `act` node
only when approved — but the frontend keys off `toolResults`) still yields a
`tool_result`, so the headline trust-stack element shows the green "Human-approved"
check on a **rejected** action. Directly contradicts the bounded-autonomy story at
the exact moment it's being demonstrated.

**Fix:** gate that stage on approval, e.g. `done: (s) => s.approval?.decision ===
'approve' && s.toolResults.some(r => r.ok)`, or drive it from the `approval`
decision rather than mere presence of a tool result.

### M2 — `/graph` and `/metrics` are not role-scoped (cross-persona exposure)
**Where:** `backend/src/app/api/routes.py:334-341` (`GET /graph`), `356-362`
(`GET /metrics`); the `GraphStore`/`MetricsStore` are **process-wide** singletons
(`routes.py:232-233`).

**Problem:** Both require only `require_auth`, not role/persona scoping. A `client`
(USER) sees the accumulated knowledge-graph nodes/edges from **admin** runs and the
full routing table/metrics. `security.md` §5 mandates "role-scoped data access …
a security control, not just UX," and the threat model claims RBAC-scoped retrieval
(ASI03). The viz graph is metadata, but it is still cross-tenant leakage of what
other personas retrieved.

**Fix:** scope the graph accumulator per persona/role (or filter the response by the
caller's `data_scope`); consider restricting `/metrics` to admin.

### M3 — `quality_score` is never computed (always `null`)
**Where:** `backend/src/app/api/routes.py:209` (`quality_score=None`).

**Problem:** The money-shot wants a "live quality score" and `backend.md` §8 calls
for RAGAS + LLM-as-judge wired as a quality gate. Nothing computes it; the field is
hard-`None`. The frontend dashboard's quality tile is therefore either empty or
filled from static data (see M4).

**Fix:** even a lightweight per-run heuristic (e.g. faithfulness proxy from retrieval
overlap, or an LLM-judge sample) would make the tile honest; otherwise label it
clearly as "not yet measured."

### M4 — Two frontend surfaces "look done" but are static placeholders
**Where:** `frontend/src/components/dashboard/Dashboard.tsx:63-64` (inline
`"2,870"`, `"41"`), `frontend/src/components/dashboard/data.ts` (`COST_TREND`,
`QUERY_VOLUME` charts), `frontend/src/components/admin/AuditLog.tsx:17-22`
(hardcoded 4-row `ROWS`).

**Problem:** The KPI tiles that *are* backed by `/metrics` are live, but the two
trend charts and two KPI tiles are literal constants, and the **audit-log view is
fully hardcoded**. `security.md` §6 lists "the audit log view" as a scored demo
artifact — right now it shows fixed rows, not the real Postgres audit trail (which
*is* being written). No backend endpoint exists to back these (no `GET /audit`, no
cost-trend/volume fields in `MetricsResponse`), so the contract doesn't cover them —
but on a projector they read as live.

**Fix:** add a read-only `GET /audit` (admin) that lists recent `AuditLog` rows and
wire `AuditLog.tsx` to it; drop or clearly label the fabricated chart/KPI values.

### M5 — `small_model_share` is config-derived, not measured
**Where:** `backend/src/app/api/routes.py:200-229` (`_small_model_share` over the
static `routing_table()`).

**Problem:** It reports the fraction of *roles* mapped to a small model in config,
not the actual share of *calls* routed small during real runs. Defensible (it
reflects the routing decision), but `hackathon.md` §2 stresses efficiency is
"measured, not claimed." An AI reader/jury probing this will find it's a constant.

**Fix:** accumulate per-call model usage in `MetricsStore` (the `run_finished` /
span data already flows through) and compute the real small-model call share.

---

## LOW

### L1 — `_default_features_for` reaches into a private attr and always predicts on record[0]
`backend/src/app/agent/deps.py:213-216` uses `getattr(store, "_requests", {})` and
`requests[0]`. Documented as illustrative, but brittle: any query predicts on the
same first synthetic record, so the SHAP/conformal panel is disconnected from the
actual subject of the query. Add a public store accessor and resolve the referenced
record from the query for a more convincing demo.

### L2 — Frontend `running` derived from a ref, not reducer state
`frontend/src/state/useRunStream.ts:44,90` — `running: runningRef.current`;
`onClose` flips it without dispatching. Works only because `run_finished` dispatches
just before close. Any transport that ends without a final dispatch leaves Run/Reset
stuck disabled. Track `running` in reducer state.

### L3 — ADR topics diverge from `backend.md` §11
Only two ADRs exist (`docs/adr/0001-litellm-as-gateway.md`,
`0002-nemo-guardrails.md`). `backend.md` §11 explicitly names ADRs for
LightRAG-vs-GraphRAG, conformal prediction, multi-model routing, and OTel-native
observability — none of which are the two present. `hackathon.md` §6 asks for "2–3
ADRs" so the *count* passes, but the highest-value "why this, not that" decisions
(the LightRAG and conformal choices — the actual differentiators) are undocumented.

### L4 — Single 966 kB JS bundle, no code-splitting
`react-force-graph-2d` + `recharts` ship in one chunk (Vite warning). Fine for a
demo, but slow first paint on a 16 GB projector laptop; lazy-load the graph/charts
routes if time allows.

### L5 — `threat_model.md` renumbers OWASP LLM IDs vs `security.md`
`threat_model.md` §"Numbering note" reconciles to OWASP LLM 2025 (v2.0) IDs while
`security.md` §2 still uses the 2023 IDs (LLM06/LLM02/LLM08). Documented, but a jury
cross-reading the two docs sees inconsistent IDs. Align `security.md` to the 2025 list.

---

## Fixed in this pass (clearly-safe, unambiguous)

- **`backend/src/app/agent/approvals.py:60`** — replaced
  `asyncio.get_event_loop().create_future()` with
  `asyncio.get_running_loop().create_future()`. `register()` is only ever called
  from async contexts (`run_agent`, `wait`), so this is behaviour-preserving and
  removes a Python 3.12 `DeprecationWarning`/"no current event loop" footgun.
  **Suite re-run after fix: 126 passed, ruff clean.**

Everything else above is left as a documented recommendation because it is either
design-level (H1, H2, M2), a product/UX choice (M3, M4, M5), or in a locked/contract
boundary that must not be changed unilaterally.

---

## Post-audit fixes applied (2026-08-03)

Verified green after each change — backend **126 passed, ruff clean**; frontend
strict build clean, lint 0 errors.

- **H1 (fixed)** — `adapter/tools.py`: `update_request_status` is now `RiskLevel.HIGH`,
  so resolving/closing a request genuinely routes to the human gate. The money-shot
  "high-risk action pauses at the gate" now fires with the real adapter.
- **H2 (fixed)** — `core/llm.py`: added a per-role, env-overridable token-price table
  and a token-based cost estimate used whenever LiteLLM has no price for the custom
  deployment. Cost is now non-zero and honest; test updated to assert the fallback.
- **M1 (fixed)** — `TrustBar.tsx` + `runReducer.ts`: "Human-approved" now lights only
  when the run actually paused at the gate **and** a human approval let a tool succeed
  (`awaitedApproval && toolResults.some(r => r.ok)`); a rejected action stays dark.
- **M2 (fixed)** — `api/routes.py`: `GraphStore` is now per-persona and `/graph`
  returns only the caller's scope; `/metrics` is admin-only. No cross-persona leakage.
- **M5 (fixed)** — `core/llm.py` + `api/routes.py`: `small_model_share` is now the
  **measured** fraction of real chat calls routed to a small model (live gateway
  tally), falling back to the config-derived share only before any call.

**Still open (recommended, not yet done):** M3 (`quality_score` always null),
M4 (frontend audit-log view + two trend charts are static placeholders — wire a
`GET /audit` admin endpoint), and L1–L5. See above for detail.

## Top 3 to address before the event

1. **H1 — make the risk-based human gate actually fire.** Mark the consequential
   tool(s) HIGH (or drop `gate_min_risk` to MEDIUM). Without this, the money-shot's
   signature "pause at the gate on a high-risk action" never happens with the real
   adapter, and state-changing tools run unattended.
2. **H2 — give the cost dashboard a real, non-zero number.** Add a token-based
   fallback in `_safe_cost`; the custom Azure deployments are not in LiteLLM's cost
   map, so today the live cost reads $0 — killing the efficiency story.
3. **M1 + M2 — fix the two "contradicts-the-narrative" bugs:** the TrustBar lighting
   "Human-approved" on a *rejection*, and `/graph`/`/metrics` leaking one persona's
   data to another. Both are small, both directly undercut the security/bounded-
   autonomy pitch in front of the jury.
