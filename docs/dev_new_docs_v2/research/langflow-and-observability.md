# Langflow as the tenant-facing flow builder · Langfuse vs Phoenix · LangSmith

**Written 2026-08-18.** Commissioned to answer three questions: can Langflow be the visual
pipeline builder tenant users compose in; is Langfuse better than the `arize-phoenix` 14.6
already wired into `backend`; and is LangSmith needed at all.

**The Langflow question was re-scoped mid-research.** The user's instruction: *"I am determined
to use langflow, just we need to keep ready with the plan."* So Q1 below is **not** an
adopt/reject verdict. It is: where Langflow sits, what it can and cannot do today, and exactly
what has to be built for a tenant user's flow to run **through** Aegis's governance rather than
around it.

---

## How to read the claims here

| Marker | Means |
|---|---|
| **[MEASURED]** | Run on this machine. Langflow 1.11.3 was installed, booted and queried in a throwaway venv at `scratchpad/lfrun`; resolves and the Phoenix probe ran in `scratchpad/lf311`. Commands in Appendix A. **Nothing was installed into `backend/.venv` or `aegis/.venv`**, and both throwaway venvs were deleted afterwards. |
| **[SOURCE-1.11.3]** | Read in the **installed, released** package. This is what we would actually run. |
| **[SOURCE-main]** | Read in the GitHub `main` branch — i.e. **unreleased 1.12.0-dev code**. §1.0 explains why that distinction is the most important thing in this document. |
| **[DOC]** | The project's own current documentation. Note: docs.langflow.org documents `main`, so it describes features 1.11.3 does not have. |
| **[THIN]** | Evidence I could not verify to my own satisfaction. Said so rather than rounded up. |

---

## The three verdicts in one paragraph each

**Q1 — Langflow.** The licence is a clean, unqualified **MIT** with no enterprise carve-out
directory, so embedding it inside a commercial multi-tenant product is permitted with no
obligation beyond shipping the copyright notice **[MEASURED]**. It installs and boots natively on
this stack with no Docker, and its resolved dependency tree has a Windows distribution for all
566 packages **[MEASURED]**. Everything else is harder than the screenshot suggests. Langflow has
**no tenant concept at all** — `User` has no org or tenant column, flows are scoped by
`Flow.user_id`, and the OSS authorization service is a documented **pass-through that returns
`True` for every request** **[SOURCE-1.11.3]**. And the component-visibility mechanism people
point at — "catalog policy" — **does not exist in the released version**: I installed 1.11.3 and
`GET /api/v1/catalog-policy/components` returns **404** **[MEASURED]**. It exists only on `main`,
and there it is a **global, superuser-owned blocklist** whose org/workspace scopes are labelled
*"schema reservation … no P1 resolution semantics"* **[SOURCE-main]**. So the plan has two
tracks (§1.4): a **proxy that filters and validates** — buildable today against 1.11.3 — and a
**pluggable policy service** that becomes available if 1.12 ships in time. Governance is the
real cost: a flow that hits Run today calls the model provider directly from inside the Langflow
process and touches **none** of Aegis's budgets, RLS, guardrails or risk gate. §1.6 specifies
what changes that, and the keystone is one endpoint.

**Q2 — Langfuse vs Phoenix.** **Keep Phoenix; do not adopt Langfuse.** Not because Langfuse is
worse — on features it is clearly ahead (organisations, projects, real RBAC, prompt management,
datasets, cost tracking, MIT core) — but because self-hosting it requires **Postgres +
ClickHouse + Redis + an S3-compatible blob store + two app processes** **[DOC]**, and
**ClickHouse has no native Windows build**; the vendor's own answer is WSL2 **[DOC]**. That is a
Linux dependency on a no-Docker Windows box, for a subsystem `plans/04` already defines as the
*ephemeral deep-dive*, not the durable record. `run_events` in Postgres remains the durable
tenant-scoped record and Langfuse does not change that split. Two things you should know
anyway: **Phoenix is Elastic Licence 2.0, not open source** — §2.5 says exactly what that
forbids, and it constrains a Phase 7 screen — and the repo's `arize-phoenix>=14.6,<15` cap is
**correct and now verified**: 20.3.0 raises `ValueError: mutable default <class 'mappingproxy'>`
on import under Python 3.11 **[MEASURED]**.

**Q3 — LangSmith.** **No.** It is proprietary SaaS; self-hosting is an **Enterprise-plan add-on
requiring a licence key from sales and a Kubernetes cluster** **[DOC]**. Being LangChain's
first-party product for LangGraph buys nothing here that OpenInference's LangChain
instrumentation into Phoenix does not already buy, and it would put tenant prompts on a third
party's servers. One operational note in §3: `langsmith` arrives transitively and is inert
**only** while `LANGCHAIN_TRACING_V2` is unset.

---

# Q1 — Langflow

## 1.0 The version split, which changes everything below

**Read this before anything else in Q1.** Almost every article, doc page and source file about
Langflow's component governance describes `main`, and `main` is **1.12.0-dev**. PyPI shows a
`1.12.0.devN` build published **every single day** for the fortnight to 2026-08-18; the newest
**stable** release is **1.11.3** **[MEASURED]**.

I installed 1.11.3 and compared it against `main`:

| Capability | `main` (1.12.0-dev) | **1.11.3 (installed, measured)** |
|---|---|---|
| `catalog_policy` — component blocking | Present: model, service, `api/v1/catalog_policy.py`, palette + run enforcement | **Absent.** No `services/catalog_policy/`, no `api/v1/catalog_policy.py`, `GET /api/v1/catalog-policy/components` → **404** |
| `CATALOG_POLICY_SERVICE` in the pluggable `ServiceType` enum | Yes | **No** — the enum has 23 members and that is not one of them |
| `model_provider_policy` / `policy_bundle` services | Present | **Absent** |
| Palette endpoint filters by policy | `get_all()` calls `_filter_component_palette_by_catalog_policy` | `get_all()` is 20 lines with **no filtering of any kind** |
| Pluggable services (`lfx.toml` / entry points) | Yes | **Yes** — `lfx/services/config_discovery.py` present |
| `AUTHORIZATION_SERVICE` pluggable | Yes | **Yes** |
| OSS authorization = pass-through `return True` | Yes | **Yes** |
| External auth / JWKS / access ceiling | Yes | **Yes** — the full `EXTERNAL_AUTH_*` settings block |
| `allow_custom_components`, `custom_component_admin_only`, `block_code_interpreter_components`, `allow_components_paths_override` | Yes | **Yes** |

**[MEASURED]** for the whole right-hand column.

The consequence: **on the version we can actually pin today, there is no seam inside Langflow
for per-user component visibility.** Not a global one, not a per-tenant one, none. That is the
central problem this plan has to solve, and §1.4 solves it outside Langflow rather than waiting
on an unreleased build.

## 1.1 The licence, exactly

`https://raw.githubusercontent.com/langflow-ai/langflow/main/LICENSE` is **21 lines, the
standard MIT text**, `Copyright (c) 2024 Langflow` **[MEASURED]**. PyPI metadata for
`langflow` 1.11.3 reports `license_expression: MIT` **[MEASURED]**.

