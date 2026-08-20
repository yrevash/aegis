# RUNBOOK.md — Day-of Operations

The one page to run everything **without an agent**. Three commands, one fallback
ladder. When something is red, the ladder tells you which mode still works.

---

## TL;DR — three commands

```powershell
# Windows (the hackathon machine)
.\scripts\bootstrap.ps1          # once: installs everything, makes .env files
.\scripts\preflight.ps1          # anytime: shows what's UP (gateway / stores)
.\scripts\start.ps1 -Mode lite   # run it (lite = real agent, NO databases)
```

```bash
# macOS/Linux (rehearsal)
./scripts/bootstrap.sh
./scripts/preflight.sh
./scripts/start.sh lite
```

Then open **http://localhost:3000** and log in with **admin / admin**.
The only secret you must set is `GENAILAB_API_KEY` in `backend/.env`.

---

## The fallback ladder (pick the highest rung that's green)

| Rung | Command | Needs | You get |
|---|---|---|---|
| **Full** | `start … full` | gateway + Postgres + Neo4j + Redis | Real RAG over stores, persisted audit, live Phoenix traces |
| **Lite** ⭐ | `start … lite` | **gateway only** (no databases) | **Real** agent, LLM, streaming, tools, gate, token/cost — in-memory records/graph/cache, SQLite audit |
| **Demo-safe** | `start … safe` | nothing | Full UI money-shot on the in-browser mock — cannot fail |

**Default to Lite.** It removes the biggest day-of risk (databases not installing)
while still being a *real* demo. Keep Demo-safe as the pitch parachute.

Run `preflight` first: gateway UP → Lite works; all UP → Full; nothing → Demo-safe.

---

## Connection map (every wire)

```
Browser (http://localhost:3000, Next.js console in web/)
  │  live-first: boots, probes the backend, falls back to the in-browser mock if it's down
  │  NEXT_PUBLIC_USE_MOCK=true (or ?mock=1) forces the mock; =false → talks to the backend
  │  NEXT_PUBLIC_API_BASE=http://localhost:8000
  ▼
Backend (http://localhost:8000, FastAPI/uvicorn)   endpoints: /auth/login(JWT) /query(SSE)
  │   /graph /ml/explain /metrics /audit   HITL: /approval /approvals /approvals/{id}/decision
  │   admin: /admin/tenants /admin/users /admin/budgets /admin/usage
  ├── Model gateway  →  https://genailab.tcs.in     (GENAILAB_API_KEY, self-signed cert → SSL verify off)
  │        └─ chokepoint enforces per-tenant budget/RPM/TPM before spend (governed logins only)
  ├── STORES=off (lite): in-memory hybrid recall + in-memory cache, SQLite, memory checkpointer ← no databases
  └── STORES=on  (full): Postgres 5432 (primary: tenants/budgets/ledger/approvals/
             checkpoints/audit) · Neo4j 7687 · Redis 6379 · Phoenix 6006
             plus the EMBEDDED vector store — an on-disk directory, no server, no port
```

| Port | Service | Needed in |
|---|---|---|
| 3000 | Console (Next.js, `web/`) | all modes |
| 8000 | Backend (FastAPI) | lite, full |
| 5432 | Postgres (no extension required) | full |
| 7687 | Neo4j (bolt) | full |
| 6379 | Redis / Memurai | full |
| 6006 | Arize Phoenix (traces) | full (optional) |

---

## What each layer installs (component-wise)

`bootstrap` installs the backend with **all** extras below (from `backend/pyproject.toml`)
plus the console (`web/`) via `npm`. You never install these by hand.

| Extra | Powers | Key libs |
|---|---|---|
| `data` | audit log, embeddings of record, RBAC tables | sqlalchemy, asyncpg, alembic |
| `agent` | the LangGraph loop + gate | langgraph, langchain-core/openai |
| `retrieval` | RAG over stores (full mode) | lightrag-hku, neo4j, redis, qdrant-client |
| `ml` | prediction + conformal + SHAP | xgboost, scikit-learn, mapie, shap |
| `guardrails` | input/output rails | nemoguardrails |
| `observability` | OTel → Phoenix | opentelemetry-*, arize-phoenix |
| `dev` | tests + lint | ruff, pytest, aiosqlite |
| `web/` | the console UI | next, react, tailwind, recharts, react-force-graph |

