# Governance

Who you are, what you may touch, and how much you may spend.

---

## 1. What it is

Aegis has one `users` table. Two customers live in it.

| id | username | email | tenant_id |
|---|---|---|---|
| 31 | dana | dana@northwind.example | 7 |
| 32 | ravi | ravi@northwind.example | 7 |
| 88 | okafor | okafor@lawpartners.example | **12** |

Tenant 7's administrator opens the Users page. The endpoint runs:

```sql
SELECT id, username, email, tenant_id FROM users ORDER BY id;
```

Three rows come back. Row 88 belongs to a law firm with nothing to do with tenant 7, and
their email address is now on tenant 7's screen.

Nobody attacked anything. Someone wrote a listing endpoint and forgot four words:
`WHERE tenant_id = 7`. When every customer's rows sit in the same table, the correctness of
your isolation is the correctness of every `WHERE` clause anyone will ever write.

So a multi-tenant system has to answer four questions structurally, plus a fifth that makes
the other four defensible.

| Control | The question |
|---|---|
| Authentication | Who is this? |
| Authorisation | What are they allowed to do? |
| Tenant isolation | Whose data can they touch? |
| Budgets | How much can they spend? |
| Audit trail | What happened, and who approved it? |

---

## 2. How it works in Aegis

### The database does the filtering

Every customer shares one set of tables with a `tenant_id` column — one database, one schema,
one migration. That is why onboarding a tenant needs no new infrastructure, and the leak
above is what it costs.

PostgreSQL **row-level security** closes it. You attach a visibility rule to a table, and
from then on the database silently adds that rule to every query against it. The forgotten
query returns rows 31 and 32; row 88 is invisible, and no application code did anything.

RLS does not stop you writing the wrong query. It changes what the wrong query costs — from
a breach to an empty page someone notices in five minutes.

Three tables carry the policy: `users`, `usage_ledger` and `approvals`. Aegis keeps the
application-level filter too, because the tests run on SQLite, which has no RLS.

Three details separate a policy that works from one that only looks like it does.

**The scope is set per transaction.** The connection announces its tenant with
`SELECT set_config('app.tenant_id', '7', true)`. That third argument scopes the setting to
the current transaction, so it is discarded on commit. At session scope, a pooled connection
would carry tenant 7's identity into the next request that borrows it.

**The policy is forced.** Postgres exempts a table's owner from that table's own policies
unless you also issue `FORCE ROW LEVEL SECURITY` — and this application connects as the
owner. `ENABLE` alone turns policies on for everybody except the one role that queries the
tables.

**The predicate cannot raise.** It pulls digits out of the setting with a regex rather than
casting it:

```sql
(substring(current_setting('app.tenant_id', true) from '^[0-9]+$') IS NULL
 OR tenant_id = substring(current_setting('app.tenant_id', true) from '^[0-9]+$')::int)
```

A plain cast would meet an empty string and error on every query against the table.

### The branch that does not restrict

Read that predicate's first line again. When no numeric scope is bound, the first half is
true and **the policy does not restrict at all**. An unset, empty or non-numeric tenant
scope **fails open**: the request sees everything the table holds.

That is deliberate. Login reads `users` by username *before* any tenant is known — the
username is how you find out which tenant someone belongs to — so a fail-closed unset branch
would mean nobody could ever log in. Platform administrators legitimately list across every
tenant too.

So: any request that binds a numeric scope is strictly enforced; a request that binds none is
not restricted. That is a named gap, not airtight isolation, and the Security page reports it
as `fail_closed=False` rather than putting a green badge on a hole.

### Identity

Passwords are stored as Argon2id hashes:

```
$argon2id$v=19$m=65536,t=3,p=4$mUTb+mXvf6RLyEygCrTcMA$iZi8R6CTUCeb3TXcirVMbdxYOG6CfCAwEv3Nj4OEXlU
```

Hash the same password again and you get a different string, because the second-to-last
segment is a random **salt** generated per hash. The rest reads left to right as the design:
64 MiB of working memory per hash (`m=65536`), three passes, four lanes. Everything needed
to verify is inside the string, so you store one column and can raise the work factor later
without a migration.

