# Where Aegis stands

Last updated 2026-08-20, after the Phase 8 audit closed.

## Status

**Phases 1–8 complete and audited.** Phase 9 (scale hardening) and Phase 10 (MCP + skills)
remain. Phase 11 (Langflow) is parked.

Verified on a quiet tree at the Phase 8 gate:
`backend 1101 passed / 1 skipped · aegis 2225 passed / 14 skipped · web 139 · ruff clean ·
next build 64/64 · conformance 13/13`

## The working rule for every phase

**build -> verify on a quiet tree -> audit -> fix the findings -> push.** Never push before
the audit closes, and never batch audits to the end. Both were learned the expensive way:
the Phase 6 audit was skipped as "just UI" and had to be retrofitted, and the Phase 7/8
audits caught a cross-tenant prompt leak, a red-team scoring defect, and a `SKILL.md` whose
first two commands could not be run in sequence.

## Open decisions for the user

- **Should a platform-scoped red-team run write `usage_ledger` rows?** `_governed` returns
  `None` with no tenant, so cap *and* ledger are gated behind a bound tenant by design, and
  `estimated_cost_usd` is the only cost such a run will ever have. Diagnosed, not changed.
- **Superset on Windows** — `docs/operations/superset-embedded.md` §6, twelve steps. Step 8
  is the one that matters: that the guest token's RLS clause actually filters rows.
- **The frontend repaint** to `DESIGN.md` (one blue hue, receipts as the signature) is
  planned but not started. Nothing built since Phase 6 has been opened in a browser.

## Known gaps, tracked not hidden

- **Phase 8's own definition of done was never executed**: *"a fresh agent session, given
  only the repo and a one-line problem statement, produces a working adapter — actually
  tried, not assumed."* The audit found no evidence it was attempted. This is the honest
  test of the whole phase.
- `mypy` is not installed in the venv, so the DoD's type-checker claim was verified by
  annotation inspection rather than by running mypy.
- `web/src/lib/stream.ts` still hand-mirrors `StreamEvent`; 8.8 published the union but did
  not generate the console's copy. A mirror test keeps it honest.
- `aegis/governance/dashboard.py:33` early-binds `_set_tenant_scope`, so the H1 spy seam is
  blind to dashboard reads. Pre-existing, harmless today.
- The `extra="ignore"` producer half: `stamp()` would silently drop a key a future event
  builder adds. Latent, not live — verified no current builder does.

## Environment facts that cost time

- **Two backend suites must not run concurrently against this Postgres.** A lane wedged 20
  minutes at 0% CPU on a `TRUNCATE` blocked by an `idle in transaction` session. Check
  `pg_stat_activity`; do not assume a code deadlock.
- **Temporal must be running for any ingest**: `temporal server start-dev`.
- **Qdrant** is installed and running locally for Phase 9 verification (v1.19.0, `:6333`).
- The gateway points at **Azure AI Foundry** (DeepSeek-V4-Flash + text-embedding-3-large),
  not genailab. `backend/.env` holds both; the genailab lines are commented directly above
  the Azure ones and are restored by uncommenting two lines. **Azure calls cost real money —
  never let a test reach the gateway.**
