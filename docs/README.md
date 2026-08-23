# Aegis documentation

Aegis is a domain-agnostic, multi-tenant agentic-AI platform in three parts: an
importable Python core (`aegis/`), a FastAPI composition root (`backend/`), and a
Next.js console (`web/`). This directory documents all three.

**Everything here describes what the repository actually does today.** Where a thing is
absent or partly built, the doc says so rather than describing the intention. If you
find a claim that is no longer true, correct it — a document that asserts a design the
system does not implement is the specific failure this documentation set is maintained
against.

---

## Start here

| If you are… | Read |
|---|---|
| **new to the system** | [`architecture/system-architecture.md`](architecture/system-architecture.md) — the whole platform, top to bottom, in one file |
| **installing it** | [`install/`](install/README.md) — the only install path, in order, with a check after every step |
| **an agent doing the install** | [`install/AGENT-CONTEXT.md`](install/AGENT-CONTEXT.md) — the rules and the failures that name something other than their cause |
| **learning one module** | [`teaching/`](teaching/README.md) — one file per `aegis.*` module, 29 of them |
| **running it on the day** | [`operations/runbook.md`](operations/runbook.md) — three commands and the fallback ladder |
| **retargeting it to a new domain** | [`../SKILL.md`](../SKILL.md) — the adapter procedure, step by step |
| **judging or reviewing it** | [`hackathon/brief.md`](hackathon/brief.md), then `system-architecture.md` and [`compliance/`](compliance/README.md) |

---

## What is in here

### `teaching/` — the modules, one file each

[`teaching/README.md`](teaching/README.md) is the syllabus. One file per module,
written for someone who has never seen the code: what the module does and why it
exists, then the mechanism with the real file and function names, then a **What is not
here** section that separates what is built from what is merely intended.

### `architecture/` — the system, and three design derivations

| File | Subject |
|---|---|
| [`system-architecture.md`](architecture/system-architecture.md) | **The one to read first.** Every layer, store, gateway and module as it runs today |
| [`memory-spec.md`](architecture/memory-spec.md) | The derivation of the memory subsystem's defaults — recall blend, consolidation cadence, context assembly. Cited by `aegis/memory/config.py` |
| [`eval-strategy.md`](architecture/eval-strategy.md) | The three evaluation layers (offline gate, CI regression gate, live traces) and which named tool each one implements the *idea* of rather than the package |
| [`synthetic-data.md`](architecture/synthetic-data.md) | How demo- and training-grade synthetic data is generated, and why none of it is real personal data |
| [`backend.md`](architecture/backend.md) | Historical backend design context from early August. Superseded by `system-architecture.md`; kept because code cites it, with its stale rows corrected in place |

### `install/` — bring-up

Prerequisites → bootstrap → demo data, plus the agent-facing context file. The
non-negotiable step is the **non-superuser serving role**: PostgreSQL skips row-level
security entirely for a superuser, so with the wrong DSN every tenant-isolation policy
installs, looks correct in `pg_policies`, and filters nobody.

### `adr/` — architecture decision records

Nine numbered decisions. Superseded ADRs are kept and marked, never deleted — the
record is the point, and three of these are more useful for what turned out to be wrong
than for what turned out to be right.

| ADR | Decision | Status |
|---|---|---|
| [`0001`](adr/0001-litellm-as-gateway.md) | LiteLLM as the single model gateway | Accepted |
| [`0002`](adr/0002-nemo-guardrails.md) | NeMo Guardrails as the Colang policy engine | Accepted |
| [`0003`](adr/0003-lightrag-over-graphrag.md) | LightRAG over Microsoft GraphRAG | Accepted; vector half superseded by `0009` |
| [`0004`](adr/0004-conformal-prediction-uncertainty.md) | Conformal prediction for calibrated uncertainty | Accepted |
| [`0005`](adr/0005-postgressaver-durable-execution.md) | Durable, resumable execution on a Postgres checkpointer | Accepted — **and in force in the deployment since 2026-08-23**, having been Accepted-but-unimplemented for the 18 days before that |
| [`0006`](adr/0006-rrf-hybrid-retrieval.md) | Reciprocal Rank Fusion for hybrid retrieval | Accepted |
| [`0007`](adr/0007-conformal-autonomy-bands.md) | Conformal autonomy bands | **Superseded** — engine deleted, wire enum survives |
| [`0008`](adr/0008-multi-tenant-rls-governance.md) | Multi-tenant governance with Postgres RLS | Accepted |
| [`0009`](adr/0009-embedded-vector-store.md) | A server-free embedded vector store | **Superseded** — the vector tier is Qdrant |

