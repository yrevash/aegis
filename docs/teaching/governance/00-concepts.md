# Governance — the concept, from zero

No code in this file. What multi-tenancy actually is, why it is the hardest requirement
in the system, and why "we filter by tenant id" is not an answer.

---

## The problem

You built one system. Three companies pay for it. Their data sits in the same database,
their requests hit the same process, their model calls go through the same gateway.

Now answer these:

- Can company A's query return company B's document?
- Can company A's admin change company B's spending limit?
- If company A burns through its month's budget in an hour, does company B still work?
- When something goes wrong, can you prove which company's user did what, and when?

If any answer is "probably not, we filter by tenant id everywhere", you do not have
multi-tenancy. You have a shared database and a convention.

**Governance is the set of controls that make those answers structural instead of
conventional.** Four of them:

| Control | The question it answers |
|---|---|
| **Authentication** | Who is this? |
| **Authorisation (RBAC)** | What are they allowed to do? |
| **Tenant isolation** | Whose data can they touch? |
| **Budgets** | How much can they spend? |

Plus a fifth that makes the other four defensible: an **audit trail** — what actually
happened, who authorised it, and which trace it belonged to.

---

## Multi-tenancy: three shapes, one choice

**Separate database per tenant.** Perfect isolation — a query physically cannot reach
another tenant's rows. Expensive: N databases to migrate, monitor, back up and connect
to. Onboarding a customer means provisioning infrastructure.

**Separate schema per tenant.** One database, N namespaces. Cheaper, still N migrations,
and connection routing gets fiddly.

**Shared tables with a `tenant_id` column.** One database, one schema, one migration.
Cheapest by a distance — and the isolation now depends entirely on **every single query
being correct**.

Aegis uses the third. It is the right choice for a platform that must onboard tenants
without provisioning, and it makes the isolation problem *your* problem rather than the
infrastructure's.

Which raises the obvious objection: one forgotten `WHERE tenant_id = ?` is a data
breach. What stops that?

---

## Row-level security: moving the filter into the database

**Row-Level Security (RLS)** is a PostgreSQL feature that attaches a visibility
predicate to a table. Once a policy is installed, *every* query against that table has
the predicate silently ANDed into it — by the database, not by your code.

The shape:

1. Each connection sets a variable identifying the current tenant.
2. The policy says: a row is visible only when its `tenant_id` matches that variable.
3. A query that forgets its `WHERE` clause returns **nothing**, not everything.

That inversion is the point. Without RLS, forgetting the filter is a leak. With RLS,
forgetting the filter is an empty result — a bug you notice in five minutes rather than
one you notice in a breach report.

**Defence in depth.** The application filter stays. Both layers do the same job for
different reasons: the application filter is the one that works on SQLite (where the
tests run), and the database policy is the one that catches the query nobody reviewed.

### Two traps, and both bit this codebase

**Trap one: Postgres exempts the table's owner from its own policies.**

`ENABLE ROW LEVEL SECURITY` turns policies on for other roles. The owner of the table
bypasses them. And applications typically connect as the role that created the tables.
So RLS can be enabled, visible in `pg_policies`, reviewed and approved — and enforced
against nobody. You need `FORCE ROW LEVEL SECURITY` as well.

**Trap two: connection pooling and variable scope.**

If the tenant variable is set at *session* scope, it lives as long as the **connection**
— and a pool hands that connection to the next request, carrying one tenant's scope into
another tenant's query. The variable must be scoped to the **transaction**, so it is
discarded on commit or rollback.

Both of these are excellent interview answers precisely because they are invisible in
code review. The code says "we set the tenant and installed the policy." Both statements
were true. Neither was enforcing anything.

---

## Authentication: proving who you are

**Passwords must never be stored.** You store a *hash* — a one-way function — and
compare hashes at login. If the database leaks, the attacker has hashes, not passwords.

Not just any hash. Fast hashes (SHA-256) are a liability here: an attacker with the
database can try billions of guesses per second on a GPU. A **password hashing
function** is deliberately slow and deliberately memory-hungry, so parallel guessing
gets expensive:

- **bcrypt** — old, well-understood, memory-light (so GPU-friendly for the attacker).
- **scrypt** — memory-hard, better.
- **Argon2id** — winner of the Password Hashing Competition, memory-hard *and* resistant
  to side-channel attacks. The current default recommendation.

Argon2 hashes are **self-describing**: the algorithm, its parameters and the salt all
live inside the encoded string. You store one column and you can raise the work factor
later without a schema change.

### Sessions, and why tokens instead

Once someone logs in, every subsequent request has to prove it is still them. Two
options:

**Server-side sessions.** A random session id in a cookie; the server looks it up in a
store. Revocation is instant (delete the row). Every request costs a lookup.

**Signed tokens (JWT).** The server issues a token containing the user's claims —
who they are, their role, their tenant, an expiry — signed with a secret. The server
verifies the signature and reads the claims. **No lookup.** Stateless, which means it
scales horizontally without shared session storage.

