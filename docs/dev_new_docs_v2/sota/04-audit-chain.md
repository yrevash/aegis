# SOTA-04 — A tamper-evident audit chain

> **Status: PLAN. Nothing here is implemented.** Written 2026-08-27.
>
> **Evidence marks, as `phase-11-langflow.md` uses them.** `[SOURCE path:line]` — read in this
> repository at that line, on this branch, today. `[MEASURED]` — a command was run on this
> checkout and this is its output. `[DOC url]` — vendor or standards-body documentation,
> fetched. Where this document asserts something none of those establish, **it says so in the
> same sentence.** Nothing here was executed against a running Aegis or a live PostgreSQL; every
> throughput, contention and latency statement below is an argument from the code, not a
> measurement, and each one says so.

---

## What this is, in one paragraph

`audit_log` carries `id`, `ts`, `action`, `actor`, `model`, `trace_id`, `payload`, `approved_by`
and `tenant_id` `[SOURCE aegis/src/aegis/governance/models.py:240-253]` and **no integrity
column of any kind** — no `row_hash`, no `prev_hash`, no signature. The database privileges do
real work: the serving role holds `SELECT, INSERT` on `audit_log`, `run_events` and
`usage_ledger` and has `UPDATE, DELETE` revoked `[SOURCE aegis/src/aegis/governance/rls.py:1257-1270]`,
so `DELETE FROM audit_log` on a request connection is refused. But the architecture document
concedes the rest of it in its own words: *"The owner role can still rewrite the trail —
tampering requires that connection, it is not impossible"* `[SOURCE
docs/architecture/system-architecture.md:215]`. Meanwhile the console tells a reader the trail is
`"GET /audit · Postgres, append-only"` `[SOURCE web/src/components/admin/AuditLog.tsx:216]` and
`docs/security/owasp-agentic.md:38` calls it an "**Immutable** audit log". This plan closes the
gap between those sentences and the schema: each row carries `H(prev_hash ‖ canonical_row)`, a
new `GET /v1/audit/verify` walks the chain and names the first break, and — because you cannot
retroactively prove that history nobody hashed was never edited — the rows that predate the
chain are marked as exactly that and never counted as verified.

---

## What is actually there today, verified

| Claim | Evidence |
|---|---|
| `AuditLog` has nine columns and no integrity field | `[SOURCE aegis/src/aegis/governance/models.py:222-253]` |
| `id` is a database-assigned serial; `ts` is a database-assigned `server_default=func.now()` | `[SOURCE :240, :247]` |
| `ts` is `TIMESTAMP WITHOUT TIME ZONE` holding UTC, and the reader normalises it | `_naive_utc`'s docstring: *"`ts` is `TIMESTAMP WITHOUT TIME ZONE` holding UTC, so comparing it against an aware bound raises on PostgreSQL"* `[SOURCE aegis/src/aegis/governance/audit.py:178-185]` |
| `payload` is `jsonb` on Postgres, JSON on SQLite | `mapped_column(JsonB, default=dict)` `[SOURCE aegis/src/aegis/governance/models.py:252]`, decorator documented at `[SOURCE :3-8]` |
| The writer opens its own short-lived session and commits per row | `record_audit` `[SOURCE aegis/src/aegis/governance/audit.py:76-114]` |
| Audit writes at the API edge are **best-effort and swallowed** | `_safe_audit` catches broadly: *"audit is best-effort at the edge"* `[SOURCE backend/src/app/api/routes.py:1270]` |
| The trail is read newest-first, tenant-scoped, with every filter in SQL | `list_recent_audit` `[SOURCE aegis/src/aegis/governance/audit.py:195-290]`, exposed as `GET /audit` `[SOURCE backend/src/app/api/routes.py:2019-2085]`, capped at 200 rows `[SOURCE :2016]` |
| Three tables are privilege-enforced append-only, partitions included | `_APPEND_ONLY_TABLES = ("audit_log", "run_events", "usage_ledger")` `[SOURCE aegis/src/aegis/governance/rls.py:1257-1261]`; the partition walk is at `[SOURCE :1272-1300]` |
| `run_events` is partitioned by range on `ts`, with a composite primary key `(id, ts)` | `[SOURCE aegis/src/aegis/runs/models.py:87-130]` |
| **There is no migration tool.** | `find . -name alembic.ini -o -type d -name migrations` returns nothing outside `node_modules`/`.venv` `[MEASURED]`, and `backend/pyproject.toml:36-38` states the choice deliberately |
| The schema is materialised by `create_all` plus two additive reconcilers | `reconcile_additive_columns` `[SOURCE aegis/src/aegis/governance/schema.py:190-273]` and `reconcile_enum_values` `[SOURCE :~280+]`, run from `[SOURCE backend/src/app/data/session.py:980]` |
| The additive reconciler **does** create an index declared on a newly added column | *"an index declared on a newly added column is created alongside it, so an added column is never left half-installed"* `[SOURCE aegis/src/aegis/governance/schema.py:34-37]`, implemented at `[SOURCE :266-268]` |
| It does **not** install indexes on pre-existing columns | `[SOURCE aegis/src/aegis/governance/models.py:233-237]` |

---

## Prior art

Microsoft shipped the **Agent Governance Toolkit** on 2 April 2026 under MIT
`[DOC https://opensource.microsoft.com/blog/2026/04/02/introducing-the-agent-governance-toolkit-open-source-runtime-security-for-ai-agents/]`.
Its audit records are **Merkle-chained — each entry hashes its predecessor with SHA-256 — and are
offline-verifiable** `[DOC ibid.]`, alongside cryptographically signed delegation chains. Two
things to take from it and one not to:

* **Take:** SHA-256, predecessor-hashing, and *offline* verifiability — the verifier must not
  need the writer's cooperation.
* **Take:** the word "anchored". A hash chain proves nothing on its own if the attacker can
  rewrite the whole chain; what makes it evidence is an anchor the attacker cannot reach. Design
  for one (task A9) even if we do not ship it before the demo.
* **Do not take:** their signing story. Aegis has no key-management story and inventing one for a
  hackathon demo would produce a key on disk next to the database, which proves nothing. **Say
  the chain is unsigned.** An unsigned chain detects *edits*; it does not detect a wholesale
  rewrite by someone with the owner connection and the hashing code. That is a real and stateable
  improvement over today, and overclaiming it would be worse than not shipping it.

---

## The design

### The two columns

```python
    #: SHA-256 over this row's canonical serialisation, prefixed by the previous row's
    #: ``row_hash``. NULL means this row predates the chain — see the genesis marker.
    row_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    #: The ``row_hash`` of this row's predecessor in this tenant's chain. NULL on the
    #: genesis row and on every pre-chain row.
    prev_hash: Mapped[str | None] = mapped_column(String(64), default=None)
```

`String(64)` — lowercase hex SHA-256. Both nullable, both without a server default, so
`reconcile_additive_columns` can install them additively rather than refusing
`[SOURCE aegis/src/aegis/governance/schema.py:75-85]`.

### Per-tenant chains, not one global chain

`GET /audit` is tenant-scoped and always has been `[SOURCE aegis/src/aegis/governance/audit.py:230-244]`.
A single global chain would mean a tenant admin cannot verify their own trail without being handed
rows belonging to other tenants — the verifier would need the whole table. Per-tenant chains make
`GET /audit/verify` answerable inside the caller's existing scope, and they cut write contention
by the number of active tenants. Platform-scoped rows (`tenant_id IS NULL` — a job, an ingest
pass, a platform probe, per the `usage_ledger` note at `[SOURCE aegis/src/aegis/governance/models.py:202-206]`)
form their own chain, keyed on the SQL-null tenant.

### Preventing forks with a constraint, not a lock alone

```python
    __table_args__ = (
        Index("ix_audit_log_tenant_ts", "tenant_id", text("ts DESC")),
        Index("ux_audit_log_chain", "tenant_id", "prev_hash", unique=True),
    )
```

Two rows in one tenant's chain cannot both claim the same predecessor: a fork becomes a unique
violation at insert time rather than a silent branch discovered months later by a verifier.
PostgreSQL treats NULLs as distinct in a unique index by default, so the pre-chain rows (all
`prev_hash IS NULL`) coexist freely — which is exactly what we want, and is the reason not to
reach for `NULLS NOT DISTINCT`.

Because this index is declared on **newly added columns**, `reconcile_additive_columns` installs
it `[SOURCE aegis/src/aegis/governance/schema.py:266-268]`. `ix_audit_log_tenant_ts` is declared
on pre-existing columns and the reconciler deliberately will not touch it — the model already
warns about this `[SOURCE aegis/src/aegis/governance/models.py:233-237]` — so nothing about the
existing index changes.

### The write, and the lock

`record_audit` gains a chain step inside its existing session `[SOURCE
aegis/src/aegis/governance/audit.py:104-114]`:

```
BEGIN
  SELECT pg_advisory_xact_lock(_AUDIT_CHAIN_LOCK_NS, COALESCE(tenant_id, 0))
  SELECT row_hash FROM audit_log
   WHERE tenant_id IS NOT DISTINCT FROM :tenant AND row_hash IS NOT NULL
   ORDER BY id DESC LIMIT 1
  INSERT ... (row_hash = H(prev ‖ canonical), prev_hash = prev)
COMMIT   -- lock released here
```

`pg_advisory_xact_lock` rather than a row lock on a head table: it needs no extra table, and it
releases at commit whether that commit succeeds or fails. Contention is per tenant, and it
serialises only audit writers — never a `/query`, never the gateway.

**The failure mode this creates, and the mitigation.** `_safe_audit` swallows exceptions
`[SOURCE backend/src/app/api/routes.py:1266-1271]`, so a lock wait that times out would today
turn into a *silently missing audit row* — worse than an unchained one. So: set a bounded
`lock_timeout` on the chain step, and on timeout **write the row with `row_hash = NULL,
prev_hash = NULL` and log at WARNING**. An unchained row is visible to the verifier as a gap it
names; a missing row is invisible forever. Losing evidence to protect a hash chain is the wrong
trade and the code must not be able to make it.

---

## Canonical serialisation — the subtle part

A hash is only evidence if the verifier reconstructs byte-for-byte what the writer hashed. Five
things in this schema break that, and each needs a decision.

### 1. `id` and `ts` are assigned by the database *after* the app has to hash

`id` is a serial and `ts` is `server_default=func.now()` `[SOURCE
aegis/src/aegis/governance/models.py:240, :247]`. Neither value exists at the moment the app
computes the hash, and neither can be patched in afterwards because **`UPDATE` is revoked on this
table** `[SOURCE aegis/src/aegis/governance/rls.py:1270]`.

**Decision:** `id` is **excluded** from the canonical form — it carries no evidentiary content
and is a database implementation detail. `ts` is **included**, and `record_audit` must therefore
**supply it explicitly** from `datetime.now(UTC).replace(tzinfo=None)` instead of leaving it to
`func.now()`.

*The honest cost:* `ts` stops meaning "the database's clock" and starts meaning "the writing
process's clock". On a single-host deployment that is a distinction without a difference; on a
multi-host one it means audit timestamps can go backwards between hosts. The chain itself does
not care — its order is `prev_hash`, not `ts` — but `GET /audit`'s `ORDER BY ts DESC, id DESC`
`[SOURCE aegis/src/aegis/governance/audit.py:246-250]` does. Write this down in the model
docstring; do not let it be rediscovered.

### 2. Timestamp precision

Python `datetime` and PostgreSQL `timestamp` both carry microseconds. Serialise `ts` as
`strftime('%Y-%m-%dT%H:%M:%S.%f')` — **always six fractional digits, never a trailing-zero trim**,
never `isoformat()`, which drops the fractional part entirely when it is zero. A timestamp landing
exactly on a whole second would otherwise hash differently on the writer and the verifier, and
that bug reproduces roughly once in a million rows, which is the worst possible frequency.

### 3. NULL versus empty string

`actor`, `model`, `trace_id` and `approved_by` are all `str | None` `[SOURCE
aegis/src/aegis/governance/models.py:249-253]`. `None` and `""` must not serialise identically,
or an attacker can blank a field without breaking the hash. **Use length-prefixed framing** rather
than a delimiter:

```
field := "-"                      when the value is NULL
       | str(len(utf8)) ":" utf8  otherwise
```

This also removes the delimiter-injection problem — an `action` containing the separator cannot
impersonate a field boundary — which a `"|".join(...)` cannot promise.

### 4. `payload` is `jsonb`, and `jsonb` is not a byte-preserving store

This is the deepest trap in the plan. PostgreSQL's `jsonb` **does not round-trip the text it was
given**: it discards key order, drops duplicate keys keeping the last, and normalises numeric
formatting. So `H(what the app sent)` and `H(what the verifier reads back)` are not the same
function of the same data unless the app writes a form that is already a `jsonb` fixed point.

**Decision, in two parts:**

1. Canonicalise the payload with **RFC 8785 JSON Canonicalisation Scheme** (sorted keys, no
   insignificant whitespace, ECMAScript number formatting) at the *writer*, and hash that string.
2. **Normalise before storing**: round-trip the dict through JCS and back (`json.loads(jcs(d))`)
   and store *that* dict. Now the value in the column is already canonical, so the verifier's
   `jcs(row.payload)` reproduces the writer's bytes.

Step 2 is the one that is easy to skip and fatal to skip. Without it, a payload containing
`{"cost": 1.0}` is stored by `jsonb` as `1.0` (jsonb preserves the numeric text of a `numeric`),
read back into Python as `1.0`, and re-canonicalised by JCS as `1` — a spurious verification
failure on a row nobody touched. **A verifier that cries wolf is a verifier that gets turned off.**

*Not verified:* I did not run a `jsonb` round-trip against PostgreSQL 16/17 to enumerate every
normalisation it applies. Task A3's first test is exactly that experiment, and if it finds a case
JCS-normalisation does not fix, the fallback is a separate `payload_canonical text` column that
is hashed and never queried — costlier in bytes, immune to the whole class.

### 5. Field order

Fixed and explicit, declared as a module constant, never derived from `__table__.columns` (whose
order changes when someone adds a column) and never from a dict. Include a **version tag** as the
first field:

```
canonical := "aegis-audit-v1" ‖ ts ‖ tenant_id ‖ action ‖ actor ‖ model ‖ trace_id ‖ approved_by ‖ jcs(payload)
row_hash  := sha256( (prev_hash or "") ‖ canonical ).hexdigest()
```

The version tag is what lets a future column be added without invalidating every existing row:
`v2` rows hash under the `v2` field list, and the verifier dispatches on the tag it finds. Without
it, the first schema change silently breaks the entire history, and the only honest response at
that point would be a second genesis marker.

---

## Backfill, honestly: you cannot prove history nobody hashed

**The rule: existing rows get `row_hash = NULL` and `prev_hash = NULL`, and nothing else.**

Computing hashes over the rows already in the table and calling the result a chain would be a
forgery. It would prove only that the rows are self-consistent *as of the moment the backfill
ran* — which is precisely the moment an attacker who had already edited them would want the
backfill to run. A chain whose genesis is "whatever was in the table when we turned this on"
attests to nothing about anything before that instant, and presenting it as though it did is the
kind of claim this repository refuses everywhere else.

**The genesis marker** is a real `audit_log` row, written once by bootstrap when it first observes
`row_hash IS NULL` for every row of a chain:

* `action = "audit.chain.genesis"`
* `payload = {"scheme": "aegis-audit-v1", "unchained_rows_before": <count>, "max_unchained_id": <id>, "started_at": <iso>}`
* `prev_hash = NULL`, `row_hash = H("" ‖ canonical)`

Everything at or below `max_unchained_id` is **unchained**; everything above it is chained.
`GET /audit/verify` reports both numbers and never folds them together. The sentence a jury hears
is: *"From this timestamp forward, any edit to any row is detectable. Before it, we make no claim,
and the endpoint says so."*

*Second-order honesty:* a hostile owner can delete the genesis row and re-run bootstrap to mint a
new one over an edited table. That is what the anchor in task A9 is for — publishing the head hash
somewhere the owner connection cannot reach. Until an anchor exists, **the chain detects edits by
anyone without the owner connection and the hashing code, and no more.** That is the sentence for
the security document.

---

## Should `usage_ledger` and `run_events` get the same treatment?

**`run_events` — no, not this way, and the reason is structural.** It is `PARTITIONED BY RANGE
(ts)` with a composite primary key `(id, ts)` `[SOURCE aegis/src/aegis/runs/models.py:87-130]`,
and PostgreSQL requires every unique constraint on a partitioned table to contain the partition
key. So the fork-preventing `UNIQUE (tenant_id, prev_hash)` becomes `UNIQUE (tenant_id,
prev_hash, ts)`, which does not prevent a fork across two partitions — the exact case a
month-boundary produces. Chaining it needs a per-partition chain with an explicit cross-partition
link, and that is a design, not a column. **Not in this plan.** `run_events` keeps its
privilege-level append-only protection `[SOURCE aegis/src/aegis/governance/rls.py:1257-1261,
:1272-1300]` and gets an epoch anchor instead (below).

**`usage_ledger` — no chain on the write path, and the reason is the money.** It is the
highest-volume table in the schema (the `run_id` column's own note calls it *"the largest table in
the schema"* `[SOURCE aegis/src/aegis/governance/models.py:220]`) and it is written from the
gateway on **every** model call, best-effort, with failures swallowed so that a logging problem
can never break a live call `[SOURCE aegis/src/aegis/governance/schema.py:9-13]`. Putting an
advisory lock and a `SELECT ... ORDER BY id DESC LIMIT 1` in front of that write serialises the
gateway per tenant. The budget caps are computed by summing these rows; anything that makes the
write slower or likelier to fail attacks the control it is supposed to protect.

**What both get instead: epoch anchors (task A9).** A periodic job computes a Merkle root over
every `usage_ledger` and `run_events` row in a closed time window and writes **one** row into
`audit_log` — which *is* chained — carrying the root, the window bounds and the row count. Cost:
one audit row per window. Property: a tenant's spend for a closed window cannot be altered without
breaking the audit chain that contains its root. This is the "Merkle-anchored" shape Microsoft's
toolkit uses `[DOC https://opensource.microsoft.com/blog/2026/04/02/introducing-the-agent-governance-toolkit-open-source-runtime-security-for-ai-agents/]`,
applied where the write path can afford it.

---

## Files to create and modify

| File | Change |
|---|---|
| `aegis/src/aegis/governance/models.py:222-253` | Two nullable `String(64)` columns; the `ux_audit_log_chain` unique index in `__table_args__` (`:238`); a docstring paragraph recording that `ts` is now app-assigned and why |
| **`aegis/src/aegis/governance/chain.py`** *(new)* | `CANONICAL_SCHEME = "aegis-audit-v1"`, `_FIELD_ORDER`, `canonical_bytes(row) -> bytes`, `row_hash(prev, row) -> str`, `jcs(obj) -> str`. **Pure, no SQLAlchemy, no I/O** — the verifier must be runnable offline against a CSV export, which is the property that makes it evidence |
| `aegis/src/aegis/governance/audit.py:76-114` | `record_audit` sets `ts` explicitly, takes the advisory lock, reads the head, computes both hashes, and degrades to `NULL/NULL` + WARNING on lock timeout |
| `aegis/src/aegis/governance/audit.py` *(new function)* | `async def verify_audit_chain(*, tenant_id, since, until, limit) -> ChainVerification` — walks ascending by `id`, recomputes, stops at the first mismatch and reports it. Bounded by `limit`; the console cannot ask it to walk ten million rows |
| `aegis/src/aegis/governance/types.py:196` | `AuditLogRow` gains `row_hash: str | None` and `chained: bool`; a new `ChainVerification` DTO beside it |
| `backend/src/app/api/routes.py` *(new route near `:2019`)* | `GET /audit/verify` |
| `backend/src/app/api/schemas.py:968` | `AuditVerifyResponse` beside `AuditLogResponse` |
| `backend/src/app/data/session.py:980` | After `reconcile_additive_columns`, call the genesis-marker installer |
| `web/src/components/admin/AuditLog.tsx:216` | The origin label currently reads `"GET /audit · Postgres, append-only"` — replace with the verified state, or with `"unchained (pre-chain)"` for rows below the genesis id. **This label is a claim; it must track the endpoint** |
| `web/src/lib/api/client.ts` / `generated/schema.d.ts` | New endpoint types; regenerate from `backend/openapi.json` |
| `docs/architecture/system-architecture.md:215` | The concession stays — it is still true — but gains the chain and its limits |
| `docs/security/owasp-agentic.md:38` | "Immutable audit log" becomes "append-only by privilege, tamper-**evident** by hash chain from \<genesis\>" |

### Migration

There is no Alembic `[MEASURED; backend/pyproject.toml:36-38]`, so:

1. **`create_all`** materialises the two columns and the unique index on a fresh database.
2. **`reconcile_additive_columns`** installs them on an existing one: both are nullable with no
   server default, so they are `addable` and not `unsafe` `[SOURCE
   aegis/src/aegis/governance/schema.py:75-85]`, and the index is created because it is declared
   on newly added columns `[SOURCE :34-37, :266-268]`.
3. **The genesis installer** runs after both, idempotently: it does nothing if a chain already
   has a genesis row.

No enum changes, no type changes, no back-fill decision for the reconciler to refuse.

---

## Tasks, in dependency order

* **A0 — `chain.py`.** Pure functions and their tests. Nothing touches the database. This is the
  whole security argument in one file, and it should be reviewable in isolation.
* **A1 — The `jsonb` round-trip experiment.** Write every payload shape the codebase actually
  produces into a real PostgreSQL 16 `jsonb` column, read it back, and assert
  `jcs(read_back) == jcs(written)` after the normalisation step. **This gates the design.** If it
  fails on a shape JCS-normalisation cannot fix, switch to the `payload_canonical text` column
  before writing any other code.
* **A2 — The columns + the index**, and a test that `plan_additive_columns` classifies both as
  addable against a schema that lacks them.
* **A3 — `record_audit`**: explicit `ts`, advisory lock, head read, both hashes, timeout
  degradation.
* **A4 — The genesis installer**, wired into bootstrap, idempotent.
* **A5 — `verify_audit_chain` + `GET /audit/verify`**, tenant-scoped through `_scope_tenant`
  exactly like `GET /audit` `[SOURCE backend/src/app/api/routes.py:2073]`.
* **A6 — The console**: verification badge, and the honest label for pre-chain rows.
* **A7 — The concurrency test** (below). This is the one that decides whether the design is real.
* **A8 — The document corrections** (`system-architecture.md:215`, `owasp-agentic.md:38`).
* **A9 — *Optional, post-demo.*** Epoch Merkle anchors for `usage_ledger` and `run_events`, and an
  external anchor for the head hash. **Nothing in A0–A8 may depend on A9.**

---

## VERIFICATION SECTION

*Everything here is a specification of what must be run. None of it has been run.*

### The endpoints, with payloads

`TOKEN` is a platform-admin bearer; `API=http://127.0.0.1:8000/v1`.

**1. A fresh row is chained.**

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$API/audit?limit=1" | jq '.rows[0] | {id, action, row_hash, chained}'
```

Expect `chained: true` and a 64-character lowercase hex `row_hash`. A `null` here on a row written
after bootstrap means the lock-timeout degradation fired — check the WARNING log rather than
assuming the feature works.

**2. The chain verifies.**

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$API/audit/verify?limit=5000" | jq
```

Expected `200`:

```json
{
  "scheme": "aegis-audit-v1",
  "tenant_id": null,
  "ok": true,
  "checked": 4188,
  "unchained_before_genesis": 12704,
  "genesis": {"id": 12705, "ts": "2026-08-27T09:14:02.114523"},
  "first_break": null,
  "head": {"id": 16892, "row_hash": "9f2c…"},
  "truncated": false
}
```

The two counts are **never summed**. `unchained_before_genesis` is not evidence of anything and
the field name has to say so.

**3. A tamper is detected, and the *first* break is named.** With the owner connection — the one
`system-architecture.md:215` concedes exists:

```sql
UPDATE audit_log SET approved_by = 'alice' WHERE id = 13001;
```

Then re-run the verify call. Expect `200` with:

```json
{"ok": false,
 "first_break": {"id": 13001, "reason": "row_hash mismatch",
                 "expected": "4a1b…", "found": "c07e…"},
 "checked": 297}
```

`checked` is the count *up to* the break — the walk stops there, because every row after a break
is unverifiable, and reporting 4,000 further "failures" downstream of one edit tells a reader
nothing and buries the finding.

**4. A deletion is detected too, and by a different mechanism.**
`DELETE FROM audit_log WHERE id = 13500;` (owner connection). The *next* row's `prev_hash` now
names a row that is gone. Expect `first_break.reason == "prev_hash names a row not in this chain"`.
**Assert this separately from case 3**: an edit and a deletion are different attacks and a
verifier that reports them identically is one a reader cannot act on.

**5. The blank-versus-null forgery fails.**
`UPDATE audit_log SET approved_by = '' WHERE approved_by IS NULL AND id = 13002;`
Expect a break. This is the specific assertion that the length-prefixed framing is doing its job;
a `"|".join` serialisation passes this test wrongly.

**6. Scope: a tenant admin verifies their own chain and cannot reach another's.**

```bash
curl -s -H "Authorization: Bearer $TOKEN_TENANT_A" "$API/audit/verify?tenant_id=2"
```

Expect `403` whether tenant 2 exists or not — the same rule `GET /audit` already holds
`[SOURCE backend/src/app/api/routes.py:2050-2057]`, so a verify call cannot become an existence
oracle. With no `tenant_id`, a tenant admin verifies their own chain only.

**7. Offline verification.** Export the trail via `GET /reports/audit.csv` and run the verifier as
a script against the CSV, with no database and no running Aegis. **It must produce the same
answer.** If it cannot, `chain.py` has a hidden dependency and the "offline-verifiable" claim is
false.

### The tests, and where they go

| File | What it asserts |
|---|---|
| `aegis/tests/governance/test_audit_chain_canonical.py` **(new)** | `None` and `""` hash differently; a `ts` on a whole second serialises with six fractional digits; an `action` containing the framing characters cannot impersonate a field boundary; two payloads differing only in key order hash **identically**; two differing in a value hash **differently**; the `v1` tag is the first thing in the preimage |
| `aegis/tests/governance/test_audit_chain_jsonb_roundtrip.py` **(new, PostgreSQL-only)** | write → read → re-canonicalise is a fixed point for every payload shape the repo produces, including floats, big integers, nested nulls, unicode and an empty dict. **Must not skip when PostgreSQL is absent** — `AEGIS_REQUIRE_PG_TESTS=1` is already set in CI for exactly this reason `[SOURCE .github/workflows/ci.yml:95-97]` |
| `aegis/tests/governance/test_audit_chain_walk.py` **(new)** | a clean chain verifies; a mutated row is reported with its id; a deleted row is reported with the *other* reason; the walk **stops at the first break** |
| `aegis/tests/governance/test_audit_chain_concurrency.py` **(new, PostgreSQL-only)** | 50 concurrent `record_audit` calls across 3 tenants produce 50 rows, 3 unbroken chains, **zero forks**, and the whole set verifies. Then: force a lock timeout and assert the row is still written with `row_hash IS NULL` and a WARNING — *the evidence-over-integrity trade, tested* |
| `aegis/tests/governance/test_audit_genesis.py` **(new)** | the installer is idempotent; running it twice mints one marker; pre-existing rows keep `row_hash IS NULL`; the verify response never sums the two counts |
| `backend/tests/api/test_audit_verify_endpoint.py` **(new)** | 200 shape; a tenant admin naming another tenant gets 403; `truncated: true` when `limit` is hit; `require_admin_or_devops` matches `GET /audit`'s posture |
| `backend/tests/api/test_audit_filters.py` *(existing, must stay green)* | every existing filter still works with the new columns and the app-assigned `ts` `[SOURCE backend/tests/api/test_audit_filters.py]` |
| `backend/tests/data/test_additive_reconcile.py` *(extend)* | `plan_additive_columns` returns both new columns as addable, and `_indexes_for` includes `ux_audit_log_chain` |

### Frontend surfaces that must change

* `web/src/components/admin/AuditLog.tsx:216` — the `origin="GET /audit · Postgres, append-only"`
  label. **This is the highest-priority frontend change in the plan**, because it is the sentence
  a jury reads, and it currently asserts a property the schema does not have.
* A verification affordance on the same view: a "Verify chain" action calling `GET /audit/verify`
  and rendering `ok`, `checked`, `unchained_before_genesis` and any `first_break`. Rows below the
  genesis id render as "unchained (predates the chain)", **not** as a warning icon — they are not
  a finding, they are an absence of evidence, and the two must not look the same.
* `web/src/components/audit/AuditInsights.tsx` and `insights.ts` describe the rows in hand
  `[SOURCE web/src/components/audit/insights.ts:9]`; the verification result is a property of the
  *chain*, not of the page, so it must not be rendered inside the insights block.
* `backend/openapi.json` regenerated (`scripts/build_openapi.py --check` is a CI gate
  `[SOURCE .github/workflows/ci.yml:130-131]`), then `npx tsc --noEmit` in `web/`.

---

## Risks, stated plainly

1. **An unsigned chain does not stop the owner connection.** It detects edits by anyone who does
   not also hold the owner connection *and* the hashing code. That is the true claim; the false
   one — "the trail is now tamper-proof" — must not appear in any document, and
   `system-architecture.md:215`'s existing concession must survive this change rather than be
   deleted by it.
2. **`ts` moves from the database clock to the app clock.** Necessary (§Canonical/1) and a real
   behavioural change to a column that four filters, one index and the console's ordering depend
   on. Multi-host deployments can now see `ts` go backwards.
3. **The advisory lock is on the audit write path, and audit writes are swallowed at the edge.**
   The degradation is designed (write unchained, log loudly) but it means a contended tenant
   produces a chain with gaps, and a verifier must treat a gap as "not evidence" rather than as
   "tampered". Getting that distinction wrong in the UI turns a benign lock timeout into an
   incident.
4. **The `jsonb` normalisation may not be a fixed point.** A1 gates the design for this reason. If
   it fails, the fallback (`payload_canonical text`) costs storage and a second copy of every
   payload.
5. **`reconcile_additive_columns` installing the unique index is a documented behaviour I read but
   did not run** `[SOURCE aegis/src/aegis/governance/schema.py:266-268]`. If it does not fire on
   the target database, forks become possible and silent. A boot-time assertion that the index
   exists is cheap.
6. **A schema change after v1 needs a v2 tag or it breaks the whole history.** The version tag
   makes this survivable; forgetting it makes the first added column a chain-wide break.

### Abandonment criteria

* **A1 fails and the `payload_canonical` fallback is also unacceptable.** Then this design cannot
  produce a verifier that does not cry wolf — abandon it, and ship *only* the honest documentation
  correction (A8), which is genuinely worth shipping alone.
* **A7 shows fork-free concurrent inserts are not achievable** at the write rate the demo
  produces. Then the write-path chain is wrong and the whole design should move to epoch anchors
  (A9) over an unchained table.
* **Under three days remain with A0–A5 not green.** Ship A8 alone. A corrected sentence in the
  security document is worth more than a half-built chain the verifier cannot walk, and a
  `GET /audit/verify` that returns `ok: true` because it checked nothing is the single worst
  possible outcome of this plan.

---

## What this plan does **not** cover

* **Signatures and key management.** The chain is unsigned. No HSM, no key rotation, no
  delegation chain of the kind the Microsoft toolkit ships `[DOC ibid.]`.
* **An external anchor.** Without publishing the head hash somewhere the owner connection cannot
  reach, a wholesale rewrite from genesis remains undetectable. A9, and out of scope before the
  demo.
* **`run_events` chaining** — structurally blocked by the partition key (see above). It keeps only
  its privilege-level protection.
* **`usage_ledger` chaining on the write path** — deliberately refused, to protect the budget caps
  the ledger feeds.
* **`memory_write_log`** — not in `_APPEND_ONLY_TABLES`, and the reasoning is recorded at
  `[SOURCE aegis/src/aegis/governance/rls.py:1244-1256]`: the memory-erasure path needs `DELETE`,
  so tamper-evidence for memory deliberately rests on `audit_log` instead. SOTA-03 assumes this
  and this plan does not change it.
* **Retroactive integrity for the 12,000-odd rows already in the table.** By design, and by the
  argument in the genesis section. There is no version of this feature that makes a claim about
  them.
* **Tamper *prevention*.** Everything here is detection. Prevention is the privilege split that
  already exists, and it already has a documented hole.
* **`GET /reports/audit.csv`** does not currently carry the hash columns; adding them is a
  one-line schema change that A5's offline-verification test will force, but it is not planned
  here as its own task.
