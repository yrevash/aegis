# 3 — The application

**~10 minutes**, mostly download time.

---

## Dependencies

```powershell
.\scripts\bootstrap.ps1
```

That creates both virtualenvs, installs the Python and Node dependencies, and trains the ML
spine once so `/ml/explain` and `/ml/model-card` answer instead of returning 503.

**Python 3.12.8 on this box; the repo requires `>=3.11`** and is developed on 3.11. That is
compatible — `temporalio` ships a `cp310-abi3` wheel that covers 3.12 — but pin the venv
explicitly rather than letting it pick whatever is on PATH.

## Keys

`backend\.env` needs:

| Key | Notes |
|---|---|
| `GENAILAB_API_KEY` | the model gateway — **currently `replace-me`** |
| `TAVILY_API_KEY` | Tavily, for the research agent (Phase 5). The file used to say `TRAVILY_API_KEY` — a name nothing read, which is why search never worked. Only `TAVILY_API_KEY` is read now; unset is fine and degrades loudly to internal-only |
| `NEO4J_PASSWORD` | from step 2 |
| `POSTGRES_DSN` / `POSTGRES_ADMIN_DSN` | written for you by `db-roles.ps1` |

## Schema and seed

```powershell
cd backend
$env:PYTHONPATH="src;..\aegis\src"
.\.venv\Scripts\python.exe -c "import asyncio; from app.data.session import bootstrap; asyncio.run(bootstrap())"
```

This runs `create_all`, the additive schema reconciler, the RLS bootstrap and the serving-role
grants — on the **admin** connection, which is the only place DDL belongs.

Watch the log for the catalog read-back. It reports any live table carrying `tenant_id` that
has no policy. **A warning there is the diagnostic working; fix the gap rather than ignoring
the line.**

---

**Next:** [`04-verify.md`](04-verify.md)
