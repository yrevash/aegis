# Changelog

All notable changes to Aegis are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the version it
tracks is `aegis.__version__` — the importable core — which follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) under the pre-1.0
rules stated in [`aegis/PUBLIC.md`](aegis/PUBLIC.md).

**One honest note before the entries.** No release has ever been tagged. The
version string has read `0.1.0` since the package was scaffolded on 2026-08-11
(`c76e4fc`) and has not moved since, across 240 commits. The `0.1.0` section
below is therefore reconstructed from git history rather than written at release
time — dated by the day the work actually landed, and traceable to the commits
named in each group. Starting the record with a fiction about past discipline
would have been the wrong first entry in a file whose whole purpose is to be
believed.

What is versioned: the `aegis/` package. `backend/` is the reference composition
root and `web/` the console; both move in lockstep with the package in this
repository and carry no separate version.

---

## [Unreleased]

The v2 phase programme — turning an application you fork into a library you
import. Several phases are in flight concurrently; only work verified present in
the tree is listed here.

### Added

- **`aegis/PUBLIC.md`** — the public/internal boundary. Three tiers, 50 Stable
  names out of 700+ exported, and a stated reason for everything left internal.
  Read by `aegis/tests/core/test_public_surface.py`, so a rename fails the suite
  rather than silently invalidating the document.
- **`aegis.core.deprecated` / `warn_deprecated` / `AegisDeprecationWarning`** —
  the package had no deprecation machinery at all; a name either existed or had
  already gone. `since`, `removed_in` and `use` are all required, and a blank
  `use` raises at decoration time, so a deprecation that does not name its
  replacement cannot be imported, let alone shipped.
- **This changelog**, and a stated versioning policy to go with it.
- **`scripts/build_api_docs.py`** — a `pdoc` reference over every `aegis`
  subpackage, generated from the Google-style docstrings ruff already enforces.
  Output goes to `docs/api/` and is git-ignored: committed generated docs go
  stale between the commit that changes a signature and the commit that remembers
  to rebuild, and they bury real diffs in review noise.
- **`backend/openapi.json`** — the HTTP contract, committed and snapshot-tested
  (`backend/tests/api/test_openapi_snapshot.py`, `scripts/build_openapi.py`). An
  API change that nobody reviewed now fails CI instead of surprising an
  integrator. Unlike `docs/api/`, this one is committed on purpose: the console's
  build reads it.
- **The `StreamEvent` union, published as a schema.** Twenty variants,
  discriminated on the `type` carried *inside* the frame's `data` payload, served
  in `components.schemas` and declared as the `text/event-stream` response of
  `POST /v1/query`. The product's primary interface existed only as Pydantic
  classes plus a hand-written TypeScript mirror; a consumer outside this repo
  could not validate a single frame.
- **A generated TypeScript client** — `web/src/lib/api/generated/schema.d.ts`,
  produced by `npm run gen:api` from `backend/openapi.json`, with
  `web/tests/api/generatedSchema.test.mjs` failing if regenerating would change
  it. `web/src/lib/api/types.ts` is now 68 aliases of generated types instead of
  775 hand-written lines mirroring Pydantic. The hand-written runtime layer —
  the SSE reader, `ApiError`, the one-shot 401 sign-out — is kept and composes on
  top of it.
- **`AGENTS.md`** — repo-root agent instructions, per the Linux Foundation spec.
  Commands, boundaries, and where to find the retargeting procedure.
- **`SKILL.md` (`retarget-aegis`)** — the authoritative procedure for pointing
  Aegis at a new domain: the ten pieces, the order, and a check after each one.
- **Ingestion (phase 4)** — a Docling seam with a pinned parser version,
  structured chunking with enriched prefixes, tables as objects with hash-cached
  summaries, `corpus_version`, re-index without re-parse, a parse quality gate, a
  local ONNX cross-encoder reranker, and a corpus-wide keyword arm.
- **Orchestration (phase 3)** — Temporal as the job orchestrator with Postgres
  remaining the system of record; `run_events`; admission control with budget
  pre-authorisation and cancellation; idempotency, a reconciler and debounced
  schedules; a tenant-scoped settings catalogue.
- **Multi-agent and web search (phase 5)** — the supervisor fan-out, a research
  sub-agent with an explicit "no key ⇒ internal evidence only, and it says so"
  degradation, and gateway resilience.
- **Console (phases 6–7)** — the chat shell with result tabs and a memory rail,
  the approvals inbox, five role portals, admin forms that refuse with a reason,
  a live red team that leaves evidence, tenant-scoped analytics, guardrail policy
  with provenance, and server-side audit filters.

