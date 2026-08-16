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
| `retrieval` | RAG over stores (full mode) | lightrag-hku, neo4j, redis, chromadb (embedded) |
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
| `POSTGRES_DSN` | set by start script | lite points this at SQLite; full at Postgres. |
| `AGENT_CHECKPOINTER` | `memory` (set `postgres` for full) | `memory` = single-process; `postgres` = durable, resumable HITL (ADR 0005). |
| `JWT_SECRET` | `backend/.env` | HS256 signing secret — **set a real one for any shared deploy** (ADR 0008). |
| `APPROVAL_SLA_SECONDS` | `backend/.env` | SLA before the sweeper auto-rejects a HIGH-risk pending gate. |
| `NEXT_PUBLIC_USE_MOCK` | set by start script | `true` = console runs with no backend. |

---

## If X is red → do Y

| Symptom | Fix |
|---|---|
| Gateway DOWN in preflight | Check `GENAILAB_API_KEY` in `backend/.env`; you're on the venue network. Until then, `start … safe` still demos the whole UI. |
| Postgres/Neo4j/Redis DOWN | Don't fight it — run `start … lite`. It needs none of them. |
| Backend won't boot | You're likely missing the venv — re-run `bootstrap`. Lite needs no databases, so it's almost always an install issue. |
| `/audit` empty in lite | Expected until an action runs; it writes to `taif_lite.db` (SQLite) on the fly. |
| Console shows `—` everywhere | Backend not reachable or no query run yet. Confirm `http://localhost:8000/docs` opens; run a query. |
| Console stuck on stale output | `rm -rf web/.next` and re-run `npm run dev`. |
| First `pytest` is slow (~30–60s) | One-time SHAP/numba JIT compile; later runs are ~2s. |
| Console banner says "mock" unexpectedly | The boot probe couldn't reach the backend — check `NEXT_PUBLIC_API_BASE` and that `:8000` is up. |

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
