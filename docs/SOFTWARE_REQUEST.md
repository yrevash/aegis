# Additional Software Requirements — for IS team (free / open-source)

> For the TAIF S2 Regional Finals laptops (16 GB Windows). All items below are
> free / open-source. Requested so the IS team can pre-install and review ahead of
> **Tue 4 Aug EOD**, saving setup time during the event. Priority ordered.

## ⭐ Highest leverage (please prioritise)

| # | Software | Source | Why we need it |
|---|----------|--------|----------------|
| 1 | **WSL2 + Ubuntu 22.04** (Windows feature; needs admin + reboot) | Microsoft Store / `wsl --install` | Lets us install Postgres, pgvector, Redis, Neo4j cleanly in Linux via `apt`, avoiding fragile Windows-native builds. Single biggest time-saver. |
| 2 | **PostgreSQL 16** + **pgvector** extension | postgresql.org (EDB installer) / apt | Our vector + relational store + audit log. `pgvector` is the tricky bit on Windows — pre-installing it for PG16 saves hours. |
| 3 | **Neo4j Community Server 5.x** + **OpenJDK 21 (Temurin)** | neo4j.com / adoptium.net | Knowledge-graph store. Needs a JDK. |
| 4 | **Redis** (via WSL2 apt) **or Memurai Developer** (free) | redis.io / memurai.com | Semantic cache. Windows has no native Redis; Memurai is the free Windows option if WSL2 isn't granted. |

## Runtimes & tooling

| # | Software | Version | Why |
|---|----------|---------|-----|
| 5 | **Python** (64-bit) | 3.11.x or 3.12.x | Backend runtime. We use `uv` (single binary, no admin) for envs. |
| 6 | **Node.js LTS** + **pnpm** (via Corepack) | 20.x LTS | Frontend (Vite + React) build/dev. |
| 7 | **Git for Windows** | latest | Version control (if not already present). |
| 8 | **Microsoft C++ Build Tools** | VS 2022 Build Tools | Insurance so any Python package that lacks a prebuilt wheel can compile. |
| 9 | **VS Code** + **Graphviz** | latest | Editor; Graphviz for architecture diagrams. Nice-to-have. |

## 🔑 Access / network (please confirm — as important as the installs)

- **Local admin rights** on the laptops (or IS to enable WSL2 + install the above),
  so we can create venvs and install pip/npm packages during the event.
- The **GenAI Wi-Fi must allow outbound HTTPS** to:
  - the model gateway `https://genailab.tcs.in` (our only remote dependency),
  - **PyPI** (`pypi.org`, `files.pythonhosted.org`) and the **npm registry**
    (`registry.npmjs.org`) — otherwise `pip`/`pnpm install` fails on the day.
  If the network is locked down, we will pre-bundle dependencies offline instead —
  please tell us which.

## Not requested (and why)

- **Docker Desktop** — not free at TCS's org size (paid license), ~2–4 GB RAM
  overhead on a 16 GB machine, and needs WSL2/admin anyway. Our architecture is
  container-free by design. *If containers are ever needed, we'd use **Podman**
  (Apache-2.0) or Docker Engine inside WSL2 — but neither is required.*
- **GPU / local model weights** — none needed; all model calls go through the API
  gateway.

## Our own mitigation

We will prepare an **offline dependency bundle** (Python wheels via `uv`, a pnpm
store) as a fallback in case registry access is restricted on the event network.