Memory is the point. GPUs crack passwords by running thousands of tiny cores in parallel,
and those cores have very little memory each. Requiring 64 MiB per guess stops that.
SHA-256 is designed to be fast, which is exactly the wrong property here.

A user with no password hash returns `False` immediately, and so does a corrupt one. The
function never raises: an absent credential is a failed login, not an error for someone else
to handle.

After login the user carries a **JWT** — their claims, signed. Two things people reverse:
the payload is encoded, not encrypted, so nothing secret goes in it; and the signature buys
integrity, not secrecy. Change `tenant_id` to `12` and the signature stops matching.

That makes the signing secret the only thing between any user and any tenant. So the accepted
algorithms are pinned explicitly at decode time, and the app **refuses to boot** outside dev
if `JWT_SECRET` is the default or under 32 characters. A runtime warning is a warning; a
refusal to start is a control. What you give up is revocation: a token is valid until it
expires, so Aegis bounds the lifetime rather than paying for a lookup per request.

### Two role vocabularies

The stored role is four values: `admin`, `ai_team`, `devops`, `client`. But the distinction
that matters most is not among those four — Dana administers tenant 7 and must never see
tenant 12, while the platform operator must see both. Both are `admin`.

So the fine tier is derived rather than stored: an admin *with* a tenant is a `tenant_admin`,
an admin *without* one is a `platform_admin`. No extra column, and the two facts cannot drift
apart, because they are the same fact.

| Fine tier | Coarse | Rank | Tenant-scoped |
|---|---|---|---|
| `platform_admin` | admin | 4 | no |
| `tenant_admin` | admin | 3 | yes |
| `ai_team` | ai_team | 2 | yes |
| `devops` | devops | 2 | yes |
| `client` | client | 1 | yes |

`ai_team` and `devops` share rank 2 on purpose — neither outranks the other. An unrecognised
tier ranks **0**, below everything, so a comparison against an unknown role can never grant
anything.

### Budgets

Tenant 7 has a $200/day ceiling. Dana, inside tenant 7, has $50/day. Ravi has no personal
cap. Those are rows in `budgets`, keyed by scope.

Dana's effective ceiling is **$50** — the tighter of the two. Ravi's is his tenant's
**$200**. The rule is one line: drop the caps that are `None`, take the smallest of what is
left. This is filesystem quota semantics — granting Dana $500 while her tenant is capped at
$200 gives her $200, and the grant never needs validating because it cannot matter.

There are four caps, and they are not redundant:

| Cap | What it bounds |
|---|---|
| `token_cap` | Total tokens over a rolling window |
| `usd_cap` | Total spend over a rolling window |
| `rpm` | Requests in the last 60 seconds |
| `tpm` | Tokens in the last 60 seconds |

The first two bound total exposure; the last two bound burst. Without a rate cap, tenant 7
can spend their whole $200 in ninety seconds, saturate every worker and starve tenant 12 —
without ever exceeding their budget. And a USD cap is the only one that binds a call billed
in something other than tokens, such as a per-minute transcription.

Windows roll: "$200 per day" means the last 86,400 seconds, so there is no reset cliff to
game.

Enforcement happens at the gateway chokepoint, before the provider is contacted. The check
sums the ledger over each budget's window and refuses on the first breach, using `>=` so
consumption *at* the cap blocks. The user's caps are checked first, so when both are breached
the error names the narrowest scope. If the check itself errors the call is denied — failing
open would silently switch off every cap in the system.

### Getting the tenant to the gateway

The tenant is resolved at the HTTP edge; the budget check happens deep inside a gateway
call. A `ContextVar` carries it there without threading it through every signature on the
way. The API resolves the principal, computes the effective caps once, and binds a
`GovernanceContext` of `tenant_id`, `user_id`, `role` and `limits`. The default is `None`,
meaning no governance in force — which keeps offline and test flows behaving exactly as they
did before governance existed.

One placement detail is load-bearing: for the streaming endpoint the context is bound
*inside* the generator task, not around it. An async generator runs in its own context, so
binding outside it would leave every model call in the run ungoverned, silently.

### Schema and audit

The USD cap is computed by summing the ledger, so that table is load-bearing for a control.
`create_all` only ever creates tables; it never alters one that already exists. A column
added to the model later would be missing on any existing database, every ledger insert
would fail, and the cap would compare $0.00 against $50.00 forever.

