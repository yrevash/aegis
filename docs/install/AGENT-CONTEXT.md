# Agent context — installing Aegis v2

You are setting up Aegis on a machine that has only a `git clone`. Read this
first, then follow [`02-bootstrap.md`](02-bootstrap.md) step by step.

---

## What you are dealing with

Aegis is a multi-tenant enterprise agentic-AI platform. Its central claim is that
**every figure is sourced and every refusal is stated**. That shapes the install:
a screen showing nothing is often *correct*, and "fixing" it by inventing data
destroys the only thing the product is selling.

**Almost none of the demo is in git.** The clone gives you code, four corpus PDFs
and the Superset asset bundle. Postgres, Qdrant, `.superset/` and `backend/.env`
all have to be rebuilt. Everything on the missing side is rebuildable from
something committed — that is the design, and it has been exercised: the first
Superset instance was destroyed by a restart and rebuilt from the bundle in about
ten minutes.

---

## Rules

**1. Run the check after every step.** Each step in `02-bootstrap.md` has one. A
step that "seemed to work" is how this project lost its entire vector index for
five hours — a store migration shipped without a re-index, and the code, the tests
and the schema were all correct while the index was empty.

**2. Never fabricate data to make a screen look populated.** If the forecast says
`available: false` with `have: 2, need: 71`, the ledger is empty and the fix is to
run `app.demo` — not to loosen the threshold. If a tenant's screen is blank, check
whether you are signed in as an un-tenanted account before you touch code.

**3. Never read, print, echo or log the value of any `*_KEY` variable.** Reference
them by name. `backend/.env` holds the owner's real Azure credentials.

**4. One step spends money.** Ingesting the corpus calls the embedding provider.
Do it once. If chunks exist in Postgres but Qdrant is empty, use
`python -m app.ingestion --reindex`, which replays stored embeddings and calls no
provider. Never loop a failed ingest.

**5. Do not start duplicate services.** Check what is already listening before
starting anything, and stop whatever you start. Six agents each running a dev
server exhausted this machine's RAM and killed the session.

**6. Do not create `web/.env.local`.** `NEXT_PUBLIC_API_BASE` overrides the
same-origin rewrite in `next.config.mjs` and reintroduces three separate failures:
CORS rejection, a broken tunnel, and connection-refused on an IPv4-only backend
because Chrome resolves `localhost` to `::1`. Three separate agents each
"fixed" the same symptom this way before the cause was found.

---

## Failures that name something other than their cause

These cost hours. Each one's error message points somewhere else.

| What you see | What it actually is |
|---|---|
| Every screen: **"Backend unavailable"** | no API base *and* no rewrite — the backend is fine |
| Console shows nothing during a run, then everything at once | Next's `compress: true` gzips SSE and buffers until close. `compress: false` |
| Superset: **"Forbidden"** on every dashboard | `superset init` was never run |
| `500 No module named 'psycopg2'` on a dataset | Superset ships no Postgres driver. The dataset *list* still renders, because listing never touches Postgres |
| `CommandException` with no detail, on import | `cachetools` missing — undeclared by the 6.1.0 wheel, like `rich` |
| `422 Signature verification failed` | guest token sent as `Authorization` instead of `X-GuestToken` |
| `403 DATASOURCE_SECURITY_ACCESS_ERROR` | the dashboard owns no charts, or the request omits `form_data.dashboardId` |
| Boards return **200 and zero rows** | `DB_CONNECTION_MUTATOR` missing. **The only failure here that leaves no error at all — check it first** |
| Retrieval returns nothing, tests all green | the fake returned a shape the real library never returns. Check against the library, not the code |
| `curl localhost:7233` → `000` | Temporal is gRPC. Use `nc -z 127.0.0.1 7233` |

---

## Verifying you are actually done

```bash
cd web     && npx tsc --noEmit && npm test && npx next build
cd aegis   && PYTHONPATH=src ../backend/.venv/bin/python -m pytest -q
cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest -q
bash scripts/prove_memory_scope.sh
```

Reference, 2026-08-23: **web 364 · aegis 2324 passed / 14 skipped · backend 1984
passed / 1 skipped**. Known: `test_reindex_admission.py` fails only when Temporal
is down.

**Then do the two checks no test performs.** Sign in at `localhost:3001` as
`northwind.client`, then as `vertex.client`. They must show **different** figures.
That is the platform's central claim, and if it is not visibly true the install is
not finished — whatever the suites say. Then park a run on the approval gate,
restart the backend, and approve it: with `AGENT_CHECKPOINTER=postgres` it finishes,
and on the default `memory` it cannot.

---

## Reporting back

Say what you ran, what it printed, and what you did **not** get to. If a step
failed, give the real output rather than a summary of it. If something looks wrong
but you are unsure, say so plainly instead of working around it — a workaround that
hides a real defect is worse than a stalled install, and this project has paid for
that lesson more than once.
