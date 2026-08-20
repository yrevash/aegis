# 01 — What to install

Versions are what this was built and verified against on 2026-08-21. Where a
version matters, it says why.

## Toolchain

| | version here | notes |
|---|---|---|
| **Python** | 3.11.11 | 3.11 specifically — the Superset venv is pinned to it |
| **Node** | 25.5.0 | anything ≥20 should work; the web build is Next 15 |
| **uv** | 0.6.7 | used for the Superset venv; `pip` also works but is slower |
| **PostgreSQL** | 14+ | needs `ALTER ROLE`, row-level security, and COLUMN grants |
| **Qdrant** | **1.19.0** | see below — this one is not a suggestion |
| **Redis** | any recent | `REDIS_URL=redis://localhost:6379/0` |

## The four services

**Postgres** — one database, `taif`. Two roles are provisioned by scripts during
bootstrap and should not be created by hand:
- `aegis_readonly` — the database console. `SELECT` and nothing else, with
  `users.password_hash` withheld by a **COLUMN grant**, so the column is absent
  from `information_schema` rather than filtered in application code.
- `aegis_superset` — owns the `analytics_*` views so their row-level security
  actually engages.

**Qdrant 1.19.0** — the vector store. Download the release binary, or run the
container. It listens on `:6333`.

> Chroma was removed entirely in `3dafbdb`. Do not reintroduce it. That migration
> shipped **without a re-index step**, which silently deleted every existing
> embedding — the bug that took the longest to find in this project, because the
> index was empty while the code and the tests were all correct.

**Redis** — the rate limiter's slot leases. A single Lua script does the whole
check-and-take, because three round trips would let two processes take the last
slot.

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