> Lite mode installs the same packages but simply **doesn't connect** to Neo4j /
> Redis / Postgres at runtime — the switch is the `STORES` env var, not the install.

---

## Env vars that matter

| Var | Where | Meaning |
|---|---|---|
| `GENAILAB_API_KEY` | `backend/.env` | **Required** for real answers. |
| `GENAILAB_SSL_VERIFY` | `backend/.env` | Keep `false` (gateway has a self-signed cert). |
| `STORES` | set by start script | `off` = lite (no databases), `on` = full (Postgres-primary). |
| `DB_BOOTSTRAP` | set by start script | `true` = create tables on startup (best-effort). |
| `POSTGRES_DSN` | `backend/.env` | The **serving** connection — every request. Must be a non-superuser role (see *Database roles* below). Lite points it at SQLite. |
| `POSTGRES_ADMIN_DSN` | `backend/.env` | The **owner/DDL** connection — `create_all`, the schema reconciler, the RLS bootstrap, and nothing else. Empty = no split (loudly reported). |
| `AGENT_CHECKPOINTER` | `memory` (set `postgres` for full) | `memory` = single-process; `postgres` = durable, resumable HITL (ADR 0005). |
| `JWT_SECRET` | `backend/.env` | HS256 signing secret — **set a real one for any shared deploy** (ADR 0008). |
| `APPROVAL_SLA_SECONDS` | `backend/.env` | SLA before the sweeper auto-rejects a HIGH-risk pending gate. |
| `NEXT_PUBLIC_USE_MOCK` | set by start script | `true` = console runs with no backend. |
| `AEGIS_SUPERSET_*` | `backend/.env` | Embedded analytics. **Optional and off by default** — Aegis boots and behaves identically without Superset. Full list and the Superset-side config: [`superset-embedded.md`](superset-embedded.md). |

---

## Database roles — the half of tenant isolation that is not code

**Full mode only.** PostgreSQL skips row security **entirely** for a superuser or a role
with `BYPASSRLS`. `FORCE ROW LEVEL SECURITY` removes the table *owner's* exemption, not
that one. So a backend connected as `postgres` installs all 13 `tenant_isolation`
policies, shows them in `pg_policies` — and is filtered by none of them. Aegis ran that
way until this split; the fix is two connections, not two lines of application code.

```bash
./scripts/db-roles.sh          # macOS/Linux
.\scripts\db-roles.ps1         # Windows (install-windows.ps1 already runs this once)
```

It creates `aegis_app` — `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`, owning
nothing, holding only `SELECT/INSERT/UPDATE/DELETE` on the app's tables — and rewrites
`backend/.env`:

| DSN | Role | Used by | Bypasses RLS |
|---|---|---|---|
| `POSTGRES_DSN` | `aegis_app` | every request | **no** — this is the point |
| `POSTGRES_ADMIN_DSN` | `postgres` (owner) | `create_all`, schema reconciler, RLS bootstrap, serving-role grants | yes, legitimately |

Idempotent — re-running rotates the password and re-applies the grants. The SQL it runs
is `scripts/sql/aegis-app-role.sql`, readable on its own.

**How you know it worked.**

```bash
cd backend && PYTHONPATH=../aegis/src:src .venv/bin/python -m app.data.rls_check
# ENFORCED    serving role 'aegis_app' is subject to RLS (owner DSN split)
```

Same line appears as the `RLS serving role` row in `preflight`. The backend runs the
same check at boot: if the serving role can bypass, it logs at ERROR — *"row-level
security is inert; every tenant policy is bypassed"* — and, when `APP_ENV` is not `dev`,
**refuses to start**. A dev box left on the superuser DSN keeps working and keeps
complaining; that asymmetry is deliberate, because a check that blocks the dev loop is a
check that gets disabled.

---

## If X is red → do Y