I checked for the open-core pattern (a permissive root licence plus a commercially licensed
`ee/` tree, which is exactly what Langfuse does — §2.2). **Langflow does not have one.**
`LICENSE.md`, `LICENSE-EE`, `ENTERPRISE_LICENSE` and `NOTICE` all return 404, and the repository
root listing contains exactly one `LICENSE` file **[MEASURED]**.

**What MIT permits, precisely, for our case:**

| We want to | Permitted? |
|---|---|
| Run it inside a commercial product we charge for | Yes, without restriction |
| Serve it to multiple tenants as part of a hosted service | Yes — MIT has no hosted-service limitation (unlike ELv2, §2.5) |
| Modify it, fork it, patch it, ship the result | Yes |
| Keep our modifications private | Yes — no copyleft, no source-disclosure term |
| Sublicense it as part of Aegis | Yes, explicitly ("sublicense, and/or sell") |

**The one obligation:** *"The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software."* If Langflow ships inside an
Aegis distribution, its MIT text must travel with it — a `THIRD-PARTY-NOTICES` file. That is the
entire compliance burden, and it is trivial.

**Two things MIT does not cover, and both matter.** First, **trademark**: MIT grants no rights to
the Langflow name or logo, so an Aegis screen must not present the canvas as an Aegis product
using Langflow's branding, nor imply endorsement. Second, and much more important, the **566
transitive dependencies carry their own licences** (§1.7) — one MIT root does not make the
installed tree MIT. I did not audit all 566 and I am marking that **[THIN]** rather than rounding
it up. For a demo it does not matter; if Aegis is ever *distributed*, it is real work.

**Nothing in the licence constrains the design.** The constraints in this document are all
technical.

## 1.2 What Langflow actually is, physically

Not a library. **A FastAPI server plus a private React SPA plus its own database.**

- **Backend:** FastAPI, SQLModel/SQLAlchemy, **Alembic-managed migrations**, ~48 route modules
  under `api/v1/` **[SOURCE-1.11.3]**.
- **Frontend:** `src/frontend/package.json` is `"name": "langflow", "private": true` — **it is
  not published to npm** **[MEASURED]**. React 19, `react-router-dom` 7, `@xyflow/react` ^12.3.6
  (plus legacy `reactflow` ^11.11.3), 93 dependencies. There is **no importable canvas
  component**: you cannot `npm install` the builder into the Next.js app.
- **Database:** SQLite by default; Postgres via `LANGFLOW_DATABASE_URL` **[DOC]**. ~25 model
  packages under `services/database/models/`.
- **The only official embed** is the chat *widget* — a `langflow-chat` web component loaded from
  a CDN that talks to one flow **[DOC]**. The **builder canvas is not offered as an embeddable
  artifact**.

**So embedding into the Next.js app means an `<iframe>` of the Langflow SPA.** One helpful
measured detail: Langflow sets **no `X-Frame-Options` and no `frame-ancestors` CSP** — a code
search for both across the repository returns **zero hits**, while `CORSMiddleware` returns two
**[MEASURED]**. Nothing in the application blocks framing it; whatever reverse proxy fronts it
must not add those headers either.

## 1.3 Multi-tenancy: what exists and what does not

### There is no tenant

`services/database/models/user/model.py` — the complete `User` table **[SOURCE-1.11.3]**:

```
id, username, password, profile_image, is_active, is_superuser,
create_at, updated_at, last_login_at, store_api_key, optins
```

**No `tenant_id`. No `org_id`. No `workspace_id`.** The only authority axis is the boolean
`is_superuser`. Flows hang off `Flow.user_id`; grouping is by `Folder`/"project", itself per-user.

Consequence for Aegis's diagnostic: **`aegis/src/aegis/governance/rls.py`'s boot-time catalog
read-back reports live tables that carry a `tenant_id` and lack a policy.** Not one Langflow
table carries that column, so if Langflow's tables landed in the Aegis database the read-back
would report **healthy** while ~25 tables of tenant work sat ungoverned. Same failure mode the
job-framework survey found across all 24 candidates, and the reason §1.5 puts Langflow in **its
own database**.

### RBAC exists as tables, and as a no-op

`services/database/models/auth/authz.py` is real and quite good: `CasbinRule`, `AuthzRole` (with
a `workspace_id` column), `AuthzRoleAssignment` (`domain_type` global/workspace/project),
`AuthzTeam`, `AuthzShare`, `AuthzEditLock`, `AuthzAuditLog`. Its module docstring: *"Authorization
(RBAC) tables (Alembic-owned; **plugins populate policy data**)"* **[SOURCE-main]**.

The OSS implementation that consumes them, `services/authorization/service.py`, is
`LangflowAuthorizationService`, docstring *"OSS pass-through authorization service (always
allows)"*, `enforce()` body `return True` — **present and identical in 1.11.3**
**[SOURCE-1.11.3]**. The class warns you about itself:

> `LANGFLOW_AUTHZ_ENABLED=true` but the OSS pass-through authorization service is registered
> (no enforcement plugin found). **Every enforce() call will return True**; route guards still
> run and audit rows still write, but no policy is applied.

The docs agree: *"The open-source build registers a pass-through service that **always allows**
every action"* **[DOC]**. Roles `viewer`/`developer`/`admin` are defined; nothing enforces them.

**Do not plan around Langflow RBAC — it does not exist in the build we can use.** Whether an
enforcement plugin is sold commercially, and by whom (IBM ships "IBM Langflow" and watsonx
Orchestrate with "SSO, RBAC, audit trails"), I could not establish from a primary source:
**[THIN]**. It does not change the plan.

### There *is* one real enforcement primitive in OSS, and it is useful

`services/authorization/access_ceiling.py` — a **request-scoped, deny-only action ceiling**
derived from a trusted external identity, stored in a `ContextVar`, consulted by the route
guards **[SOURCE-main]**; the `EXTERNAL_AUTH_ACCESS_CEILING_*` settings that drive it are present
in 1.11.3 **[MEASURED]**. Levels: `viewer` → `{read}`, `editor` → `+{write, create, delete,
execute, ingest}`, `admin` → everything.

Two things follow, both load-bearing:

1. **A per-request `ContextVar` carrying identity-derived policy is Langflow's own established
   pattern** — not something we are inventing.
2. **A coarse read-only / editor split is available today from an Aegis JWT claim, with zero
   code.**

### Component access control on `main`: what it would be

For completeness, since 1.12 may land before 30 August. `CatalogPolicyRule` +
`api/v1/catalog_policy.py` **[SOURCE-main]**:

- It is a **blocklist**. `CatalogPolicyMode.ALLOW` carries the comment *"`ALLOW` is a schema
  reservation for a future allowlist phase and has no P1 resolution semantics."*
- It is **global**. *"P1 reads and writes only `GLOBAL` rules. Organization and workspace values
  reserve the existing authorization domain shape for a later scoped-policy phase and have no P1
  resolution semantics."*
