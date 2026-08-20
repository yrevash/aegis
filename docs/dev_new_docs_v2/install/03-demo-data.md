# 03 — The demo data, and removing it

Three seeders, three different jobs. All three are reversible, and the reversal is
the point: **the blind problem's data must land on a clean platform.**

---

## What each one writes

### `app.seed` — identity and governance

11 users, 2 tenants, budgets, role assignments, 6 document *records*. **Not demo
data** — this is the platform's own furniture, and nothing works without it. It has
no `--wipe`.

The 11 users, and which have data behind them:

| id | username | role | tenant | ledger rows |
|---|---|---|---|---|
| 1 | `admin` | platform admin | — | 757 |
| 5 | `client` | client | — | 793 |
| 6 | `northwind.admin` | tenant admin | 1 | 2,658 |
| 8 | `northwind.client` | client | 1 | 2,653 |
| 9 | `vertex.admin` | tenant admin | 2 | 1,975 |
| 11 | `vertex.client` | client | 2 | 1,795 |

**Demo as the tenant-bound accounts.** `client` and `ai` have `tenant_id = NULL`,
so every tenant-scoped screen is correctly empty for them — which looks like broken
software rather than the wrong account. The login screen offers the tenant-bound
ones for this reason.

### `app.demo` — 90 days of operating history

```bash
AEGIS_DEMO_DATA=1 python -m app.demo          # write
python -m app.demo --wipe                     # remove, no flag needed
```

~57,000 rows: `usage_ledger` (16,624 over 90 days with weekday/weekend rhythm and
two deliberate spikes), `runs` folded from their own events, `run_events`,
`job_runs`, `audit_log`, `approvals`, `redteam_runs`.

Every row is tagged with a reserved **`demo-` prefix on that table's own
correlation id** — `usage_ledger.trace_id`, `runs.run_id`, `job_runs.workflow_id`,
and so on. No extra column. Real trace ids are 32-char hex and real run ids are
`ingest:1:7` / `rt-<hex>`, so none can begin `demo-` and `LIKE 'demo-%'` is exact.
Each of those columns is already indexed, so the wipe is a seek.

The seasonality is real enough that the forecaster **selects `SeasonalNaive` over
`AutoARIMA` on measured backtest** (sMAPE 19.9 vs 38.4). A flat series could not.

### `app.memory_demo` — what the agent remembers

```bash
python -m app.memory_demo                     # write
python -m app.memory_demo --wipe              # remove
```

21 facts across 7 principals, written **through `POST /v1/memory/facts` as each
principal** — so subject resolution, the input guardrail, live embedding and the
audit write all run. Tagged `demo-` on `predicate`.

Northwind remembers 16 CFR 435/703 deadlines; Vertex remembers Reg Z and the CFPB
breakdown — matching the corpus split, so neither tenant cites the other's
regulation.

> **There is no shared-memory bucket.** `recall()` filters `subject_id` *and*
> `tenant_id` on every arm, and the write path only accepts subjects from a
> server-built set of `users` rows — a `tenant:` subject cannot be created. "Shared
> vs private" is expressed as **reach**: a client sees 1 subject, a tenant admin
> its tenant's 3, a platform admin all 11. `scripts/prove_memory_scope.sh` proves
> it and exits 0.

### The corpus — not a seeder

The four PDFs in `docs/corpus/` are real published documents, ingested through the
normal upload path. They are **not** demo data and should survive a wipe: they are
what makes retrieval demonstrable. See [`02-bootstrap.md`](02-bootstrap.md) step 7.

---

## Before the hackathon

```bash
cd backend
PYTHONPATH=src:../aegis/src .venv/bin/python -m app.demo --wipe
PYTHONPATH=src:../aegis/src .venv/bin/python -m app.memory_demo --wipe
```

Both report what they removed. Verified exact on the reference machine: seeding then
wiping returned every table to its pre-existing count, and a test plants *untagged*
rows in all seven tables — including one with `trace_id = NULL`, the shape a
hand-written `NOT LIKE` gets wrong, since `NULL LIKE 'demo-%'` is `NULL` — and
asserts they survive.

**Decide deliberately what else goes.** The corpus documents, the seeded users and
the Superset dashboards are all *demo scaffolding* in some sense. The rule that has
served: remove anything that would be **mistaken for the blind problem's own data**,
keep anything that is **platform furniture**.

## One thing a wipe cannot undo

`app.demo` writes into `usage_ledger`, and the budget gate counts against that
window. During the corpus ingest, tenant 2 hit its token cap because the seeder had
already spent 1.1M tokens that day. Caps are currently **10,000,000 tokens** on both
tenants and both demo users, with USD caps of $50/$25/$5/$2.50 unchanged.

If ingestion fails with `BudgetExceededError`, that is the gate working. Raise the
cap through `POST /v1/admin/budgets` — and **send `usd_cap` in the same call**,
because the endpoint writes the whole row and an omitted field is written as `NULL`.
