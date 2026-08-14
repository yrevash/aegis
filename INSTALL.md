# INSTALL.md — Setup & Run Guide

> **Bare Windows machine?** `scripts\install-windows.ps1` (elevated) does the whole
> setup — toolchain, the four native stores, then the app dependencies. Add
> `-SkipStores` if you only intend to run `-Mode lite`. It has not yet been executed
> on Windows; the manual steps below remain the fallback.
>
> **Fastest path (no agent needed):** `scripts\bootstrap.ps1` → `scripts\preflight.ps1`
> → `scripts\start.ps1 -Mode lite` (Windows; `.sh` twins for mac/Linux). The
> one-page day-of guide with the fallback ladder is **`docs/operations/runbook.md`**;
> the teaching path starts at **`docs/learn/00-what-aegis-is.md`**. The rest of this
> file is the long-form manual.

Complete, copy-pasteable setup for the TAIF S2 agentic platform. Two paths:

- **Path A — Demo in 2 minutes (no backend, no infra):** the console ships a
  full in-browser **mock transport**, so you can see the whole UI (streaming
  agent trace, animated knowledge graph, SHAP + conformal panel, human-approval
  gate, dashboards) with **zero backend or database**. Best for a quick look or a
  projector demo.
- **Path B — Full stack:** FastAPI backend + local stores (Postgres, Qdrant,
  Neo4j, Redis) + Arize Phoenix, streaming live over SSE.

> **Environment target:** 16 GB laptop, **no Docker, no GPU**. Everything is a
> local install or an API call. The only remote calls are the model gateway
> (`genailab.tcs.in`). Developed on macOS, runs on Windows/Linux — no
> OS-specific assumptions.

---

## 0. Prerequisites

| Tool | Version | Why |
|------|---------|-----|
| **Python** | ≥ 3.11 | backend (`pyproject.toml` requires `>=3.11`) |
| **uv** | latest | fast Python env + installer (`pip install uv` or see astral.sh/uv) |
| **Node.js** | ≥ 18.18 (20+ recommended) | console (`web/`) build/dev |
| **npm** | ≥ 10 (ships with Node) | console package manager |
| **PostgreSQL** | ≥ 15 | relational + KV/doc-status + audit log *(Path B)* |
| **Qdrant** | ≥ 1.12 (server) | vector DB — ANN for retrieval + memory recall *(Path B; dev uses embedded)* |
| **Neo4j** | 5.x (Desktop/Community) | knowledge graph *(Path B)* |
| **Redis** | ≥ 7 (or Memurai on Windows) | semantic cache *(Path B)* |

Phoenix runs **in-process** (a pip dependency) — nothing to install separately.

---

## Path A — Console-only demo (mock mode)

```bash
cd web
npm install
cp .env.example .env.local     # leave NEXT_PUBLIC_API_BASE empty for the mock demo
npm run dev                    # → http://localhost:3000
```