- Every endpoint is `Depends(get_current_active_superuser)`; docstrings say *"the complete
  **global** component block set"*.
- It **fails open**: *"An empty snapshot is intentionally fail-open: until a durable policy has
  been hydrated, every component and template is available."*

So even on `main`, **the user's core requirement — a tenant admin grants component access per
user — has no implementation.** What exists there is one platform-wide denylist, editable only by
the Langflow superuser, that reserves the *shape* of the thing we want.

## 1.4 The component-access seam — two tracks, and Track A works today

### Track A — the Aegis proxy (build this; it does not depend on Langflow's roadmap)

Langflow already sits behind an Aegis reverse proxy for auth and iframe delivery. Make that
proxy the policy point. It needs to do exactly three things, and the routes are enumerable
because I read them out of the installed package **[SOURCE-1.11.3]**:

| Proxy behaviour | Routes it must cover |
|---|---|
| **1. Filter the palette** — decompress the response, drop components not on this user's allowlist, re-encode | `GET /api/v1/all` (returns `{category: {name: {...}}, component_display_names}`) |
| **2. Validate on write** — reject a flow whose `data.nodes[].data.type` contains a key not on the allowlist | `POST /api/v1/flows/`, `PATCH /api/v1/flows/{flow_id}`, `POST /api/v1/flows/batch/`, `POST /api/v1/flows/upload/` |
| **3. Validate on run** — same check against the stored/submitted graph | `POST /api/v1/run/{flow_id_or_name}`, `POST /api/v1/build/{flow_id}/flow`, `POST /api/v1/webhook/{flow_id_or_name}`, `POST /api/v1/build_public_tmp/{flow_id}/flow` |
| **4. Deny outright** | `POST /api/v1/custom_component`, `POST /api/v1/custom_component/update` |

**Default-deny the route table.** The proxy forwards an explicit allowlist of paths and 404s
everything else. Langflow has ~48 route modules including `a2a`, `mcp`, `deployments`,
`voice_mode`, `store`, `knowledge_bases` — most of which a tenant user has no business calling,
and each of which is a surface we have not reviewed. This is the single highest-value line of
the whole proxy.

Properties worth stating plainly:

- **It works on 1.11.3 today**, with no unreleased dependency and no fork.
- **It is enforcement, not cosmetics** — steps 2 and 3 mean a user who bypasses the filtered UI
  and POSTs a flow with a forbidden component gets a 403 at write *and* at run.
- **It fails closed by construction**: no identity ⇒ no allowlist ⇒ empty allowlist ⇒ nothing
  passes. The opposite of Langflow's fail-open default, and the right way round.
- Cost is a payload rewrite and a graph walk. The palette response is **799,651 bytes compressed
  / 374 components across 103 categories** **[MEASURED]** — cache the filtered variant per
  grant-set, not per request.
- The honest weakness: it is **outside** the thing it governs. If someone reaches Langflow's port
  directly, none of it applies. Bind Langflow to `127.0.0.1`, and treat "the Langflow port is not
  reachable except via the proxy" as a security invariant with a test.

### Track B — the pluggable policy service (only if 1.12 ships and we choose to move)

`src/lfx/PLUGGABLE_SERVICES.md` documents a first-class service-replacement mechanism:
*"LFX now supports a pluggable service architecture that allows you to customize and extend
service implementations **without modifying core code**"* **[SOURCE-main]** — decorator,
`lfx.toml`, or a `[project.entry-points."lfx.services"]` entry point, with config files at
highest priority. `config_discovery.py` is **present in 1.11.3** **[MEASURED]**, so the mechanism
is real today; only the `catalog_policy_service` key is missing.

If 1.12 releases in time:

```toml
# lfx.toml
[services]
catalog_policy_service = "aegis_langflow.policy:AegisCatalogPolicyService"
authorization_service  = "aegis_langflow.authz:AegisAuthorizationService"
```

`BaseCatalogPolicyService.snapshot` is an **abstract property** **[SOURCE-main]**, read at three
enforcement points that already take the snapshot as a parameter:

| Point | Symbol | Stops |
|---|---|---|
| Palette | `api/v1/endpoints.py` `get_all()` → `_filter_component_palette_by_catalog_policy` | Blocked components never render |
| Build/run | `lfx/graph/graph/base.py` `Graph.from_payload` → `validate_catalog_policy_for_flow` | A saved flow with a blocked component refuses to run |
| Custom code | `api/v1/custom_component_policy.py` | *"Catalog denial is evaluated first and **has no superuser bypass**"* |

Our implementation resolves per request:

```
snapshot → identity from ContextVar (set by middleware from the Aegis JWT)
         → allowed component keys for (tenant, user)
         → CatalogPolicySnapshot(blocked = ALL_REGISTRY_KEYS − allowed)
```

Three details that make it correct rather than merely plausible:

1. **Allowlist expressed as a blocklist.** Enumerate the registry once via
   `langflow.interface.components.get_and_cache_all_types_dict` and subtract. Cache per
   grant-set.
2. **Fail closed.** An empty snapshot means *everything allowed*. Unset ContextVar must return
   *everything blocked*. This is the most likely way to ship a silent hole.
3. **Aliases.** Blocking resolves canonical identity via `build_component_identity_index`, so a
   legacy alias still maps to the blocked component. Key our allowlist the same way; do not
   string-match.

Set `external_policy_snapshot` non-`None` so `_raise_if_externally_managed` makes Langflow's own
superuser API return **409** on any write — Aegis owns the policy and the built-in admin screen
cannot diverge from it.

**Track B's own risk:** the snapshot comes from a `ContextVar`, which is copied into `asyncio`
tasks but does not cross processes. Pin `LANGFLOW_JOB_QUEUE_TYPE=asyncio` and one worker — on
Windows that is forced anyway, *"On Windows and macOS, `langflow run` uses a single Uvicorn
process"* **[DOC]**. Any path that runs a flow outside the originating request (scheduled,
webhook, MCP) must re-resolve identity from the stored flow's owner. Enumerate those paths and
write a test per path.

### Recommendation

**Build Track A. Treat Track B as an optimisation, not a dependency.** Track A is version-proof,
it is enforcement rather than UI filtering, and its route-allowlist does far more security work
than component filtering alone. If 1.12 lands and proves stable, Track B moves the palette filter
in-process and Track A keeps the route allowlist and the write/run validation as defence in
depth. Do not sequence anything behind an unreleased version.

### Cost

The proxy is a bounded piece of work: a route allowlist, one response rewriter, one graph
validator, one cached allowlist resolver. **The expensive half is the admin UI** — a tenant-admin
screen where component grants are set per user or per seat, persisted in the Phase 3 `settings`
catalogue as `SettingSpec` entries with `merge: tighten_only`, so a tenant can never widen past
the platform allowlist. Reuse that mechanism; do not invent a second permission store.

## 1.5 Where it physically lives

**Separate process, separate venv, separate Postgres database, same box, behind the Aegis proxy,
framed in the Next.js app.**

