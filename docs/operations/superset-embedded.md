# Embedded analytics — Apache Superset, inside Aegis

Aegis renders Superset's charts **on an Aegis page**. There is no link to
`localhost:8088` anywhere in the product, and an operator never leaves the console to
see a chart.

This document is the runbook for the half Aegis's own test suite cannot reach. That
suite drives a **faked** Superset HTTP surface and never opens a socket to a BI server,
so what it proves is the Aegis side: the request built, the credential attached, the
tenant filter derived, and the behaviour when Superset is not there.

The Superset side has been **partly confirmed against a live 6.1.0** — install, sign-in,
embed registration and guest-token minting, with the RLS clause landing inside the signed
token. Those steps are marked *(Verified.)* below and quote real responses. Everything
else is a check for you to run, with the expected output stated so you can tell.
§7 is the honest ledger of which is which.

---

## 1. What was built, and why in this order

| Path | What it is | Depends on |
|---|---|---|
| **Server-side data path** (default) | Aegis builds the Superset query context, calls `POST /api/v1/chart/data`, and draws the rows with Aegis's own chart components — light theme, Aegis chrome, no iframe. | Superset's REST API |
| **Embed** (opt-in, per board) | A short-lived Superset *guest token* handed to Superset's embedded SDK, which mounts an embedded dashboard in an iframe on the Aegis page. | Superset's REST API **and** `EMBEDDED_SUPERSET` |

Both read the **same six views** over Aegis's own tables, as the same read-only Postgres
role — see §5. That is the pipeline: Superset is the charting layer for Aegis's
operational data, not a bolt-on for one screen.

The data path was built first on purpose, and both paths are shipped. `EMBEDDED_SUPERSET`
and the guest-token mint have since been **confirmed working on a live 6.1.0** (§7), so
the embed is not a gamble — but the charts path is still the one that keeps working when
the iframe does not, and it is what the page opens on. Set
`AEGIS_SUPERSET_EMBED_ENABLED=false` to drop to charts-only; nothing else changes.

## 2. The security model, stated plainly

**Aegis holds the Superset service credential; the browser never does.** Aegis signs in
as a Superset service account for exactly one purpose — to *mint guest tokens*. That
JWT owns the whole BI instance and never leaves the backend process. It is asserted by
test that no Aegis response contains it
(`backend/tests/api/test_analytics_endpoints.py`).

**The tenant filter is a `WHERE` clause the browser cannot remove.** A guest token is
minted with `rls: [{"clause": "tenant_id = 3"}]` and the clause lands **inside the signed
JWT**, under `rls_rules` — observed, not assumed. Superset compiles that predicate into
every query it runs under the token, and the browser holding it cannot edit a signed
claim. The clause is derived from `AuthContext.tenant_scope()`
— the sealed authority — by `aegis/src/aegis/analytics/rls.py`, and from nothing the
request carried. The request body has exactly one field (`window`, a key from a fixed
list); a body carrying `tenantId` or `datasourceId` is a 422, by name.

**Three narrowings, at two levels, and every one of them fails closed.**

| # | Level | Mechanism | Covers | Fails how |
|---|---|---|---|---|
| 1 | Query | The guest token's `rls` clause, compiled into the `WHERE` by Superset | Every query run under the token — the embed's charts included, which Aegis never sees | **Closed.** A refused token is no data at all |
| 2 | Query | The `filters` list in the query context **Aegis built** for the server-side path | `POST /analytics/boards/{id}/data` only | **Closed.** No query is built at all for an unresolved scope |
| 3 | Database | Each `analytics_*` view carries its own tenant predicate, and `DB_CONNECTION_MUTATOR` sets the GUC that predicate reads | Every query Superset runs against an Aegis dataset, whatever asked for it | **Closed.** An unmutated connection returns **zero rows** |

Layer 3 deserves a paragraph, because the obvious way to build it fails open and this
one does not.

Aegis's own `tenant_isolation` policy is deliberately null-tolerant —
`substring(current_setting('app.tenant_id', true) from '^[0-9]+$') IS NULL OR tenant_id = …`
— so an unset GUC does not restrict. That is correct for Aegis, whose request path always
binds the GUC, and whose login lookup and platform-admin reads legitimately happen outside
any tenant scope. It is **wrong for a connection Aegis did not open**. `DB_CONNECTION_MUTATOR`
is exactly the kind of hook that stops firing quietly: a typo in the username format, a
Superset upgrade changing what it receives, a connection created outside the request path.
Inheriting the null-tolerant predicate would mean the database layer silently became a
no-op while this table still claimed three layers — and a layer that silently becomes
nothing is worse than no layer at all.

