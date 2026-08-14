# Governance — the theory

Password hashing parameters, JWT structure and its attack surface, the RLS policy
algebra, and the isolation models we did not choose.

---

## 1. Password hashing: why slow and memory-hard

An attacker with your database wants to find `p` such that `H(p) = h`. Their cost is
(guesses) × (cost per guess). You control only the second factor.

**Fast hashes are catastrophic here.** SHA-256 on a modern GPU runs on the order of
$10^{10}$ hashes/second. A password from a 10-billion-entry wordlist falls in about a
second.

A **password hashing function** deliberately inflates the per-guess cost along two axes:

- **Time cost** — iterations. Raises cost linearly for both you and the attacker.
- **Memory cost** — working memory per hash. This is the important one. GPUs and ASICs
  win on parallel *compute*; they have comparatively little memory per core. A function
  that needs 64 MiB per hash cannot be run 10,000-way parallel on a GPU.

**Argon2** (Biryukov, Dinu & Khovratovich; Password Hashing Competition winner, 2015)
has three variants:

| Variant | Property | Use |
|---|---|---|
| Argon2d | Data-dependent memory access — maximally GPU-hostile, but leaks timing | Not for password hashing on shared hardware |
| Argon2i | Data-independent access — side-channel resistant, weaker against GPUs | Key derivation |
| **Argon2id** | Data-independent first pass, data-dependent after | **The recommended default** |

Argon2id is the current OWASP recommendation, and it is what Aegis uses.

**Self-describing hashes.** An Argon2 encoded hash looks like:

```
$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>
```

Variant, version, memory cost, time cost, parallelism, salt, digest — all in one string.
Two consequences worth stating: you store **one column**, and you can raise the work
factor for new passwords without a schema change (old hashes verify with their own
recorded parameters).

**Salting** is per-hash and automatic. It exists so that identical passwords produce
different hashes, which defeats precomputed rainbow tables and stops "these 4,000 users
share a password" being visible in the dump.

**Verification must be constant-time**, or timing differences leak how much of the digest
matched. The library handles this; you must not hand-roll `==` on digests.

### The fail-closed rule

`verify_password(p, None)` — a user with no password set — must return `False`, never
raise and never accidentally pass. The general principle: **an absent credential is a
failed authentication, not an error condition to be handled elsewhere.**

---

## 2. JWT: structure, and what actually protects you

A JWT is three base64url segments joined by dots:

```
header.payload.signature
```

**Header** — `{"alg":"HS256","typ":"JWT"}`.
**Payload** — the claims. Registered ones (`sub`, `exp`, `iat`, `iss`, `aud`) plus
whatever you add.
**Signature** — `HMAC-SHA256(base64(header) + "." + base64(payload), secret)`.

**The payload is encoded, not encrypted.** Anyone holding the token can read every claim.
Never put a secret in a JWT.

What the signature buys you is **integrity**: a tampered payload produces a signature
mismatch. That is the entire security model, and it means the secret is everything.

### HS256 vs RS256

**HS256** is symmetric: one secret both signs and verifies. Simple; anyone who can verify
can also forge. Fine for a single service that issues its own tokens.

**RS256** is asymmetric: a private key signs, a public key verifies. Services can verify
without being able to mint. Necessary when verification happens somewhere you would not
trust with issuance.

Aegis uses HS256 because one service issues and verifies. It is the right call *and* it
concentrates all the risk in one secret.

### The classic attacks

**`alg: none`.** Early libraries honoured a header claiming no algorithm and skipped
verification. The defence is to pass the accepted algorithms **explicitly** at decode
time and never trust the header's own claim.

**Algorithm confusion.** With an RS256 public key in hand, an attacker changes `alg` to
HS256 and signs with the public key as the HMAC secret. A library that picks the
algorithm from the header will verify it. Same defence: pin the algorithm list.

**A weak secret.** HMAC with a short or guessable secret is brute-forceable offline —
the attacker needs no server interaction, just one token. A framework's documented
default secret is not a weak secret; it is a **published** one.

That is why the strongest control here is a **startup guard**: a non-dev deployment must
refuse to boot on a default or too-short signing secret. A runtime warning is a warning;
a refusal to start is a control.

### Revocation

You cannot un-issue a JWT. The options are all bad in different ways:

