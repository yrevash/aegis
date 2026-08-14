# Governance — our exact implementation

The package is `aegis/src/aegis/governance/`:

| File | Lines | What it owns |
|---|---|---|
| `__init__.py` | 211 | Public surface + `configure_governance` |
| `types.py` | 326 | `Role`, `TokenClaims`, contexts, admin DTOs — **dependency-free** |
| `security.py` | 284 | Argon2id + JWT + the fine/coarse role split |
| `context.py` | 60 | The `ContextVar` seam |
| `models.py` | 220 | The ORM: `Tenant` / `User` / `Budget` / `UsageLedger` / `AuditLog` |
| `enforcement.py` | 758 | Limits, the chokepoint check, the ledger, admin rollups |
| `rls.py` | 152 | `set_tenant_scope` + `bootstrap_rls` |
| `schema.py` | 254 | Additive column reconciliation |
| `audit.py` | 153 | The audit writer/reader |
| `dashboard.py` | 179 | Budget status + usage summary + the full snapshot |
| `config.py` | 122 | The RBAC ladder and effective config as data |

---

## How you import it

```python
from aegis.governance import configure_governance, configure_security

configure_security(jwt_secret="...", jwt_algorithm="HS256", jwt_expire_minutes=720)
configure_governance(session_factory=lambda: Session(), set_tenant_scope=my_binder)
```

Two separate wiring calls because they inject different things.
`configure_security` (`security.py:84`) installs the JWT config.
`configure_governance` (`__init__.py:193`) injects the **session factory** — the host
owns the engine — and optionally the RLS binder, forwarding both to
`configure_enforcement` and `configure_audit` (`__init__.py:210-211`).

**The dependency split matters.** `aegis.governance.types` imports pydantic and stdlib
only (`types.py:1-23`), so the API/wire contracts can be shared without dragging in
SQLAlchemy, PyJWT or argon2. Importing the package proper pulls those (they live under
the `aegis[governance]` extra) but **no** `litellm`, `fastapi` or `langgraph`
(`__init__.py:15-17`).

---

## Identity: `security.py`

**Password hashing.** One process-wide `PasswordHasher()` at `security.py:55`.
`hash_password(password)` (`:171`) returns the self-describing Argon2 encoded hash —
algorithm, parameters and salt all inside the string, so no extra columns beyond
`User.password_hash`.

`verify_password(password, password_hash)` (`:184`) **fails closed**: `None` hash →
`False` immediately (`:196-197`), and every exception → `False` (`:198-201`). It never
raises.

**The two role vocabularies.**

`Role` (`types.py:47`) is the coarse enum: `ADMIN`, `AI_TEAM`, `DEVOPS`, `CLIENT`.

`principal_role(role, tenant_id)` (`security.py:126`) derives the fine tier:

```python
if role is Role.ADMIN:
    return TENANT_ADMIN if tenant_id is not None else PLATFORM_ADMIN
return role.value
```

`PLATFORM_ADMIN = "platform_admin"`, `TENANT_ADMIN = "tenant_admin"`, and
`MEMBER = "user"` — the legacy fine alias for a plain member (`security.py:121-123`).

No extra column. The comment at `security.py:112-120` is explicit: only `admin` needs the
finer split, and tenancy already carries the information.

`coarse_role_from_fine(fine_role)` (`security.py:145`) is the inverse:
`platform_admin`/`tenant_admin` → `"admin"`; `ai_team`/`devops` → themselves (they *are*
coarse roles); anything else, including the legacy `"user"` → `"client"`.

**Tokens.** `create_access_token(*, user_id, username, role, tenant_id,
coarse_role=None, expires_minutes=None)` (`security.py:209`) builds the payload at
`:237-244`: `username`, `role` (fine), `coarse_role`, `tenant_id`, `iat`, `exp`, and
`sub` **as a string** when a user id exists (`:245-248` — RFC 7519 / PyJWT 2.13 require
it).

`decode_access_token(token)` (`security.py:252`) passes `algorithms=[...]` **explicitly**
(`:265-267`) — the defence against `alg:none` and algorithm confusion. It rejects a token
missing `username` or `role` (`:271-272`), and prefers the dedicated `coarse_role` claim,
falling back to an honest re-derivation only for tokens minted before it existed
(`:276-277`).

**Why `coarse_role` is a claim rather than derived.** The re-derivation is lossy in the
other direction: it collapses everything unrecognised to `client`. Carrying the true
four-valued role as its own claim means the API reads it directly instead of
reconstructing it. That is the difference between a role that survives a round trip and
one that silently degrades.