So the views carry **their own** predicate, and it is not null-tolerant:

```sql
WHERE (current_setting('app.analytics_all_tenants', true) = 'on'
    OR <alias>.tenant_id = substring(
         current_setting('app.tenant_id', true) from '^[0-9]+$'
       )::int)
```

An unset or non-numeric `app.tenant_id` yields `tenant_id = NULL`, which is never true.
An unmutated connection therefore sees **nothing** rather than everything. Reading across
tenants requires `app.analytics_all_tenants = 'on'` — a GUC in its own name that nothing
else in Aegis ever writes, so it is an opt-out somebody chose and not one a session can
drift into. In particular it is *not* the empty `app.tenant_id` that `set_tenant_scope`
writes when it resets: that is the value a connection *returns to*, and using it would be
the implicit opt-out this design exists to avoid.

> **If a board is unexpectedly empty, the mutator is not firing.** That is the failure
> this shape converts a silent hole into. Check, in order: the guest token's
> `user.username` is `aegis-tenant-<id>` (decode the token — §6 step 6); the
> `DB_CONNECTION_MUTATOR` in `superset_config.py` matches that prefix exactly; and
> Superset is not serving the query from a cached connection made before the hook was
> added. A blank chart when Superset is otherwise healthy is *always* this, never "your
> tenant has no data" — the same query with `app.tenant_id` set by hand in `psql` will
> show you the rows are there.

Two conditions still have to hold, and neither fails open:

- Superset's database role must have **neither `SUPERUSER` nor `BYPASSRLS`** — Postgres
  skips row security entirely for a role that has either, and a view owned by such a role
  would reach the base tables unrestricted. `python -m aegis.analytics` provisions
  `NOSUPERUSER NOBYPASSRLS`; §6 step 2 checks it.
- Superset must not hand a pooled connection carrying one tenant's GUC to another
  tenant's query. Aegis cannot verify Superset's pooling from here — but note that the
  worst case is one tenant seeing another's *aggregate* through a stale connection, not
  every tenant seeing everything, because there is no state in which the predicate is
  absent.

`backend/tests/data/test_analytics_views.py` proves all of it on a real cluster with a
real `NOSUPERUSER NOBYPASSRLS` role: the shipped view returns zero rows with the GUC
unset while a control view carrying Aegis's null-tolerant predicate returns both tenants',
and a control view identical to the shipped one but for `ALTER VIEW … OWNER TO` leaks the
same way.

**Who sees what.** Each board declares an `audience` — the fine roles allowed to select
it. A client cannot open a platform operator's board: the refusal is a 404 on the
server, identical to the answer for a board that does not exist, so it cannot be used to
enumerate the boards a caller is not allowed to open. The nav hiding the board is a
courtesy, not the enforcement.

---

## 3. Superset-side configuration — apply this by hand

**Install and initialise.** The step that is easy to miss is the third, and skipping it
is what produces "Forbidden on every API call":

```powershell
uv venv superset-uv --python 3.11
uv pip install apache-superset rich cachetools   # rich + cachetools are missing from the 6.1.0 wheel's deps
$env:SUPERSET_CONFIG_PATH = "C:\path\to\superset_config.py"
superset db upgrade
superset fab create-admin --username admin --firstname A --lastname B --email a@b.c --password admin
superset init
```

`superset init` is what syncs the Admin / Alpha / Gamma permissions. Without it every
API call is Forbidden, and the workaround that suggests itself — bulk-linking every pvm
to Admin — treats the symptom **and** hands the guest role far more than it should ever
have. Run `superset init`; do not grant permissions by hand.

Then add the following to `superset_config.py`. Every `superset` CLI command needs
`SUPERSET_CONFIG_PATH` pointing at it.