Open <http://localhost:3000>. Log in with any of the demo roles (see
[Demo logins](#demo-logins)). The console is **live-first with a labelled mock
fallback**: it probes the backend once on boot and, finding none, plays the whole
scenario from the in-browser mock transport — no backend required. Force the mock
at any time with `NEXT_PUBLIC_USE_MOCK=true` (or `?mock=1` in the URL). To point
the console at a live backend, set `NEXT_PUBLIC_API_BASE=http://localhost:8000`
in `.env.local`.

---

## Path B — Full stack

### 1. Local infrastructure

**PostgreSQL**

```sql
-- once, as a superuser, create the target database (default name: taif)
CREATE DATABASE taif;
```

Postgres holds the relational tables, LightRAG's KV + doc-status stores, and the audit
log. No `pgvector` extension is needed — vector ANN search runs on **Qdrant**.

**Qdrant** — the vector DB (ANN for retrieval + memory recall). Run a local server (e.g.
the `qdrant/qdrant` binary/container) listening on `http://localhost:6333`, and set
`QDRANT_URL` (+ optional `QDRANT_API_KEY`). In full stores mode a reachable Qdrant is
**required** and the backend fails loud at boot if it is down (exactly like Postgres/
Redis). Dev/tests use the explicit **embedded** Qdrant engine — a real on-disk/in-memory
HNSW index, never a silent RAM fallback.

**Neo4j** — install Neo4j Desktop or Community, start a local DB, and set a
password. Default bolt URI is `bolt://localhost:7687`, user `neo4j`.

**Redis** — install and start locally (`redis-server`). Default URL
`redis://localhost:6379/0`.

On **Windows** (including locked-down enterprise images with no Docker and no
WSL) use **Memurai**, the maintained Redis-compatible Windows service. It speaks
the same wire protocol on the same port, so **no application or config change is
needed** — `REDIS_URL` stays exactly as above and `redis-py` drives it
identically. The only difference is the CLI: `memurai-cli ping`, not
`redis-cli ping`. Verify with:

```powershell
memurai-cli ping        # -> PONG
Get-Service Memurai*    # should be Running
```

If it is installed but the port is closed, it is usually just stopped after a
reboot: `Start-Service Memurai`.

> No Docker is used anywhere. Each store is a native local install.

### 2. Backend

```bash
cd backend
uv venv                                   # creates .venv (Windows: same command)
source .venv/bin/activate                 # Windows: .venv\Scripts\activate

# Install the core + every feature extra (data, auth, observability, agent,
# retrieval, ml, guardrails) plus dev tools:
uv pip install -e ".[data,auth,observability,agent,retrieval,ml,guardrails,mcp,dev]"

cp .env.example .env                      # then fill in the secrets below
```

> **New optional-dependency groups** (production upgrade):
> - **`auth`** — `pyjwt` (signed access tokens) + `argon2-cffi` (Argon2id password
>   hashing) for multi-tenant RBAC (ADR 0008). Required for JWT login/governance.
> - **`agent`** now also pulls **`langgraph-checkpoint-postgres`** — the durable
>   `PostgresSaver` for checkpointed pause/resume (ADR 0005). Only *used* when
>   `AGENT_CHECKPOINTER=postgres`; the default `memory` saver needs neither it nor a DB.

Edit `backend/.env`:

```dotenv
GENAILAB_BASE_URL=https://genailab.tcs.in
GENAILAB_API_KEY=<your key>               # required for any live model call
GENAILAB_SSL_VERIFY=false                 # gateway uses a self-signed cert (scoped exception)

POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/taif
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<your neo4j password>
REDIS_URL=redis://localhost:6379/0

# ── Auth / multi-tenant RBAC (ADR 0008) ──
JWT_SECRET=<a long random secret>         # REQUIRED in prod; NEVER ship the dev default
JWT_EXPIRE_MINUTES=720                     # access-token lifetime

# ── Durable execution + approvals inbox (ADR 0005) ──
AGENT_CHECKPOINTER=postgres               # 'memory' (default) or 'postgres' (durable)
APPROVAL_SLA_SECONDS=3600                 # SLA before the sweeper acts on a pending gate
APPROVAL_DEFAULT_TIER=tier-1              # approver tier a fresh gate is assigned

STORES=on                                 # 'on' = real stores (Postgres-primary); 'off' = lite
DB_BOOTSTRAP=true                         # create tables on startup (best-effort)

PHOENIX_ENABLED=true
APP_ENV=dev
LOG_LEVEL=INFO
```

> **Postgres-primary posture.** In the full stack one local PostgreSQL is the primary
> store for *everything durable* — tenants, users, budgets, the usage ledger, the
> approvals inbox, the LangGraph checkpoints, and the audit log. Vector embeddings live
> in **Qdrant** (Postgres keeps only the JSON embedding-of-record). Set
> `AGENT_CHECKPOINTER=postgres` for durable, resumable HITL runs; the `memory` default
> keeps single-process/offline runs zero-dependency.

Run the API:

```bash
uvicorn app.main:app --reload --app-dir src   # → http://localhost:8000
```

- OpenAPI docs: <http://localhost:8000/docs>
- **Agent:** `POST /auth/login` (JWT), `POST /query` (SSE), `GET /graph`,
  `POST /ml/explain`, `GET /metrics`, `GET /audit`.
- **Approvals inbox (async HITL, admin):** `GET /approvals`,
  `POST /approvals/{id}/decision`, plus the live `POST /approval`.
- **Admin governance:** `GET /admin/tenants` (platform-admin),
  `GET /admin/users`, `GET|POST /admin/budgets`, `GET /admin/usage` (tenant-scoped).

### 3. Console against the live backend

```bash
cd web
npm install
cp .env.example .env.local
# set NEXT_PUBLIC_API_BASE=http://localhost:8000 in .env.local
npm run dev                                # → http://localhost:3000
```

The backend enables CORS for `http://localhost:3000` out of the box.

---

## Demo logins

| Username | Password | Coarse role | Persona           | Portal           |
|----------|----------|-------------|-------------------|------------------|
| `admin`  | `demo`   | `admin`     | `operations_lead` | `/app/admin/…`   |
| `ai`     | `demo`   | `ai_team`   | `operations_lead` | `/app/ai_team/…` |
| `aiteam` | `demo`   | `ai_team`   | `operations_lead` | `/app/ai_team/…` |
| `devops` | `demo`   | `devops`    | `operations_lead` | `/app/devops/…`  |
| `client` | `demo`   | `client`    | `client`          | `/app/client/…`  |

Every operational role (`admin` / `ai_team` / `devops`) maps to the full
`operations_lead` persona; `client` gets the self-scoped `client` persona.

These are a **dev-only fallback** (`_DEMO_USERS` in `backend/src/app/api/routes.py`),
consulted **only** when `APP_ENV=dev` *and* the username is not a real `users` row —
so a seeded account is never overridden, and a wrong password for an existing user
never falls through to the demo table. In any non-dev environment the demo table is
disabled entirely. Being platform-scoped (no `tenant_id`), their runs are **ungoverned**.

The demo principals mint **signed JWTs** and map to the `platform_admin` tier
(global, un-tenanted) for back-compat. A real deployment seeds the `users` table with
Argon2-hashed passwords and a `tenant_id`, so login yields a **tenant-scoped** JWT and
runs are governed (budget + RLS). Tenant/user/budget management is under `/admin/*`
(platform-admin: tenants; tenant-admin: own users/budgets/usage). See ADR 0008 and
`backend/src/app/api/routes.py`.

The **async approvals inbox** (durable HITL, ADR 0005): a gated run persists a `PENDING`
row; an admin lists it at `GET /approvals` and resolves it out-of-band at
`POST /approvals/{id}/decision` — the run resumes from its checkpoint and the tool runs
exactly once.

---

## Verify the install

**Backend — tests + lint** (from `backend/`, venv active):

```bash
python -m pytest tests -q      # expect: 266 passed, 1 skipped (the opt-in LLM-judge)
ruff check src tests           # expect: All checks passed!
ruff format --check src        # formatting
```

> The offline **quality gate** runs here too (`tests/eval/test_eval_gate.py`): it drives
> the real hybrid retrieval path over a fixed seed corpus and **fails** if
> context-precision/recall or groundedness regress below threshold. Set
> `TAIF_EVAL_LLM_JUDGE=1` to additionally run the reasoning-model LLM-as-judge pass.

> First `pytest` run is slow (~30–60s) because SHAP/XGBoost trigger a one-time
> numba JIT compile; subsequent runs are ~2s.

**Console — build + lint** (from `web/`):

```bash
npm run build  # next build (TypeScript strict) — expect clean
npm run lint   # ESLint (next/core-web-vitals + next/typescript) — 0 errors
```

---

## Environment variable reference

**Backend (`backend/.env`)** — loaded and typed by `app/config.py`:

| Var | Default | Meaning |
|-----|---------|---------|
| `GENAILAB_BASE_URL` | `https://genailab.tcs.in` | model gateway base URL |
| `GENAILAB_API_KEY` | *(empty)* | gateway API key — **required for live calls** |
| `GENAILAB_SSL_VERIFY` | `false` | verify gateway TLS (self-signed cert → false) |
| `POSTGRES_DSN` | `postgresql://postgres:postgres@localhost:5432/taif` | Postgres DSN (relational + KV + audit; the primary durable store) |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant vector DB — **required** in full stores mode (fails loud at boot if unreachable) |
| `QDRANT_API_KEY` | *(empty)* | optional API key for a secured Qdrant node |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | `bolt://localhost:7687` / `neo4j` / *(empty)* | Neo4j |
| `REDIS_URL` | `redis://localhost:6379/0` | near-exact semantic cache |
| `JWT_SECRET` | *(dev-insecure default)* | HS256 signing secret — **set a real one in prod** (ADR 0008) |
| `JWT_ALGORITHM` / `JWT_EXPIRE_MINUTES` | `HS256` / `720` | token algorithm + lifetime |
| `AGENT_CHECKPOINTER` | `memory` | `memory` (default) or `postgres` (durable `PostgresSaver`; ADR 0005) |
| `APPROVAL_SLA_SECONDS` | `3600` | SLA before the sweeper acts on a pending gate |
| `APPROVAL_DEFAULT_TIER` | `tier-1` | approver tier a fresh gate is assigned |
| `APPROVAL_SWEEPER_INTERVAL_SECONDS` | `30` | how often the SLA sweeper scans the inbox |
| `STORES` | `on` | `on` = real stores (Postgres-primary), `off` = lite (no databases) |
| `DB_BOOTSTRAP` | `false` | create tables on startup (best-effort; run scripts set `true`) |
| `PHOENIX_ENABLED` | `true` | start in-process Phoenix tracing |
| `APP_ENV` / `LOG_LEVEL` | `dev` / `INFO` | app env + root log level (applied via `logging.basicConfig` at app startup) |

Model routing is **role-based**: override any role with `MODEL_<ROLE>` (e.g.
`MODEL_GENERATION=genailab-maas-gpt-4o`). See `backend/src/app/core/models.py`.

**Console (`web/.env.local`)**:

| Var | Default | Meaning |
|-----|---------|---------|
| `NEXT_PUBLIC_API_BASE` | *(empty ⇒ same-origin)* | live backend base URL, e.g. `http://localhost:8000` |
| `NEXT_PUBLIC_HEALTH_PATH` | `/health` | path the boot probe hits to detect a reachable backend |
| `NEXT_PUBLIC_USE_MOCK` | `false` | `true` forces the in-browser mock transport (`?mock=1` does the same per-tab) |

---

## Troubleshooting

- **`pytest` seems to hang the first time** — it's the one-time numba/SHAP JIT
  compile, not a hang. Give the first run 60s; later runs are ~2s.
- **`next build` appears stuck or reuses stale output** — remove the build cache
  and rebuild: `rm -rf web/.next && (cd web && npm run build)`.
- **Qdrant unreachable at boot (full stores mode)** — the backend fails loud if
  `QDRANT_URL` is down. Start the Qdrant server (`http://localhost:6333`) or set
  `STORES=off` for the databaseless lite demo. Dev keeps an embedded Qdrant engine.
- **TLS errors calling the gateway** — the gateway uses a self-signed cert;
  keep `GENAILAB_SSL_VERIFY=false` (a documented, scoped exception).
- **Windows Redis** — use Memurai or Redis under WSL2; the default `REDIS_URL`
  still applies.

---

## What runs where

| Component | Local process | Port |
|-----------|---------------|------|
| Console (Next.js dev) | `npm run dev` (from `web/`) | 3000 |
| Backend (FastAPI/uvicorn) | `uvicorn app.main:app` | 8000 |
| PostgreSQL | native install | 5432 |
| Qdrant (vector DB) | native install / binary | 6333 |
| Neo4j | native install | 7687 (bolt) / 7474 (http) |
| Redis / Memurai | native install | 6379 |
| Arize Phoenix | in-process (with backend) | 6006 (UI, if enabled) |

See `docs/learn/10-architecture.md` (the whole system), `docs/architecture/backend.md`,
`web/README.md` (console context), and `README.md` for architecture.
