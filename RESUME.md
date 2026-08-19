# Resume notes — paused 2026-08-19

Working tree is clean. Everything is committed and pushed. Nothing in progress on disk.

## Where we stopped

**Phases 1–7 are complete.** Phase 8 is 4 of 12 done, 3 paused mid-task, 5 not started.

Suites at the last clean measurement (commit `8fcddaa`):
`backend 1089 passed / 1 skipped · aegis 2216 passed / 14 skipped · web 137 · ruff clean`

The `wip` checkpoint `7ac8c36` on top of that is **not verified** — three lanes were
stopped mid-edit. Run all three suites before trusting it.

## The three paused lanes — resume by relaunching with these briefs

| Task | Stopped at | Owns |
|---|---|---|
| **8.6 + 8.7 + 8.8** — `/v1` prefix, OpenAPI snapshot, generated TS client, `StreamEvent` schema | just before "the mutation proof and the full backend suite" — likely nearly done | every `backend/src/app/api/routes*.py`, `ingest_log.py`, `schemas.py`, `main.py`, `web/src/lib/api/*` |
| **8.3** — `Aegis.from_env(adapter=...)` | "the package export" | a new runtime module + `aegis/src/aegis/__init__.py` |
| **8.5** — conformance suite | about to run against the reference adapter | `aegis/src/aegis/conformance/`, `aegis/pyproject.toml` |

**8.3 was deliberately forbidden from editing `backend/src/app/main.py`** (the `/v1` lane
owns it) and was told to report the replacement for the ten `configure_*` calls instead.
That report was never delivered — the main.py consolidation still has to happen, and it
must happen *after* the `/v1` lane lands.

## Phase 8 — not started

- **8.7's dependency chain**: 8.6 → 8.7 → 8.8 are one lane and must stay one lane.
- Nothing else outstanding in Phase 8 beyond finishing the three above.

## Next after Phase 8, in the user's stated order

1. **Frontend testing pass.** The user wants to drive the UI in a browser and iterate
   before any scale work. This matters: this session added the approvals inbox, five
   portals, admin forms, generated settings screens, memory management, red team,
   analytics, reports, forecast, the database console and seats — **none of it has been
   looked at in a browser.** All of it is verified only by types, tests and `next build`.
2. Phases 9 (scale hardening) and 10 (MCP/skills). Phase 11 (Langflow) is parked.

## Open decisions for the user

- **Should a platform-scoped red-team run write `usage_ledger` rows?** Today `_governed`
  returns `None` when there is no tenant, so cap *and* ledger are both gated behind a
  bound tenant by design. `estimated_cost_usd` is the only cost figure such a run will
  ever have. Diagnosed, not changed — widening it would start ledgering every ungoverned
  flow in the product.
- **Superset runbook verification on Windows** — `docs/operations/superset-embedded.md` §6,
  twelve steps. Four things remain unverified there, the important one being step 8: that
  the guest token's RLS clause actually filters rows.
- Stale counts in the root `README.md` ("18 packages, 723 tests", "51 endpoints, 593
  tests"). Left deliberately — they move every hour while lanes are landing.

## Environment notes that cost time today

- **Two backend suites must not run concurrently against this Postgres.** A lane wedged
  for 20 minutes at 0% CPU on a `TRUNCATE` blocked by a relation lock held by an
  `idle in transaction` session. Check `pg_stat_activity`, do not assume a code deadlock.
- **Temporal must be running** for any ingest: `temporal server start-dev`. Without it
  `/documents` fails at boot with a clear message naming the fix.
- The gateway currently points at **Azure AI Foundry** for testing, not genailab.
  `backend/.env` holds both; the genailab lines are commented directly above the Azure
  ones and are restored by uncommenting two lines. DeepSeek-V4-Flash is a demo fleet —
  genailab will not have it on 30 August.