```python
import os  # if superset_config.py does not already import it

# ── Embedding ────────────────────────────────────────────────────────────────
# Turns on /embedded/<uuid>, POST /api/v1/dashboard/<id>/embedded and the guest-token
# endpoint. Verified present and working in 6.1.0.
FEATURE_FLAGS = {"EMBEDDED_SUPERSET": True}

# The Superset role a guest token is granted. "Gamma" is Superset's built-in limited
# role and is the value this flow was verified against — it is populated correctly by
# `superset init` and is emphatically NOT Admin. Harden it later to a dedicated
# read-only role (see below); never leave it as Admin, and never "fix" a Forbidden by
# granting the guest role more.
GUEST_ROLE_NAME = "Gamma"

# The secret guest tokens are signed with. Not SECRET_KEY, and read from the
# environment so it is not in the file.
GUEST_TOKEN_JWT_SECRET = os.environ["SUPERSET_GUEST_TOKEN_JWT_SECRET"]
GUEST_TOKEN_JWT_ALGO = "HS256"
GUEST_TOKEN_HEADER_NAME = "X-GuestToken"
# Short, because this is the one credential that reaches a browser. The embedded SDK
# refreshes it through Aegis automatically.
GUEST_TOKEN_JWT_EXP_SECONDS = 300

# REQUIRED, and the one that bites silently. A minted guest token's `aud` claim
# defaults to "http://0.0.0.0:8080/" — an address nobody is talking to. Minting still
# returns 200, so nothing looks wrong until the browser's embed call is rejected on
# audience validation. Set it to the exact origin the BROWSER uses to reach Superset.
GUEST_TOKEN_JWT_AUDIENCE = "http://localhost:8088"

# ── Letting the Aegis origin call and frame Superset ─────────────────────────
ENABLE_CORS = True
CORS_OPTIONS = {
    "supports_credentials": True,
    "allow_headers": ["*"],
    "resources": ["*"],
    "origins": ["http://localhost:3000"],   # the Aegis console's origin
}

# X-Frame-Options: Superset sends SAMEORIGIN by default, which blocks the iframe
# outright, before any CSP is considered. Emptying this is required.
HTTP_HEADERS = {}

# frame-ancestors: the CSP half of the same permission, owned by Talisman.
#
# TALISMAN_ENABLED = False is what the verified run used, and it is the blunt version:
# it removes the CSP entirely. Acceptable for a localhost demo, wrong for anything
# shared. The configured form below is the one to ship; if the iframe is blank, drop to
# `TALISMAN_ENABLED = False` to confirm the CSP is the cause, then fix the CSP rather
# than leaving it off.
TALISMAN_ENABLED = True
TALISMAN_CONFIG = {
    "force_https": False,                   # plain http on localhost
    "content_security_policy": {
        "default-src": ["'self'"],
        "img-src": ["'self'", "data:", "blob:"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "script-src": ["'self'", "'unsafe-inline'", "'unsafe-eval'"],
        "frame-ancestors": ["'self'", "http://localhost:3000"],
    },
}

# CSRF stays ON. Aegis fetches GET /api/v1/security/csrf_token/ after signing in and
# returns it as X-CSRFToken on every POST, so there is no reason to turn a security
# control off to make the integration work. `WTF_CSRF_ENABLED = False` was used for
# hand-probing with curl only.

# ── Defence in depth: carry the tenant onto Superset's own DB connection ─────
# Superset opens its own pooled Postgres connection, which carries none of Aegis's
# request context. The guest token's username is the only channel this hook gets, so
# Aegis encodes the tenant there: "aegis-tenant-<id>" for a tenant, "aegis-platform"
# for a deliberate platform-wide read.
#
# The analytics_* views FAIL CLOSED (§2): a connection this hook does not touch reads
# zero rows, not every row. So getting this wrong shows up as an empty dashboard, which
# is the point. Mirror `aegis.analytics.rls.analytics_connect_options`, which is the
# same three-way decision under test in the Aegis repo.
def DB_CONNECTION_MUTATOR(uri, params, username, security_manager, source):
    options = ""
    prefix = "aegis-tenant-"
    if username and username.startswith(prefix):
        suffix = username[len(prefix):]
        if suffix.isdigit():
            options = f"-c app.tenant_id={suffix}"
    elif username == "aegis-platform":
        # The deliberate opt-out, in a GUC nothing else in Aegis writes.
        options = "-c app.analytics_all_tenants=on"
    if options and str(uri).startswith(("postgresql", "postgres")):
        connect_args = dict(params.get("connect_args") or {})
        connect_args["options"] = options
        params["connect_args"] = connect_args
    return uri, params
```

Do **not** put `"*"` wildcards in `FAB_ROLES`: FAB runs `re.match("*", …)`, which raises
`re.error: nothing to repeat` and 500s every request. Do **not** run with
`--reload --debugger`: the reloader watches `site-packages` and restarts constantly.
The dashboard list is at `/dashboard/list/`, not `/dashboards/list/`.

### Hardening the guest role (do this after the flow works end to end)

