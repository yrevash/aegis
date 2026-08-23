# 02 — Bootstrap, in order

Every step has a **check**. Run it. A step that "seemed to work" is how this
project lost a vector index for five hours.

Paths are relative to the repo root. The backend venv is `backend/.venv`.

---

## 1. Dependencies and the database

```bash
scripts/bootstrap.sh                       # toolchain + venvs + npm install
# Windows: scripts\bootstrap.ps1
```

`bootstrap` also trains the ML spine once (`python -m app.ml`, offline, ~10 s, no
key and no database). Until that artifact exists `/ml/explain` and `/ml/model-card`
return **503** — deliberately, because the train-on-demand fallback it replaced fitted
the built-in *noise synthesiser* whenever the domain adapter was unimportable and
served the result as domain evidence.

Create the `taif` database, then create the **serving role**:

```bash
scripts/db-roles.sh                        # Windows: scripts\db-roles.ps1
```

That creates `aegis_app` as `LOGIN NOSUPERUSER NOBYPASSRLS`, grants it exactly the
DML it needs, and rewrites `backend/.env` so `POSTGRES_DSN` is the serving role and
`POSTGRES_ADMIN_DSN` is the owner. With `DB_BOOTSTRAP=true` the schema, the RLS
policies and the grants are created on first boot, on the admin connection — the
only place DDL belongs.

**Check — this is the one that silently matters:**

```bash
cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m app.data.rls_check
# ENFORCED    serving role 'aegis_app' is subject to RLS (owner DSN split)
```

`BYPASSED` means `.env` still points at a superuser and **every tenant-isolation
claim in the product is false while everything still appears to work**. Do not
continue. `UNVERIFIED` means Postgres is unreachable. Also watch the boot log for
the RLS catalogue read-back: it names any live table carrying `tenant_id` with no
policy. A warning there is the diagnostic working — close the gap, never silence
the line.

**Check** — `psql "$POSTGRES_ADMIN_DSN" -c '\dt'` lists tables.

---

## 2. Start the stores

Qdrant on `:6333`, Redis on `:6379`, Neo4j on `:7687`.

**Check**
```bash
curl -s localhost:6333/ | head -c 60     # {"title":"qdrant …","version":"1.19.0"
redis-cli ping                           # PONG   (memurai-cli ping on Windows)
nc -z localhost 7687 && echo neo4j-up    # Bolt is not HTTP; curl is the wrong tool
```

Neo4j needs `NEO4J_URI`, `NEO4J_USER` and `NEO4J_PASSWORD` in `backend/.env`.
Without it the dense retrieval arm still answers; the graph arm and `GET /v1/graph`
do not.

---

## 3. Identity and governance

```bash
cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m app.seed
```

Writes 11 users, 2 tenants, budgets, and 6 document *records*. Password `demo`
unless `AEGIS_SEED_PASSWORD` says otherwise.

**Check** — 11 rows:
```bash
psql "$POSTGRES_ADMIN_DSN" -tAc "select count(*) from users"
```

> Documents 1–6 are **records, not bytes** — `seed.py` says so itself. They stay
> `PENDING` with 0 chunks. That is correct; do not try to "fix" it.

---

## 4. Start the backend

Set `AGENT_CHECKPOINTER=postgres` in `backend/.env` first. The default is `memory`,
which is what the test suites use; with it, a run parked on the human-approval gate
dies with the process. On `postgres` the graph's state is written to LangGraph's own
checkpoint tables, created idempotently on the **owner** DSN at boot and then granted
to the serving role, which owns nothing — without that grant a fresh box fails
mid-run rather than at boot. Measured cost: ~1.4 ms per checkpoint. Nothing prunes
them; the tables grow without bound.

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8110
```

**Check** — `curl -s localhost:8110/health` → `{"status":"ok", …}`

**Check the durable half the only way that counts:** park a run on the approval
gate, kill the backend, start it again, approve on the new process, and watch the
run finish. `GET /v1/agent/checkpoints/{run_id}` shows the history (channel values,
interrupt values and task results are deliberately withheld — they carry the query,
the retrieved passages and any PII). Another tenant's run answers **404**,
byte-identical to a run that does not exist.

---

## 5. Demo data — the thing that makes screens non-empty

```bash
cd backend
AEGIS_DEMO_DATA=1 PYTHONPATH=src:../aegis/src .venv/bin/python -m app.demo
PYTHONPATH=src:../aegis/src .venv/bin/python -m app.memory_demo
```

`app.demo` writes ~57,000 rows over 90 days: usage ledger, runs, run events, jobs,
audit, approvals, red-team runs. `app.memory_demo` writes 21 memory facts **through
the HTTP route as each principal**, so scoping, the input guardrail, embedding and
the audit write all run — not SQL.

**Check** — the forecast needs 71 observations and now has them:
```bash
TOKEN=$(curl -s -X POST localhost:8110/v1/auth/login -H 'content-type: application/json' \
  -d '{"username":"admin","password":"demo"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s localhost:8110/v1/forecast/budget -H "authorization: Bearer $TOKEN" | head -c 80
# "available": true   ← if this is false, the ledger is empty
```

Both are reversible: `--wipe` on either. See [`03-demo-data.md`](03-demo-data.md).

---

## 6. Superset — optional, adds the embedded dashboards

```bash
scripts/superset.sh install                       # ~5 min
AEGIS_SUPERSET_DB_PASSWORD='<the aegis_superset role password>' scripts/superset.sh import
scripts/superset.sh start
```

Then create the dashboard, attach the charts, and register it for embedding — the
importer loads the database, datasets and charts but **silently skips the
dashboard**. The exact API calls, and the six failures that stand between
"installed" and "serving data", are in
[`docs/operations/superset-embedded.md`](../operations/superset-embedded.md).
Read that before debugging anything Superset-shaped; every one of those six errors
names something other than its cause.

Paste the resulting numeric dataset and dashboard ids into
`docs/operations/superset/aegis-boards.json`. The catalogue **refuses placeholder
ids by name**, so a missed paste is a sentence, not a silent empty chart.

**Check**
```bash
curl -s -X POST localhost:8110/v1/analytics/boards/spend-by-model/data \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"window":"last_30_days"}' | head -c 100
# rows with real models and costs
```

> **200 with zero rows** means `DB_CONNECTION_MUTATOR` is missing from
> `.superset/superset_config.py`. It is the only failure in this integration that
> leaves no error anywhere, so check it first.

---

## 7. The corpus — the only step that spends money

Temporal must be up. There is no CLI on the build machine, so:

```bash
cd backend && .venv/bin/python -c "
from temporalio.testing import WorkflowEnvironment
import asyncio
async def main():
    await WorkflowEnvironment.start_local(ip='127.0.0.1', port=7233)
    await asyncio.Future()
