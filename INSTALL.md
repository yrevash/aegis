# INSTALL.md — Windows setup, start to finish

This is the install guide for a **Windows** machine, which is the machine Aegis is
demonstrated on. Everything is a native install: **no Docker, no WSL, no GPU, no
compose file.** Sections 0–8 are the guide. Everything from *§9 Long-form reference*
down is the older cross-platform manual, kept because parts of it are still the only
written record of a setting — but where the two disagree, this half wins.

macOS/Linux: the same steps with the `.sh` twin of each script.

**This file and [`docs/install/`](docs/install/README.md) are not rivals.** This one is
Windows-specific and answers *where do I get each piece and how do I run it* — Memurai
instead of Redis, Neo4j Desktop's manual instance, which service starts which port.
`docs/install/` is the ordered, cross-platform runbook with a check that proves every
step, plus what the demo seeders write and how to remove it before the hackathon. Do
§0–§6 here, then read `docs/install/03-demo-data.md` before you demo anything.

---

## 0. Before anything

**Clone, do not download the ZIP.** Windows marks every file extracted from a ZIP as
"from the internet" and PowerShell refuses to run them. Cloning avoids the mark:

```powershell
git clone <repo-url> aegis
cd aegis
```

If you already have a ZIP, unblock it once:

```powershell
Get-ChildItem -Recurse .\scripts\*.ps1, .\backend\scripts\*.ps1 | Unblock-File
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass    # this session only
```

**Use an elevated PowerShell for §1 and §2.** PostgreSQL and Memurai register Windows
services, and registering a service needs administrator rights. Everything after §2
works in an ordinary shell.

---

## 1. The scripted path

One command does the whole of §2 and §3:

```powershell
.\scripts\install-windows.ps1
```

It installs the toolchain (Python 3.11, Node LTS, Git, uv), then the stores, then the
Python and Node dependencies, then seeds `backend\.env` and trains the ML spine.

It is **honest about the one thing it cannot do**: Neo4j Desktop needs an instance
created by hand (§2.5). It will tell you so and carry on, because a missing graph
degrades one screen rather than stopping the platform.

If it completes cleanly, skip to §4. If any row failed, §2 is that row on its own.

---

## 2. The stores, one at a time

Five components. Four of them Aegis needs; one is optional and says so.

| # | Component | Port | Where to get it | Required? |
|---|---|---|---|---|
| 2.1 | Python 3.11 + Node LTS | — | `winget install Python.Python.3.11` · `winget install OpenJS.NodeJS.LTS` | yes |
| 2.2 | PostgreSQL 16 | 5432 | `winget install PostgreSQL.PostgreSQL.16` | **yes** |
| 2.3 | Memurai (Redis for Windows) | 6379 | <https://www.memurai.com/get-memurai> · `winget install Memurai.MemuraiDeveloper` | yes |
| 2.4 | Qdrant | 6333 | <https://github.com/qdrant/qdrant/releases> — `qdrant-x86_64-pc-windows-msvc.zip` | yes |
| 2.5 | Neo4j Desktop | 7687 | <https://neo4j.com/download/> · `winget install Neo4j.Neo4jDesktop` | optional |
| 2.6 | Temporal CLI | 7233 | <https://github.com/temporalio/cli/releases> | for ingest |

### 2.2 PostgreSQL — the one that is not optional

Every tenant boundary in this platform is a Postgres **row-level-security policy**.
That is why there is no SQLite mode and why `start-windows.ps1` refuses to launch the
API when Postgres is down: a stack that starts against a different database is one
where the isolation story is not running and nothing on screen says so.

During install, set a password for the `postgres` superuser and keep it — §3 needs it.

### 2.3 Memurai — Redis, under another name

Windows has no maintained Redis server, and this project has no Docker or WSL to fall
back on. **Memurai is a Redis-compatible server for Windows**: same wire protocol,
same port, so every `REDIS_URL` in this repo points at it unchanged and no
application code knows the difference.