`Gamma` is not Admin and is the verified starting point, but it is broader than a guest
needs. Once §6 passes, move to a dedicated role:

```powershell
superset fab create-role --name AegisGuest
```

Grant it, in **Settings → List Roles → AegisGuest**, only: `can read on Chart`,
`can read on Dashboard`, `can read on Dataset`, `can explore json on Superset`,
`can read on Datasource`, and `datasource access` on each `analytics_*` dataset. Nothing
else — no write, no SQL Lab, no `all_datasource_access`. Then set
`GUEST_ROLE_NAME = "AegisGuest"` and re-run §6 steps 6–8.

## 4. Aegis-side configuration

In the Aegis backend environment (`backend/.env`):

```dotenv
AEGIS_SUPERSET_ENABLED=true
AEGIS_SUPERSET_BASE_URL=http://localhost:8088
AEGIS_SUPERSET_USERNAME=aegis-service
AEGIS_SUPERSET_PASSWORD=<the service account's password>
AEGIS_SUPERSET_TENANT_COLUMN=tenant_id
AEGIS_SUPERSET_BOARDS=C:/path/to/aegis-boards.json
# The iframe. Confirmed working on 6.1.0; still opt-in, because the charts path is what
# the page opens on and must keep working when the iframe does not.
AEGIS_SUPERSET_EMBED_ENABLED=true
```

The prefix is `AEGIS_` because Superset reads `SUPERSET_*` names of its own
(`SUPERSET_CONFIG_PATH`, `SUPERSET_HOME`, `SUPERSET_SECRET_KEY`) and both processes run
on the same host.

### The board catalogue

The browser names a **board id** and nothing else. A board is a question Aegis is willing
to ask Superset, defined server-side. With no catalogue there are no boards, and the page
says so and names this variable.

