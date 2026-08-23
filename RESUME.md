# Where Aegis stands

Last updated 2026-08-24. The hackathon starts **2026-08-30**.

## Status

**Phases 1–10 complete and audited.** Phase 11 (Langflow) is parked by decision —
`docs/dev_new_docs_v2/phase-11-langflow.md` says so and nothing may take a dependency on it.

Verified on a quiet tree:
`backend 2112 passed / 1 skipped · aegis 2388 passed / 14 skipped · web 370 · ruff clean ·
tsc clean · openapi current`

Compliance: **37 of 114 mapped controls enforced** · 53 partial · 19 not implemented · 5 not
applicable. Two frameworks enforced in every control that applies here — **NIST AI RMF** (4 of
4) and **MITRE ATLAS** (9 of 9 applicable; `AML.T0018` cannot apply, no downloaded model
artefact exists). The public band derives that list; nothing names a framework in code.

## The working rule for every phase

**build → verify on a quiet tree → audit → fix the findings → push.** Never push before the
audit closes, and never batch audits to the end. Both were learned the expensive way: the
Phase 6 audit was skipped as "just UI" and had to be retrofitted, and the Phase 7/8 audits
caught a cross-tenant prompt leak, a red-team scoring defect, and a `SKILL.md` whose first two
commands could not be run in sequence.

## Open decisions for the user

- **Should a platform-scoped red-team run write `usage_ledger` rows?** `_governed` returns
  `None` with no tenant, so cap *and* ledger are gated behind a bound tenant by design, and
  `estimated_cost_usd` is the only cost such a run will ever have. Diagnosed, not changed.
- **"Certified against none."** — four words still in the landing band's heading. The amber
  readiness-not-certification banner above the grid is gone; this is what remains of it.
- **The forecast has 2 distinct ledger days against the 71 it needs.** Either seed ~90 days
  narrowly, or leave the honest refusal standing on three screens.

## Known gaps, tracked not hidden

- **Vertex Logistics has no corpus.** Its three documents are metadata-only seed stubs
  (151 bytes, no file, no workflow), so any retrieval demo as `vertex.client` returns nothing.
  Northwind's corpus is real but carries test uploads (`notif-live-*`, `zz-markall-*`) that
  rank top on some client runs.
- **Voice and Vision are down** in this environment — both hosted deployments answer
  `NotFoundError: The API deployment for this resource does not exist`. They fail closed, so
  they still demo as refusals; no successful transcription or image analysis can be shown.
- **`audit_log` meets CERT-In's 180 days by the absence of a deleter, not by a control.**
  There is no retention job that keeps it and no test that would notice one being added.
- `mypy` is not installed in the venv, so the type-checker claim is verified by annotation
  inspection rather than by running mypy.
- The `extra="ignore"` producer half: `stamp()` would silently drop a key a future event
  builder adds. Latent, not live — verified no current builder does.

## Environment facts that cost time

- **Two backend suites must not run concurrently against this Postgres.** A lane wedged 20
  minutes at 0% CPU on a `TRUNCATE` blocked by an `idle in transaction` session. Check
  `pg_stat_activity`; do not assume a code deadlock.
- **The live backend runs against `taif_run1`, but `backend/.env` says `taif`.** The running
  process carries the override. Restart it from `.env` alone and you are looking at a
  different database with different numbers — this is the same class of bug as Superset
  pointing at the wrong database and rendering confidently.
- **Temporal must be running for any ingest**: `temporal server start-dev` (`:7233`).
- **Qdrant** runs locally, v1.19.0 on `:6333`. **Superset** on `:8088`.
- The gateway points at **Azure AI Foundry** (`DeepSeek-V4-Flash` + `text-embedding-3-large`),
  **not** genailab. `backend/.env` holds both; the genailab lines are commented directly above
  the Azure ones and are restored by uncommenting them. **Restore genailab before demo day** —
  and note that chunk text leaves this machine for embedding, to a US Azure region, so "tenant
  data never leaves" is not a sentence anyone should say.
- **Azure calls cost real money — never let a test reach the gateway.**
- All three text roles (`MODEL_GENERATION`, `MODEL_CHEAP`, `MODEL_REASONING`) point at one
  deployment, so small-model routing has nothing to route between. `GET /v1/savings` detects
  this from the ledger and reports `$0 saved` with the figure as `projected_usd`; it flips
  back on its own once a multi-deployment fleet returns.
- Live posture: `GUARDRAILS_ENGINE=both`, `AGENT_CHECKPOINTER=postgres`.