So an additive reconciler runs at bootstrap. It adds columns the models declare and the live
tables lack, never drops or renames, and logs every change. Drift it cannot fix safely — a
`NOT NULL` column with no default, where no code can invent a value for the rows already
there — raises, and the API **refuses to serve**, because a control reads this table.

Every autonomous action writes an audit row: the action, the actor, the model, the approver,
a payload, and the **trace id**. The trace id is the field people skip and the one that makes
the log worth keeping. "Refund issued, $4,200, approved by alice@" tells you *what* happened;
the trace id lets you open that run and see *how* the system got there.

---

## 3. How you use it in code

Two wiring calls at startup, because they inject different things:

```python
from aegis.governance import configure_governance, configure_security

configure_security(jwt_secret=..., jwt_algorithm="HS256", jwt_expire_minutes=720)
configure_governance(
    session_factory=lambda: Session(),   # the host owns the engine
    set_tenant_scope=my_binder,          # optional; defaults to the package's own
)
```

### Login

```python
from aegis.governance import (
    hash_password, verify_password, create_access_token, decode_access_token,
)

user.password_hash = hash_password("hunter2")

if verify_password(submitted, user.password_hash):
    token = create_access_token(
        user_id=user.id, username=user.username,
        role="tenant_admin", coarse_role="admin", tenant_id=7,
    )

claims = decode_access_token(token)   # raises InvalidTokenError if bad
```


### Binding a request

```python
from aegis.governance import (
    GovernanceContext, effective_limits, set_governance_context,
    reset_governance_context,
)

limits = await effective_limits(tenant_id=7, user_id=31)
token = set_governance_context(
    GovernanceContext(tenant_id=7, user_id=31, role="tenant_admin", limits=limits)
)
try:
    ...   # every gateway call inside here is now governed
finally:
    reset_governance_context(token)
```

You rarely call `enforce_governance` yourself — the gateway hook calls it before every model
call. `record_usage` writes the ledger row afterwards, in its own session, so a request that
rolls back cannot un-record money that was really spent.

### The rest of the surface

| Function | What it does |
|---|---|
| `effective_limits(tenant_id, user_id)` | The clamped caps for a principal |
| `enforce_governance(tenant_id=, user_id=)` | Raises `BudgetExceededError` if over any cap |
| `record_usage(...)` | One ledger row, including `audio_seconds` / `images` |
| `record_audit(action=, actor=, model=, trace_id=, payload=)` | One audit row; the tenant comes from the context if omitted |
| `upsert_budget(...)` / `list_budgets(...)` | Admin surfaces. A write onto another tenant's row raises `CrossTenantBudgetError`. |
| `bootstrap_rls(engine)` | Installs the policies. Run after `create_all`. |
| `role_rank(tier)` / `RBAC_LADDER` | The ladder as data |

### Settings worth changing

| Setting | Default | What it does |
|---|---|---|
| `JWT_SECRET` | — | Signing secret. The app refuses to boot outside dev if it is default or short. |
| `jwt_expire_minutes` | `720` | Token lifetime |
| `budget_fail_open` | `False` | Allow model calls when the budget read fails |

---

## 4. Why it helps us

**One forgotten `WHERE` is a bug, not a breach.** The database applies the tenant filter
underneath application code, and applies it to the owning role too.

**Spend has a ceiling that binds before the money is spent**, per tenant and per user, with
the tighter cap always winning.

**Everything on the authorisation path fails closed.** Absent credential, unknown role,
unparseable token, enforcement error, irreconcilable schema drift — all refuse. The one
deliberate exception is the RLS predicate with no numeric scope bound, and it is reported
honestly rather than dressed up.

**Every action is attributable.** The audit row names the actor and the approver, and the
trace id joins it to the run that produced it.

**The dashboard and the enforcer agree**, because they run the same ledger summation.

Without it, a shared-table deployment leaks on the first endpoint someone writes in a hurry,
spend is unbounded and unattributed, and an incident review has an audit line with no way to
find out what led to it.

**Next:** [`40-diagrams.md`](40-diagrams.md)