### Separate venv is forced, not preferred

Co-installing into `backend/.venv` fails on a **major-version conflict**, measured against what
is installed today:

| Package | Aegis `backend/.venv` | Langflow resolve | |
|---|---|---|---|
| `protobuf` | **7.35.1** | **6.33.6** | major conflict |
| `grpcio` | 1.83.0 | 1.78.0 | downgrade |
| `onnxruntime` | 1.28.0 | 1.23.2 | downgrade — this is the Phase 4 reranker's runtime |
| `litellm` | 1.96.0 | 1.97.0 | minor |
| `chromadb` | 1.5.9 | 1.5.9 | identical |
| `langgraph` | 1.2.11 | **1.2.11** | identical |
| `langchain-core` | 1.5.4 | 1.5.6 | compatible |
| `pydantic` / `sqlalchemy` / `fastapi` / `alembic` | 2.13.4 / 2.0.52 / 0.141.1 / 1.19.1 | identical | identical |

**[MEASURED]** — `uv pip compile --python-version 3.11 --python-platform windows`.

The bottom rows are the interesting ones: Langflow pins **the same LangGraph version Aegis runs**
and agrees on the whole web stack, so nothing here is architecturally hostile. But protobuf 6-vs-7
and an `onnxruntime` downgrade onto the reranker's runtime settle it. **Two venvs, two
processes** — which is also right for a second reason: Langflow can execute user-authored Python,
and that should not share an interpreter with the RLS-scoped database session.

### Separate database, and this is the tenancy answer

`LANGFLOW_DATABASE_URL` points at Postgres and Langflow runs **its own Alembic migrations**
**[DOC/SOURCE-1.11.3]**. Point it at a **separate database on the same Postgres 17 instance**
(`langflow`), owned by a **separate role with no grants on the Aegis database**.

Why not a schema inside the Aegis database:

- ~25 Langflow tables, **none with a `tenant_id`**, would sit in the catalog `rls.py`'s read-back
  scans. They would not be *reported* (no `tenant_id` ⇒ invisible to the diagnostic), which is
  worse than being reported: **the health check would say healthy.**
- Langflow's Alembic and Aegis's deliberately-Alembic-free additive reconciler would share a
  migration surface. `02-ROADMAP.md` makes not dragging in Alembic an explicit constraint.
- A separate database is one env var and zero code.

**Then: how are flows scoped per tenant, if Langflow has no `tenant_id`?**

**Through identity, not schema.** One Langflow user per Aegis user, provisioned just-in-time from
an Aegis-minted JWT; `Flow.user_id` scopes flows to that user, and Langflow's ownership checks —
*"users who aren't superusers cannot use their API keys to access other users' resources"*
**[DOC]** — do the per-user isolation.

The cross-tenant boundary is therefore **the identity mapping, not a database predicate**, and it
must be written down as such. Aegis keeps the authoritative mapping in an Aegis-owned,
tenant-scoped, RLS-governed table:

```
langflow_identities (tenant_id, user_id, langflow_user_id, created_at)
   -- registered in aegis.governance.rls._TENANT_SCOPED_TABLES
```

That table **does** carry `tenant_id`, so the boot-time read-back sees it and demands a policy —
the invariant stays honest. Any tenant-level listing or sharing query answers from Aegis's side
of the mapping, never from Langflow's tables.

**Say plainly what this does not give you.** Two users in different tenants are separated by
Langflow's ownership checks and by being different Langflow users — **not by Postgres RLS**. A
privilege bug in Langflow's ownership checks is a cross-tenant leak Aegis's RLS cannot catch,
because the query never touches an Aegis table. What actually mitigates it: bind Langflow to
loopback so only the proxy reaches it; `LANGFLOW_AUTO_LOGIN=false` and a rotated superuser
credential that is never issued to a person; `LANGFLOW_EXTERNAL_AUTH_DISABLE_API_KEYS_FOR_EXTERNAL_USERS=true`
(the default) so a Langflow API key cannot bypass the Aegis-issued identity **[DOC]**; and the
proxy refusing any request without a valid Aegis identity.

### Single sign-on: supported in 1.11.3, no fork

`LANGFLOW_EXTERNAL_AUTH_ENABLED=true` plus **[DOC, settings verified MEASURED in 1.11.3]**:

| Variable | Use |
|---|---|
| `LANGFLOW_EXTERNAL_AUTH_JWKS_URL` | Aegis's JWKS endpoint — Langflow verifies our signature (https enforced by a validator) |
| `..._AUDIENCE` / `..._ISSUER` / `..._ALGORITHMS` | standard claim checks |
| `..._SUBJECT_CLAIM` / `..._USERNAME_CLAIM` / `..._EMAIL_CLAIM` | JIT user provisioning from our token |
| `..._ACCESS_CEILING_ENABLED` + `..._ACCESS_CLAIM` + `..._ACCESS_CLAIM_MAPPING` | maps an Aegis role claim to viewer/editor/admin, enforced by §1.3's ceiling |
| `..._IDENTITY_RESOLVER` | **a Python import path to our own resolver** — total control over identity mapping |
| `..._TRUSTED_JWT_DECODE` | **leave false.** It skips signature verification entirely |

Login story: Aegis mints a short-lived JWT carrying `tenant_id`, `user_id`, `fine_role` and the
component-grant version; the iframe request carries it; Langflow verifies it against Aegis's
JWKS and binds the local user; the proxy uses the same token to resolve the allowlist. **One
token drives authentication, the access ceiling, and component visibility.**

## 1.6 Governance — the question that decides the architecture

> *If a tenant user builds a flow and hits Run, does it execute through Aegis's budgets, RLS,
> guardrails and the risk gate, or around them?*

**Today, unambiguously around them.** A Langflow "Language Model" node instantiates a LangChain
chat model inside the Langflow process and calls the provider directly. It never enters
`aegis.gateway.complete`, so:

- **Budgets are not checked.** `backend/src/app/core/llm.py:_GovernanceHook.enforce` is the
  chokepoint and it is gated on a bound `GovernanceContext`: *"an unscoped request (no governance
  context …) **skips the database entirely**"* **[SOURCE]**. No context ⇒ no enforcement,
  silently.
- **`usage_ledger` gets no row**, so tenant spend is invisible and the budget pill lies.
- **Guardrails do not run.** `check_input`/`check_output` are on Aegis's agent path only.
- **The risk gate does not exist** on this path — no `interrupt()`, no approvals row.
- **RLS is irrelevant** because the query never reaches an Aegis table.
- **A Custom Component is arbitrary Python in the server process.**

Four things fix it, ordered by value per unit of work.

### G1 — The model chokepoint: an Aegis OpenAI-compatible endpoint (the keystone)

Aegis already has the right shape. `aegis/src/aegis/gateway/llm.py` routes everything through
*"a custom OpenAI-compatible provider — model string form `openai/<deployment_id>`, with
`api_base` + `api_key` supplied per call"*, with **budget/rate governance and observability as
injected hooks** **[SOURCE]**.

