# INSTALL.md — Setup & Run Guide

> **Setting up the hackathon machine?** Follow [`docs/install/`](docs/install/) instead — a
> four-step runbook with the exact commands, the checks after each one, and the three
> measurements Phase 3 still needs from that box. This file remains the general reference.

> **Bare Windows machine?** `scripts\install-windows.ps1` (elevated) does the whole
> setup — toolchain, the four native stores, then the app dependencies. Add
> `-SkipStores` if you only intend to run `-Mode lite`.
>
> If you downloaded a **ZIP** rather than cloning, Windows marks every file as
> "from the internet" and PowerShell refuses to run them. Unblock first:
>
> ```powershell
> Get-ChildItem -Recurse .\scripts\*.ps1 | Unblock-File
> Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass   # this session only
> ```
>
> Cloning with `git clone` avoids the mark entirely and is the better path.
>
> **The ML spine must be trained once** before `/ml/explain` or `/ml/model-card`
> answer: `cd backend && .venv/bin/python -m app.ml` (offline, ~10s, no API key and
> no database needed). `scripts/bootstrap.sh` and `scripts\bootstrap.ps1` now do this
> for you. Until the artifact exists those two endpoints return **503** — deliberately,
> because the old train-on-demand fallback fitted the built-in *noise synthesiser*
> whenever the domain adapter was unimportable and served it as domain evidence.
>
> **Fastest path (no agent needed):** `scripts\bootstrap.ps1` → `scripts\preflight.ps1`
> → `scripts\start.ps1 -Mode lite` (Windows; `.sh` twins for mac/Linux). The
> one-page day-of guide with the fallback ladder is **`docs/operations/runbook.md`**;
> the system walkthrough starts at **`docs/learn/00-what-aegis-is.md`** and the
> per-module course at **`docs/teaching/README.md`**. The rest of this file is the
> long-form manual.

Complete, copy-pasteable setup for the TAIF S2 agentic platform. Two paths:

- **Path A — Demo in 2 minutes (no backend, no infra):** the console ships a
  full in-browser **mock transport**, so you can see the whole UI (streaming
  agent trace, animated knowledge graph, SHAP + conformal panel, human-approval
  gate, dashboards) with **zero backend or database**. Best for a quick look or a
  projector demo.
- **Path B — Full stack:** FastAPI backend + local stores (Postgres, Neo4j,
  Redis) + Arize Phoenix, streaming live over SSE. The vector store is *embedded* —
  it runs inside the backend process, so there is no fourth server to install.

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
| **Neo4j** | 5.x (Desktop/Community) | knowledge graph *(Path B)* |
| **Redis** | ≥ 7 (or Memurai on Windows) | semantic cache *(Path B)* |
| **Temporal CLI** | latest | **the ingest substrate** — every document upload runs as a Temporal workflow *(Path B)* |

Phoenix runs **in-process** (a pip dependency) — nothing to install separately.

> **Temporal is not optional on Path B.** Without it running, `POST /documents` stores
> the bytes, fails to start the workflow, and returns a 503 carrying a raw transport
> error. Worse, the upload's `content_sha256` dedup then refuses to re-ingest those
> bytes and no `job_runs` row exists for `requeue` to act on — so the document is stuck
> until somebody edits the database. Start it before uploading anything:
>
> ```bash
> temporal server start-dev            # listens on localhost:7233
> ```
>
> `scripts/preflight.sh` does **not** probe Temporal and will report "all UP" while it
> is down. The Windows runbook in `docs/install/02-services.md` covers installing it.

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
log. No `pgvector` extension is needed — vector ANN search runs in the embedded vector
store.