Two things do differ, and both have cost people time:

* **its CLI is `memurai-cli`, not `redis-cli`** — `redis-cli ping` will not be found;
* **its service is `Memurai`**, so it starts on boot and the fix when it is down is
  `Start-Service Memurai`, not launching a binary.

```powershell
memurai-cli ping        # expect: PONG
Get-Service Memurai     # expect: Running
```

### 2.4 Qdrant — a zip with one binary

No installer, no service. Unzip it somewhere permanent and put that folder on `PATH`;
`start-windows.ps1` launches it in its own window and `stop-windows.ps1 -Stores` stops
it. Qdrant is the **one** vector engine — both `aegis.retrieval` and LightRAG read the
same node, so there is nothing to keep in sync.

```powershell
curl http://localhost:6333        # expect JSON naming the version
```

### 2.5 Neo4j Desktop — the step that cannot be scripted

Neo4j Desktop is a GUI that ships its own JDK, and an **instance** is created
interactively with the password chosen in that dialog. No script can do it, so do it
once by hand:

1. Open **Neo4j Desktop**
2. **Local instances → Create instance**
3. Choose a password — **write it down**, it goes into `backend\.env` in §3
4. **Start** the instance
5. Confirm: something is now listening on **7687**

Leave the username as `neo4j` and the bolt port as `7687`; `backend\.env` assumes both.

**If you skip this,** the platform still runs. `GET /v1/graph` degrades and the graph
screen is empty; retrieval falls back to its vector and keyword arms. Nothing else
changes. That is a deliberate design property, not a workaround.

### 2.6 Temporal — needed before any document is ingested

Ingestion is a six-stage durable workflow, and the workflow needs a Temporal server.
Without it the API still answers questions, but an uploaded document sits at
`pending` forever with no error anywhere — so if documents never leave `pending`,
check this first.

```powershell
temporal server start-dev        # start-windows.ps1 does this for you
```

---

## 3. Configure `backend\.env`

`scripts\bootstrap.ps1` writes this file from the template. Then fill in four things:

```ini
# The serving DSN. The password is the one you set in 2.2.
POSTGRES_DSN=postgresql://aegis_app:<password>@localhost:5432/taif
POSTGRES_ADMIN_DSN=postgresql://postgres:<superuser-password>@localhost:5432/taif

# Neo4j — the password from the Desktop dialog in 2.5.
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<the password you chose>

# Memurai answers on the Redis port under the Redis URL scheme.
REDIS_URL=redis://localhost:6379/0
QDRANT_URL=http://localhost:6333

# The model gateway. Without a key the platform runs and refuses to invent answers.
GATEWAY_BASE_URL=...
GATEWAY_API_KEY=...
```

**Never set `NEXT_PUBLIC_API_BASE` in `web\.env.local`.** The start script passes it
per-run. A value written into that file outlives every port change and is invisible
from the browser, which is a bad hour to spend.

---

## 4. Database roles and row-level security

This is the step most easily skipped and the one that silently matters most.

```powershell
.\scripts\db-roles.ps1
```

It creates `aegis_app` — a **non-superuser, NOBYPASSRLS** serving role. Without it the
app connects as an owner, every RLS policy installs correctly and **filters nobody**.
Verify, and accept nothing but `ENFORCED`:

```powershell
cd backend
$env:PYTHONPATH="src;..\aegis\src"
.venv\Scripts\python -m app.data.rls_check
# ENFORCED    serving role 'aegis_app' is subject to RLS (owner DSN split)
```

---

## 5. One-time setup

```powershell
cd backend
.venv\Scripts\python -m app.seed        # tenants, users, budgets, documents
.venv\Scripts\python -m app.ml          # trains the ML spine (~10s, offline)
```

The ML spine must be trained **once** or `/v1/ml/explain` and `/v1/ml/model-card`
return **503** — deliberately. The old behaviour trained on demand and, when the
domain adapter was unimportable, quietly fitted the built-in *noise synthesiser* and
served it as domain evidence.