So: expose `POST /v1/chat/completions` and `/v1/embeddings` on the Aegis backend, which
authenticate the caller, **bind the `GovernanceContext` for that tenant/user**, and delegate to
`aegis.gateway.complete` / `embed`. Configure every Langflow model component with
`base_url = https://aegis/v1` and a **per-user, short-lived key**.

What that one move buys:

| Control | How it applies |
|---|---|
| Budgets | `enforce_governance` runs before spend, **fail-closed** on a DB error |
| Usage ledger | `record_usage` writes a row per call, in the call's real billing units |
| Model allowlist | The endpoint resolves `ModelRole`, so a tenant gets their tier's models and cannot name a model directly |
| Guardrails | `check_input` / `check_output` at the endpoint, on prompt and completion |
| Observability | The gateway's injected sink already emits `gen_ai.*` spans |
| Cancellation, rate limits, admission caps | One place |

**Belt and braces:** on Track A the proxy blocks the provider components outright; on Track B
also replace `model_provider_policy_service`. Either way a tenant user should be unable to
construct a direct provider call even by typing a key into a node.

**This is the highest-leverage integration in the plan** — it converts every model call in every
flow from ungoverned to governed with no Langflow code change. Do it before the grant UI.

### G2 — Kill arbitrary code execution, on day one

All four flags exist in 1.11.3 **[MEASURED]**:

- `LANGFLOW_ALLOW_CUSTOM_COMPONENTS=false` — *"blocks execution of components whose code does not
  match a known server template and disables registered built-in code-execution components at
  runtime"* **[SOURCE-1.11.3]**
- `LANGFLOW_CUSTOM_COMPONENT_ADMIN_ONLY=true`
- `LANGFLOW_BLOCK_CODE_INTERPRETER_COMPONENTS=true` — blocks *"Python Interpreter/REPL/Function,
  Smart Transform, CSV Agent, CodeAct, Cuga, OpenDsStar"*
- `LANGFLOW_ALLOW_PUBLIC_CUSTOM_COMPONENTS=false` (already the default)
- `LANGFLOW_STORE_ENVIRONMENT_VARIABLES=false` so a flow cannot read the host process's
  environment, which in this deployment includes database credentials

**And quote this to whoever signs off the architecture** — it is Langflow's own docstring on
`allow_custom_components` **[SOURCE-1.11.3]**:

> *Note: this is a beta feature. **For security in a multi-tenant environment, use
> hardware-level isolation** to restrict access.*

The vendor is telling you their in-process code control is not a multi-tenant boundary. We are
not going to run one VM per tenant before 30 August, so the honest posture is: **custom code is
off, the proxy denies the two `custom_component` routes, and the security posture document says
this is a defence-in-depth control, not an isolation boundary.**

There is one subtlety that is actually a feature: `LANGFLOW_COMPONENTS_PATH` acts as *"an
admin-curated allow-list that remains executable even when `allow_custom_components` is False"*
**[SOURCE-1.11.3]**. That is exactly the mechanism G3 needs — our components stay runnable while
everyone else's arbitrary code does not. (`allow_components_paths_override=false` disables that
bypass; we want it left at its default `true`.)

### G3 — Aegis components: retrieval, tools, memory, the agent itself

Ship an Aegis component bundle via `LANGFLOW_COMPONENTS_PATH` — thin Langflow components that
call Aegis's `/v1` API with the user's token:

- **Aegis Retrieval** — hybrid retrieval, RLS-scoped in Aegis, so a tenant user physically cannot
  retrieve another tenant's chunks. Contrast with the raw OpenSearch/Chroma nodes in the
  screenshot, which have **no tenancy at all**.
- **Aegis Tool** — one node over the typed tool registry, allowlist resolved server-side.
- **Aegis Agent** — runs the real agent graph, guardrails and risk gate included, returning the
  answer plus its `run_id`.
- **Aegis Memory** — per-subject memory, `session_id` bound server-side.

Then the tenant allowlist is drawn **mostly from this bundle** and the built-in bundles are
blocked. That inverts the risk posture: instead of governing 374 third-party components, you
govern the ~10 on the allowlist and everything else is denied by default.

### G4 — Runs are recorded in `run_events`, or they did not happen

Langflow has its own tracing (`services/tracing/`, including a `native` tracer that writes to its
own DB) and can export OTLP to Phoenix via `PHOENIX_COLLECTOR_ENDPOINT` **[SOURCE-main]** — point
that at the local Phoenix so flow spans and Aegis spans share one trace tree.

But per `plans/04` §2.1, **Phoenix is not the record.** Every Aegis-mediated call from a flow (G1
and G3) emits `run_events` rows carrying `tenant_id`, `user_id`, the flow id and the `trace_id`,
so a tenant's flow run is as replayable and auditable as a console run. This costs almost nothing
extra *if* G1 and G3 are the only paths that can reach a model or the data — which is the point
of doing them.

### The honest residue

Even with all four, **the flow topology itself is not governed.** Aegis's risk gate reasons about
*actions*, not graphs; a user can wire allowed components into a loop or a fan-out nobody
reviewed. Cheap mitigations: max node and edge count per flow, a per-flow run timeout, and the
Phase 3 per-tenant admission cap applied to flow runs. Full flow-level static analysis is not
worth building before 30 August — say so rather than implying the gate covers it.

## 1.7 Windows 11, no Docker, 16 GB — measured

**Native install and boot: verified.** I installed and ran `langflow` 1.11.3 on Python 3.11 in a
throwaway venv, with no Docker and no container of any kind **[MEASURED]**:

| Measurement | Value |
|---|---|
| Install exit code | `0` — 566 packages, no build failures |
| **On-disk footprint** | **1.8 GB** (`du -sh` on the venv), 1,115 entries in `site-packages` |
| **Cold `import langflow`** | **2.7 s**; `from langflow.main import create_app` a further **1.6 s** |
| Import-only peak RSS | **492 MB** |
| Boot to `GET /health` → `{"status":"ok"}` 200 | under 90 s (single Uvicorn process, SQLite) |
| **Idle resident set after startup** | **≈1.09 GB** (`ps -o rss` = 1,112,592 KB) |
| RSS after serving the full palette | **≈1.10 GB** |
| `GET /api/v1/all` | 200, **799,651 bytes** compressed, ~0.2 s warm |
| Components in the default palette | **374**, across **103** categories |

This was measured on macOS/arm64, not Windows — the numbers will differ somewhat, but not by an
order of magnitude, and the failure modes that would matter (a package with no Windows wheel, a
compiler requirement) are covered separately below. **The number that governs the plan is ≈1.1 GB
resident and 1.8 GB on disk for a second Python web application.**

**Dependency weight, resolved for Windows [MEASURED]** (`--python-version 3.11 --python-platform
windows`):

| Distribution | Packages | Windows wheel bytes | Notes |
|---|---|---|---|
| `langflow` (everything) | **566** | **≈505 MB** | Every one has a Windows-compatible distribution — **0 unresolved** |
| `langflow-base[complete]` | **376** | **≈345 MB** | The server without the vendor bundles |

