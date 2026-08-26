# SOTA build — the master plan

> **STATUS: PLANNED, NOT STARTED.** Ten plans were written 2026-08-27 by parallel agents,
> each against the running source. This file sequences them. It does not restate them —
> every task lives in its own document and this one only decides order, gates and risk.

> **The rule this plan is built on.** No phase is finished when its code is written. A
> phase is finished when an audit pass has run every endpoint it touched, every test it
> added, and every screen it changed, and found nothing. That gate is described in
> §Audit gate and it is not optional.

---

## What was planned

| # | Document | Subject | Lines |
|---|---|---|---|
| 01 | `01-a2a-protocol.md` | A2A 1.0 — signed Agent Card, JSON-RPC surface, RLS-backed multi-tenancy | 605 |
| 02 | `02-mcp-2026-07-28.md` | MCP current-revision **verification** (not migration — see below) | 545 |
| 03 | `03-memory-write-rail.md` | `GuardStage.MEMORY_WRITE` + memory-poisoning probes | 548 |
| 04 | `04-audit-chain.md` | Tamper-evident audit chain + `GET /v1/audit/verify` | 543 |
| 05 | `05-sbom-agbom.md` | CI SBOM from lockfiles + `GET /v1/platform/agbom` | 544 |
| 06 | `06-compliance-asi-india.md` | OWASP Agentic ASI01–10, IT Amendment Rules 2026 | 1054 |
| 07 | `07-long-horizon-ceiling.md` | Enforced run ceiling (Track A) / trajectory compaction (Track B) | 762 |
| 08 | `08-agent-verify-loop.md` | act → verify → reflect → retry, grounded by read-back | 978 |
| 09 | `09-evals-ragas-deepeval.md` | Real `ragas` + `deepeval` through the metered gateway | 986 |
| 10 | `10-design-system.md` | IBM Plex Sans, GSAP, Impeccable detector, `DESIGN.md` rewrite | 1143 |

---

## Corrections the planning pass produced

The research that motivated this work contained six claims that did not survive contact
with the source. They are recorded here because a plan built on them would have wasted
days, and because the same errors will otherwise be repeated in the pitch.

| Claim | Verdict | Evidence |
|---|---|---|
| The MCP server is one protocol era behind | **FALSE** | `backend/src/app/mcp/server.py:1438` calls `server.streamable_http_app(...)`, the SDK's own current builder. `StreamableHTTPSessionManager` appears only in docstrings at `:1405` and `:1462`. Measured serving `server/discover`, `ttlMs`/`cacheScope`, `resultType`, `traceparent` |
| Aegis ships no SBOM | **FALSE** | `GET /v1/stack/sbom` is live — CycloneDX 1.6 + SPDX 2.3, with a console download |
| The India AI Governance Guidelines are missing | **FALSE** | Already mapped since 2025-11-05, four rows under `india-sectoral`. The genuinely new instrument is the **IT Amendment Rules 2026** |
| Aegis states the stale 2 Aug 2026 EU AI Act date | **FALSE** | No such text exists. High-risk obligations were deferred to 2027-12-02; a timeline is worth *adding*, nothing needs *fixing* |
| CI may resolve a backdoored `litellm` | **FALSE** | A fresh-venv resolve of CI's exact command yields `litellm==1.96.0`. The `constraint-dependencies` block at `backend/pyproject.toml:222-226` is honoured |
| `huggingface-hub` declares no `click` dependency | **FALSE** | It declares `click<9.0.0,>=8.4.2`. The `deepeval` conflict is real — see Phase 5 |

**One correction of method.** An earlier combined `uv pip install --dry-run` appeared to
resolve `ragas` + `deepeval` cleanly. It did — by silently downgrading `huggingface-hub`
1.27.0 → 1.16.1, which is the ingestion/ML stack's floor. A resolution that succeeds is
not the same as an absence of conflict, and the difference was the whole risk.

---

## Traps found during planning

These are the findings that change how the work must be done, not merely what it is.

**A raised repair budget retries your own guardrail.** `graph.py:1218` computes
`ok = bool(ok) and allowed`, so a guardrail block and a genuine tool failure produce a
byte-identical `ok=False` and the result row cannot tell them apart. Raising
`max_plan_iterations` — the change that unlocks the demo — turns that into a retry loop
against the rail. **Separating the two cases is a prerequisite, not a follow-up.**