- **Short lifetimes + refresh tokens.** Standard. Adds a refresh endpoint and state.
- **A denylist.** Reintroduces the per-request lookup you chose JWTs to avoid.
- **Rotate the secret.** Invalidates every token at once. A blunt instrument that works.

Aegis takes bounded lifetimes. Know the trade so you can state it: *"a compromised token
is valid until it expires; we bound the window rather than pay for per-request state."*

---

## 3. RLS: the policy algebra

Postgres RLS attaches predicates to a table:

```sql
ALTER TABLE t ENABLE ROW LEVEL SECURITY;
ALTER TABLE t FORCE  ROW LEVEL SECURITY;
CREATE POLICY p ON t USING (<visibility predicate>);
```

- `USING` is applied to **reads** and to the rows an UPDATE/DELETE may *see*.
- `WITH CHECK` is applied to **rows being written**. If omitted, Postgres reuses `USING`
  — so under a bound tenant scope, an INSERT stamping a different tenant is **rejected by
  the database**, not merely hidden. That is a free and valuable property.
- Multiple policies on a table are combined with `OR` (permissive) by default;
  `RESTRICTIVE` policies are ANDed.

### The owner exemption

The rule, stated precisely: **a table's owner bypasses its own RLS policies unless
`FORCE ROW LEVEL SECURITY` is set.**

The reason this is such a good trap is the deployment shape. Your application runs
`create_all` at bootstrap, so it *is* the owner, and it uses that same engine to serve
every request. `ENABLE` alone therefore protects you from exactly nobody, while
`pg_policies` shows a healthy row and a code review shows a policy.

Two ways out. `FORCE` — one statement, no infrastructure change. Or connect as a
non-owning role with only DML rights, which is the more classically correct answer and
requires provisioning a second role and splitting migration from serving. Aegis uses
`FORCE`.

### Binding the tenant, and the parameterisation trap

The tenant travels to the policy through a **GUC** (Grand Unified Configuration
variable) — a custom setting like `app.tenant_id` readable in SQL via
`current_setting('app.tenant_id', true)`. The `true` means "return NULL if unset rather
than erroring."

You would write:

```sql
SET app.tenant_id = :tid
```

and it does not work. **`SET` takes a literal, not a bind parameter.** Sent over the
extended query protocol with a placeholder, Postgres raises
`syntax error at or near "$1"`.

You cannot solve it by interpolating the value into the string — that is SQL injection in
the one place you least want it.

The correct form is the function call:

```sql
SELECT set_config('app.tenant_id', :tid, true)
```

`set_config` is an ordinary function, so it parameterises properly. And the third
argument is `is_local`: `true` scopes the setting to the **current transaction**, so it
is discarded on commit or rollback.

That third argument is the pooling fix. A session-scoped `SET` lives as long as the
connection; a pool then hands that connection, still carrying tenant A's scope, to a
request for tenant B.

### Writing a predicate that cannot raise

The policy has to handle an unset or malformed GUC. `current_setting('app.tenant_id',
true)` returns `NULL` when unset and `''` if something wrote an empty string.

`''::int` **raises**. And you cannot protect it with an `OR` guard, because SQL gives no
evaluation-order guarantee — the planner may evaluate the cast first.

The robust construction extracts digits with a regex before casting:

```sql
substring(current_setting('app.tenant_id', true) from '^[0-9]+$')
```

This yields the id, or SQL `NULL` for anything that is not purely digits. It cannot
raise, whatever is in the variable.

### The unbound branch: a deliberate, documented hole

Given the extracted scope `s`, the predicate is:

```
s IS NULL  OR  tenant_id = s::int
```

Read the two branches:

- **`s` is a number** → a row is visible only if its `tenant_id` matches. This is the
  isolation the policy exists for.
- **`s` is NULL** (unset, or the empty string written for an unscoped request) → the
  policy **does not restrict**.

The second branch is a hole, and it is deliberate. Two paths need it: the **login
lookup**, which reads `users` by username *before* any tenant is known, and the
**platform-admin listings**, which legitimately span tenants. Under a fail-closed unset
branch with `FORCE` on, both would return zero rows — including login, which would lock
everyone out.

Closing it properly requires the authentication path to bind a scope before it reads, and
that lives in the host, not in the RLS module.

**Be precise about what this is.** It is not "RLS does not work." It is: *any request
that binds a numeric scope is strictly enforced; a request that binds none is not
restricted; and this is strictly more enforcement than before FORCE, when the policy was
inert for the owning role in every case.* That is a defensible position and an honest
one. Claiming fail-closed isolation would not be.

