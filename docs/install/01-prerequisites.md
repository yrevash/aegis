# 01 — What to install

Versions are what this was built and verified against on 2026-08-21. Where a
version matters, it says why.

## Toolchain

| | version here | notes |
|---|---|---|
| **Python** | 3.11.11 | 3.11 specifically — the Superset venv is pinned to it |
| **Node** | 25.5.0 | anything ≥20 should work; the web build is Next 15 |
| **uv** | 0.6.7 | used for the Superset venv; `pip` also works but is slower |
| **PostgreSQL** | 14+ (17.11 on the hackathon box) | needs `ALTER ROLE`, row-level security, and COLUMN grants |
| **Qdrant** | **1.19.0** | see below — this one is not a suggestion |
| **Redis** | any recent | `REDIS_URL=redis://localhost:6379/0` |
| **Neo4j** | Desktop 2.2.1 / any 5.x | the knowledge-graph store, Bolt on `:7687` |

## The five services

**Postgres** — one database, `taif`. **Three** roles, none of them created by hand:

- `aegis_app` — the **serving** role the application connects as. `LOGIN
  NOSUPERUSER NOBYPASSRLS`, created by `scripts/db-roles.sh` (`.ps1` on Windows),
  which also rewrites `backend/.env` so `POSTGRES_DSN` points at it and
  `POSTGRES_ADMIN_DSN` points at the owner. **This split is not cosmetic** —
  PostgreSQL skips row-level security *entirely* for a superuser, and `FORCE ROW
  LEVEL SECURITY` removes only the *owner's* exemption, not that one. This platform
  connected as `postgres` for its whole early life, so thirteen tenant-isolation
  policies were installed, visible in `pg_policies`, reviewed — and enforced against
  nobody. Step 1 of `02-bootstrap.md` has the check that proves which state you are in.
- `aegis_readonly` — the database console. `SELECT` and nothing else, with
  `users.password_hash` withheld by a **COLUMN grant**, so the column is absent
  from `information_schema` rather than filtered in application code.
- `aegis_superset` — owns the `analytics_*` views so their row-level security
  actually engages.

The owner role (`POSTGRES_ADMIN_DSN`) is used only for DDL, the RLS bootstrap and
the grants. It is also the only role that can rewrite `audit_log`, `usage_ledger`
or `run_events`: the serving role holds `SELECT, INSERT` on those three and nothing
more, so `DELETE FROM audit_log` on a request connection is refused by Postgres
rather than by application code.

**Neo4j** — the graph half of hybrid retrieval, Bolt on `:7687`. Set `NEO4J_URI`,
`NEO4J_USER` and `NEO4J_PASSWORD` in `backend/.env`; LightRAG reads them from the
environment, which `aegis.retrieval.lightrag_backend` populates from config.
Retrieval's dense arm works without it, but the graph arm and `GET /v1/graph` do
not. It is the largest single memory consumer on a 16 GB box — an Electron app plus
a JVM — so if RAM gets tight it is the first thing to close, and say so plainly
rather than pretending the graph arm is running.

**Qdrant 1.19.0** — the vector store. Download the release binary, or run the
container. It listens on `:6333`.

> Chroma was removed entirely in `3dafbdb`. Do not reintroduce it. That migration
> shipped **without a re-index step**, which silently deleted every existing
> embedding — the bug that took the longest to find in this project, because the
> index was empty while the code and the tests were all correct.

**Redis** — the rate limiter's slot leases (a single Lua script does the whole
check-and-take, because three round trips would let two processes take the last
slot) and the notification fan-out (pub/sub, so an alert written by a Temporal
worker reaches a browser attached to a different process). On Windows use
**Memurai**, the Windows-native Redis build — same wire protocol, so the
application needs no change; the CLI is `memurai-cli`, not `redis-cli`. On the
record, because it will be asked: the platform uses Redis; Memurai is what makes a
Docker-less Windows install possible.

**Temporal** — only needed to ingest *new* documents. Retrieval works without it.
There is no `temporal` CLI on the build machine; a dev server is started in-process
(see `02-bootstrap.md` step 7). It listens on `:7233` over **gRPC**, so `curl`
returning `000` is correct — check with `nc -z 127.0.0.1 7233`.

**Superset 6.1.0** — optional, adds the embedded dashboards. `scripts/superset.sh`
installs it into `.superset/` **inside the project**, which is gitignored. It lives
there rather than in a temp directory for a reason recorded in
`docs/operations/superset-embedded.md`.

## Environment

`backend/.env` is **not** in git and must be recreated. The variable *names* the
running deployment sets:

```
POSTGRES_DSN  POSTGRES_ADMIN_DSN  QDRANT_URL  REDIS_URL  STORES  DB_BOOTSTRAP  APP_ENV
NEO4J_URI  NEO4J_USER  NEO4J_PASSWORD  AGENT_CHECKPOINTER
AZURE_API_KEY  AZURE_END_POINT  MODEL_GENERATION  MODEL_REASONING  MODEL_CHEAP  MODEL_EMBEDDING
GENAILAB_API_KEY  GENAILAB_BASE_URL  GENAILAB_SSL_VERIFY  GATEWAY_API_KEY  GATEWAY_BASE_URL
TAVILY_API_KEY  PHOENIX_ENABLED  LOG_LEVEL
AEGIS_DB_CONSOLE_ENABLED  AEGIS_DB_CONSOLE_DSN
AEGIS_SUPERSET_ENABLED  AEGIS_SUPERSET_BASE_URL  AEGIS_SUPERSET_USERNAME
AEGIS_SUPERSET_PASSWORD  AEGIS_SUPERSET_PROVIDER  AEGIS_SUPERSET_BOARDS
AEGIS_SUPERSET_EMBED_ENABLED  AEGIS_MCP_SERVER_URL
```

Embeddings are `text-embedding-3-large` at **`EMBED_DIM = 3072`**. Changing the
embedding model means re-embedding the corpus; the dimension is not negotiable
against an existing index.

`AEGIS_SUPERSET_BOARDS` must be an **absolute path** to
`docs/operations/superset/aegis-boards.json`. A relative path resolves against the
backend's working directory and the analytics page then reports, correctly, that
it cannot read the catalogue.
