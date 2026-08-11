# aegis.governance — Multi-Tenant Governance (extraction) Design Spec

- **Date:** 2026-08-12 · **Branch:** `feat/aegis-module-contract` · Module 5 of 8
- **Map:** `.superpowers/sdd/module-governance-map.md` · Reuses `aegis.data` (from module 4).

## 1. Goal

Extract the multi-tenant governance core into a standalone importable **`aegis.governance`**: JWT (HS256) +
Argon2id auth, four-role RBAC tiers, `GovernanceContext` (contextvars), per-tenant/user budget enforcement
(token/usd/rpm/tpm), the usage ledger, the audit writer, tenant/user/budget ORM on `aegis.data`, and the RLS
bootstrap. **Config injected** (no `app.config`); heavy deps (sqlalchemy/pgvector/pyjwt/argon2-cffi) under
`aegis[governance]`. FastAPI auth request-wiring stays app-layer. Security-critical → opus review.

## 2. Scope (surgical — only the governance/tenancy core)

MOVE to `aegis.governance`: `core/security.py` (JWT/Argon2/RBAC tiers), `core/governance.py` (contextvars),
`data/governance.py` (enforce/record/effective_limits/update_user_role/upsert_budget/admin rollups),
`data/audit.py` (record_audit/list_recent_audit), and the **tenancy ORM tables** Tenant, User, Budget,
UsageLedger, AuditLog (on `aegis.data.AegisBase`), plus the RLS bootstrap for `users`/`usage_ledger`/`approvals`.
LEAVE in `app.data` (belong to other modules): Approval (agent HITL), Chunk (retrieval), EvalResult (evals),
PromptVersion (ops). These stay on the app Base for now; both metadatas are create_all'd.

## 3. Design (`aegis/src/aegis/governance/`)

- **`types.py`** — `Role(StrEnum)` (moves here — a governance concept); `TokenClaims`; `GovernanceLimits`,
  `GovernanceContext`; the admin wire DTOs (`TenantRow, AdminUserRow, BudgetRow, UsageByModel,
  UsageSeriesPoint, AuditLogRow`) as pydantic models (moved from `app.api.schemas`). `RiskLevel` → add to
  `aegis.core.types` (Approval uses it; guardrails already put GuardVerdict there).
- **`security.py`** — Argon2id `hash_password`/`verify_password` (fail-closed); JWT `create_access_token`/
  `decode_access_token`; RBAC `principal_role`/`coarse_role_from_fine` + PLATFORM_ADMIN/TENANT_ADMIN/MEMBER.
  Config (jwt_secret/algorithm/expire_minutes) **injected** via a `SecurityConfig` dataclass / module-level
  `configure_security(...)`. NO app.config import.
- **`context.py`** — `GovernanceContext`, `set/get/reset_governance_context` (ContextVar). Pure.
- **`models.py`** — Tenant/User/Budget/UsageLedger/AuditLog on `aegis.data.AegisBase` (+ enums TenantStatus/
  BudgetScope/BudgetWindow/ApprovalStatus etc. that these tables need).
- **`enforcement.py`** — `enforce_governance(*, tenant_id, user_id, session_factory)`, `record_usage(...)`,
  `effective_limits(...)`, `update_user_role(...)` (last-platform-admin lockout → `LastPlatformAdminError`),
  `upsert_budget(...)`, admin rollups. Takes an injected **session factory** + `set_tenant_scope`. Raises
  `BudgetExceededError` (import from `aegis.gateway.types` — already extracted). Budget-fail-open stays the
  gateway hook's policy (unchanged).
- **`rls.py`** — `set_tenant_scope(session, tenant_id)`, `bootstrap_rls(engine)` (ENABLE RLS + tenant_isolation
  policy on users/usage_ledger/approvals; fail-closed unset GUC). No app.config.
- **`audit.py`** — `record_audit(...)` (pulls tenant from context if None), `list_recent_audit(...)`.

## 4. Extras

`aegis[governance] = ["sqlalchemy[asyncio]>=2.0", "pgvector>=0.3", "pyjwt>=2.9", "argon2-cffi>=23.1"]` (add `aegis[data]`). Add to `all`. `aegis.core` stays pydantic-only (sqlalchemy/pyjwt/argon2 banned from core — extend the guard).

## 5. Strangler shim

`backend/src/app/core/security.py` + `core/governance.py` + `data/governance.py` + `data/audit.py` → shims
delegating to `aegis.governance`, calling `configure_security(...)` at import with `app.config` (jwt_secret/
alg/ttl; PROD secret validation STAYS in app.config), and passing the app session factory + set_tenant_scope.
`app.api.schemas` re-exports `Role`, the admin DTOs, `RiskLevel` (identity) so routes/agent/existing importers
are unchanged. `app.data.models` re-exports Tenant/User/Budget/UsageLedger/AuditLog from
`aegis.governance.models` (identity) so app code keyed on them works. `app.data.session.bootstrap` registers
`aegis.data.AegisBase.metadata` (memory build already did) + calls `aegis.governance.rls.bootstrap_rls`. The
gateway's `_GovernanceHook` now wraps `aegis.governance.enforcement.enforce_governance`/`record_usage`. ALL of
`routes.py` (auth, require_* guards, admin handlers, _resolve_governance) unchanged.

## 6. Testing & proof

Port: tests/data/{test_governance_models,test_approvals}, tests/core/{test_governance,
test_governance_enforcement,test_security,test_startup_guard}, tests/api/{test_admin_governance,
test_auth_backdoor,test_cross_tenant_holes,test_roles_rbac}, tests/integration/test_governed_budget — into
`aegis/tests/governance/` where they're framework-free (security/enforcement/models on SQLite), keeping the
FastAPI-dependent ones (routes/RBAC-guards/cross-tenant-HTTP) in the backend against the shim. Add: import
guard (`import aegis.governance` pulls no fastapi/litellm). Backend parity: the full suite green minus the 2
env failures; **cross-tenant isolation, RBAC guards, last-platform-admin lockout, argon2 verify-fail-closed,
JWT decode** all green through the shim.

## 7. Definition of done

`aegis.governance` importable + `aegis[governance]`-installable, JWT/Argon2/RBAC + budget enforcement + RLS +
audit preserved, config injected, `Role`/DTOs moved with identity re-exports, tenant tables on `aegis.data`,
`aegis.core` still pydantic-only, backend green through the shim (minus 2 env failures) with all
security/isolation tests passing.
