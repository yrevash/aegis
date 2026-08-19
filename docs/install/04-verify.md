# 4 — Verify, and the numbers Phase 3 still needs

Two parts: a check that the box is correctly set up, and the **three measurements task 3.0
could not take from a Mac**. Both matter; the second is a deliverable.

---

## The checklist

Work down it. Anything that fails, stop and fix — later steps assume the earlier ones.

| # | Check | Command | Expected |
|---|---|---|---|
| 1 | Postgres up | `psql -U postgres -c "SELECT 1;"` | `1` |
| 2 | **RLS genuinely enforced** | `python -m app.data.rls_check` | `ENFORCED` |
| 3 | Memurai | `memurai-cli ping` | `PONG` |
| 4 | Neo4j | `Test-NetConnection localhost -Port 7687 -InformationLevel Quiet` | `True` |
| 5 | Temporal | `temporal operator cluster health` | `SERVING` |
| 6 | Readiness board | `.\scripts\preflight.ps1` | all green |
| 7 | Backend suite | `pytest -q` in `backend` | passing, no regressions |
| 8 | Aegis suite | `pytest -q` in `aegis` | passing, no regressions |
| 9 | Web builds | `npx next build` in `web` | compiled |

**Check 2 is the one that silently matters.** The other eight fail loudly when wrong. That one
prints `BYPASSED` and everything still appears to work — while every tenant-isolation claim in
the product is false.

---

## Measurement A — memory with everything running

Task 3.0's open question. Start Postgres, Memurai, Neo4j, Temporal, the backend and the web dev
server, then:

```powershell
Get-Process postgres, memurai, java, temporal, python, node -ErrorAction SilentlyContinue |
  Group-Object ProcessName |
  Select-Object Name, @{n='RSS_MB';e={[math]::Round(($_.Group | Measure-Object WorkingSet64 -Sum).Sum/1MB)}} |
  Sort-Object RSS_MB -Descending
```

**Record the total.** For reference, measured on macOS: Temporal dev server **135 MB**, and a
Docling parse peaks at **3,363 MB** on a 126-page table-dense document (Audit C, 2026-08-19,
`docling==2.120.3`). An earlier figure of 2,199 MB in this file was measured on a smaller
document and an older Docling; **peak RSS scales with the document, not just with the models**,
so size the box against the largest document you intend to ingest rather than against a
constant.

**Then answer one question with the number:** does a Docling parse fit alongside everything
else? If it is close, that is the hardware reason parses serialise on a single-slot queue —
**two concurrent parses of a large document is ~6.7 GB** and would be the thing that kills the
box. That is the measurement that justifies `max_concurrent_activities=1` on the CPU queue.

**And record the parse duration**, because it decides what you dare upload live. Measured on
Apple Silicon with MPS: a 15-page text-dense paper is **7 s warm** (16 s in a cold process, the
difference being model load); a 67-page table-dense report is **~215 s**; the 126-page IRS
instructions are **838 s — fourteen minutes**. The Windows box has no GPU, so treat those as
floors. **Do not upload a large document on stage:** the 15-page paper in a warm process is the
demo-safe artefact. A killed parse restarts that stage from zero, so a crash at minute 13 of 14
costs the whole fourteen.

---

## Measurement B — the kill test, for parity

Already passed on macOS. Run it here to confirm Windows behaves the same, because the whole
resumability claim rests on it.

The macOS result, for comparison:

```
parse    pid=8377        ### HARD-KILL
chunk    pid=8377        embed    pid=8610   ← replayed: it never completed
enrich   pid=8377        index    pid=8610
embed    pid=8377        graph    pid=8610

stages that re-ran: ['embed']   ← only the in-flight one
```

`parse`, `chunk`, `enrich` did **not** re-run. Anything different on Windows is a finding worth
stopping for.

**And note what the replayed `embed` proves:** an activity that is not idempotent will
double-write on every crash. That is why every activity is keyed on `(workflow_id, stage)`.

---

## Measurement C — the sandbox boundary

Characterised on macOS, worth confirming here since it decides the module layout:

| At import time | Result |
|---|---|
| `time.time()`, `os.environ.get(...)` | **accepted** |
| `asyncio.run(...)` | **REJECTED** — `RuntimeError: Failed validating workflow` |

The rule is therefore **"a workflow module must not run an event loop at import"** — not "keep
it pure". Ordinary module-level constants are fine.

---

## When it is all green

Record the three measurements in the Phase 3 PR, and mark task 3.0's Windows leg done in
[`../dev_new_docs_v2/phase-03-platform-spine.md`](../dev_new_docs_v2/phase-03-platform-spine.md).

## If something is wrong

| Symptom | Cause |
|---|---|
| `rls_check` says `BYPASSED` | `backend\.env` still points at `postgres`; re-run `db-roles.ps1` |
| Catalog read-back warns about a table | A tenant-scoped table is unregistered — add it to `_TENANT_SCOPED_TABLES`, never silence the warning |
| PowerShell refuses to run a script | Downloaded as a ZIP; `Get-ChildItem -Recurse .\scripts\*.ps1 \| Unblock-File` |
| Workflow fails validation | Its module runs an event loop at import — move the definition into `backend\src\app\jobs\flows\` |
| `/ml/explain` returns 503 | The spine was never trained: `python -m app.ml` |
