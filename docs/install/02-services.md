# 2 — Memurai, Neo4j, Temporal

**~20 minutes.** Memurai and Neo4j need the admin password; Temporal needs nothing.

---

## Memurai (Redis-compatible cache)

```powershell
msiexec /i "$env:USERPROFILE\Downloads\Memurai-for-Redis-v8.2-RC2.msi"
```

Defaults are fine — port **6379**, installed as a Windows service so it starts with the box.

**Check:**

```powershell
memurai-cli ping     # → PONG
```

The CLI is `memurai-cli`, not `redis-cli`. Same wire protocol, so the application needs no
change either way.

> **On the record, because it will be asked:** the platform uses **Redis**. Memurai is the
> Windows-native build of it, which is what makes a Docker-less install possible. A production
> deployment uses Redis directly.

---

## Neo4j Desktop (graph store)

```powershell
& "$env:USERPROFILE\Downloads\neo4j-desktop-2.2.1-x64.exe"
```

Then in the app: create a local DBMS, set a password, **start it**, and confirm the Bolt port
is **7687**.

Put the password in `backend\.env` as `NEO4J_PASSWORD`.

**Check:**

```powershell
Test-NetConnection localhost -Port 7687 -InformationLevel Quiet   # → True
```

### If RAM gets tight, this is the first thing to drop

Neo4j Desktop is an Electron app plus a JVM database and is the largest single consumer on the
box. The graph arm is positioned as the **explainability** arm, not the quality engine —
measured, LightRAG scores 48.0 against BM25's 74.7 at our corpus size. Nothing in the quality
story depends on it, so if the machine is struggling, close it first and say so plainly.

---

## Temporal (durable orchestration)

Already verified on this machine — CLI 1.8.2, Server 1.31.2, UI 2.50.1. Nothing to install.

```powershell
$T = "$env:USERPROFILE\Downloads\temporal_cli_1.8.2_windows_amd64\temporal.exe"
& $T server start-dev --db-filename "$env:USERPROFILE\.temporal\dev.db" --ui-port 8233
```

Leave it running. The Web UI is at **http://localhost:8233** and is a real replay/inspection
surface — worth having open during a demo.

**Check, in a second terminal:**

```powershell
& $T operator cluster health     # → SERVING
```

### What it is and is not doing

Temporal owns **execution** state — retries, timers, stage resumability, cancellation. It does
**not** hold your data. `documents` and `job_runs` stay in Postgres, tenant-scoped and
RLS-protected, linked only by a `workflow_id` string. That split is the whole reason it was
adopted: durable execution without putting tenant data outside the boundary Phase 1 built.

For a longer-running setup, install it as a service with NSSM rather than leaving a terminal
open.

---

**Next:** [`03-app.md`](03-app.md)