Largest items: `opencv-python` 44 MB, `scipy` 37 MB, `ibm-db` 28 MB, `pyarrow` 28 MB, `litellm`
24 MB, `chromadb` 24 MB, `langflow-base` 18 MB. `pywin32` 7 MB resolves cleanly, which is a good
sign for the Windows path.

**Python version:** `requires_python >=3.10,<3.15`; Aegis runs 3.11; verified working on 3.11
**[MEASURED]**. Windows is documented as supported; only **Langflow Desktop** needs a C++ build
tool **[DOC]**, and we run the server.

**Concurrency:** on Windows, `langflow run` is **one Uvicorn process** — Gunicorn multi-worker is
Linux-only, and multi-worker additionally requires Redis 6+ for the job queue **[DOC]**. Memurai
would serve if ever needed. Single-process is correct for the demo and is what makes Track B's
ContextVar design sound.

**The 16 GB verdict.** Postgres 17, Memurai, Neo4j Desktop, Temporal, the Aegis backend (with an
in-process Phoenix and an ONNX reranker) and the Next.js dev server already share this machine.
Adding **≈1.1 GB resident** is affordable but not free, and it is the tightest constraint in this
plan. **Do this: measure resident set on the actual Windows box with everything else running,
before Phase 6 depends on it.** If it does not fit, the lever is `langflow-base[complete]` plus
the Aegis bundle — 190 fewer packages, ~160 MB less download, and correspondingly less import
weight — at the cost of a less-travelled configuration that would need its own verification.

**No Docker at any point.** Postgres is already native; Langflow is a pip install.

## 1.8 The plan, in dependency order

| # | Step | Depends on | Size |
|---|---|---|---|
| **L0** | Second venv, `langflow==1.11.3` pinned from a local wheel cache, own Postgres DB + role, bound to `127.0.0.1` | Phase 3 (tenants exist) | Config |
| **L1** | Lock it down before anyone sees it: `ALLOW_CUSTOM_COMPONENTS=false`, `CUSTOM_COMPONENT_ADMIN_ONLY=true`, `BLOCK_CODE_INTERPRETER_COMPONENTS=true`, `AUTO_LOGIN=false`, `STORE_ENVIRONMENT_VARIABLES=false`, superuser credential rotated and unissued | L0 | Config |
| **L2** | SSO: Aegis JWKS + `EXTERNAL_AUTH_*` + access ceiling; `langflow_identities` table registered in `_TENANT_SCOPED_TABLES` with a policy | L1, Phase 3 `fine_role` on the wire | Small |
| **L3** | **G1** — Aegis `/v1/chat/completions` + `/v1/embeddings` over `aegis.gateway`, per-user keys, Langflow model components pointed at it | Phase 3 governance context | **Keystone.** Medium |
| **L4** | **Track A proxy** — default-deny route allowlist, palette filter, flow-write and flow-run validation, `custom_component` denied | L2 | Medium |
| **L5** | Tenant-admin grant UI as `SettingSpec` entries with `merge: tighten_only` | L4, Phase 7 settings catalogue | Medium — the UI is the bulk |
| **L6** | **G3** — Aegis component bundle via `LANGFLOW_COMPONENTS_PATH`; vendor bundles denied at the proxy | L3 | Medium |
| **L7** | **G4** — `run_events` rows for flow-originated calls; `PHOENIX_COLLECTOR_ENDPOINT` at the local Phoenix; "open trace" link | Phase 3 `run_events` | Small |
| **L8** | Embed: iframe in Next.js, token handoff, node/edge caps, per-flow timeout, admission cap | L2 | Small |
| **L9** | *Optional, only if 1.12 releases and proves stable:* Track B plugin moves the palette filter in-process; the proxy keeps route allowlist + validation as defence in depth | L4 | Medium |

**The demo sentence this earns:** *"A tenant admin grants this user four components. The user
builds a pipeline, hits Run, and the answer comes back — and every model call in it was budgeted,
guard-railed, ledgered against their tenant, and retrieved only their tenant's documents, because
the only components they were given are the governed ones."*

**Do L3 before L5.** A component-grant UI over ungoverned components is the nice UI on the hole.

## 1.9 What could still go wrong, stated plainly

1. **The release we can pin has no component governance at all** (§1.0). Track A exists precisely
   because of this. Do not let any plan item depend on 1.12.
2. **Langflow's own code-execution control is documented as beta and explicitly not a
   multi-tenant boundary** (§1.6 G2). We compensate with layered controls, not isolation. Write
   that in the security posture in those words.
3. **Cross-tenant isolation is by identity, not RLS** (§1.5). Do not let any document imply RLS
   covers Langflow data.
4. **≈1.1 GB resident, unmeasured on the target machine** (§1.7).
5. **A fast release train.** `1.12.0.dev31` published 2026-08-18, a dev build every day for two
   weeks **[MEASURED]**. Pin exactly, vendor the wheels, freeze before rehearsal.
6. **The proxy is a bypassable boundary if the port is reachable.** Loopback bind + a test.
7. **Trademark and third-party notices** if Aegis is ever distributed (§1.1).

---

# Q2 — Langfuse vs Arize Phoenix

## 2.1 The question that actually decides it

Not features. **Whether it installs on Windows 11 with no Docker.**

Langfuse self-hosted requires, from its own architecture documentation **[DOC]**:

- **Postgres** — "the main database for transactional workloads" ✔ we have it
- **ClickHouse** — "stores traces, observations, and scores" ✘
- **Redis/Valkey** — queue and cache ✔ Memurai
- **S3 / blob store** — "persist all incoming events, multi-modal inputs, and large exports" ✘
- **Two application processes** — web server + worker (Node/Next.js, not Python)

**ClickHouse has no native Windows binary.** The vendor's knowledge base and community answer is
WSL2, and there is an open request for native Windows support **[DOC]**. An S3-compatible store
means MinIO or similar — another service with its own Windows story.

So Langfuse on this machine costs: WSL2 (a Linux VM, which is the thing the no-Docker constraint
exists to avoid), a ClickHouse server, a MinIO server, a Node runtime, and two more processes on
a 16 GB box that §1.7 just showed is already tight. **Not a close call.**

Langfuse Cloud sidesteps all of it and has a real free tier — but it sends tenant prompts and
completions to a third party, and a demo should not depend on the venue's network.

## 2.2 Licences, since they differ in kind

- **Langfuse:** root `LICENSE` is **MIT Expat** with a carve-out — *"All content that resides
  under the `ee/`, `web/src/ee/`, and/or `worker/src/ee/` directories … is licensed under the
  license defined in `ee/LICENSE`"* **[MEASURED]**, which is a commercial Enterprise Licence
  requiring a paid agreement. Genuine open core: *"All core Langfuse features and APIs are
  available in Langfuse OSS (MIT licensed) without any limits"*, with these behind
  `LANGFUSE_EE_LICENSE_KEY` **[DOC]**: **project-level RBAC roles**, protected prompt labels,
  data-retention policies, **audit logs**, server-side data masking, UI customisation,
  organization creators, Org Management API/SCIM, Instance Management API.

  Note what that means for us: organisations and projects are free, but **project-level RBAC and
  audit logs — the two a multi-tenant platform actually needs — are the paid ones.**