---

## 4. Budgets: resolution and window semantics

**Nearest-binding resolution.** With `None` meaning uncapped:

$$\text{effective}(c_u, c_t) = \min\{c \in \{c_u, c_t\} : c \ne \text{None}\}$$

and `None` when both are absent. A present cap always binds over an absent one; two
present caps resolve to the minimum. Same semantics as filesystem quotas.

**Rolling windows.** "100k tokens per day" is a sum over the last 86,400 seconds, not
since midnight. No reset cliff to game, at the cost of a range scan per check. The
implementation needs an index on `(scope_id, ts)`.

**Rate windows** are the same query over 60 seconds.

**Attribution order.** Check the user's caps before the tenant's, so an error names the
narrowest scope that was breached. Same refusal, better diagnosis.

**Why token and USD caps are not redundant.** Token caps are stable and predictable —
they do not move when a price changes. USD caps are what finance actually cares about,
and they are the *only* caps that bind non-token charges: an audio minute is not a token,
so a token cap correctly ignores it while the USD cap catches it, provided the per-minute
charge was priced into the ledger row.

---

## 5. The natural-key collision that a global key creates

Budget rows are keyed by `(scope_type, scope_id, window)` — e.g.
`("user", 42, "day")`.

That triple is **global**. It contains no tenant. So two different tenants can
legitimately produce the same triple, and an upsert that matches on the natural key alone
will find the *other* tenant's row.

Three ways out, and the choice is instructive:

1. **Add tenant to the key.** Cleanest, but changes the identity of a row that already
   exists and has to be migrated.
2. **Narrow the lookup to the tenant.** Looks right, and is wrong: when a conflicting row
   exists, the narrowed lookup finds nothing and **inserts a second row** for the same
   scope+window. The enforcement reader then picks between duplicates arbitrarily. You
   have converted a takeover into non-determinism.
3. **Keep the full natural-key lookup, then check ownership and refuse.** Detects the
   collision and fails loudly.

Aegis takes (3). The reasoning is worth remembering: **when a uniqueness assumption is
violated, detect and refuse — do not silently create a second row and hope the reader
picks the right one.**

---

## 6. Migrations without a migration tool

`create_all` is `CREATE TABLE IF NOT EXISTS`. It creates a table once and never touches
it again. Add a column to the model and every existing database keeps the old shape.

A project with no Alembic needs *something*, and the design space is:

| Approach | Safety | Cost |
|---|---|---|
| Full migration tool | Handles everything | A dependency, a versions directory, discipline |
| Additive reconciliation at boot | Only ADD COLUMN | Small, idempotent, cannot destroy data |
| Nothing | — | Silent drift |

Additive reconciliation is defensible when the properties are stated and enforced:

- **Additive only** — never drop, rename, retype or reorder. It cannot destroy data, so
  it is safe to run on every boot.
- **Idempotent** — plan from `information_schema`; a second run finds nothing. Emit
  `IF NOT EXISTS` so two processes racing at startup cannot collide.
- **Loud** — log every added column; **raise** on drift that cannot be added safely.

That last point is the design decision. A `NOT NULL` column with no server default has no
correct value for existing rows — only a human can decide one. Skipping it silently
leaves the table unwritable. **Refusing to boot is the correct outcome** when the table
in question is the one a spend cap is computed from.

---

## What you should now be able to explain

- Why memory-hardness, not just slowness, is what defeats GPU cracking
- The three Argon2 variants and why Argon2id is the default
- JWT structure, why the payload is readable, and what the signature actually guarantees
- `alg:none` and algorithm confusion, and the one-line defence against both
- Why a startup guard on the signing secret beats a runtime warning
- `USING` vs `WITH CHECK`, and what you get free by omitting `WITH CHECK`
- The owner exemption, and the two ways to close it
- Why `SET` cannot be parameterised and `set_config` can, and what `is_local` fixes
- Why the RLS predicate extracts digits before casting
- The unbound branch, why it exists, and how to describe it honestly
- Nearest-binding cap resolution and rolling-window semantics
- Why narrowing a colliding natural-key lookup makes things worse
- What an additive schema reconciler may and may not do, and why it must refuse to boot

**Next:** [`20-in-aegis.md`](20-in-aegis.md) — the exact implementation.