**The dev secret.** `DEFAULT_JWT_SECRET` (`security.py:61`) is deliberately long enough to
clear PyJWT's minimum-key-length warning on the offline path, and its comment says it
*"must NEVER sign tokens in a real deployment."* The host owns validation — see
`create_app`'s `settings.ensure_secure_secrets()` at `backend/src/app/main.py:252`, which
raises `InsecureConfigurationError` and refuses to boot a non-dev deployment on a default
or too-short secret.

---

## The per-request context: `context.py`

`GovernanceContext` (`types.py:120`) is a frozen dataclass: `tenant_id`, `user_id`,
`role`, `limits`. `GovernanceLimits` (`types.py:103`) holds `token_cap`, `usd_cap`,
`rpm`, `tpm`, each `None` for uncapped.

`context.py:31` declares the slot:

```python
_governance_context: ContextVar[GovernanceContext | None] = ContextVar(
    "governance_context", default=None
)
```

with `set_governance_context` (`:36`), `get_governance_context` (`:49`) and
`reset_governance_context` (`:54`).

**Why a `ContextVar`.** The tenancy boundary has to reach the gateway chokepoint without
threading a tenant id through every graph node signature. A `ContextVar` propagates
automatically into tasks created from the current context, and `None` — the default —
means "no governance in force", which keeps every ungoverned flow behaving exactly as
before.

---

## The ORM: `models.py`

All five tables register on the shared `aegis.data.AegisBase` metadata
(`models.py:26`), so a host's `create_all` materialises them — `jsonb` on Postgres, the
cross-dialect `JsonB` decorator on SQLite.

- **`Tenant`** (`:73`) — `id`, unique indexed `name`, `status` (`TenantStatus`, `:47`),
  `created_at`.
- **`User`** (`:90`) — `username` (unique, indexed), `role` (`SAEnum(Role,
  name="user_role")`, defaulting to `CLIENT`), nullable indexed `tenant_id` FK, `email`,
  `password_hash`, `is_active`. The comment at `:103-112` records the exact `ALTER TYPE`
  a live Postgres needs to widen the enum from the old two-label form.
- **`Budget`** (`:124`) — nullable `tenant_id` (the *owner*), `scope_type`
  (`BudgetScope`, `:54`), `scope_id`, `window` (`BudgetWindow`, `:61`), and the four caps
  `token_cap` / `usd_cap` / `rpm` / `tpm`.
- **`UsageLedger`** (`:154`) — `tenant_id`, `user_id`, indexed `ts`, `model`,
  `prompt_tokens`, `completion_tokens`, **`audio_seconds`** (`:191`), **`images`**
  (`:193`), `cost_usd`, indexed `trace_id`. Read the docstring at `:168-174`: it is the
  postmortem of the migration bug, and it names `reconcile_additive_columns` as the fix.
- **`AuditLog`** (`:198`) — `tenant_id`, `ts`, `action`, `actor`, `model`, `trace_id`,
  `payload` (JsonB), `approved_by`.

---

## Enforcement: `enforcement.py`

**Wiring.** `configure_enforcement(*, session_factory=None, set_tenant_scope=None)`
(`:84`). `_set_tenant_scope` defaults to the package's own RLS binder (`:81`). `_session()`
(`:107`) raises a clear `RuntimeError` if nothing was injected — it does **not** silently
degrade.

Every function opens its own **short-lived session** from the factory, so callers never
thread a session through their signatures.

**Windows.** `_WINDOW_SECONDS` (`:118`) — `DAY` = 86,400, `MONTH` = 2,592,000.
`_RATE_SECONDS = 60` (`:123`).

**Limit resolution.** `_clamp_inward(user_cap, tenant_cap)` (`:149`) drops `None`s and
returns the minimum of what is left — a present cap always binds over an absent one.

`effective_limits(tenant_id, user_id)` (`:182`) binds the tenant scope (`:198`), loads
both budget rows via `_budgets_for` (`:159`), and returns a `GovernanceLimits` with each
field clamped inward (`:204-209`). An unscoped principal (`tenant_id is None`) returns
empty limits immediately (`:195-196`).

Note `_budgets_for`'s sort at `:179`: **user-scoped rows first**, so a user breach is
attributed to the user when both trip.

**The chokepoint check.** `enforce_governance(*, tenant_id, user_id)` (`:235`):

1. `tenant_id is None` → return (`:250-251`). Ungoverned is a no-op.
2. Bind the tenant scope so Postgres RLS engages (`:256`).
3. For each governing budget row, pick the ledger column — `UsageLedger.tenant_id` or
   `.user_id` depending on `scope_type` (`:259-263`).
