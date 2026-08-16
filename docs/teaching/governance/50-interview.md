# Governance — interview questions and answers

This is the module that carries the hardest questions. It is also the one where the
answers are most convincing, because every control here failed at least once and was
fixed.

The reasoning behind every answer below is in [`10-guide.md`](10-guide.md).

---

### "How do you keep one tenant's data away from another's?"

Two layers, and the second exists because the first is a convention.

**Layer one** is the application filter — `WHERE tenant_id = :ctx` on every governed
read. It is the only layer that works on SQLite, which is what the test suite runs.

**Layer two** is PostgreSQL row-level security. Each transaction binds the tenant into a
custom setting, and a policy on `users`, `usage_ledger` and `approvals` restricts every
row read against it — for any request that actually binds a numeric scope; see the last
question for the branch that does not. The inversion is the point: without RLS, a
forgotten `WHERE` is a leak; with RLS, it is an empty result — a bug you notice in five
minutes instead of one you notice in a breach report.

One free property worth mentioning: the policy has no explicit `WITH CHECK`, so Postgres
reuses the `USING` predicate for writes. Under a bound scope, an INSERT that would stamp a
different tenant is rejected by the **database**, not merely hidden.

---

### "What's the trap with row-level security?"

Two, and both were live here.

**Postgres exempts a table's owner from its own policies unless you also issue `FORCE ROW
LEVEL SECURITY`.** And applications typically connect as the owner — in our case
`create_all` and the RLS bootstrap both run on the serving engine, so the app *is* the
owner. RLS was enabled, visible in `pg_policies`, reviewed, and enforced against nobody.
There is no way to see that in the code; the only way to find it is to test the negative —
connect as the app, bind tenant A, assert tenant B's row is not returned.

**The tenant variable must be transaction-scoped, not session-scoped.** A session-level
`SET` lives as long as the connection, and a pool hands that connection to the next
request carrying the previous tenant's scope. We use `set_config(..., is_local=true)`, so
it is discarded on commit or rollback.

---

### "Tell me about a security bug you found."

The best one is that `SET app.tenant_id = :tid` **is not executable**.

Postgres' `SET` takes a literal. Sent over the extended query protocol with a bind
parameter, it raises `syntax error at or near "$1"`. So every tenant-scoped path on
Postgres — the ledger write, the budget reads, the user listings, the approvals inbox —
raised, not "returned wrong rows", *raised*.

**And the suite never caught it, which is the part worth dwelling on.** The tests run
SQLite, and the function returns at a dialect check three lines in. Every test hit that
early return. The function was covered, exercised, and never once executed its body.

The fix is `SELECT set_config('app.tenant_id', :tid, true)` — an ordinary function call,
so it parameterises correctly. And parameterising matters: interpolating the value into
the string would be SQL injection in the one place you least want it.

The `true` is `is_local`, which fixes a second, independent bug — the pooled-connection
scope leak that would have appeared the moment the first was fixed.

The general lesson I took: **a test suite on a different database engine gives you zero
coverage of engine-specific code**, and coverage tooling will happily report the function
as covered because the first two lines ran.

---

### "How do budgets work?"

Two levels — tenant and user — enforced **inward**: the effective cap is the tighter of
the two, with `None` meaning uncapped, so a present cap always binds over an absent one.
Filesystem quota semantics.

Four cap types: token cap and USD cap over a rolling window, plus rpm and tpm over the
last sixty seconds. Total caps control exposure; rate caps control burst. You need both —
a monthly cap does not stop a tenant consuming it in ninety seconds and starving everyone
else.

Enforcement happens at the gateway chokepoint **before** the model call. It sums the
usage ledger for the scope over the window and raises `BudgetExceededError` on the first
breach. User-scoped rows are checked first, so when both trip the error names the user.

**Is it a hard cap?** No, and I would not claim it is. The check and the spend are not
atomic, so with *k* concurrent requests you can overshoot by up to *k* calls' worth. A
hard cap needs reserve-then-settle — a synchronous write before every model call plus
reconciliation of unused reservations. The overshoot is bounded by concurrency rather
than unbounded, and for spend caps that is the right trade.

---

### "One tenant could take over another's budget. How?"

The budget natural key is `(scope_type, scope_id, window)` — for example
`("tenant", 7, "day")`. That triple is **global**: it contains no tenant, and all three
parts arrive as fields on the admin request, so any admin can *name* any other tenant's
row.

The upsert looked up on that key with no tenant predicate and then assigned
`existing.tenant_id = caller` unconditionally. So tenant 12's admin posting for
`("tenant", 7, "day")` found **tenant 7's row**, overwrote 7's caps, and re-stamped the
row as 12's. Tenant 7's spending limit was gone and the row vanished from tenant 7's
listing.

**The interesting part is that the obvious fix is worse.** Adding a tenant predicate to
the lookup means that when a conflicting row exists the lookup finds nothing, so the
insert branch runs and creates a **second** row for the same scope and window. The
enforcement reader then picks between duplicates arbitrarily — you have converted a
deterministic takeover into non-deterministic enforcement, which is harder to diagnose.

So we kept the full natural-key lookup, added an ownership check, and **refuse**:
`CrossTenantBudgetError`, surfaced as a 403 rather than escaping as a 500. Only two
different, non-null tenants collide — a platform admin may write any row, and an unowned
row may be claimed.

The principle: **when a uniqueness assumption is violated, detect and refuse; do not
silently create a second row and hope the reader picks the right one.**

And a related detail: the API layer already authorised this write. The data layer must
not depend on that. Defence in depth means the layer performing the write refuses a write
it can prove is wrong.

---

### "Why is a schema migration a security control here?"

Because the USD budget cap is computed by **summing the usage ledger**, so the cap is only
as real as the ledger's ability to accept rows.

Two columns were added to the ledger model with the `ALTER TABLE` written only in a
docstring. There is no Alembic in this project, and `create_all` is `CREATE TABLE IF NOT
EXISTS` — it never alters an existing table. So on any pre-existing database every ledger
INSERT raised `UndefinedColumn`.

And the gateway records usage **best-effort** — a model call that already succeeded must
not be failed by an accounting write — so the exception was swallowed and logged. Rows
vanished. The sum stayed flat. Every USD cap stopped binding, and the system kept serving
paid model calls with no ceiling and no record.

**Every layer in that chain is individually defensible.** The bug is entirely in the seam.

The fix is an additive schema reconciliation at bootstrap: additive only so it cannot
destroy data, idempotent so it is safe on every boot, Postgres-only, and **loud** — drift
it cannot fix raises `SchemaDriftError` and the API refuses to serve. That required
re-raising ahead of the host's blanket "the database is optional" startup handler.

Refusing to boot is the correct outcome when the table that cannot be written is the one a
spend cap is computed from. A running system whose caps silently do not bind is strictly
worse than one that will not start.

---

### "Why Argon2id and not bcrypt?"

Argon2id won the Password Hashing Competition and is the current default recommendation.
The property that matters is **memory-hardness**, not just slowness.

An attacker's cost is guesses times cost-per-guess. GPUs and ASICs win on parallel
*compute* but have little memory per core, so a function that needs tens of megabytes per
hash cannot be run ten-thousand-way parallel. bcrypt is memory-light by comparison and
therefore more GPU-friendly for the attacker.

Argon2id specifically is the hybrid variant: a data-independent first pass for
side-channel resistance, data-dependent after for GPU hostility.

The practical benefit is that Argon2 hashes are **self-describing** — variant, version,
memory cost, time cost, parallelism and salt all live in the encoded string. One column,
and you can raise the work factor for new passwords without a schema change, because old
hashes verify with their own recorded parameters.

And verification **fails closed**: a user with no password hash returns `False`, and any
exception returns `False`. It never raises.

---

### "Why JWTs? What do you give up?"

Statelessness. The token carries the claims — user, fine role, coarse role, tenant,
expiry — signed with HMAC-SHA256, so verification is a signature check with **no database
lookup**. That scales horizontally without shared session storage.

What you give up is **revocation**. A JWT is valid until it expires; you cannot un-issue
it without adding the state you were avoiding. The options are short lifetimes plus
refresh tokens, a denylist (which reintroduces the per-request lookup), or rotating the
secret (which invalidates everything at once). We bound the lifetime and accept the
window.

**Two things I would want to say unprompted.** The payload is encoded, not encrypted —
anyone holding the token reads every claim, so nothing secret goes in it. And the decode
call passes `algorithms=[...]` **explicitly**, which is the one-line defence against both
`alg:none` and algorithm confusion, where an attacker flips RS256 to HS256 and signs with
the public key as the HMAC secret.

Since it is HS256, one secret both signs and verifies, so anyone who can verify can also
forge. That is fine for a single issuing service and it means the secret is everything —
which is why there is a **startup guard** that refuses to boot a non-dev deployment on a
default or too-short signing secret. A warning is a warning; a refusal to start is a
control.

---

### "Why do you have two role vocabularies?"

Because four roles cannot express the distinction that matters most.

The coarse enum has four values mapping to real personas: an operator role, an AI/ML
engineering role, a platform/operations role, and a self-scoped business end-user. But an
operator who administers **one tenant** is not the same as one who administers the
**whole platform** — the first must never see another tenant, the second must see them
all.

Rather than add a fifth value, we **derive** the split from data already present: an admin
with a tenant is a tenant-admin; an admin without one is a platform-admin. No extra
column, and the two facts cannot drift, because they are the same fact.

**The consequence worth naming:** the token carries *both* the fine tier and the true
coarse role, as separate claims. Re-deriving the coarse role from the fine one is lossy —
it collapses everything unrecognised to `client` — so a token that carried only the fine
tier would let a role silently degrade on a round trip.

The ladder is also expressed as data with explicit ranks, and `ai_team` and `devops`
deliberately **share** a rank: neither dominates the other. An unknown tier ranks 0, below
every real tier, so any ordering comparison fails closed.

---

### "How does the tenant reach the gateway without being threaded through everything?"

A `ContextVar`. The tenancy boundary has to reach the single chokepoint where model calls
happen, and threading a tenant id through every graph node signature would be both ugly
and easy to forget.

The API resolves the principal, computes the effective caps once, and binds a
`GovernanceContext`. The gateway's governance hook reads it at the chokepoint. `None` —
the default — means "no governance in force", so ungoverned flows are a complete no-op:
no database read, no ledger row.

**One placement detail that is load-bearing:** the context is bound *inside* the SSE
generator task, not around it, with a reset in a `finally`. An async generator runs in its
own context; binding outside would not be visible at the chokepoint.

---

### "What happens if the budget check itself fails?"

It **fails closed** by default. A real breach propagates as `BudgetExceededError`; any
*other* exception from the enforcement read — a database blip — produces a synthetic
`BudgetExceededError` with `limit_type="enforcement_error"` and denies the call.

The reasoning: a fail-open enforcement error disables **every cap in the system**, at
exactly the moment nobody is watching. A denied call is a visible, recoverable outage; an
undetected uncapped spend is not.

Fail-open is available as a configured opt-in and logs a warning when it fires, so the
degraded posture is at least visible.

---

### "Was there anything the machinery worked for but wasn't attached to?"

Yes, and it is a different class of bug from the others.

`/vision/analyse` makes two paid model calls — a prompt-injection screen and the analyst
call — and the route never bound the governance context. Both enforcement and ledgering
are gated on the context existing, so both calls were uncapped, unattributed and invisible
in the cost dashboard.

The machinery was perfect. A route simply did not opt in.

**That is the failure mode of any opt-in control**: a control you must remember to attach
is a control that will eventually not be attached. The structural answers are middleware
that binds on every request, or making the context mandatory at the chokepoint. We fixed
the route and added a test that drives the **real** route through the **real** governance
hook to the **real** ledger — an integration test, because a unit test of the hook would
have kept passing throughout.

---

### "How would you test tenant isolation properly?"

Three things, and the first is the one people skip.

**Test the negative, on the real engine.** Connect as the application role, bind tenant A,
and assert that tenant B's row is **not** returned. That is the only test that would have
caught the missing `FORCE` — a positive test ("tenant A sees their own row") passes
whether or not the policy is enforced.

**Test each governed path binds a scope.** We keep a monkeypatchable seam in the host shim
specifically so a test can spy on every governed call's tenant scope. That is how we found
that one read — `user_tenant_id` — was the single governed function in the module that
never bound a scope at all.

**Test the cross-tenant writes explicitly.** A budget upsert against another tenant's row
must raise, not write. A role change against a user outside the caller's tenant must read
back as unknown.

And more generally: **the Postgres-specific paths need a Postgres test.** A SQLite suite
tells you nothing about RLS, GUCs, or `ALTER TABLE` semantics, and three of the five bugs
in this module were invisible to it.

---

### "What's the weakest part of this design?"

The unbound branch of the RLS predicate, and I would rather state it than be caught on it.

The policy says: if a numeric tenant scope is bound, a row is visible only when it
matches; if no numeric scope is bound, the policy does not restrict. That second branch is
deliberate — the login lookup reads `users` by username before any tenant is known, and
the platform-admin listings legitimately span tenants. Under a strictly fail-closed unset
branch with `FORCE` on, both would return zero rows, including login.

Closing it properly means the authentication path binds a scope before it reads, which is
host work outside the RLS module. It is documented in place, with the follow-up named.

**The accurate description is:** any request that binds a numeric scope is strictly
enforced; a request that binds none is not restricted — it fails **open**, deliberately;
and this is strictly more enforcement than before FORCE, when the policy was inert for the
owning role in every case.

The codebase reports it that way too, which is the part I would want to point at. The
effective-config surface returns `fail_closed=False` for the RLS posture
(`aegis/src/aegis/governance/config.py:133`), and the comment there explains why: that
value renders a "fail-closed" badge on the console's Security page, and reporting `True`
would put a false assurance on a security dashboard — worse than the gap it would hide. It
flips to `True` once the auth path binds a scope before querying `users`.