**The reduced-motion kill switch cannot reach GSAP, and this is now measured.** Under
`prefers-reduced-motion: reduce` in Chromium, a `@keyframes` element snapped to its 300px
endpoint while a GSAP tween sat mid-flight at 105px. The boundary needs a test, not a
convention.

**IBM Plex Sans is 4.75% *narrower* than Inter, and reads 5.5% smaller** (x-height 51.60
vs 54.59). The instinct to bump sizes ~5% gives back exactly the width the narrower face
won and *then* overflows the dense tables. Take the face, leave the ramp alone.

**`.eyebrow` is already 10.88px** — under the 11px functional-text floor. The undersized
text problem is a platform-wide token, not three spots in `PipelineIso.tsx`.

**The `verification` event needs 14 wire touchpoints and four have no tripwire** —
`describeEvent`, `agentLanes`, `stageTimeline`, `harness`. That is the exact shape of the
bug that made `reflection` invisible once already.

**A naive wall-clock bound fires while a human sits at the approval gate**, finalising
every approved run the moment someone resumes it. The deadline must exclude interrupted
time.

**A2A 1.0 methods are PascalCase** — `SendMessage`, `GetTask`. Anything written from
memory is wrong on the wire. `a2a-sdk 1.1.2` also forces protobuf 7.35.1 → 6.33.6.

**No Alembic exists.** Migrations are `create_all` + `reconcile_additive_columns`
(`backend/pyproject.toml:36-38` is explicit). The audit-chain plan is written to that.

**`RAGAS_DO_NOT_TRACK=1` silently does nothing** — ragas compares the value against the
string `"true"`. Set it wrong and telemetry stays on while you believe it is off.

**`ToolCorrectnessMetric` is only free with `available_tools=None`.** It gained an LLM
path in 4.x.

---

## Phases

Ordered by *what unblocks what*, and by putting the highest credibility risk early enough
that there is time to react if it goes badly. Each phase ends at an audit gate.

### Phase 0 — settle the unknowns · plan 02, 06

Two questions must be answered before anything is built on them.

- **A0** — read the OWASP Agentic PDF directly. `genai.owasp.org` returned HTTP 403 during
  planning, so all ten ASI titles currently come from secondary sources. A compliance page
  that misquotes a framework's own identifiers is worse than one that omits it.
- **M0** — measure one authenticated MCP round trip through Aegis's governance on the
  SDK's modern path. If `ServerRequestContext.request` is not populated there,
  `resolve_caller` breaks and per-call identity silently degrades to connection-scoped —
  the property the entire MCP module exists to provide.

**Gate:** both answered in writing, with the measurement pasted in.

### Phase 1 — design system · plan 10

First because it touches every screen, and every later phase's screenshots should be taken
in the final typeface rather than re-shot.

IBM Plex Sans + Plex Mono; the size ramp untouched; the two `side-tab` findings fixed; the
11px floor applied starting with `.eyebrow`; GSAP wired only through `useGSAP` +
`gsap.matchMedia()`; the boundary test that fails on any tween outside that pattern;
`DESIGN.md` rewritten to record what changed and why, including that adding GSAP alongside
Motion contradicts §7's own bundle reasoning and that this was a deliberate decision.

**Gate:** `tsc` clean · `npm test` (baseline **399**) · `impeccable detect` · no horizontal
overflow at 390/834/1440 on every portal screen · reduced-motion verified to suppress
*every* GSAP tween, not merely the CSS ones.

### Phase 2 — the agent loop · plan 08

The single most valuable thing in this list, and the one the pitch is built on.

Separate rail-block from tool-failure **first**. Then the `verify` node — deterministic
checks, then read-back against the record, then a judge only where the first two are
inconclusive. Then the budget split, the termination bounds, all 14 wire touchpoints, and
the attempt bands in the console.

**Gate:** a forced-failure run that visibly retries and then verifies · every termination
bound proven to fire · a retried HIGH-risk write raising a **second** approval interrupt ·
exactly-once still holding · the attempt bands rendering in a browser.

### Phase 3 — memory rail + audit chain · plans 03, 04

Both are governance surfaces and both are demo-ready in the same sitting.