- **Phoenix:** **Elastic Licence 2.0** — `arize-phoenix` 20.3.0 PyPI metadata reads
  `license: Elastic-2.0`, and the repository `LICENSE` is the ELv2 text **[MEASURED]**. Not open
  source. §2.5.

## 2.3 Features, honestly

| | Phoenix (installed: 14.6) | Langfuse |
|---|---|---|
| Tracing | Yes — OTel + OpenInference, which is exactly what Aegis emits | Yes — OTel-compatible plus native SDKs |
| Multi-tenancy | **None.** Projects + instance-level users; the docs are explicit that there are no organizations or tenants **[DOC]** | **Organizations → projects**, five roles. Project-level roles are EE **[DOC]** |
| Prompt management | Basic playground / prompt store | Stronger — versions, labels, deploy-by-label |
| Datasets & experiments | Yes | Yes |
| Evals | Yes (`arize-phoenix-evals` 3.4.0 installed) | Yes, plus LLM-as-judge on live traces |
| Cost tracking | Present, model-price based | Stronger, per-user and per-session |
| Self-host footprint | **One `pip install`, in-process, no server** | Postgres + ClickHouse + Redis + S3 + 2 services |
| Licence | Elastic 2.0 | MIT core / EE periphery |

**Langfuse wins the feature comparison and loses the deployment one decisively.** On the
calibration rule ("SOTA, not over-complex, not conservative"), the not-over-complex clause
settles it: four new infrastructure services, one needing a Linux VM, to improve a subsystem
`plans/04` deliberately defines as ephemeral.

## 2.4 Does Langfuse change the `run_events` split?

**No — it argues for it.**

`plans/04` §2.2 splits: `run_events` is the durable, tenant-scoped, never-sampled, RLS-governed
record; Phoenix is the ephemeral deep-dive with full payloads. Langfuse would be a *better*
ephemeral deep-dive. It would still not be the durable record, because:

- Its data lives in ClickHouse, outside Aegis's RLS and outside the boot-time catalog read-back.
- Its tenant model is Langfuse organisations — a **second** tenancy system to keep in sync with
  Aegis's, which is the exact "third mechanism where two suffice" the plan warns against. (Note
  we are already accepting one of these for Langflow, §1.5. Accepting a second, for
  observability, with no equivalent forcing reason, would be a mistake.)
- The console, audit trail, harness, replay and per-agent inspection are already specified as
  five queries over one table.

**The split stands.**

## 2.5 Two things about Phoenix that should be written down

**(a) Phoenix is Elastic Licence 2.0, not open source.** The binding clause **[MEASURED]**:

> *You may not provide the software to third parties as a hosted or managed service, where the
> service provides users with access to any substantial set of the features or functionality of
> the software.*

Aegis's current use is safe: `plans/04` puts Phoenix's audience as *"us, while debugging"*, and
internal use is squarely permitted. **The line is crossed if a tenant-facing screen exposes the
Phoenix UI**, or proxies a substantial set of its functionality to tenants. Phase 7 builds
per-role dashboards and Phase 6 a live agent panel, so this is a live constraint, not a
hypothetical.

**The rule to adopt: Phoenix is an operator tool on localhost; tenant-facing observability is
built on `run_events`, always.** Which is what the plan already says — this gives it a second,
legal reason. Also note ELv2's *"you may not remove or obscure any licensing, copyright, or other
notices"*, and that `arize-phoenix` belongs in `THIRD-PARTY-NOTICES` as **Elastic-2.0**, not
lumped in with the Apache/MIT set.

**(b) The `arize-phoenix>=14.6,<15` cap is correct, and now verified.** `backend/pyproject.toml`
says 15+ "fails to import on Python 3.11". Reproduced **[MEASURED]** by installing
`arize-phoenix==20.3.0` into a throwaway 3.11 venv:

```
File ".../phoenix/trace/dsl/filter.py", line 195, in <module>
    @dataclass(frozen=True)
ValueError: mutable default <class 'mappingproxy'> for field boolean_names is not allowed:
            use default_factory
```

A `mappingproxy` dataclass default: rejected by 3.11's `dataclasses`, accepted from 3.12. PyPI
still declares `requires_python: >=3.10,<3.15`, so **pip will happily install a build that cannot
be imported** — the cap is the only thing between a clean rebuild and a dead app. Keep it, keep
the comment. If Phoenix ever needs upgrading, the fix is Python 3.12+, not a version bump.

## 2.6 Recommendation

**Keep `arize-phoenix` 14.6 exactly as pinned. Do not adopt Langfuse.** Switching cost is not the
argument — Phoenix's integration is one module (`aegis/src/aegis/observability/otel.py`, ~130
lines, config injected, with a documented console-exporter fallback) and both speak OTel, so the
swap would be small. The argument is that Langfuse's *deployment* is four services and a Linux VM
for a benefit the durable record already provides.

**What would reverse it:** the deploy target stops being Windows-without-Docker. Then Langfuse's
organisations/projects + prompt management + cost tracking are a real upgrade, and the right shape
is Langfuse replacing Phoenix as the deep-dive while `run_events` stays the durable record.
Nothing here forecloses that; both are OTel consumers behind one injected sink.

**One thing worth doing regardless, nearly free:** point Langflow's tracer at the same Phoenix via
`PHOENIX_COLLECTOR_ENDPOINT` **[SOURCE-main]**, so a flow run and the Aegis calls it triggers
appear in one trace tree.

---

# Q3 — LangSmith

**Recommendation: no. Not now, and not conditionally.**

- **Licence.** Proprietary. There is no self-hostable open-source LangSmith.
- **Self-hosting.** An **add-on to the Enterprise plan**, requiring a licence key from
  `sales@langchain.dev`, deployed on **Kubernetes** (Docker Compose for dev only), with Postgres,
  Redis and ClickHouse behind it **[DOC]**. On a no-Docker Windows box with one engineer this is
  not a decision, it is an impossibility.
- **Cost.** Developer $0/seat (1 seat, 5k base traces/mo), Plus $39/seat/mo (10k base traces),
  Enterprise custom; LCU $1.50, LSU $1.00 **[DOC]**. Self-hosting is Enterprise-only.
- **Duplication.** Tracing, datasets, evals, prompt management, cost tracking — Phoenix covers
  four of five and Langfuse would cover all five. LangSmith adds nothing not already covered or
  already rejected on deployment grounds.

**Does "first-party for LangGraph" matter in practice?** In principle yes — LangSmith renders
LangGraph node/edge structure natively. In practice no, for a specific reason: Aegis's
observability is **OpenTelemetry + OpenInference semantic conventions**, and
`openinference-instrumentation-langchain` (0.1.70 in the resolved set, 0.1.57 of the base package
installed in `backend`) turns LangChain and LangGraph into OTel spans Phoenix renders as a span
tree. The first-party advantage is polish, not capability, and it costs a proprietary dependency
plus tenant prompts leaving the machine.