---

## 6. Start and stop

```powershell
.\backend\scripts\start-windows.ps1      # stores, then API, then console
.\backend\scripts\stop-windows.ps1       # API + console only
```

`start-windows.ps1` starts the Postgres and Memurai **services**, launches Qdrant and
Temporal in their own windows, reports Neo4j, and only then starts the API — refusing
if Postgres is down. Useful flags:

| Flag | Effect |
|---|---|
| `-Skip Web` | API only, no console |
| `-NoInfra` | check the stores, never start them |
| `stop … -Stores` | also stop Qdrant and Temporal |
| `stop … -Services` | also stop the Postgres and Memurai services (prompts first) |

`stop-windows.ps1` stops **only the API and console by default**, on purpose: those
services are shared with the rest of your machine, and a stop script that shuts down
your database because you wanted to restart an API is one nobody runs twice. It finds
processes by the port they hold, never by image name — `Stop-Process -Name python`
would also take out your other checkout and the shell doing the killing.

Neo4j Desktop is never stopped from a script. Close it from its own window; killing
its JVM from underneath it is how an instance ends up needing repair.

---

## 7. Verify

| # | Check | Command | Expect |
|---|---|---|---|
| 1 | Stores answering | `.\scripts\preflight.ps1` | every row green except Neo4j if you skipped it |
| 2 | RLS enforced | `python -m app.data.rls_check` | `ENFORCED` |
| 3 | API alive | `curl http://127.0.0.1:8000/health` | `{"status":"ok"}` |
| 4 | Console | open `http://localhost:3000` | the login page |
| 5 | Backend suite | `cd backend; .venv\Scripts\python -m pytest -q` | all pass |

---

## 8. Windows-specific troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `redis-cli` not recognised | Memurai's CLI is `memurai-cli` | use that name |
| Redis port dead, no binary to run | Memurai is a **service** | `Start-Service Memurai` |
| Documents stuck at `pending`, no error | Temporal is not running | `temporal server start-dev` |
| Graph screen empty, everything else fine | No Neo4j instance | §2.5 — create one in Desktop |
| `.ps1` refuses to run | ZIP download mark | `Unblock-File` (§0) |
| Service will not start | Not elevated | reopen PowerShell as administrator |
| `rls_check` prints anything but `ENFORCED` | App connects as owner | run `db-roles.ps1` (§4) |
| API starts, tenants see each other's data | Same as above | same as above — this is what §4 prevents |

---

# 9. Long-form reference

Everything below predates the guide above and is kept for the settings it is still the
only record of. It names port 8000 where some deployments run 8110, and it describes a
`-Mode lite` that no longer exists — that mode swapped Postgres for SQLite and silently
disabled every tenant-isolation policy in the platform. **Where the two halves
disagree, sections 0–8 win.**

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
log. No `pgvector` extension is needed — vector ANN search runs in Qdrant.

**Vector store — Qdrant, one node, unzip and run.** ANN for retrieval + memory recall
runs in **Qdrant**, and so do LightRAG's internal vectors (`QdrantVectorDBStorage`):
one engine, one URL, one thing to install. v1.19.0 publishes
`qdrant-x86_64-pc-windows-msvc.zip` — Apache-2.0, a zip with a binary — so there is no
Docker, no installer and no Windows service, which is what keeps Aegis installable on a
locked-down enterprise machine. Unzip it, run `qdrant.exe`, and point `QDRANT_URL` at it
(default `http://localhost:6333`).

It used to be embedded, and that is exactly what changed. An embedded vector store is
**single-process**: Chroma's `PersistentClient` holds a SQLite metadata lock, so
`uvicorn --workers 2` failed in a way that looked like index corruption rather than a
configuration error. Aegis now **refuses to boot** with more than one worker while an
embedded store is configured, and the durable path is a node every worker can share.
In full stores mode `QDRANT_URL` is **required** and the backend fails loud at boot if
the node does not answer (exactly like Postgres/Redis) — it never degrades to a silent
in-process index. Tests and dev use `qdrant_client`'s in-process mode, chosen out loud,
which is a real index, not a fake.

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
> in Qdrant (Postgres keeps only the JSON embedding-of-record). Set
> `AGENT_CHECKPOINTER=postgres` for durable, resumable HITL runs; the `memory` default
> keeps single-process/offline runs zero-dependency.