**Vector store — nothing to install.** ANN for retrieval + memory recall runs
**embedded**: Chroma's `PersistentClient` (a pip dependency) for Aegis's own store, and
LightRAG's file-backed NanoVectorDB for its internal vectors. Both live inside the
backend process and write to a local directory, so there is no server binary, no Windows
service and no open port — which is precisely what lets Aegis install on a locked-down
enterprise machine. Point `VECTOR_STORE_PATH` at a writable directory (default
`vector_storage`, relative to the backend's working directory). In full stores mode that
directory is **required** and the backend fails loud at boot if it is unusable (exactly
like Postgres/Redis) — it never degrades to a silent in-RAM index. Tests use an explicit
in-memory engine, which is a real index, not a fake.

**Neo4j** — install Neo4j Desktop or Community, start a local DB, and set a
password. Default bolt URI is `bolt://localhost:7687`, user `neo4j`.

On **Neo4j Desktop 2.x** (Windows), installing the app is only half the job — it
ships its own JDK but starts with *no instance*, so nothing listens on 7687 until
you create one. This step cannot be scripted; the password is chosen in the
dialog:

1. **Local instances → Create instance**
2. Set a password, and put that **same value** in `backend\.env` as
   `NEO4J_PASSWORD` (leave `NEO4J_USER=neo4j`, `NEO4J_URI=bolt://localhost:7687`)
3. **Start** the instance — Desktop does not auto-start it, and it stops when
   Desktop closes
4. Re-run `scripts\preflight.ps1`; the Neo4j row should turn green

A down Neo4j is **non-fatal**: graph retrieval degrades, and every other surface
— including the whole console — is unaffected.

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
uv pip install -e ".[data,auth,observability,agent,retrieval,ingestion,ml,guardrails,mcp,dev]"

cp .env.example .env                      # then fill in the secrets below
```

> **`ingestion`** pulls Docling (layout + TableFormer) and, through it, torch and
> opencv — **+816 MB in the venv and 730 MB of model weights** on first use. Prime the
> model cache while there is still network:
> `PYTHONPATH=../aegis/src .venv/bin/python ../spikes/docling_spike.py --prefetch ~/.cache/docling/models`
> (measured: 730 MB in 67 s). Set `DOCLING_WARM_ON_START=true` on the box that runs the
> ingest worker.

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
> in the embedded vector store (Postgres keeps only the JSON embedding-of-record). Set
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

## Seeding the accounts

**Nobody can log in until the database is seeded.** There is no fallback login table
any more (`_DEMO_USERS` was deleted in §3.8): an account exists only if a `users` row
exists, and a login attempted against an empty table answers **503** naming this command
rather than pretending the password was wrong.

```bash
cd backend
PYTHONPATH=src:../aegis/src .venv/bin/python -m app.seed
```

It is idempotent — run it as often as you like; it creates what is missing, touches
nothing that already exists, and prints what it did. Accounts it creates use the password
`demo` unless `AEGIS_SEED_PASSWORD` is set.

**Platform staff** (no `tenant_id`, so their runs are ungoverned):

| Username | Password | Coarse role | Persona           | Portal           |
|----------|----------|-------------|-------------------|------------------|
| `admin`  | `demo`   | `admin`     | `operations_lead` | `/app/admin/…`   |
| `ai`     | `demo`   | `ai_team`   | `operations_lead` | `/app/ai_team/…` |
| `aiteam` | `demo`   | `ai_team`   | `operations_lead` | `/app/ai_team/…` |
| `devops` | `demo`   | `devops`    | `operations_lead` | `/app/devops/…`  |
| `client` | `demo`   | `client`    | `client`          | `/app/client/…`  |

Every operational role (`admin` / `ai_team` / `devops`) maps to the full
`operations_lead` persona; `client` gets the self-scoped `client` persona. `admin` has no
tenant, which is what makes it the `platform_admin` tier.

**Two tenants**, each with a tenant admin, two users, a daily budget and three documents:

| Tenant | Tenant admin | Users |
|---|---|---|
| Northwind Trading | `northwind.admin` | `northwind.analyst`, `northwind.client` |
| Vertex Logistics  | `vertex.admin`    | `vertex.analyst`, `vertex.client`       |

A tenant admin's login yields a **tenant-scoped** JWT, so its runs are governed (budget +
RLS) and the per-tenant screens have something to render. Tenant/user/budget management is
under `/admin/*` (platform-admin: tenants; tenant-admin: own users/budgets/usage). See ADR
0008, `backend/src/app/seed.py` and `backend/src/app/api/routes.py`.

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
| `VECTOR_STORE_PATH` | `vector_storage` | directory for the **embedded** vector store — **required** and must be writable in full stores mode (fails loud at boot otherwise). No server, no port. |
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
- **Vector store unusable at boot (full stores mode)** — the backend fails loud if
  `VECTOR_STORE_PATH` cannot be created or written. Point it at a writable directory
  (`VECTOR_STORE_PATH=C:\Users\you\aegis-vectors`, say), or set `STORES=off` for the
  databaseless lite demo. There is no server to start — the store is in-process.
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
| Neo4j | native install | 7687 (bolt) / 7474 (http) |
| Redis / Memurai | native install | 6379 |
| Arize Phoenix | in-process (with backend) | 6006 (UI, if enabled) |

See `docs/learn/10-architecture.md` (the whole system), `docs/architecture/backend.md`,
`web/README.md` (console context), and `README.md` for architecture.