| Symptom | Fix |
|---|---|
| Gateway DOWN in preflight | Check `GENAILAB_API_KEY` in `backend/.env`; you're on the venue network. Until then, `start … safe` still demos the whole UI. |
| Postgres/Neo4j/Redis DOWN | Don't fight it — run `start … lite`. It needs none of them. |
| `RLS serving role` DOWN / boot logs "row-level security is inert" | The backend is connected as a superuser, so every tenant policy is bypassed. Run `./scripts/db-roles.sh` (or `.\scripts\db-roles.ps1`) and restart. See *Database roles* above. |
| Backend refuses to boot with `RlsBypassError` | Same cause, non-dev `APP_ENV`: it will not serve tenants with isolation off. Provision `aegis_app` as above; do not "fix" it by setting `APP_ENV=dev`. |
| `permission denied for table …` after adding a model | The serving role has no grant on a brand-new table. Restart with `DB_BOOTSTRAP=true` — bootstrap re-grants on the owner connection — or re-run `db-roles`. |
| Backend won't boot | You're likely missing the venv — re-run `bootstrap`. Lite needs no databases, so it's almost always an install issue. |
| `/audit` empty in lite | Expected until an action runs; it writes to `taif_lite.db` (SQLite) on the fly. |
| Console shows `—` everywhere | Backend not reachable or no query run yet. Confirm `http://localhost:8000/docs` opens; run a query. |
| Console stuck on stale output | `rm -rf web/.next` and re-run `npm run dev`. |
| First `pytest` is slow (~30–60s) | One-time SHAP/numba JIT compile; later runs are ~2s. |
| Console banner says "mock" unexpectedly | The boot probe couldn't reach the backend — check `NEXT_PUBLIC_API_BASE` and that `:8000` is up. |
| Queries answer with no sources / retrieval returns nothing, but `/documents` shows SUCCEEDED | The dense index is empty while the chunks are fine — the two stores disagree. Confirm with `curl -s localhost:6333/collections/lightrag_vdb_chunks \| grep points_count`, then rebuild from the rows that survived: `python -m app.ingestion --reindex` (see *Rebuilding the dense index* below). |

---

## Verify (proof it works)

```bash
# backend — from backend/, venv active
python -m pytest tests -q          # 266 passed, 1 skipped (opt-in LLM-judge)
ruff check src tests               # All checks passed!

# console — from web/
npm run build                      # next build (TS strict) — clean
npm run lint                       # 0 errors
```

Lite mode is covered by `tests/retrieval/test_memory.py` (retrieval runs with zero
infrastructure); the end-to-end flow by `tests/integration/` (governed-budget block,
durable approval round-trip, ML abstain, RRF hybrid provenance, cross-tenant isolation);
and the **offline quality gate** by `tests/eval/test_eval_gate.py` (fails CI if hybrid
retrieval quality regresses). See `INSTALL.md` for the long-form setup and `README.md`
for the architecture.

**Admin / tenancy + approvals flow (full mode).** Log in as a tenant-bound user → runs
are budget-governed at the gateway and RLS-isolated. Platform-admins manage tenants at
`/admin/tenants`; tenant-admins manage their own `/admin/users`, `/admin/budgets`, and
`/admin/usage`. A HIGH-risk (or uncertain) action **defers**: it lands in the durable
inbox (`GET /approvals`); resolve it out-of-band at `POST /approvals/{id}/decision` and
the run resumes from its checkpoint, executing the tool exactly once.

---

## Rebuilding the dense index (after a vector-store swap or a lost volume)

`chunks.embedding` is the **embedding of record** — the durable vectors live in
PostgreSQL beside the text, and the vector store is a derived index built from them.
So losing the vector store loses nothing that cannot be replayed, and replaying it
costs **no provider calls and no money**.

```bash
# from backend/, venv active
python -m app.ingestion --verify                 # audit only: writes nothing
python -m app.ingestion --reindex                # every tenant, every document
python -m app.ingestion --reindex --tenant 1
python -m app.ingestion --reindex --document 7
```

Both verbs finish by reading the index back and comparing it against the durable rows,
and **exit non-zero if the two disagree** — so this is safe to wire into a smoke check.
`--reindex` is idempotent (point ids are content-addressed, so a re-run overwrites) and
non-destructive (nothing is deleted first, so a half-finished run leaves strictly more
of the corpus searchable than it found).

This is deliberately *not* the same thing as the scheduled re-index
(`app.ingestion.reindex.reindex_corpus`), which re-runs every stage but `parse` and is
the right answer when the chunker, the D7 prefix or the **embedding model** changed.
Use that one when what a chunk *should be* has changed; use this one when only the
index was lost. This one needs no parse artifact, so it still works on a corpus whose
uploaded bytes have been pruned.

