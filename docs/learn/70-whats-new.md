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
- One portal per role, each a distinct route (`frontend/src/App.tsx`) with a focused surface
  set (`frontend/src/routes/Portal.tsx` `ROLE_SECTIONS`):

  | Role | Route | Surfaces |
  |---|---|---|
  | `admin` | `/admin` | Overview · Approvals · Governance · Audit · Roles & Access (oversight/delegation only) |
  | `ai_team` | `/ai-team` | Console · Overview · Memory · Improvement · Access demo |
  | `devops` | `/devops` | Overview · Tech Stack & Versions · Patch Check · Audit |
  | `client` | `/client` | Overview · Savings · Risk Map · Access demo |

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

## Frontend

- **Light-theme only** — dark mode removed (`frontend/src/components/layout/theme.ts`:
  `useTheme` returns a fixed `'light'`, no-op `toggle`; `.dark` in `index.css` is inert).
- **Responsive** fluid desktop layouts, no horizontal overflow.
- **Notification icon removed** from the top bar.
- **Admin portal trimmed** to oversight surfaces (`ROLE_SECTIONS.admin` in `Portal.tsx`).
