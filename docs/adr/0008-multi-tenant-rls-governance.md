# ADR 0008 — Multi-tenant RBAC + budget governance with Postgres RLS

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** Team
- **Related:** `docs/ARCHITECTURE_REVIEW.md` §3, `docs/security.md` §6.7 (enterprise
  procurement), `app/core/security.py`, `app/core/governance.py`, `app/core/llm.py`,
  `app/data/governance.py`, `app/data/session.py` (RLS), `app/api/routes.py`.

## Context

Auth was a two-entry demo dict minting raw `uuid4` tokens, with only `admin`/`user`
roles and **no tenancy anywhere** — not in identity, not in the schema (`users` had no
`tenant_id`, no budget), and not in spend (the LiteLLM chokepoint *tracked* cost in a
process-global tally but **enforced nothing**). Any authenticated user was effectively
global, the token dashboard could not be attributed per customer, and there was no
backpressure against cost/DoS via tokens. This fails the "secure enough to buy" thesis
and the enterprise-procurement framing (`docs/security.md` §6.7).

## Decision

Add a three-tier tenancy hierarchy and enforce it at identity, data, and spend:

- **Identity / JWT.** Replace opaque tokens with signed JWTs (`pyjwt`, HS256) carrying
  `{sub, username, role, tenant_id}`; passwords hashed with **Argon2id**
  (`argon2-cffi`). The fine-grained tier — `platform_admin` (admin, no tenant),
  `tenant_admin` (admin, scoped to one tenant), `user` — is derived from the frozen
  coarse `Role` + tenancy, so no enum migration is needed
  (`app/core/security.py::principal_role`). Guards: `require_platform_admin`,
  `require_tenant_admin`, and a `_scope_tenant` resolver that pins every admin request
  to its tenant.
- **Governance at the chokepoint.** A `GovernanceContext` (tenant_id, user_id, effective
  caps) is threaded via **`contextvars`** — so **no node signature changes and the
  adapter never sees tenancy** — and read inside `core.llm.complete`/`embed`. Before
  spend it runs a **budget/rate check** (token/usd/rpm/tpm caps, enforced **inward**:
  a user cap is clamped to its tenant cap); a breach raises `BudgetExceededError`, which
  the orchestrator surfaces as a terminal `budget_exceeded` event — the system degrades
  to "budget exceeded" instead of runaway cost. After each call it writes a durable
  `usage_ledger` row (the persistent form of the in-RAM tally).
- **Tenant data isolation = RLS + app-scoping (defense in depth).** Postgres
  **Row-Level Security** is the enforced boundary (`SET app.tenant_id` per request +
  `CREATE POLICY` per table on `tenant_id`), so a missed `WHERE` cannot leak; app-level
  `WHERE tenant_id = :ctx` scoping is the belt-and-suspenders layer that also runs on
  the SQLite test database (Open Decision D3).
- **Admin surfaces.** `/admin/tenants` (platform), `/admin/users`, `/admin/budgets`,
  `/admin/usage` (tenant-scoped) — beside the existing `/metrics` and `/audit`.

The demo principals still log in (mapped to `platform_admin`, ungoverned) for
back-compat, so the offline/no-seed path is unchanged.

## Consequences

- **+** A real tenant boundary at identity, data, and spend — per-customer attribution
  on the token dashboard and a cost/DoS ceiling at the one chokepoint every call passes.
- **+** RLS makes the database the last line of defense (a code bug can't leak
  cross-tenant), validated by `tests/integration/test_cross_tenant_isolation.py` and
  `tests/api/test_admin_governance.py`.
- **+** `contextvars` keeps the tenancy invisible to the graph and the adapter — the
  domain code never learns about tenants; governance stays pure core.
- **+** Enforcement fails **open** on a DB blip (caps are soft ceilings) and is fully
  bypassed for unscoped/offline requests, so tests and the lite demo behave as before.
- **−** RLS adds per-connection `SET` management with a pool (set on checkout), and a
  budget read per governed call — mitigated by a short-TTL in-process cap cache and by
  gating all of it behind a bound tenant.
- **−** Full JWT + Argon2 + hierarchy is real work and adds two optional deps (`pyjwt`,
  `argon2-cffi`).
- **Note:** we enforce budgets **in-`complete`** rather than running a separate LiteLLM
  *proxy* server (virtual keys + hierarchical budgets out of the box) — the proxy is a
  second process that breaks the single-box, no-Docker story; the in-process path keeps
  identical semantics and one process (Open Decision D2).

## Alternatives considered

- **App-level scoping only (no RLS).** Simpler, but one missed `WHERE` is a cross-tenant
  leak — unacceptable for a "secure to buy" boundary; RLS is worth the pooling
  complexity as the enforced last line.
- **A separate LiteLLM proxy server for budgets/keys.** Gives hierarchical budgets and
  RPM/TPM for free, but is a second server process (breaks no-Docker single-box); the
  code is structured so a proxy can drop in later with the same semantics.
- **Opaque session tokens + a server-side session store.** Keeps state on the server,
  defeating the stateless-worker / horizontal-scale principle; JWTs carry the claims so
  any worker validates without a lookup.
