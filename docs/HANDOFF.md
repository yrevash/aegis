# HANDOFF — for the next agent

Read this first, then `docs/sessions/2026-08-12-admin-and-dev-bringup.md` for the full log.
Project is **Aegis** — a domain-agnostic agentic platform (FastAPI `backend/` + importable
`aegis/` package) with a **Next.js `web/`** frontend being built one-dashboard-per-module across
4 role-scoped portals (admin / ai_team / devops / client). We are **mid Phase 3**.

## TL;DR state
- **Committed & frontend-verified** (mock): the whole admin-overview redesign + platform-wide
  text/density cleanup + matured Audit page + real `/metrics` fields. HEAD = `3cdfb1f`.
- **Uncommitted, in progress**: the **functional admin** — real CRUD. Backend half is
  code-complete (create user/tenant endpoints + tests); frontend half is **not started**.
- **Not yet verified**: the new backend endpoints + `/metrics` fields (the authoring agent's
  sandbox couldn't run the backend — see "Environment gotchas").

## Do this next (in order)
1. **Finish the functional admin — frontend** (backend already done, uncommitted):
   - Add `createUser` / `createTenant` to `web/src/lib/api/client.ts` (copy the shape of
     `createBudget` / `assignUserRole`), request types in `web/src/lib/api/types.ts`.
   - Make the mocks **stateful** in `web/src/mock/fixtures.ts`: mutable `USER_SEED` + tenant list,
     `mockCreateUser` / `mockCreateTenant`, so a create reflects in the users list, Governance,
     and the Clients page **offline** (this is how you verify without the backend).
   - **Create User form** in `web/src/components/admin/RolesAccess.tsx` (username, email, password,
     role incl. client/devops/ai/admin, tenant) → `createUser` → refetch.
   - **New Clients page** (add a section in `web/src/lib/portal.ts` + a mount in the section
     router `web/src/app/app/[role]/[section]/page.tsx`) listing tenants + users/usage/spend +
     "Add client" (`createTenant`).
   - **Tenant detail** (click a tenant in Governance/Clients) → set budget (`POST /admin/budgets`
     exists); stub policies/ingestion/tenant-guardrails.
   - Verify with headless Chrome in mock (pattern: seed `localStorage['aegis.session']` =
     `{role:'admin',token:'mock',username:'admin',tenantId:null}`, open `…/app/admin/…?mock=1`).
2. **Run the backend + tests once** (in the OWNER's terminal, not the sandbox — see below) to
   verify the new endpoints and the `/metrics` fields. Command is in the session log §5.
3. **Commit** the functional-admin work once green. (Owner: commit locally, **don't push** unless
   asked — "push does not need always pushing".)
4. Remaining from the owner's list (later): audit already matured; Approvals text already
   trimmed; tenant-specific ingestion pipeline / guardrails / policies are future.
5. Eventually: the **parity switch** (retire Vite `frontend/` → Next.js `web/`) — do a full
   4-portal click-through first. Then Phase 4 (pipelines).

## Environment gotchas (READ — this cost the last agent hours)
- **The agent Bash sandbox has ~100× slow file I/O.** Reading 300 small files timed out >60s.
  Consequence: the backend's huge import graph takes **10–20 min to boot in the sandbox**, and
  `sample`/`lsof` on the live process *slow it further*. **Do NOT try to run the backend or
  pytest inside the sandbox** — hand those commands to the owner's terminal.
- **Node / `next` is fine in the sandbox** — build, lint, `next dev/start`, and headless-Chrome
  screenshots all work. Verify the FRONTEND yourself; delegate BACKEND runs to the owner.
- To run the backend at all (owner terminal or, painfully, sandbox), you need ALL of:
  `xattr -dr com.apple.quarantine backend/.venv` · `PYTHONPATH=…/aegis/src` · `< /dev/null` on
  the launch · `LITELLM_LOCAL_MODEL_COST_MAP=True HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`.
  All of this is baked into **`scripts/dev-native.sh`** — just run that.
- Stores already up on the dev machine: **Redis** (6379), **Postgres** (`taif` db, `postgres`
  role). **Neo4j** installed (best-effort). **Qdrant** embedded (dev). **No `GENAILAB_API_KEY`**
  (only the Console needs it; dashboards/CRUD don't).
- **First backend boot is slow even in a normal terminal** (cold ML-stack import, ~1–2 min).
  A worthwhile optimization: lazy-load torch/transformers/NeMo/LangChain so the API boots fast.

## Owner preferences (hard rules — see also the memory files)
- **LIGHT / white theme only.** Keep the 4 portals visually distinct. Clarity/UX is mandatory.
- **Nothing fabricated** — every number ties to a real accessor; honest empty states, not fake
  zeros. This is load-bearing (jury rubric).
- **Dense & clean** — the owner aggressively removed explainer prose ("names say what it is").
  Don't reintroduce card `description`s or page subtitles.
- **Real, not demo/mock** for the actual deployment; no Docker (strict).
- Prefer **industry SOTA libraries** over homegrown. Keep tests ~20–50, not hundreds.
- Align everything to the jury rubric (Prototype 25% biggest) — see `~/.claude/.../memory/`
  (`aegis-jury-rubric`, `aegis-frontend-preferences`, `aegis-project`, `aegis-modularization`).

## Key files / entry points
- Admin overview: `web/src/components/dashboard/AdminCommandCenter.tsx`
- Section router: `web/src/app/app/[role]/[section]/page.tsx` · nav catalogue: `web/src/lib/portal.ts`
- Chrome: `web/src/components/layout/{Sidebar,Topbar,NotificationBell}.tsx`
- Admin pages: `web/src/components/admin/{RolesAccess,AuditLog,audit}.tsx`, `governance/GovernanceView.tsx`, `approvals/ApprovalsInbox.tsx`
- Backend admin routes: `backend/src/app/api/routes.py` (`/admin/*`) · schemas: `…/schemas.py`
- Governance data-layer: `aegis/src/aegis/governance/enforcement.py` (`create_user`, `create_tenant`, `list_users`, `update_user_role`, `upsert_budget`, …)
- Run everything: `scripts/dev-native.sh`