> **If Qdrant's storage is ever lost or replaced, the corpus is not.** Postgres holds the
> embedding of record, so the search index is rebuilt from it — no re-parse, no re-embed,
> no provider spend:
>
> ```bash
> cd backend            # venv active
> python -m app.ingestion --verify     # audit only, writes nothing; exits 1 on drift
> python -m app.ingestion --reindex    # replay chunks.embedding into Qdrant
> ```
>
> Run `--verify` after any vector-store change. An empty index is invisible from the
> outside — retrieval simply answers with nothing — so nothing else will tell you. See
> *Rebuilding the dense index* in `docs/operations/runbook.md` for the scoped forms.

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

| Username | Password | Coarse role | Persona           | Portal                   |
|----------|----------|-------------|-------------------|--------------------------|
| `admin`  | `demo`   | `admin`     | `operations_lead` | `/app/platform_admin/…`  |
| `ai`     | `demo`   | `ai_team`   | `operations_lead` | `/app/ai_team/…`         |
| `aiteam` | `demo`   | `ai_team`   | `operations_lead` | `/app/ai_team/…`         |
| `devops` | `demo`   | `devops`    | `operations_lead` | `/app/devops/…`          |
| `client` | `demo`   | `client`    | `client`          | `/app/client/…`          |

Every operational role (`admin` / `ai_team` / `devops`) maps to the full
`operations_lead` persona; `client` gets the self-scoped `client` persona. `admin` has no
tenant, which is what makes it the `platform_admin` tier.

**The URL carries the fine role, not the coarse one** (§7.2). A tenant's own admin —
`northwind.admin` below — lands on `/app/tenant_admin/…`, a different portal with
different sections, because administering one tenant and operating the platform are
different jobs. Signing in is what decides which; there is no way to type your way into
the other one.

**Two tenants**, each with a tenant admin, two users, a daily budget and three documents:

| Tenant | Tenant admin | Users |
|---|---|---|
| Northwind Trading | `northwind.admin` | `northwind.analyst`, `northwind.client` |
| Vertex Logistics  | `vertex.admin`    | `vertex.analyst`, `vertex.client`       |

…and **one parked approval each**, plus one un-tenanted gate of Aegis's own, so the
approvals inbox has something real in all three of its scopes on a fresh database. Sign
in as `northwind.admin` to decide Northwind's; as `admin` to decide Aegis's own and to
see (without deciding) both tenants'; as `client` to see the fate of the gate that
account raised.

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
| `QDRANT_URL` | `http://localhost:6333` | the **Qdrant node** — the one vector engine, read by both `aegis.retrieval` and LightRAG. **Required** in full stores mode and the backend fails loud at boot if it does not answer. |
| `QDRANT_API_KEY` | *(empty)* | token for a secured Qdrant node; leave empty for a local one |
| `VECTOR_STORE_PATH` | `vector_storage` | LightRAG's local working directory (its own bookkeeping — no vectors, no KV) |
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
- **Vector store unreachable at boot (full stores mode)** — the backend fails loud if
  nothing answers on `QDRANT_URL`. Unzip Qdrant and run `qdrant.exe` (it listens on
  6333), or set `STORES=off` for the databaseless lite demo.
- **`uvicorn --workers 2` refuses to boot** — that is deliberate. It means an *embedded*
  vector store is configured, and an embedded store is single-process: the second worker
  would diverge from or corrupt the first worker's index while every health check stayed
  green. Set `QDRANT_URL` to a running node, or run one worker.
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

See `docs/architecture/system-architecture.md` (the whole system),
`web/README.md` (console context), and `README.md` for architecture.
