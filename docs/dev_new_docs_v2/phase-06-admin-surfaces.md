# Phase 6 — Admin surfaces

**1 day. Mostly frontend — the backend already works.**

This phase feels much bigger than it is, because the thing that makes the dashboards feel
fake — "admin adds a user and that user really logs in" — is already built and tested on the
server. What is missing is two API client functions and two forms.

---

## What is actually wrong

Four things, verified in source.

### 1. `POST /admin/users` already works. There is no form.

```python
# backend/src/app/api/routes.py:1262
@router.post("/admin/users", response_model=AdminUserRow, status_code=201, tags=["admin"])
async def admin_create_user(
    req: AdminUserCreateRequest,
    auth: AuthContext = Depends(require_tenant_admin),
) -> AdminUserRow:
```

The handler is complete: the password is Argon2-hashed in the data layer and never logged, a
tenant-admin is pinned to its own tenant and gets a clean 403 if it targets another, a
duplicate username returns 409, and the action writes an `admin.user.create` audit row.
`POST /admin/tenants` sits beside it at `routes.py:1232`.

And the user really logs in. `_authenticate` (`routes.py:225`) reads the `users` table
**first**; the built-in demo principals (`_DEMO_USERS`, `routes.py:205`) are a dev-only
fallback consulted only for usernames that have no real row.

The gap is entirely in the browser:

- `web/src/lib/api/client.ts` has `getTenants` (280), `getUsers` (286), `getBudgets` (296),
  `createBudget` (310) and `assignUserRole` (558) — and **no `createUser`, no `createTenant`**.
- `web/src/components/admin/` contains four files: `audit.ts`, `AuditLog.tsx`,
  `roleCatalog.ts`, `RolesAccess.tsx`. There is no form anywhere.

*(Plan 01 named these client functions `upsertBudget` and `setUserRole`. The real names are
`createBudget` and `assignUserRole`. Same gap, different labels.)*

### 2. The audit page cannot filter

```python
# backend/src/app/api/routes.py:1090
async def audit(limit: int = 50, auth: AuthContext = Depends(require_admin_or_devops)):
```

`limit`, clamped to `[1, 200]`, and nothing else. The read is already tenant-scoped through
`_scope_tenant` (`routes.py:436`), so a tenant-admin correctly sees only its own rows — but a
platform admin has no way to *select* a tenant, and nobody can filter by actor, action or date.
`AuditLog` is indexed on `ts` and `action`, not on `(tenant_id, ts)`.

### 3. Approvals are decided by the wrong admin

`GET /approvals` (`routes.py:1113`), `POST /approvals/{id}/decision` (`routes.py:1162`) and
`POST /approval` (`routes.py:1191`) all guard with `require_admin` (`routes.py:342`), which
admits **either** tier. `_scope_tenant` hands a platform admin every tenant's pending queue,
and the tenant check exempts them outright:

```python
# backend/src/app/api/routes.py:1144  (_enforce_approval_tenant)
if auth.fine_role == PLATFORM_ADMIN:
    return
```

So the Aegis operator can decide a tenant's business gate. The v2 doc calls this out
explicitly and it is right: Aegis approves Aegis's own actions; the tenant approves the
tenant's.

**This change breaks the current demo.** The demo logs in as `admin` — `_DEMO_USERS` at
`routes.py:205`, `tenant_id=None`, therefore platform-scoped — and approves. After this
change that principal can no longer decide a tenant's gate, by design. The seed must change
in the same commit.

### 4. There are no exports

No `Content-Disposition`, no CSV writer, no report endpoint anywhere in `routes.py`.

---

## What we are fixing now, and what waits

| | |
|---|---|
| **Now** | `createUser` / `createTenant` in the API client, and the two forms. |
| **Now** | Approvals ownership moves to the tenant admin — and the demo seed moves with it. |
| **Now** | A tenant filter on the audit page, plus actor / action / date. |
| **Now** | CSV downloads for audit, tenant roster and budget. |
| **Waits** | Tenant-defined sub-roles and a `require_permission` catalogue. |
| **Waits** | PDF, scheduled reports, and the forecast report — that one belongs with the forecast surface. |

Ownership is moved this phase using the fine role that already exists (`platform_admin` vs
`tenant_admin`). The full permission model is a separate project and it is in
[`backlog-post-hackathon.md`](backlog-post-hackathon.md).

---

## Tasks

### 6.1 — The two forms (0.4d) — **CORE**

- `web/src/lib/api/client.ts`: `createUser` and `createTenant`, with mirrored types in
  `web/src/lib/api/types.ts`.
- `web/src/components/admin/CreateUserForm.tsx` and `CreateTenantForm.tsx`, mounted beside
  `RolesAccess.tsx` on the admin portal.
- **Surface the backend's real errors.** 409 duplicate username, 403 cross-tenant. Do not
  swallow them into a generic toast — the 403 is the isolation story showing its work in
  front of the jury, and it costs nothing to render properly.