`GuardStage.MEMORY_WRITE` screening injected at `_reconcile`; a `WriteOp.REFUSED` audit op;
four `MEMORY_POISONING` probes. Then `row_hash`/`prev_hash`, per-tenant chains, the
fork-preventing unique index, `GET /v1/audit/verify`, and a genesis marker that makes no
claim about pre-chain rows.

**Gate:** the battery run with the rail off then on, showing poisoned facts landing then
being refused and audited · the chain verified end to end · a deliberately tampered row
detected and reported at the right index.

### Phase 4 — compliance · plan 06

ASI01–10 and the IT Amendment Rules 2026, each control mapped to named enforcing code.
The existing overclaim tests must pass unchanged.

**Gate:** `GET /v1/compliance` returns the new frameworks · the not-applicable
justification test passes · the compliance screen and the landing band both render the new
rows.

### Phase 5 — evals · plan 09

`ragas` in the backend venv, where it must live to route through the metered gateway;
`deepeval` isolated in its own `uv run` environment so the ingestion/ML stack is never
downgraded. Adapters over `aegis.gateway.complete`/`.embed` — never a `base_url`, which
would make every eval call invisible to the ledger. `RAGAS_DO_NOT_TRACK` set to the string
it actually checks.

Then retire every "ragas-style" / "no ragas dependency" claim in the repo. Shipping the
real libraries while the site still disclaims them is the dishonesty this codebase exists
to refuse.

**Gate:** eval calls appearing in the usage ledger, proving they went through the gateway ·
the empty "ragas · answer relevancy" cell in `EvalsView.tsx` filled with a real number ·
the ingestion and ML suites still green.

### Phase 6 — A2A · plan 01

Highest pitch value, highest unknown. Signed Agent Card at `/.well-known/agent-card.json`
built from the existing capabilities surface; the JSON-RPC methods in their real PascalCase
spelling; multi-tenancy served off the existing RLS scope. Then either justify or rename
the four `app.a2a.*` span attributes — shipping a real implementation makes them correct;
shipping nothing leaves them as the one finding a hostile juror can turn into a credibility
problem.

**Gate:** the card fetched and its signature verified by an external tool · one full task
round trip · tenant isolation proven by a cross-tenant attempt that fails.

### Phase 7 — SBOM/AgBOM + run ceiling · plans 05, 07

CI SBOM generated from the lockfiles; `GET /v1/platform/agbom`; the enforced run ceiling
(Track A) with an honest refusal when exceeded. Track B — trajectory compaction — is
recorded as planned and **not** attempted here; the real exposure is one unbounded tool
result, not turn count, and the plan says so.

**Gate:** the AgBOM listing every tool with its risk tier, every deployment, every rail ·
the ceiling refusing at the boundary rather than truncating silently.

---

## Audit gate

Run after **every** phase, before the next begins. Nothing here is advisory.

1. **Static** — `tsc --noEmit`, the full `npm test` suite, `ruff`, the backend suites,
   `openapi` regenerated and diffed.
2. **Endpoints** — every route the phase touched, called for real, with the response
   compared against what the plan said it would return. Not a smoke test: the actual
   assertion the plan made.
3. **Frontend** — every screen the phase changed, at 390/834/1440, checked for horizontal
   overflow and console errors, and *looked at*.
4. **Logic** — an adversarial pass over the diff, specifically hunting: a claim in code or
   copy that the implementation does not support; a test that passes vacuously; a new
   event with a missing reducer case; a silent fallback where a refusal belongs.
5. **Regression** — the phase's own gate, plus every prior phase's gate re-run.

A phase that fails its gate is not carried forward with a note. It is fixed, and the gate
runs again.

---

## What this plan does not decide

- **Order beyond Phase 2.** Phases 3–7 are genuinely re-orderable and should be re-cut
  against whatever the demo turns out to need.
- **Anything about the hackathon date.** The owner's instruction was to plan the complete
  state-of-the-art build with no scope cut for time. Which subset ships by any given date
  is a separate decision, deliberately not made here.
- **Track B compaction**, **WebMCP**, **agentic payments**, **DID/TEE identity**. The first
  is planned but out of scope; the rest were examined and rejected with reasons recorded in
  the source documents.