### Changed

- **The domain seam gained the four things the core was deciding for it** (phase 8,
  after the retarget rehearsal). `personas.PERSONA_BY_ROLE` / `persona_for_role`
  (the host's login path chose between two hardcoded persona ids, so re-voicing
  `PERSONAS` — which `SKILL.md` instructs — made every sign-in raise `KeyError`);
  `generator.DOMAIN_SERIES_LABEL` / `DOMAIN_SERIES_UNIT` / `domain_series_events`
  (`app.forecast` read the shipped record collection by name and owned the chart's
  client-facing title); `memory_spec.PROFILE_ALIASES` (the alias table lived in
  `aegis.memory.consolidate` naming one domain's fields); and `ToolSpec.destructive`
  / `ToolSpec.idempotent` (the MCP surface kept them in a table keyed by the shipped
  domain's tool names). `MLSpecModule.training_frame`'s keyword is now `num_records`,
  not `num_requests`: a core Protocol may not make every future domain call its rows
  "requests". `aegis.adapter`'s sub-Protocols document all of it; the eleven top-level
  members are unchanged, so `missing_members` and `isinstance` behave as before.
- **`AuditEntry.action`'s example** no longer names a shipped-domain tool. The
  committed `backend/openapi.json` and the generated TypeScript client move with it.

- **One vector engine: Qdrant. Chroma deleted, not demoted** (2026-08-19, phase 9
  §9.1). Both consumers now write to one node through `QDRANT_URL` —
  `aegis.retrieval`'s own store and LightRAG's `QdrantVectorDBStorage`, which
  replaces `NanoVectorDBStorage` (its own docstring calls it a brute-force cosine
  scan held in memory, persisted by rewriting a whole JSON file). The `chromadb`
  dependency, `ChromaVectorStore` and every construction site are gone.
  **Breaking:** `ChromaVectorStore` → `QdrantVectorStore`, and full mode now
  requires `AEGIS_VECTOR_STORE_URL` (also read from `QDRANT_URL`) where it
  required `AEGIS_VECTOR_STORE_PATH`; `VECTOR_STORE_PATH` survives as LightRAG's
  working directory only. **Existing vectors are re-ingested, not migrated** — an
  accepted cost, paid before a demo corpus exists.

  This reverses the 2026-08-15 entry below, and the reason is worth stating: that
  decision's premise was "the target enterprise Windows machine allows no
  additional server software", which is true of installers and Windows services
  and *not* true of Qdrant v1.19.0 — it publishes
  `qdrant-x86_64-pc-windows-msvc.zip`, Apache-2.0, a zip with a binary, the same
  shape as the Superset already on the box. What the embedded choice cost was
  decisive: an embedded store is single-process, so `uvicorn --workers 2` failed
  in a way that looked like index corruption. Aegis now **refuses to boot** with
  more than one worker while an embedded store is configured, naming the worker
  count, where that number came from, and the fix. Keeping a Chroma path would
  have left that ceiling reachable by configuration, and a ceiling you can still
  configure your way back into is not removed. Tests and dev still need no server:
  `qdrant_client`'s in-process mode stays available and stays named out loud.
- **Vector search runs off the event loop** (phase 9 §9.7). The lite backend's
  store calls — search *and* index — were synchronous on the loop thread: 13.5 ms
  of pure CPU per query at 50k vectors, blocking every other request for that
  long, including ones that never touch retrieval. They now run in worker threads,
  with the per-partition fan-out gathered rather than serialised. The memory
  index's calls were already threaded.
- **Every HTTP route moved under `/v1`** — the version boundary that lets the API
  promise anything at all, the counterpart of `PUBLIC.md` for the interface an
  integrator actually consumes. **Breaking for any caller outside this repo:**
  `POST /query` is now `POST /v1/query`. The three infrastructure probes
  deliberately did **not** move and are served at exactly one path each —
  `/health`, `/ready`, `/readyz` — because a liveness URL that moves with the API
  version starts 404-ing halfway through a rollout. The console followed in one
  place (`web/src/lib/api/config.ts`).
- **`AegisModuleRow.category` and `.status` are published as their real closed sets.**
  `app.capabilities.AegisModule` has always typed them `Literal[...]`; the API
  projection restated them as `str`, so the OpenAPI document — and therefore the
  generated TypeScript client — described a field with five legal values as
  `string`. The generated client can only be as precise as the document it is
  generated from, so a projection that widens a closed set reintroduces exactly the
  drift generating the client removed.
- `chunks` became a tenant-scoped table.
- `aegis.core.__all__` gained the three deprecation names above.
- **`aegis.gateway` re-exports `GatewayConfig`, `GovernanceHook` and
  `ObservabilitySink`.** They are `configure()`'s parameter types and lived only in
  `aegis.gateway.llm`, so a host writing a budget hook had to bind to a submodule
  path `PUBLIC.md` calls internal — and `aegis.runtime`'s own `TYPE_CHECKING`
  import of all three named nothing the package exported, leaving three annotations
  on `Aegis.from_env` unresolvable for a type checker on a package that ships
  `py.typed` to promise otherwise. Provisional, not Stable.
- **`PUBLIC.md` promises the composition root.** `aegis.Aegis` and the four errors
  `from_env` raises were in the top-level `__all__` but in no Stable table, so the
  one supported way to bring a process up carried no stability promise at all. The
  Stable count moved from 44 to 50; `aegis.active` is stated Provisional.

### Deprecated

- **`backend/src/app/adapter/SWAP.md`** is retired as a procedure. It held a full
  retarget checklist, `adapter/README.md` held another, and the module docstrings
  held a third numbering — three copies of one procedure, which is how one of them
  ends up wrong, and this directory really did carry five different denominators
  between them. `SWAP.md` is now a pointer to the root `SKILL.md` and nothing
  else. It is kept rather than deleted only because other files in that directory
  still link to it; delete it once those links are re-pointed.

  `backend/tests/adapter/test_piece_manifest.py` was re-pointed accordingly: the
  "the checklist names every piece" guarantee now applies to `SKILL.md`, the
  document an agent is actually handed, and a new check fails if `SWAP.md` grows a
  checklist back.

### Fixed

- **A retarget can no longer pass every suite while being broken.** `pytest --pyargs
  aegis.conformance` is fourteen checks, not thirteen: the new one reads the **core**
  rather than the adapter and fails when any module outside
  `backend/src/app/adapter/` still names the shipped domain — with the file, the line
  and the word. A fresh agent given only this repo and a one-line problem statement
  had produced an integration that passed conformance, the adapter suite, the agent
  suite and ruff, and was broken in four places at once, every one of them a
  shipped-domain string in a core module.
- **Two conformance checks could go vacuous, and one fixture could dissolve.** The
  playbook-reachability check read string constants out of `select_skills.__code__`,
  so hoisting the keyword table to a module constant emptied the set it reasoned over
  and the check reported clean while verifying nothing; it now reads the module's
  constants too, and a selector that can never return a playbook fails instead of
  passing for lack of evidence. `backend/tests/adapter/broken_adapter/` imported the
  *production* adapter, so its intended memory break evaporated the moment a retarget
  re-pointed those literals (`12 failed, 1 passed` → `11 failed, 2 passed`); it is now
  self-contained, and a test asserts it imports no domain code. The new core check is
  built to the same rule — an empty word list, too few files scanned, a reader that
  matches nothing, or a quarantined word the reference adapter no longer uses is a
  failure, not a quiet pass.
- **`SKILL.md` was wrong in six places, all found by following it literally.** Its
  first command could not run (no `.venv` in a fresh checkout, and `scripts/bootstrap.sh`
  was reachable only through `AGENTS.md`) — there is now a step 0. "You leave its
  `__all__` alone" was impossible and self-contradicted twice; the contract is the
  Protocol, and the file now says which names actually have to survive. It never said
  that every test in the repo is red from step 1 until step 8 finishes, that
  `tests/adapter/*` must be rewritten as part of the steps, or that step 8 includes
  `sub_agent_roster`'s tool allowlists; and its "do not touch" list omitted
  `app.forecast`.

---

## [0.1.0] — 2026-08-11

Never tagged. The version was set once, at `c76e4fc`, and the entries below are
the work that carried it. Grouped by the day each group landed.

### Added

- **The importable package** (2026-08-11, `c76e4fc`). `aegis/` scaffolded as a
  standalone distribution with per-component extras, alongside the AG-UI
  streaming spine and its shared event-name registry. This is the commit where
  "fork the application" started becoming "import the library".
- **Eight modules extracted** (2026-08-11 → 2026-08-12), each by the same
  strangler pattern — extract the pure core, delegate the old path to it through
  a shim, port the tests: `aegis.ml`, `aegis.retrieval`, `aegis.gateway`,
  `aegis.memory` + `aegis.data`, `aegis.governance`, `aegis.evals` + `aegis.ops`,
  `aegis.observability`, `aegis.agent`.
- **The Next.js console** (2026-08-12) — a full rebuild onto Next.js + Tailwind,
  one dashboard per module across four role-scoped portals, real JWT login with
  RBAC enforcement, and the live streaming console.
- **Enterprise guardrails** (2026-08-12) — the NeMo Guardrails engine on the live
  path, Microsoft Presidio PII detection with a regex fallback, an MLCommons
  S1–S13 content-safety rail, topical and output-grounding self-check rails, and a
  custom-rail extension seam.
- **A real knowledge graph** (2026-08-12 → 2026-08-13) — entity/relation
  extraction, then `GET /graph` served from Neo4j and unioned with the live run
  delta, replacing a fabricated chain.
- **Three more first-class modules** (2026-08-14) — `aegis.voice`,
  `aegis.vision`, `aegis.forecast`; plus a media guardrail seam and billing for
  non-chat calls.
- **The teaching course** (2026-08-14 → 2026-08-16) — 16 modules of guide,
  diagrams and interview questions, later rewritten to be read rather than waded
  through, and rendered to HTML beside the Markdown.
- **The red-team harness** (2026-08-12) — an importable battery run against the
  guardrails, not a mock.
- **Windows support** (2026-08-14) — PowerShell scripts parseable under Windows
  PowerShell 5.1, and Memurai in place of Redis at the same wire protocol.

### Changed

- **Qdrant replaced by embedded vector stores** (2026-08-15) — Chroma
  `PersistentClient` over a local directory, plus LightRAG's NanoVectorDB. No
  server binary and no port, which is what lets the stack install on a
  locked-down enterprise machine.
- **ML left the agent graph** (2026-08-17). It decorated a decision it never
  made: nothing routed, gated or branched on its output. ML is now a capability
  the deployment *offers* (`POST /ml/explain`, `GET /ml/model-card`, the forecast
  dashboard), and the human gate is driven by tool risk alone.
- **`pgvector` removed from the data layer** (2026-08-12). Embeddings persist as
  JSON of record; ANN search runs in the embedded store.
- **The adapter's own count of itself corrected** (2026-08-18). The directory
  simultaneously claimed "piece 2 of 5", "3 of 5", "4 of 5", "6 of 5" and
  "**6 of 6**" while holding eight modules plus `corpus/` and `skills/` — and
  `roster.py` and `skills/` appeared in no checklist at all, so a swap-day edit
  would have missed them. Now ten pieces everywhere, checked against the
  filesystem by `backend/tests/adapter/test_piece_manifest.py`.

### Fixed

- **Tenant isolation made real** (2026-08-17), verified against a live Postgres
  cluster over a `NOSUPERUSER NOBYPASSRLS` role — because row-level security
  policies are enforced against nobody else. Five cross-tenant leaks found by
  audit were closed (2026-08-19), along with the type error behind them.
- **`aegis.require` did not exist.** The package README told an integrator to
  call it; the helper is `aegis.core.require`, so the first line copied out of the
  README raised `AttributeError`. Fixed, and guarded by
  `aegis/tests/core/test_documented_public_surface.py`.
- **The SSE reader discarded every frame the server sent** (2026-08-19).
- **`agent_id` was being dropped on the wire** by the event base schema.
- **One approval could authorise what it did not show** (2026-08-19) — closed
  along with the rest of the phase 5 audit.
- **The prompt registry was tenant-blind**, and deleting an active version
  deleted the floor.
- **A stored spend cap that cannot bind is now refused**, and a tenant cannot be
  created without one.
- **SQLite removed from the test path.** It does not enforce foreign keys without
  a per-connection pragma and has no row security at all, so a suite running on it
  reported tenant-isolation and spend-attribution guarantees that were never
  actually being checked.

### Security

- **PEP 561 marker added** (`aegis/src/aegis/py.typed`). Without it every
  annotation in the package is invisible to an integrator's type checker, however
  complete it is — including the annotations on the retrieval scope that is a
  security boundary.
- **Six guardrail layers** stand between the model and a real action, with an
  append-only audit row and an OpenTelemetry trace on every run.
- **Fail-closed by default.** A control that cannot run refuses and says so;
  in-memory backends are returned only when the mode is explicitly `lite`.

---

## Before 0.1.0 — 2026-08-06 to 2026-08-10

Aegis existed as an application you fork: a FastAPI backend, four role-scoped
portals, retrieval, and the domain adapter seam. The whole v2 programme is the
consequence of one retrospective finding from the Mumbai hackathon — building a
domain solution *on top of* that application was bulky, and when a coding agent
was pointed at it, *"the agent did not pick this up, I had to do back and forth."*
Everything since is an answer to that sentence.

[Unreleased]: https://github.com/yrevash/aegis/compare/main...HEAD