**One operational note that is not optional.** `langsmith` 0.11.0 arrives as a **transitive
dependency of Langflow** **[MEASURED]**, and `langchain-core` already pulls it into `backend`. It
is inert **only** while `LANGCHAIN_TRACING_V2` / `LANGSMITH_TRACING` and an API key are unset. Make
sure neither appears in any `.env` on either side — otherwise tenant prompts ship to LangChain's
servers as a side effect of an environment variable nobody meant to enable. **This deserves a
startup assertion**, in the same spirit as the RLS catalog read-back: a control that is invisible
when it fails is not a control.

---

# Appendix A — commands, so every number here can be re-run

Nothing below touched `backend/.venv` or `aegis/.venv`. Working directory was the session
scratchpad; `lf311` and `lfrun` were created with `backend/.venv/bin/python -m venv` (using that
interpreter to *create* separate venvs, not installing into it) and deleted afterwards.

```bash
# Licence (§1.1)
curl -sL https://raw.githubusercontent.com/langflow-ai/langflow/main/LICENSE     # 21 lines, MIT
for f in LICENSE.md LICENSE-EE ENTERPRISE_LICENSE NOTICE; do
  curl -s -o /dev/null -w "%{http_code}\n" -L \
    https://raw.githubusercontent.com/langflow-ai/langflow/main/$f; done          # 404 404 404 404

# Dependency weight and version conflicts (§1.5, §1.7)
echo langflow > req.in
uv pip compile --python-version 3.11 --python-platform windows req.in -o lf-win-lock.txt
grep -c '^[a-z0-9]' lf-win-lock.txt                                              # 566
echo 'langflow-base[complete]' > base.in
uv pip compile --python-version 3.11 --python-platform windows base.in -o lfbase-win.txt  # 376
python size.py lf-win-lock.txt     # 505 MB of Windows wheels, 0 packages unresolved
ls backend/.venv/lib/python3.11/site-packages | grep dist-info                   # Aegis pins

# Install, boot, and measure Langflow 1.11.3 (§1.0, §1.7)
uv pip install --python lfrun/bin/python langflow                                # exit 0
du -sh lfrun                                                                     # 1.8G
lfrun/bin/python -c "import time,resource;t=time.time();import langflow;print(time.time()-t)"
export LANGFLOW_CONFIG_DIR=... LANGFLOW_DATABASE_URL=sqlite:///... \
       LANGFLOW_AUTO_LOGIN=true LANGFLOW_PORT=7862 LANGFLOW_HOST=127.0.0.1
lfrun/bin/langflow run &
curl -s http://127.0.0.1:7862/health                       # {"status":"ok"} 200
ps -o rss= -p $(lsof -ti tcp:7862)                         # 1112592 KB ≈ 1.09 GB
curl -s -c ck.txt http://127.0.0.1:7862/api/v1/auto_login  # 200
curl -s --compressed -b ck.txt http://127.0.0.1:7862/api/v1/all -o all.json
                                                           # 200, 799651 bytes, 374 components
curl -s -b ck.txt http://127.0.0.1:7862/api/v1/catalog-policy/components
                                                           # 404 — absent in 1.11.3

# What 1.11.3 has and lacks (§1.0)
ls lfrun/.../site-packages/langflow/services/          # no catalog_policy/
grep -n CATALOG_POLICY lfrun/.../site-packages/lfx/services/schema.py    # no hits
grep -n "def get_all" -A 20 lfrun/.../langflow/api/v1/endpoints.py       # no policy filter
grep -n EXTERNAL_AUTH lfrun/.../lfx/services/settings/auth.py            # full block present
grep -n allow_custom_components lfrun/.../lfx/services/settings/groups/security.py

# Phoenix 20 on Python 3.11 (§2.5b)
uv pip install --python lf311/bin/python arize-phoenix==20.3.0
lf311/bin/python -c "import phoenix"    # ValueError: mutable default <class 'mappingproxy'>

# Framing headers (§1.2) — 0 hits for both
gh api -X GET search/code -f q='repo:langflow-ai/langflow X-Frame-Options'   # total_count 0
gh api -X GET search/code -f q='repo:langflow-ai/langflow frame_ancestors'   # total_count 0
```

# Appendix B — primary sources

**Langflow, installed 1.11.3** (`site-packages`, read 2026-08-18):
`lfx/services/schema.py` · `lfx/services/config_discovery.py` ·
`lfx/services/settings/auth.py` · `lfx/services/settings/groups/security.py` ·
`lfx/services/settings/groups/variables.py` · `langflow/services/authorization/service.py` ·
`langflow/services/auth/external.py` · `langflow/api/v1/endpoints.py` (`get_all`, run/webhook/
custom_component routes) · `langflow/api/v1/flows.py` · `langflow/api/v1/chat.py`

**Langflow `main` (1.12.0-dev)**: `LICENSE` · `services/database/models/{user,flow,auth/authz,
catalog_policy}/model.py` · `services/authorization/{service,access_ceiling}.py` ·
`services/catalog_policy/service.py` · `api/v1/{catalog_policy,custom_component_policy}.py` ·
`src/lfx/services/catalog_policy/base.py` · `src/lfx/graph/graph/base.py` ·
`src/lfx/PLUGGABLE_SERVICES.md` · `services/tracing/{arize_phoenix,langfuse,service}.py` ·
`src/frontend/package.json`

**Langflow docs:** `/authentication-overview` · `/authorization` · `/external-authentication` ·
`/api-keys-and-authentication` · `/environment-variables` · `/get-started-installation` ·
`/configuration-custom-database` · `/deployment-multi-worker` · `/embedded-chat-widget` ·
`/concepts-flows`

**Langfuse:** `LICENSE` and `ee/LICENSE` at `main` · `langfuse.com/self-hosting` ·
`langfuse.com/self-hosting/license-key` · `langfuse.com/docs/administration/rbac`

**Phoenix:** `LICENSE` at `Arize-ai/phoenix@main` · PyPI `arize-phoenix` 20.3.0 metadata ·
`arize.com/docs/phoenix/self-hosting/features/authentication`

**LangSmith:** `langchain.com/pricing-langsmith` · `docs.langchain.com/langsmith/kubernetes`

**ClickHouse:** `clickhouse.com/docs/resources/support-center/knowledge-base/setup-installation/install-clickhouse-windows10` ·
`github.com/ClickHouse/ClickHouse/issues/86093`

**Aegis:** `aegis/src/aegis/governance/rls.py` (`_TENANT_SCOPED_TABLES`) ·
`aegis/src/aegis/governance/enforcement.py` · `aegis/src/aegis/gateway/llm.py` ·
`aegis/src/aegis/observability/otel.py` · `backend/src/app/core/llm.py` (`_GovernanceHook`) ·
`backend/pyproject.toml` · `docs/dev_new_docs_v2/plans/04-enterprise-substrate.md` §2