4. Sum the ledger over the row's window via `_usage_sums` (`:212`), which uses
   `func.coalesce(func.sum(...), 0)` so it behaves identically on SQLite and Postgres.
5. Raise on the first breach: `token_cap` (`:270`), `usd_cap` (`:272`), then — only if a
   rate cap exists — re-sum over the last 60 seconds for `rpm` (`:279`) and `tpm`
   (`:281`).

`_raise` (`:285`) constructs the `BudgetExceededError` with scope, limit type, limit and
used, so the wire event can be built from it.

Note the comparison is `>=`, not `>`: consumption *at* the cap blocks.

**The ledger write.** `record_usage(...)` (`:296`) binds the tenant scope, adds one
`UsageLedger` row including `audio_seconds` / `images`, commits. Its docstring
(`:313-315`) states the connection precisely: *"`cost_usd` already prices them, which is
what makes a USD cap bite on a per-minute-billed call — the token caps deliberately do
not, since an audio minute is not a token."*

**Admin surfaces.** `list_tenants` (`:349`), `create_tenant` (`:366`), `create_user`
(`:393`, Argon2-hashing the password before it is ever persisted, `:430`), `list_users`
(`:450`), `list_budgets` (`:471`), `user_tenant_id` (`:498`), `update_user_role` (`:549`,
with the last-platform-admin lockout guard at `:580-594`), `upsert_budget` (`:622`) and
`usage_rollup` (`:713`).

Three named errors: `DuplicateTenantError` (`:341`), `DuplicateUserError` (`:345`),
`CrossTenantBudgetError` (`:530`), `LastPlatformAdminError` (`:540`) — each mapped by the
API to a proper status code rather than escaping as a 500.

---

## RLS: `rls.py`