The trade-off is revocation. A JWT is valid until it expires; you cannot un-issue it
without adding the very state you were avoiding. That is why lifetimes are bounded, and
why the claims must carry everything the request needs so no per-request lookup sneaks
back in.

**The claims are also a security surface.** If a token carries `tenant_id`, then the
tenant of a request is whatever the token says. That is fine — the token is signed — but
it means the *signing secret* is the single thing standing between a user and any
tenant. A deployment running the framework's default dev secret is not multi-tenant; it
is a system where anyone who reads the source can mint an admin token.

---

## Authorisation: roles, and why there are two vocabularies

Authentication says *who*. Authorisation says *what they may do*.

The simplest workable model is **role-based access control (RBAC)**: a principal has a
role, and endpoints require a role.

Aegis has four roles that map to real personas: an **operator/governance** role, an
**AI/ML engineering** role, a **platform/operations** role, and a **business end-user**
role that is always scoped to its own data.

But there is a distinction the four values cannot express: an operator who administers
**one tenant** is not the same as an operator who administers **the whole platform**.
The first must never see another tenant; the second must see all of them.

You could add a fifth role. Aegis does something better: it **derives** the finer tier
from the data already present. An admin *with* a tenant is a tenant-admin; an admin
*without* one is a platform-admin. No extra column, and the two facts cannot drift apart
— because they are the same fact.

That gives two vocabularies:

- The **coarse** role — four values, the thing stored in the database.
- The **fine** tier — the coarse role split by tenancy, used for authorisation checks.

**Why this is worth understanding rather than memorising:** carrying both means a token
must carry both, or you have to re-derive one from the other. And re-derivation is
**lossy** — going from `platform_admin` back to a coarse role is fine, but collapsing
several coarse roles into one and then trying to recover them is not. Any place you
re-derive instead of carrying is a place a role can silently degrade.

---

## Budgets: the hierarchy and the direction

Two levels of cap: per tenant, and per user within a tenant.

The rule is **inward enforcement**: a user cannot be granted more than their tenant has.
The effective cap is the *tighter* of the two, with "no cap set" meaning unlimited — so a
cap that exists always binds over one that does not.

This is exactly filesystem quota semantics, and the intuition transfers: your personal
quota does not let you exceed the volume's capacity.

Caps come in four flavours, and they are not interchangeable:

- **token cap** — total tokens over a rolling window
- **USD cap** — total spend over a rolling window
- **rpm** — requests per minute
- **tpm** — tokens per minute

Token and USD caps control *total exposure*. Rate caps control *burst* — they stop one
tenant monopolising throughput even while well within their monthly budget. You need
both: a monthly cap does not stop a tenant consuming it in ninety seconds and starving
everyone else.

**And note which caps a non-token charge binds.** An audio minute is not a token, so a
token cap correctly does not bite on transcription. The USD cap does — but only if the
per-minute charge was priced into the ledger row. That connection, from billing unit to
ledger to cap, is the whole reason [`gateway/`](../gateway/) cares about billing units.

---

## The audit trail

Every autonomous action, the human who approved it (if any), the model involved, and the
trace id.

The trace id is the part people skip and it is the part that matters. An audit row that
says "refund issued, $4,200, approved by alice@" tells you *what*. An audit row that also
carries the trace id lets you open the trace and see *how the system got there* — what
was retrieved, what the model planned, which guardrail passed. Without it, the audit log
and the observability system are two accounts of the same event that cannot be joined.

---

## Where the schema itself becomes a control

Here is a connection that is easy to miss and is worth internalising.

The USD budget cap is computed by **summing the usage ledger**. So the cap is only as
real as the ledger's ability to accept rows.

If a column is added to the ledger model and the live database never grows it, every
INSERT fails. And because ledger writes are deliberately best-effort — a model call that
succeeded must not be failed by an accounting write — that failure is *swallowed*. The
rows vanish. The sum stays flat. The cap never binds. The system keeps serving paid model
calls with no ceiling and no record, and nothing anywhere says so.

**A schema migration is therefore a security control**, not a maintenance chore. That is
not a general truth about all schemas — it is true of this one, because this table is
what a control reads.

---

## What you should now be able to explain

- The three multi-tenancy shapes and why shared tables shifts the isolation burden
- What RLS is, and why "forgot the WHERE clause" changes from a leak to an empty result
- Why `ENABLE` without `FORCE` can enforce against nobody
- Why session-scoped variables leak across a connection pool
- Why passwords need a *slow, memory-hard* hash and what Argon2id is
- JWT vs server-side sessions, and what you give up for statelessness
- Why there are two role vocabularies, and why re-deriving one from the other is lossy
- Inward budget enforcement, and why rate caps and total caps are both needed
- Why the usage-ledger schema is a control surface

**Next:** [`10-theory.md`](10-theory.md) — the cryptography, the policy algebra, and the
trade-offs.