asyncio.run(main())"
```

**Check** — `nc -z 127.0.0.1 7233` succeeds. (`curl` returns `000`; it is gRPC.)

Upload the four PDFs in `docs/corpus/` through `POST /v1/documents`, **as a tenant
admin** — a platform admin holds no tenant and the endpoint refuses it, correctly.
`docs/corpus/SOURCES.md` has the exact commands and which document went to which
tenant.

**Check**
```bash
curl -s localhost:6333/collections/lightrag_vdb_chunks | grep -o '"points_count":[0-9]*'
# 113 on the reference machine
```

If chunks exist in Postgres but Qdrant is empty — which is what a vector-store
change does — **rebuild from the stored embeddings instead of re-uploading**:

```bash
cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m app.ingestion --verify
cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m app.ingestion --reindex
```

`--reindex` replays `chunks.embedding` and **calls no provider**. Free, idempotent.
It also fills LightRAG's chunk key-value table, which is what lets the graph arm
resolve an entity back to a passage.

The graph arm needs a third index that neither upload nor `--reindex` writes — the
entity and relation vectors:

```bash
cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m app.ingestion --backfill-graph
```

Order matters and the command's own docstring says so: **reindex first**, because
entity vectors written over an empty chunk KV find entities that resolve to nothing.
Both are idempotent. On the reference machine a full corpus gives 167 entity vectors
and 150 relations across 21 documents.

Unlike `--reindex`, **`--backfill-graph` spends money**: there is no stored embedding
of record for an entity, so its text is embedded as it goes. `--backfill-graph
--dry-run` reports how much before you commit to it.

**Check** — a query's merged ranking reports `origins: ["vector","graph","bm25"]`.
If `graph` never appears, the arm is inert; `lightrag_vdb_entities` holding 0 points
is the usual reason, and it was the real state of this system until 2026-08-23.

---

## 8. The web app

```bash
cd web && npm run dev -- -p 3001
```

The API is proxied same-origin by a rewrite in `next.config.mjs`. **Do not create
`web/.env.local`** — `NEXT_PUBLIC_API_BASE` overrides the rewrite, which reintroduces
CORS, breaks any tunnelled host, and fails on IPv4-only backends because Chrome
resolves `localhost` to `::1`.

**Check** — `curl -s -o /dev/null -w '%{http_code}' localhost:3001/health` → `200`.
That proves the proxy, not just the app.

---

## Final verification

Work down this table. Anything that fails, stop and fix — later rows assume the
earlier ones.

| # | Check | Command | Expected |
|---|---|---|---|
| 1 | Postgres up | `psql "$POSTGRES_ADMIN_DSN" -c 'SELECT 1'` | `1` |
| 2 | **RLS genuinely enforced** | `python -m app.data.rls_check` | `ENFORCED` |
| 3 | Redis | `redis-cli ping` (`memurai-cli` on Windows) | `PONG` |
| 4 | Qdrant | `curl -s localhost:6333/` | version JSON |
| 5 | Neo4j | `nc -z localhost 7687` | succeeds |
| 6 | Temporal | `nc -z 127.0.0.1 7233` | succeeds |
| 7 | Readiness board | `scripts/preflight.sh` (`.ps1`) | all green |
| 8 | Backend suite | `pytest -q` in `backend` | passing |
| 9 | Core suite | `pytest -q` in `aegis` | passing |
| 10 | Web builds | `npx tsc --noEmit && npm test && npx next build` in `web` | compiled |

**Row 2 is the one that silently matters.** The others fail loudly when wrong. That
one prints `BYPASSED` and everything still appears to work — while every
tenant-isolation claim in the product is false.

```bash
cd web     && npx tsc --noEmit && npm test && npx next build
cd aegis   && PYTHONPATH=src ../backend/.venv/bin/python -m pytest -q
cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest -q
```

Reference numbers on 2026-08-23: **web 364 tests · aegis 2324 passed / 14 skipped ·
backend 1984 passed / 1 skipped**. `tests/jobs/test_reindex_admission.py` fails
**only when Temporal is down** — an environment condition, not a defect.

Then do the two checks no unit test makes for you:

1. Sign in at `localhost:3001` as **`northwind.client`** and **`vertex.client`**
   (password `demo`) and confirm they show **different** data. That is the
   platform's central claim.
2. Park a run on the approval gate, restart the backend, and approve it. That is
   the durable-execution claim, and it is false the moment `AGENT_CHECKPOINTER`
   slips back to `memory`.
