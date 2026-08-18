# 1 — PostgreSQL 17, and the role that makes RLS real

**~15 minutes.** Needs the admin password once, for the installer.

---

## Install

```powershell
& "$env:USERPROFILE\Downloads\postgresql-17.11-1-windows-x64.exe"
```

In the wizard:

| Setting | Value | Why |
|---|---|---|
| Port | **5432** | Everything in the repo assumes it |
| Superuser password | choose one, **write it down** | Needed by the next step and by the admin DSN |
| Locale | default | |
| Stack Builder at the end | **skip** | Nothing here needs it |

Confirm it is up:

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -c "SELECT version();"
```

---

## Create the application database

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -c "CREATE DATABASE taif;"
```

---

## The serving role — do not skip this

```powershell
.\scripts\db-roles.ps1
```

This creates `aegis_app` as `LOGIN NOSUPERUSER NOBYPASSRLS`, grants it exactly the DML it
needs, and rewrites `backend\.env` so:

- `POSTGRES_DSN` → the **serving** role, which RLS actually applies to
- `POSTGRES_ADMIN_DSN` → the **owner**, used only for DDL, the RLS bootstrap and the grants

### Why this exists, in one paragraph

PostgreSQL skips row security entirely for a superuser or any role with `BYPASSRLS`, and
`FORCE ROW LEVEL SECURITY` removes only the table *owner's* exemption — not that one. This
platform connected as `postgres` for its whole life, so thirteen tenant-isolation policies were
installed, visible in `pg_policies`, reviewed, and **enforced against nobody**. Verified on a
scratch database at the time:

```
superuser     scoped to tenant 1 → sees 2 of 2 rows   ← bypassed
non-superuser scoped to tenant 1 → sees 1 of 2 rows   ← enforced
```

Splitting the connections is what makes bypass a property of the *connection* rather than
something application code is trusted to avoid.

---

## Check

```powershell
cd backend
$env:PYTHONPATH="src;..\aegis\src"
.\.venv\Scripts\python.exe -m app.data.rls_check
```

**Expected:**

```
ENFORCED    serving role 'aegis_app' is subject to RLS (owner DSN split)
```

If it says `BYPASSED`, `db-roles.ps1` did not take effect or `backend\.env` still points at
`postgres`. **Do not continue** — every tenant-isolation claim downstream is false until this
line reads ENFORCED.

If it says `UNVERIFIED`, Postgres is unreachable; fix that first.

---

**Next:** [`02-services.md`](02-services.md)
