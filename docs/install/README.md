# Install — the hackathon machine

Follow these in order on the Windows box. Each file is a runbook you work through top to
bottom; each ends with a check that either passes or tells you what is wrong.

| # | File | What it sets up |
|---|---|---|
| 1 | [`01-postgres.md`](01-postgres.md) | PostgreSQL 17 **and the non-superuser serving role** |
| 2 | [`02-services.md`](02-services.md) | Memurai, Neo4j Desktop, Temporal |
| 3 | [`03-app.md`](03-app.md) | Python + Node dependencies, schema bootstrap, seed |
| 4 | [`04-verify.md`](04-verify.md) | The full check, and the numbers Phase 3 still needs |

---

## Order, and why it is not arbitrary

**Postgres must be first**, because `scripts\db-roles.ps1` runs against it and everything
else assumes the serving role exists.

**The role step is not optional.** Skip it and the app connects as `postgres`, a superuser.
PostgreSQL skips row-level security **entirely** for a superuser — so all thirteen tenant
policies install, appear correct in `pg_policies`, and filter nobody. That was a real defect
in this codebase, found by measurement, and it is the single easiest way to reintroduce it.

Temporal is last only because nothing depends on it during setup. It is already verified
working on this machine.

---

## What is already on this box

| Component | Where | State |
|---|---|---|
| PostgreSQL 17.11 | `Downloads\postgresql-17.11-1-windows-x64.exe` | installer, not run |
| Memurai 8.2 RC2 | `Downloads\Memurai-for-Redis-v8.2-RC2.msi` | installer, not run |
| Neo4j Desktop 2.2.1 | `Downloads\neo4j-desktop-2.2.1-x64.exe` | installer, not run |
| Temporal CLI 1.8.2 | `Downloads\temporal_cli_1.8.2_windows_amd64\temporal.exe` | **verified running** |

Machine: Windows 11 Pro, x64, Intel Core 5 220U, 15.55 GB RAM, Python 3.12.8, no Docker.

**x64 matters.** `temporalio` publishes a `win_amd64` wheel and no `win_arm64` one. This box is
x64, so that is settled — but if you ever move to an ARM machine, Temporal is off the table and
the fallback substrate in `phase-03-platform-spine.md`'s git history is what replaces it.

---

## Two habits that will save you

**Read the check at the end of each file before starting it.** Every runbook here ends with a
command whose output tells you whether the step worked. If you know what you are aiming at, a
wrong turn is obvious immediately rather than three steps later.

**When something says it failed, believe it.** This project's recurring defect is a control
that reports healthy while doing nothing. If a check prints a warning, it is not noise.