`_RLS_TABLES = ("users", "usage_ledger", "approvals")` (`rls.py:32`). `approvals` lives
host-side (it is the agent's human-in-the-loop table) but is governed by the same
per-tenant policy, so it is bootstrapped here alongside.

**`set_tenant_scope(session, tenant_id)`** (`rls.py:35`). Returns immediately on a
non-Postgres dialect (`:52-54`) — that is the SQLite test path. Then:

```python
if tenant_id is None:
    await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
else:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )
```

The comment at `rls.py:55-69` gives both reasons — correctness (`SET` cannot take a bind
parameter) and isolation (`is_local=true` scopes the GUC to the transaction). Both are
covered in [`30-deep-dive.md`](30-deep-dive.md).

**The policy predicate** — `_TENANT_ISOLATION_PREDICATE` at `rls.py:101-105`:

```sql
(substring(current_setting('app.tenant_id', true) from '^[0-9]+$') IS NULL
 OR tenant_id = substring(current_setting('app.tenant_id', true) from '^[0-9]+$')::int)
```

The `substring` yields the id or SQL NULL — it can never raise, which a bare `''::int`
cast would. The docstring at `:79-100` documents the unbound branch as a deliberate,
named limitation rather than leaving it to be discovered.

**`bootstrap_rls(engine)`** (`rls.py:108`) — Postgres-only, idempotent. Per table
(`:137-152`):

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE  ROW LEVEL SECURITY;   -- the load-bearing statement
DROP POLICY IF EXISTS tenant_isolation ON <t>;
CREATE POLICY tenant_isolation ON <t> USING <predicate>;
```

The policy has **no explicit `WITH CHECK`**, so Postgres reuses `USING` for writes
(`:125-127`): under a bound scope, an INSERT/UPDATE stamping a different tenant is
rejected by the database, not merely hidden.

---

## Schema reconciliation: `schema.py`

`plan_additive_columns(existing, metadatas)` (`schema.py:89`) is **pure** — it takes a
set of `(table, column)` pairs and the declarative metadata and returns
`(addable, unsafe)`. Being database-free makes the decision testable without a live
Postgres.

The classification is at `:117-125`: a column is **unsafe** if it is a primary key, or if
it is neither nullable nor carrying a `server_default`. A table absent entirely is
skipped (`:113-114`) — `create_all` owns brand-new tables.

`reconcile_additive_columns(conn, metadatas)` (`schema.py:174`):

- non-Postgres → `[]` (`:198-199`);
- reads `information_schema.columns` (`_existing_columns`, `:71`);
- **unsafe drift → `SchemaDriftError`** (`:203-219`), logged at CRITICAL *and* raised;
- otherwise emits `ALTER TABLE "<t>" ADD COLUMN IF NOT EXISTS <ddl>` (`:229`), where the
  DDL is rendered by SQLAlchemy's own `CreateColumn` compiler (`_column_ddl`, `:129`) so
  the added column matches exactly what `create_all` would have produced;
- a failing ALTER is logged at CRITICAL and **re-raised**, never swallowed (`:231-243`);
- indexes declared solely on newly added columns are created alongside (`_indexes_for`,
  `:148`), so a column is never left half-installed.

The module docstring (`schema.py:1-37`) is one of the best pieces of documentation in the
codebase — read it.

---

## Audit and dashboards

`record_audit(...)` (`audit.py:63`) and `list_recent_audit(...)` (`audit.py:115`), with
the same injected-session-factory pattern (`configure_audit`, `:35`).

`budget_status(...)` (`dashboard.py:53`) joins each cap with its live consumption over
that cap's own window. The `BudgetStatusRow` docstring (`types.py:215-224`) states the
invariant: it runs *"the identical summation `enforce_governance` runs, so the dashboard
and the enforcer never disagree."* `*_remaining` is `None` when uncapped and floored at
zero once breached — `used` still reveals the overage.

`usage_summary(...)` (`dashboard.py:116`) and `governance_dashboard(...)`
(`dashboard.py:151`) build the full tenant-scoped snapshot.

---

## Config as data: `config.py`

`RBAC_LADDER` (`config.py:43-79`) is the role hierarchy expressed as data:

| Fine tier | Coarse | Rank | Tenant-scoped |
|---|---|---|---|
| `platform_admin` | admin | 4 | no |
| `tenant_admin` | admin | 3 | yes |
| `ai_team` | ai_team | 2 | yes |
| `devops` | devops | 2 | yes |
| `client` | client | 1 | yes |

`ai_team` and `devops` deliberately **share rank 2** — neither dominates the other.
`role_rank(fine_role)` (`:84`) maps the legacy `MEMBER` alias to `client` and returns
**0** for an unknown tier, failing closed for any ordering comparison.

`effective_config()` (`:95`) returns the live JWT knobs (never the secret — only
`secret_is_dev_default`, `:108`), the ladder, the window spans and the RLS posture.

**One honest caveat worth knowing before an interview.** `effective_config` reports
`RlsConfig(fail_closed=True)` at `config.py:119`. As documented at
`rls.py:79-100`, the installed predicate does **not** restrict when no numeric scope is
bound. That field overstates the posture, and the accurate statement is the one in
`rls.py`: *a bound numeric scope is strictly enforced; an unbound request is not
restricted.*

---

## How the backend composes it

Three shims, each wiring at import time:

**`backend/src/app/data/governance.py`** — `configure_governance(...)` at `:77-80`,
injecting `lambda: get_sessionmaker()()` and a **late-binding** scope wrapper `_scope`
(`:64`) that resolves `set_tenant_scope` from this module's globals on every call, so a
test monkeypatching `app.data.governance.set_tenant_scope` still intercepts the scope
applied deep inside `aegis.governance`.

**`backend/src/app/core/governance.py`** — a pure re-export of the contextvar seam.

**`backend/src/app/core/llm.py:216-220`** — the gateway hook wiring, which is what
connects governance to spend. `_GovernanceHook.enforce` (`core/llm.py:136`) calls
`enforce_governance`, and `record` (`:175`) calls `record_usage`.

**Bootstrap order** — `backend/src/app/data/session.py`, inside `bootstrap()`:

```python
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
    await conn.run_sync(AegisBase.metadata.create_all)
    await reconcile_additive_columns(conn, metadatas)   # additive drift
    await _align_timestamp_columns(conn, metadatas)     # naive -> timestamptz
await bootstrap_rls(engine)
```

(`session.py:274-280`). And `backend/src/app/main.py:153-160` re-raises
`SchemaDriftError` **ahead of** the blanket "database is optional" handler at `:161`,
with the reasoning spelled out in the lifespan docstring at `:136-144`: booting anyway
would serve paid model calls with no spend ceiling and no record.

**Per-request wiring** — `backend/src/app/api/routes.py`:

- `require_auth` (`:294`) decodes the bearer token; role dependencies at `:342`
  (`require_admin`), `:352` (`require_roles`), `:372` (`require_devops`), `:382`
  (`require_ai_team`), `:392` (`require_client`), `:402` (`require_platform_admin`),
  `:412` (`require_tenant_admin`).
- The cross-tenant guard at `:444-451`: a platform-admin may name any tenant; anyone else
  requesting a different tenant gets a 403, and an omitted `tenant_id` defaults to their
  own.
- `_resolve_governance(auth)` (`:454`) builds the context; it is bound **inside** the SSE
  generator task at `:920` and reset in a `finally` at `:934`.

**Next:** [`30-deep-dive.md`](30-deep-dive.md) — five real bugs, all verified against a
live Postgres.