- The user form takes username, role, tenant and password; a tenant-admin sees its own tenant
  pinned and not editable, because that is what the server enforces.

**Verify it by logging out and logging back in as the user you just created.** That single
round trip is the entire point of this task.

### 6.2 — Approvals move to the tenant admin (0.25d) — **CORE**

- Delete the `PLATFORM_ADMIN` early exit at `routes.py:1144`.
- The two decision endpoints require a tenant-scoped admin for a tenant-owned gate. A
  platform admin keeps a **read-only** view of every tenant's queue, with the decision
  buttons disabled and an honest reason string — not hidden, disabled and explained.
- A platform admin may still decide gates whose `tenant_id IS NULL` — Aegis's own actions.
- **In the same commit**, seed a real demo tenant with a real `tenant_admin` login and update
  `_DEMO_USERS` (`routes.py:205`) and the seed script together. Then rehearse the gate flow
  immediately.

That last bullet is not optional. This is a one-hour fix if it is caught now and a demo-day
disaster if it is not.

### 6.3 — Audit filtering (0.15d)

- `GET /audit` gains `tenant_id`, `actor`, `action_prefix`, `since` and `until` — all still
  passed through `_scope_tenant`, so a tenant-admin cannot widen its own scope by sending a
  parameter.
- `AuditLog.tsx` gains the controls. The tenant selector renders only for a platform admin.
- Add the index on `(tenant_id, ts DESC)` — it is the filter's driving predicate and today
  only `ts` and `action` are indexed.

### 6.4 — Downloadable reports (0.2d)

- One module: `backend/src/app/platform/reports.py`, streaming CSV with a shared
  `Content-Disposition` helper.
- `GET /reports/audit.csv` (the filtered trail), `GET /reports/tenant.csv` (the roster:
  users, roles, last login), `GET /reports/budget.csv` (caps versus consumption).
- `budget.csv` reads the same `BudgetStatusRow` the enforcer reads, so the report and the cap
  cannot disagree.
- **Every export writes its own audit row** (`report.export`) carrying the filter parameters.
  An export of the audit trail that is not itself audited is the first hole a procurement
  reviewer finds.
- CSV, not PDF. PDF needs a rendering dependency on a no-Docker Windows box; CSV opens in
  Excel, which is what actually happens to a compliance export.

---

## If time runs short

Per the master plan's cut order, **this phase drops to 6.1 only** — the two forms. That is the
checkpoint demo ("create a tenant and a user in the UI, log in as that user") and it is the
one thing here the backend cannot already do for us.

6.2 is the second thing to keep. It is one deletion plus a seed change, and "the Aegis
operator can see this tenant's gate and cannot decide it" is the most jury-legible sentence in
the phase.

6.3 and 6.4 go to the backlog without argument.

**Do not half-land 6.2.** Moving the guard without changing the seed leaves the demo unable to
approve anything at all. Either both, or neither.

---

## Definition of done

- [ ] A platform admin creates a tenant and a user through the UI, and that user logs in.
- [ ] A duplicate username and a cross-tenant create both show the server's real message.
- [ ] A tenant-owned approval is decidable by that tenant's admin and **not** by the Aegis
      platform admin, who sees it read-only with a stated reason.
- [ ] The demo seed and `_DEMO_USERS` were changed in the same commit as the guard, and the
      gate flow has been rehearsed since.
- [ ] The audit page filters by tenant, actor, action and date, and a tenant-admin cannot
      widen its own scope through a query parameter.
- [ ] Audit, tenant and budget CSVs download, and each download appears as the newest audit row.
- [ ] `pytest` green; the web build and web tests green.

## Demo at the end of this phase

Create *Acme Corp*. Create a user inside it. Log out, log in as that user for real. Raise a
HIGH-risk action in Acme's tenant — it lands in Acme's admin inbox, and the Aegis platform
admin can see it but cannot decide it. Then filter the audit page to Acme, download the CSV,
and reload the page: the download itself is the newest row in the trail.

## Risks

**The demo-seed change is the whole risk in this phase.** Everything else fails visibly during
development; this one fails on stage, in the middle of the money shot, if 6.2 lands without
its seed. Write the seed change in the same commit and rehearse the approval flow the same
afternoon.

**A 403 rendered as a generic error looks like a bug.** Cross-tenant refusal is the control
working. Render it as a refusal with a reason, not as "something went wrong".

**Exports are being written before RLS fails closed on an unset scope** — that hardening is
backlog. Until then the export path must go through the same `_scope_tenant` as the read it
mirrors, with a test per report proving a tenant-admin's CSV contains only its own rows.

**One day is one day.** 6.1 and 6.2 are firm. 6.3 and 6.4 are the estimates at risk, and they
are the two the cut order already names as droppable.