A ready-made catalogue over the six views is committed at
[`superset/aegis-boards.json`](superset/aegis-boards.json). Copy it, then replace every
`"datasourceId": 0` with the real Superset dataset id (Superset → Datasets → the
dataset's edit URL, `/tablemodelview/edit/<id>`) and the `embeddedUuid` placeholder with
the UUID from the dashboard's **Embed dashboard** dialog. `0` is refused at load with a
sentence naming the board, so a half-filled catalogue is loud rather than mysterious.

Board fields:

- `datasourceId` is the **dataset** id, not the chart id.
- `embeddedUuid` comes from the dashboard's **… → Embed dashboard** dialog. It is *not*
  the id in the dashboard's URL.
- A metric is either the **name of a saved metric** on the dataset (a bare identifier —
  `spend_usd`, `runs_total`, all defined in the dataset YAMLs below) or a structured
  `{"aggregate": "SUM", "column": "cost_usd"}`. A metric written as a SQL string is
  refused at load: a config file is exactly where a subquery reading past the tenant
  filter would sit quietly.
- Every dataset a board reads must carry the tenant column named in
  `AEGIS_SUPERSET_TENANT_COLUMN`. All six views do.
- `audience` is mandatory. A board with no audience is reachable by nobody, which is
  never what was meant, so it is a load error rather than a silent hide.

Windows: `last_7_days`, `last_30_days`, `last_quarter`, `last_year`, `no_filter`.

---

## 5. The pipeline — what Superset actually reads

A guest token pointed at nothing is not an integration. Superset reads **six
purpose-built views** over tables Aegis already writes, as a **dedicated read-only
Postgres role**. Nothing is invented and nothing is a raw table: a view gives the RLS
clause a `tenant_id` column to filter on and keeps conversation content out of reach.

| View | Source table | What a board reads from it |
|---|---|---|
| `analytics_spend_daily` | `usage_ledger` | spend, calls and tokens per day and model |
| `analytics_runs_daily` | `runs` | runs per day and outcome, latency, cache hits, guardrail blocks |
| `analytics_approvals_daily` | `approvals` | human gates per day, by risk and status, with time-to-decide |
| `analytics_jobs_daily` | `job_runs` | job throughput and failure rate per job type |
| `analytics_redteam_runs` | `redteam_runs` | every red-team run: suite, mode, block rate, verdict |
| `analytics_audit_daily` | `audit_log` | recorded governance actions per day |

The definitions live in `aegis/src/aegis/analytics/provision.py` and the SQL is generated
from them, so the views and the datasets cannot drift from each other silently.

### The read-only role, and the one line that makes it defence in depth

```bash
# From the repo, with the aegis package importable:
python -m aegis.analytics --role aegis_superset --password '<a strong password>' > analytics.sql
# Review it, then run it as the OWNER of the Aegis tables:
psql -d aegis -f analytics.sql
```

What that script does, and why each step is there:

1. creates `aegis_superset` **`NOSUPERUSER NOBYPASSRLS`** — PostgreSQL skips row security
   *entirely* for a superuser or a `BYPASSRLS` role, so an owner-connected Superset would
   make all thirteen `tenant_isolation` policies inert for every query it ran;
2. grants it `SELECT` on exactly the six source tables — no `chat_messages`, no
   `memory_*`, no `documents`;
3. creates each view **with its own fail-closed tenant predicate** (§2) — welded in by
   `AnalyticsView.sql`, so a view added next month cannot omit it: there is no field in
   which to write a `WHERE` that replaces it, only one that is `AND`-ed after it;
4. **immediately `ALTER VIEW … OWNER TO aegis_superset`**. This is
   the load-bearing line. A view executes its query with the privileges of its *owner*,
   so a view left owned by the table owner reaches the base table as the owner — and
   where that owner can bypass RLS, the view is a hole straight through the policy while
   looking exactly like a safe projection. `backend/tests/data/test_analytics_views.py`
   proves both halves on a real cluster: the provisioned view returns one tenant's rows,
   and a control view identical but for that one line returns both;
5. revokes `CREATE` on the schema again, which was needed only so the role could accept
   ownership.

To remove it all: `python -m aegis.analytics --revoke | psql -d aegis`.

Re-run the provisioning after any Aegis schema change. It is idempotent, and that is
tested.

### The dataset and dashboard artefacts

`docs/operations/superset/` is a Superset **import bundle**, committed so the analytics
layer is reproducible from the repository rather than living only as clicks in somebody's
`superset.db`:

```
docs/operations/superset/
  metadata.yaml
  aegis-boards.json                     # the Aegis-side board catalogue
  databases/Aegis.yaml                  # the connection (password redacted)
  datasets/Aegis/analytics_spend_daily.yaml
  datasets/Aegis/analytics_runs_daily.yaml
  datasets/Aegis/analytics_approvals_daily.yaml
  charts/Spend_by_model.yaml
  charts/Runs_by_outcome.yaml
  charts/Human_gates_by_risk.yaml
  dashboards/Aegis_Operations.yaml
```

The dataset YAMLs are the load-bearing half — they define the columns and the saved
metrics (`spend_usd`, `runs_total`, `gates_total`, …) that the board catalogue names.
`aegis/tests/analytics/` asserts every shipped dataset names a view that still exists and
declares only columns that view still selects, so the artefacts cannot rot silently.

**UNVERIFIED: this bundle has never been imported into a running Superset.** It follows
Superset's documented v1 import format and every file parses as YAML, and that is all
that is proven. Import it with:

```powershell
# From docs/operations/, with the password filled into databases/Aegis.yaml first:
Compress-Archive -Path superset\* -DestinationPath aegis-superset.zip -Force
$env:SUPERSET_CONFIG_PATH = "C:\path\to\superset_config.py"
superset import-dashboards -p aegis-superset.zip
```

If it is rejected, import `datasets/` alone (the half that matters), build the dashboard
in the UI, and **export it back into this directory** so the repo stays the source of
truth:

```powershell
superset export-dashboards -f aegis-superset.zip
# unzip over docs/operations/superset/, review the diff, commit
```

Redact the password out of `databases/Aegis.yaml` before committing an export.

---

## 6. VERIFY — the runbook, with expected output

Steps 3–7 have been **observed working against a live Superset 6.1.0**; the responses
quoted are real. They are here so you can confirm your own instance rather than trust
mine. Steps 1–2 and 8–12 are yours to run.

**1. The pipeline exists.** Provision the views and the read-only role (§5), then
confirm as that role:

```bash
psql "postgresql://aegis_superset:<password>@localhost:5432/aegis" \
  -c "\dv analytics_*" \
  -c "SELECT count(*) FROM analytics_spend_daily;" \
  -c "SET app.tenant_id = '1'; SELECT count(*) FROM analytics_spend_daily;"
```

Expected: six views listed; the **first** count is `0`, and the second is however many
rows tenant 1 has. A first count that is not zero means the fail-closed predicate is
missing and layer 3 is a no-op. A `permission denied` means the provisioning did not run
as the table owner.

**2. The role cannot bypass row security.**

```sql
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'aegis_superset';
```

Expected: `f`, `f`. If either is `t`, layer 3 is inert and the `ALTER VIEW … OWNER TO`
step bought nothing — re-provision with a role that has neither.

**3. Superset answers.**

```powershell
$env:SUPERSET_CONFIG_PATH = "C:\path\to\superset_config.py"
superset run -p 8088 --with-threads
# in another shell:
curl.exe -s http://localhost:8088/health
```

Expected: `OK`.

**4. The service account can sign in.** *(Verified.)*

```powershell
curl.exe -s -X POST http://localhost:8088/api/v1/security/login `
  -H "Content-Type: application/json" `
  -d '{\"username\":\"aegis-service\",\"password\":\"...\",\"provider\":\"db\",\"refresh\":true}'
```

Expected: 200 with `access_token`. A 401 with every credential correct almost always
means `superset init` was never run.

**5. Register each dashboard for embedding, and keep the uuid.** *(Verified.)* This is
where every board's `embeddedUuid` comes from — not the numeric id in the dashboard's
URL.

```powershell
curl.exe -s -X POST http://localhost:8088/api/v1/dashboard/1/embedded `
  -H "Authorization: Bearer <access_token>" -H "Content-Type: application/json" `
  -d '{\"allowed_domains\":[\"http://localhost:3000\"]}'
```

Expected: 200 and a body carrying `uuid`, e.g.
`78354696-9add-4383-995c-1ca33bb83908`. `allowed_domains` must list the **Aegis
console's** origin. Paste each uuid into `aegis-boards.json`.

**6. A guest token can be minted, and the clause is inside it.** *(Verified.)*

```powershell
curl.exe -s -X POST http://localhost:8088/api/v1/security/guest_token/ `
  -H "Authorization: Bearer <access_token>" -H "Content-Type: application/json" `
  -d '{\"user\":{\"username\":\"aegis-tenant-3\",\"first_name\":\"Aegis\",\"last_name\":\"tenant 3\"},\"resources\":[{\"type\":\"dashboard\",\"id\":\"<uuid from step 5>\"}],\"rls\":[{\"clause\":\"tenant_id = 3\"}]}'
```

Expected: 200 with `{"token": "eyJ…"}`. Decode the payload (any JWT decoder) and expect:

```json
{"user": {"username": "aegis-tenant-3", ...},
 "resources": [{"type": "dashboard", "id": "78354696-..."}],
 "rls_rules": [{"clause": "tenant_id = 3"}],
 "iat": ..., "exp": ..., "aud": "http://localhost:8088", "type": "guest"}
```

Two things to check in that payload, and both have bitten:

- the clause appears under **`rls_rules`** (the *request* field is `rls`). If it is
  absent, the mint silently dropped it and layer 1 does not exist;
- **`aud` is your Superset origin, not `http://0.0.0.0:8080/`.** That default is what you
  get with `GUEST_TOKEN_JWT_AUDIENCE` unset. Minting still returns 200, so the failure
  shows up much later as a blank iframe. Set it and re-mint.

**7. A guest token can read chart data.** Aegis authenticates the server-side chart read
with the **guest token**, so both paths are narrowed by the same clause. This is also why
every board needs an `embeddedUuid`: Superset derives a guest token's *dataset* access
from the dashboards in `resources`, so a token granting no dashboard can read no chart.

```powershell
curl.exe -s -X POST http://localhost:8088/api/v1/chart/data `
  -H "Authorization: Bearer <guest token from step 6>" -H "Content-Type: application/json" `
  -d '{\"datasource\":{\"id\":7,\"type\":\"table\"},\"queries\":[{\"columns\":[\"model\"],\"metrics\":[\"spend_usd\"],\"filters\":[{\"col\":\"tenant_id\",\"op\":\"==\",\"val\":3}],\"row_limit\":10}],\"result_format\":\"json\",\"result_type\":\"full\"}'
```

Expected: `{"result": [{"colnames": [...], "data": [...]}]}`, every row tenant 3's. A 403
means the guest role lacks `datasource access` on that dataset — fix the grant. The
fallback of using the service token instead is deliberately not configurable: it would
silently drop the RLS clause and the tenant-bearing username together.

**8. The clause is actually applied.** *This is the single most important check in the
document.* Repeat step 6 with `"clause": "tenant_id = 2"`, then step 7 with the new token
and **no `filters` at all**. Expected: only tenant 2's rows. If the rows are not filtered,
Superset is not honouring the guest token's RLS, layer 1 is not in force, and the feature
should stay off.

**9. Aegis reports it.** Start the Aegis backend and open **Analytics** in the console.

- With Superset up: `GET /analytics/status` returns `reachable: true`, the board chips
  render, and a chart draws.
- With Superset stopped: the page reads *"Superset is not answering at
  http://localhost:8088"* followed by the `superset run` command, and **every other page
  in Aegis is unaffected**. Stop Superset and confirm this — a page that spins forever or
  shows zeros is a bug, not a degradation.

**10. The embed.** Set `AEGIS_SUPERSET_EMBED_ENABLED=true`, reload, and switch a
dashboard-backed board to **Superset dashboard**. Expected: the dashboard renders inside
the Aegis page, light-themed, with no Superset navigation. A blank frame is, in order of
likelihood: `aud` (step 6), `allowed_domains` (step 5), `HTTP_HEADERS` / `frame-ancestors`
(§3). The browser console names which.

**11. Cross-tenant, end to end.** Sign in as tenant A's admin, open a board, then as
tenant B's admin and open the same board. Expected: different numbers. Then confirm a
`client`-role account cannot open a board whose `audience` excludes it — expected: it is
not in the list, and `POST /analytics/boards/<id>/data` returns 404.

**12. The DB-level layer, end to end.** With everything above passing, a board that draws
rows *is* the proof that the mutator fired: the views return nothing without it (step 1).
So this step is the negative check. Comment out `DB_CONNECTION_MUTATOR` in
`superset_config.py`, restart Superset, and reload the board.

Expected: the chart is **empty** — not wrong, not another tenant's, empty. Put the hook
back. If the board still draws rows with the mutator gone, the views were provisioned
without their predicate; re-run `python -m aegis.analytics` and repeat step 1.

## 7. Superset 6.1.0 — what is confirmed, and what is still risky

The 6.1.0 PyPI wheel ships broken paths: `rich` and `cachetools` are missing from its
dependencies, the parquet example-loader path is never wired into `import_dataset`, and
`superset init` is easy to skip (which is what produces "Forbidden on every API call" —
not a missing `FAB_ROLES`).

| Thing | Status | Note |
|---|---|---|
| `EMBEDDED_SUPERSET` / `POST /api/v1/dashboard/{id}/embedded` | **Confirmed working** | Returns the uuid. Present in 6.1.0. |
| `POST /api/v1/security/guest_token/` with `rls` | **Confirmed working** | The clause lands in the signed token under `rls_rules`. |
| `GUEST_TOKEN_JWT_AUDIENCE` unset | **Confirmed broken by default** | `aud` becomes `http://0.0.0.0:8080/`. Minting still returns 200; the browser rejects it later. Set it. |
| Guest token on `POST /api/v1/chart/data` | Unverified | Step 7. A 403 is a `datasource access` grant on the guest role, not a credential problem. |
| Guest-token RLS clause actually applied to the rows | **Unverified — highest impact** | Step 8. This is the security property. Do not enable the feature until it passes. |
| `DB_CONNECTION_MUTATOR` receiving the guest username | Unverified | No longer a silent risk: the views fail closed, so a hook that is not firing shows as an **empty** board (step 12), never as another tenant's rows. |
| The import bundle in `docs/operations/superset/` | Unverified | Never imported. Parses as YAML; that is all that is proven. |
| `GUEST_ROLE_NAME = "Gamma"` | Confirmed working | Not Admin. Harden to `AegisGuest` once the flow passes (§3). |

## 8. Where the code is

| Concern | File |
|---|---|
| The tenant filter derivation — the whole safety property | `aegis/src/aegis/analytics/rls.py` |
| The board catalogue and its refusals | `aegis/src/aegis/analytics/catalogue.py` |
| The views, the read-only role, and the SQL that makes them | `aegis/src/aegis/analytics/provision.py` |
| The committed Superset import bundle | `docs/operations/superset/` |
| The Superset query context Aegis builds | `aegis/src/aegis/analytics/query.py` |
| The Superset HTTP contract | `aegis/src/aegis/analytics/client.py` |
| Honest states / degradation | `aegis/src/aegis/analytics/service.py` |
| The API | `backend/src/app/api/routes_analytics.py` |
| The page | `web/src/components/analytics/` |
| Tests — the Superset side is faked, never called | `aegis/tests/analytics/`, `backend/tests/api/test_analytics_endpoints.py`, `web/tests/analytics/` |
| Tests — the views and the role, on a real cluster | `backend/tests/data/test_analytics_views.py` |

---

## Provisioning log — what actually broke, in order

Recorded 2026-08-20, against Superset **6.1.0** and the seeded demo corpus. Every item below
was a hard stop that produced a *misleading* error, which is the reason to write them down: none
of the five messages names its own cause.

### 1. `superset init` had never been run

The symptom the operator saw was **"Forbidden"** on every dashboard. `superset init` syncs the
role↔permission tables; without it the roles exist by name and hold nothing.

    superset init          # idempotent, safe to re-run, takes ~1 min

### 2. `psycopg2` was not installed in Superset's virtualenv

    GET /api/v1/dataset/2  →  500  {"error": "No module named 'psycopg2'"}

Superset ships no Postgres driver by default. Nothing in the UI says so; the dataset list renders
fine, because listing datasets reads Superset's own metadata DB and never touches Postgres.

    uv pip install psycopg2-binary       # into Superset's venv, then restart

### 3. The export bundle's JSON-string fields are rejected by 6.1.0's schemas

    Schema validation failed for charts/Spend_by_model.yaml: {'params': ['Not a valid mapping type.']}
    AttributeError: 'str' object has no attribute 'get'     # databases/Aegis.yaml, `extra`

`params`, `extra`, `query_context`, `json_metadata` and `position_json` are carried as JSON
*strings* by older exports and must be YAML *mappings* here. The bundle in
`docs/operations/superset/` has been re-exported with the mappings baked in, so this should not
recur — but a bundle taken from another instance may need the same conversion.

### 4. A guest token authorises nothing until the dashboard owns the charts

    403 DATASOURCE_SECURITY_ACCESS_ERROR — "requires the datasource 2 ... permission"

Superset derives a guest's dataset access from the dashboards its token names. `import-directory`
created the charts unattached, so the embedded dashboard granted access to nothing. Attach them
(`PUT /api/v1/chart/<id>` with `{"dashboards": [<id>]}`), then re-mint.

**And the request must name the dashboard too.** `raise_for_access` reads
`form_data.dashboardId` off the chart-data body and resolves it by `Dashboard.id`. The token's
`resources[].id` is the *embedded UUID*; this is the *numeric* id — two different identifiers for
one dashboard, and both are required. Aegis carries them as `embeddedUuid` and `dashboardId` in
the board catalogue, and refuses a `chart` board missing either.

### 5. The guest token goes in `X-GuestToken`, never `Authorization`

    422 {"msg": "Signature verification failed"}

Sent as a Bearer token, FAB verifies it against `SECRET_KEY` rather than
`GUEST_TOKEN_JWT_SECRET`. The message reads like a rotated secret and is really a token in the
wrong header. Fixed in `aegis.analytics.client`.

### 6. And the one that returns 200 with nothing in it

With all five fixed, every board answered **200 and zero rows** over views holding 2,502 spend
rows — because `DB_CONNECTION_MUTATOR` (specified above) was absent from the local
`superset_config.py`. The `analytics_*` views fail closed, so a connection the hook does not touch
reads nothing rather than everything. **This is the design working**: the failure is an empty
dashboard, not another tenant's money. It is also the only item here that leaves no error at all,
so check it first when charts render empty.

### Verifying the whole chain

```
POST /v1/analytics/boards/spend-by-model/data   {"window": "last_30_days"}
  → 200, 14 rows, [{"model": "genailab-maas-gpt-4o", "spend_usd": 49.14}, …]
```

And confirm isolation is real, not assumed — mint two guest tokens and compare:

| guest username | `analytics_spend_daily`, DeepSeek-R1 |
|---|---|
| `aegis-platform` | $12.11 |
| `aegis-tenant-1` | its own, smaller slice |

If both return the same number, `DB_CONNECTION_MUTATOR` is not being called.

### The instance is not in this repo

Superset itself — the venv and the SQLite metadata DB holding these dashboards — lives outside
version control. **Only the asset bundle in `docs/operations/superset/` is durable.** Rebuilding
is: create the venv, `superset db upgrade`, `superset fab create-admin`, `superset init`, install
`psycopg2-binary`, point `SUPERSET_CONFIG_PATH` at a config carrying the `GUEST_TOKEN_*` keys and
the `DB_CONNECTION_MUTATOR`, then `superset import-directory -o docs/operations/superset`. Paste
the resulting dataset and dashboard ids into `aegis-boards.json` — the catalogue refuses
placeholders by name, so a missed paste is a sentence, not a silent empty chart.