### `security/` and `compliance/`

| File | Subject |
|---|---|
| [`compliance/README.md`](compliance/README.md) | **The current, evidence-backed surface.** 114 controls across 12 frameworks, every claim resolving to a file, route or test, and every gap named. Served live at `GET /v1/compliance` |
| [`security/threat-model.md`](security/threat-model.md) | One-page mapping to the OWASP LLM Top 10, the OWASP Agentic (ASI) Top 10 and the lethal trifecta |
| [`security/owasp-agentic.md`](security/owasp-agentic.md) | The agentic mapping in depth, each control naming a real file. Read by `backend/src/app/platform/risk_map.py` |
| [`security/overview.md`](security/overview.md) | The security design and how to demonstrate it |

### `operations/`

| File | Subject |
|---|---|
| [`runbook.md`](operations/runbook.md) | Day-of operations: three commands and the fallback ladder for when something is red |
| [`superset-embedded.md`](operations/superset-embedded.md) | The Superset integration, and the six failures that stand between "installed" and "serving data" — every one of which names something other than its cause |
| `superset/` | The committed asset bundle: database, datasets, charts, and the board catalogue |

### `module/` — the importable core

[`module/MODULE_REFERENCE.md`](module/MODULE_REFERENCE.md) — the Module Contract, the
streaming spine, the module map with each module's install extra, and the debt list.
[`module/PIPELINES.md`](module/PIPELINES.md) is **generated** from
`aegis.pipelines.spec` (`python -m aegis.pipelines > docs/module/PIPELINES.md`), so a
stage cannot be documented there and absent in code.

**Looking up a signature?** The generated API reference is deliberately not committed —
it would go stale between the commit that changes a signature and the commit that
remembers to rebuild it. Build it when you need it:

```bash
backend/.venv/bin/python scripts/build_api_docs.py   # -> docs/api/, git-ignored
```

What is *promised*, as opposed to merely present, is [`../aegis/PUBLIC.md`](../aegis/PUBLIC.md).

### `corpus/`, `hackathon/`, `testing/`, `design/`

| Path | Subject |
|---|---|
| [`corpus/SOURCES.md`](corpus/SOURCES.md) | The four real published PDFs the demo corpus is built from, with provenance and the upload commands |
| [`hackathon/brief.md`](hackathon/brief.md) | Mission context: what is being built, the six weighted scoring areas, and the constraints |
| `testing/test-runbook.html` | The per-account end-to-end test runbook — open it in a browser |
| [`design/CLAUDE-REFERENCE.md`](design/CLAUDE-REFERENCE.md) | A reference design system, kept for comparison only. **The console's own design authority is [`../DESIGN.md`](../DESIGN.md)** and nothing else |

### `dev_new_docs_v2/` — the surviving planning record

Most of the v2 plan shipped and was deleted on 2026-08-23; it is in git history. What
remains is only what is still load-bearing, and [that directory's own
README](dev_new_docs_v2/README.md) says why each file survived.

---

## Outside this directory

| File | Subject |
|---|---|
| [`../README.md`](../README.md) | Repository overview, the stack table, the module manifest |
| [`../SKILL.md`](../SKILL.md) | **The retargeting procedure** — the adapter pieces, the order, and the check after each step |
| [`../AGENTS.md`](../AGENTS.md) | Instructions for coding agents: layout, commands, the boundaries, how to verify |
| [`../DESIGN.md`](../DESIGN.md) | The console's design system. The single authority; there is deliberately no second copy |
| [`../INSTALL.md`](../INSTALL.md) | The older long-form setup reference. `docs/install/` supersedes it |
| [`../CHANGELOG.md`](../CHANGELOG.md) | Keep-a-Changelog history and the versioning policy |
| [`../aegis/PUBLIC.md`](../aegis/PUBLIC.md) | The public API boundary: Stable, Provisional, internal, and why |
| [`../web/README.md`](../web/README.md) · [`../backend/README.md`](../backend/README.md) · [`../aegis/README.md`](../aegis/README.md) | Per-package quick references |

Live, self-describing surfaces worth knowing about: `GET /docs` (generated OpenAPI),
`GET /v1/platform/capabilities` (the module manifest as data), `GET /v1/compliance`
(the control map), and `GET /v1/about`.
