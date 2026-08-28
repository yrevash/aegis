# AGENTS.md

Instructions for coding agents working in this repository.
Format: the [AGENTS.md](https://agents.md) spec — read from this checkout, which
is how Aegis is distributed. Humans should start at [`README.md`](README.md).

## What this repo is

Aegis is a domain-agnostic enterprise agentic-AI platform: every autonomous action
is uncertainty-bounded, explainable, guarded, human-approved and fully traced.

**The thesis is import, not fork.** `aegis/` is a library you install; the domain
lives entirely in one adapter directory. If you are making the core learn
something about the domain, you have gone the wrong way.

## Layout

| Path | What it is |
|---|---|
| `aegis/` | **The importable core.** 29 subpackages. Knows nothing about any domain. Versioned; see `aegis/PUBLIC.md`. |
| `backend/` | The FastAPI composition root. Wires the core to real infrastructure. |
| `backend/src/app/adapter/` | **The domain seam** — ten pieces, the only thing that changes per domain. |
| `backend/src/app/a2a/` | The Agent2Agent 1.0 surface: the public card, its signing, and the JSON-RPC endpoint. Read its module docstrings before touching it — every one of them records an attack it closes. |
| `web/` | The Next.js console. Landing page plus five role-scoped portals (`web/src/lib/portal.ts`). |
| `docs/` | `architecture/system-architecture.md` (the system end to end), `install/`, `teaching/` (one document per `aegis` subpackage, plus one per console persona), `adr/`, `security/`, `compliance/`, `governance/`. Index: `docs/README.md`. |
| `scripts/` | `bootstrap` · `preflight` · `start`, each as `.sh` and `.ps1`. |

## Commands

Both suites run from a venv in `backend/`. `PYTHONPATH` is not optional — the
packages are used from source, not installed.

**Every command below is written from the repository root, and each `cd` is wrapped
in a subshell so running them one after another in one terminal works.** Without the
subshell the second command lands in the first command's directory and fails on
`cd: no such file or directory`.

```bash
# Backend suite  (baseline 2026-08-28: 2209 passed, 1 skipped)
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest -q)

# Core package suite  (baseline 2026-08-28: 2424 passed, 14 skipped)
(cd aegis && PYTHONPATH=src ../backend/.venv/bin/python -m pytest -q)

# Lint — must be clean.
# NOT clean on `main` as of 2026-08-28: 33 findings, almost all `I001` import order
# plus a few `F401`/`ANN401`, left behind by the A2A, ragas and trajectory-ceiling
# work. Capture the count BEFORE you start and compare against it, or you will spend
# an hour attributing someone else's import block to your own edit. Do not `--fix`
# files you are not otherwise touching; another agent may be in them.
backend/.venv/bin/python -m ruff check aegis backend

# Console
(cd web && npm run build && npm test)

# Generated API reference -> docs/api/ (git-ignored)
backend/.venv/bin/python scripts/build_api_docs.py

# Conformance — fourteen checks that an adapter is wired right, and that the core
# still knows no domain. No database, no key, no model call, under a second. Every
# check descends from a wiring defect this repo shipped, and every failure names the
# fix. Run it after any adapter edit AND after any core edit — check 11 reads
# `backend/src/app/**` (minus the adapter) and `aegis/**` and fails if a
# shipped-domain string survives there. `--aegis-adapter` is required and its
# absence is one usage error.
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest \
    --pyargs aegis.conformance --aegis-adapter app.adapter -q)

# The HTTP contract -> backend/openapi.json (COMMITTED, snapshot-tested), and the
# TypeScript client generated from it. Run both after changing any route, request
# model, response model or StreamEvent variant; the suites fail if you do not.
backend/.venv/bin/python scripts/build_openapi.py
(cd web && npm run gen:api)
```

**A fresh clone has no `backend/.venv`, so run the installer before any command
above:** `./scripts/bootstrap.sh` (macOS/Linux) or `.\scripts\install-windows.ps1`
(Windows). It is idempotent and needs no Docker, GPU or database.

Install and run: `./scripts/bootstrap.sh && ./scripts/dev-native.sh`, then
`cd web && npm run dev`. Windows: `.\scripts\install-windows.ps1` then
`.\backend\scripts\start-windows.ps1`. There is no Docker, no GPU and no WSL anywhere;
every store is a native local install. Full setup is in `INSTALL.md`.

Three run modes so a demo never depends on infrastructure being healthy: `safe`
(console only, mock transport), `lite` (real agent, no databases), `full`
(everything).

## The one thing you are most likely to be asked to do

**Retarget Aegis to a new problem domain** — read [`SKILL.md`](SKILL.md). It is
the authoritative procedure: the ten adapter pieces, the order to edit them in,
what "done" looks like at each step, and the command that proves it.

`SKILL.md` is the procedure; nothing else in `docs/` supersedes it.

## Boundaries

These are invariants, not preferences. Each one has a reason and most have a test.

1. **Never import `app.*` from `aegis.*`.** The core has zero knowledge of the
   host. This currently holds with no exceptions — verify with
   `grep -rn "^from app\.\|^import app\." aegis/src/` returning nothing.
2. **Never import one leaf `aegis` module from another leaf.** Hoist the shared
   type into `aegis.core`. `aegis.core` itself must stay dependency-free —
   `aegis/tests/core/test_core_is_dep_free.py` imports it in a subprocess and
   fails if litellm, torch, langgraph, xgboost, fastapi, redis, nemoguardrails,
   sqlalchemy, jwt, argon2 or opentelemetry appears in `sys.modules`.
3. **Every tenant-scoped table must be covered by `aegis.governance.rls`.**
   `audit_rls_enforcement` reports any live table that is unprotected. A table
   that stores tenant data and is not in the RLS plan is a cross-tenant leak, and
   this repo has shipped five of those and closed them.
4. **No silent fallbacks.** A control that cannot run **fails closed and says
   so** — it never degrades quietly into something that looks like it worked. An
   in-memory store is returned only when the mode is explicitly `lite`, and it is
   loudly labelled. This is the single most important rule in the codebase: a
   silent downgrade is worse than an outage because nobody goes looking.
5. **Domain logic never leaks into the core.** It goes in
   `backend/src/app/adapter/`. See `SKILL.md`. This one is **enforced**, not merely
   asserted: `aegis.conformance`'s core check scans every module outside the adapter
   for the shipped domain's vocabulary — persona ids, record types, feature keys,
   tool names, the forecast label — and fails naming the file and line. It exists
   because a retarget rehearsal produced an integration that passed every suite and
   was broken in four places, all of them one shipped-domain string in a core module.
   The quarantined word list is `aegis/src/aegis/conformance/_vocabulary.py`; if you
   change what the reference adapter calls things, update it in the same commit (the
   check fails when a listed word no longer appears in the adapter either).
6. **Optional dependencies go through `aegis.core.require(extra, module)`**,
   which raises naming the exact `pip install`. Never `except ImportError: pass`.
7. **A control is not shipped until the caller that matters passes it.** This repo
   has shipped four *declared-but-unbound* seams — `read_back_for`, the first memory
   `screen`, `max_tool_result_tokens`, and the memory-write rail — and the pattern was
   identical every time: the capability was real, tested, and reachable from one
   caller, and the caller on the hot path passed `None`. When you add an optional
   hook, bind it on **every** path that can reach it in the same commit, and prove it
   fired from data rather than from the code reading as if it would. The memory-write
   rail's proof was one query: `select op, count(*) from memory_write_log group by 1`
   returned `ADD | 28` and zero `REFUSED`, ever. `backend/src/app/memory/screen.py`
   exists to make that rail un-forgettable; read its docstring before adding a seam.

## Conventions

- **Python 3.11**, ruff with `E,F,I,UP,B,SIM,ANN,D` and Google-style docstrings,
  line length 100. Annotations are required, including on tests' fixtures where
  ruff asks.
- **Docstrings carry the reasoning**, not just the signature. This repo's
  docstrings explain *why* a thing is the way it is, often naming the defect that
  caused it. Match that; a docstring that restates the parameter names is noise.
- **`aegis/PUBLIC.md` is the API boundary.** 50 Stable names out of 700+ exported.
  Adding to a package's `__all__` is cheap; adding to the Stable table is a
  promise. Removing a Stable name needs `aegis.core.deprecated` and one minor
  release first.
- **`CHANGELOG.md`** — Keep a Changelog format. Anything that changes a contract
  gets an entry.
- **Every HTTP route lives under `/v1`.** The prefix is applied once, in
  `app.main.create_app`; route modules declare bare paths and the console gets the
  segment from `web/src/lib/api/config.ts`. Three families sit outside it, each for a
  stated reason. The infrastructure probes — `/health`, `/ready`, `/readyz` — stay at
  the root and are served at exactly one path each, because a liveness URL that moves
  with the API version 404s halfway through a rollout. The two A2A well-known paths —
  `/.well-known/agent-card.json` and `/.well-known/jwks.json` — stay at the root
  because the specification fixes them and registers them with IANA; a well-known URI
  that is not where the standard says it is has not been served. And the MCP front door
  is an ASGI **mount** at `/mcp` (client URL `/mcp/mcp`), not a route at all: the SDK
  hands over a complete application — transport, auth middleware, session manager and
  its lifespan — and re-hosting that inside a FastAPI route would mean reimplementing
  the parts we adopted on purpose. The A2A JSON-RPC endpoint *is* a product route and
  *is* versioned: `POST /v1/a2a`.
- **Request models carry `extra="forbid"`.** Pydantic's default drops an unknown
  field in silence and answers 200; that has swallowed a request field four times
  in this project. The published schema shows it as `additionalProperties: false`
  and `backend/tests/api/test_openapi_snapshot.py` asserts it for every body.
- **Postgres, not SQLite, in tests.** SQLite does not enforce foreign keys without
  a per-connection pragma and has no row security at all, so a suite running on it
  reports tenant-isolation guarantees that are never actually checked.

### Frontend: quiet by default, and every page explains itself

Two rules govern every screen for every role. Both are written out in full in
`DESIGN.md` §4 — read them before adding a panel.

- **A listing opens closed.** Any panel whose body is a list of rows shows only a
  bar with its title, its row count and one key figure. Hover expands, click pins.
  Use `DataPanel`'s `collapsible` prop — do not hand-roll a disclosure, and never
  `display:none` the rows, because collapsed must stay in the accessibility tree
  and reachable by keyboard. One surface per page — the one the page is named
  after — stays open.
- **A page explains itself without prose.** A reviewer names what a screen does
  from its structure, not from a paragraph. Before adding a sentence to a page,
  cut it in this order: a restatement of the label, then advice; keep only a
  sentence naming a file, route, table, rail or figure. Everything else belongs
  in `docs/`. Mechanism goes in an `InfoTip`, a number's defence in a `Receipt`.

The forcing function is a **ten-to-fifteen minute** demo of the whole platform. A
screen a reviewer cannot read in seconds has failed, however correct it is. What
this never licenses is deleting evidence: a `Receipt`, an `Absence` and an active
failure's remediation always stay. Quiet comes from relocating, never removing.

## Verify before you claim done

Never report success without pasting real output. In order:

```bash
(cd backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest -q)
(cd aegis && PYTHONPATH=src ../backend/.venv/bin/python -m pytest -q)
backend/.venv/bin/python -m ruff check aegis backend
```

Both suites must be at least as green as the baselines above. A test that is
weakened, skipped or deleted to make a change pass is a regression, not a fix.

If a doc can only be written by first fixing something, fix it or say so — do not
write around it.

## Using SKILL.md as a Claude Code skill

`SKILL.md` carries valid Agent Skill frontmatter, but `.claude/` is git-ignored in
this repo (it holds linked worktrees), so the file lives at the root and ships
with the checkout. To load it as an on-demand skill:

```bash
mkdir -p .claude/skills/retarget-aegis && ln -sf ../../../SKILL.md .claude/skills/retarget-aegis/SKILL.md
```

Reading it directly works just as well, and is the intended path for an agent that
was handed this repository.
