# 70 · What's new (recent changes, at a glance)

A skimmable, honest index of everything recently added or changed, grouped by area — each
line names the real file/endpoint so you can go read it. Detail lives in the numbered docs
(`00`–`60`); this page is the "what moved" map.

## RBAC + role-scoped portals

Roles expanded from `{admin, user}` to **four**: `admin`, `ai_team`, `devops`, `client`.

- Coarse `Role` enum — `backend/src/app/api/schemas.py`.
- Fine tier (`platform_admin` / `tenant_admin` / `user`) + a signed **`coarse_role`** JWT
  claim — `backend/src/app/core/security.py` (`principal_role`, `coarse_role_from_fine`).
- Per-role guards `require_admin` / `require_devops` / `require_ai_team` / `require_client`
  + multi-role `require_roles` (and `require_admin_or_devops` / `_ai_team` / `_client`) —
  `backend/src/app/api/routes.py`.
- Admin role assignment `POST /admin/users/{id}/role` →
  `backend/src/app/data/governance.py::update_user_role`, with a last-platform-admin lockout
  guard (`LastPlatformAdminError`).
- Public liveness `GET /health` — unauthenticated (`routes.py::health`).
- One portal per role, each its own route subtree (`web/src/app/app/[role]/[section]`) with
  a focused surface set (`web/src/lib/portal.ts` `ROLE_SECTIONS`):

  | Role | Route | Surfaces |
  |---|---|---|
  | `admin` | `/app/admin/…` | Overview · Governance · Approvals · Audit · Roles & Access (oversight/delegation only) |
  | `ai_team` | `/app/ai_team/…` | Console · Harness · MLOps · LLMOps · Evals · Token opt · Memory · RAG · Graph · Cache · Guardrails · Access demo |
  | `devops` | `/app/devops/…` | Overview · Tech Stack & Versions · Patch Check · Security · Red-team · Latency · Audit |
  | `client` | `/app/client/…` | Overview · Savings · Risk Map · Access demo |

## New platform surfaces + endpoints

Backed by `backend/src/app/platform/*` (honest throughout — no fabricated data):

| Endpoint | Backing file | Frontend surface |
|---|---|---|
| `GET /stack` | `platform/stack.py` (`build_stack`, `importlib.metadata`) | `components/devops/StackVersions.tsx` |
| `POST /stack/patch-check` | `platform/patches.py` (live PyPI; honest `online=false` offline) | `components/devops/PatchCheck.tsx` |
| `GET /risk-map` | `platform/risk_map.py` (grounded in `docs/SECURITY_OWASP_AGENTIC.md`) | `components/client/RiskMap.tsx` |
| `GET /savings` | `platform/savings.py` (from the real usage ledger) | `components/client/SavingsView.tsx` |
| `POST /admin/users/{id}/role` | `data/governance.py::update_user_role` | `components/admin/RolesAccess.tsx` |

## Retrieval intelligence (all ON in production, honest fallbacks)

- **Query rewrite** before retrieval — `backend/src/app/retrieval/query_rewrite.py`.
- **Bounded agentic / Self-RAG loop** (retrieve → judge sufficiency → re-retrieve → merge) —
  `backend/src/app/agent/retrieval_loop.py` (`agentic_retrieve`), wired in
  `agent/graph.py::retrieve`.
- **Answer-level semantic cache**, scoped per tenant+persona+role —
  `backend/src/app/retrieval/answer_cache.py`, checked in `agent/graph.py::plan`.
- **A2A-labelled handoff spans** on the route node —
  `backend/src/app/observability/semconv.py` (`A2A_*`) + `agent/graph.py`.
- The rewrite/judge model-call costs are **accrued into per-run telemetry** (no hidden spend).

## Evaluation

- **DeepEval-pattern CI regression gate** — `backend/src/app/eval/regression.py`
  (`python -m app.eval.regression`), per-metric thresholds + an agentic tool-selection case,
  wired as the pass/fail bar in `scripts/preflight.sh` / `scripts/preflight.ps1`.
- Cross-links to the three-layer strategy: `docs/EVAL_STRATEGY.md`.

## Memory

- **Recall-frequency is real** — recall bumps `access_count` / `last_access_at`
  (`backend/src/app/memory/recall.py`), feeding a live frequency term (`memory/scoring.py`,
  `config.w_freq = 0.1 > 0`).
- **Forgetting is wired** — a bitemporal **soft-archive** prune pass
  (`memory/consolidate.py::prune_forgotten`, run inside `sweep_pending`), governed by
  `config.forget_floor` / `forget_min_age_days`. Never a hard delete.

## Console (`web/`)

- **Rebuilt on Next.js 15 (App Router) + React 19** in `web/` — the old Vite app has been
  retired and deleted. Every surface is now a URL-addressable route
  (`/app/[role]/[section]`) instead of local tab state.
- **Light-theme only** — a single light identity in `web/src/app/globals.css`; no dark
  variant is defined or applied.
- **Responsive** fluid desktop layouts, no horizontal overflow.
- **Admin portal trimmed** to oversight surfaces (`ROLE_SECTIONS.admin` in
  `web/src/lib/portal.ts`).
