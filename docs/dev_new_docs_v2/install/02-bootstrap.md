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

Create the `taif` database and put `POSTGRES_DSN` / `POSTGRES_ADMIN_DSN` in
`backend/.env`. With `DB_BOOTSTRAP=true` the schema is created on first boot.

**Check** — `psql "$POSTGRES_ADMIN_DSN" -c '\dt'` lists tables.

---

## 2. Start the stores

Qdrant on `:6333`, Redis on `:6379`.

**Check**
```bash
curl -s localhost:6333/ | head -c 60     # {"title":"qdrant …","version":"1.19.0"
redis-cli ping                           # PONG
```

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

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8110
```

**Check** — `curl -s localhost:8110/health` → `{"status":"ok", …}`

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
[`docs/operations/superset-embedded.md`](../../operations/superset-embedded.md).
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

```bash
cd web     && npx tsc --noEmit && npm test && npx next build
cd aegis   && PYTHONPATH=src ../backend/.venv/bin/python -m pytest -q
cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest -q
```

Reference numbers on 2026-08-21: **web 231 tests · 67/67 pages · aegis 2271 ·
backend 1181**. One aegis failure
(`test_interrupt_is_never_reached_from_inside_a_gathered_task`) is pre-existing.
`tests/jobs/test_reindex_admission.py` fails **only when Temporal is down** — that
is an environment condition, not a defect.

Then sign in at `localhost:3001` as **`northwind.client`** and **`vertex.client`**
and confirm they show different data. That is the platform's central claim, and it
is the one check no unit test makes for you.
