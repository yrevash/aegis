# Phase 11 — Langflow as the tenant-facing flow builder

> **Source of every claim here:** `research/langflow-and-observability.md`. That document marks
> each fact `[MEASURED]` (run on a machine), `[SOURCE-1.11.3]` (read in the released code),
> `[SOURCE-main]` (read on the unreleased branch) or `[DOC]` (vendor documentation). This plan
> inherits those marks. Where this document says something the research did not establish, it
> says so in the same sentence.

---

## What this phase is, in one paragraph

A tenant admin grants a user a handful of components. That user opens a visual builder, wires a
pipeline, and hits Run — and every model call in it is budgeted, guard-railed, ledgered against
their tenant, and retrieves only their tenant's documents. **The builder is Langflow; the
governance is entirely ours.** Langflow contributes the canvas and the execution engine. It
contributes no tenancy, no budget, no policy, and — in the version we can pin — no component
governance at all.

---

## What is actually wrong with the naive integration

The screenshot version of this feature is: install Langflow, iframe it, done. That build is
**ungoverned**, and the research is unambiguous about why. A flow that hits Run today
instantiates a LangChain chat model **inside the Langflow process** and calls the provider
directly. It never enters `aegis.gateway.complete`. Therefore:

| Control | State on the naive path | Why |
|---|---|---|
| Budgets | **Not checked** | `_GovernanceHook.enforce` is gated on a bound `GovernanceContext`; an unscoped request *skips the database entirely* **[SOURCE]** |
| `usage_ledger` | **No row** | Tenant spend invisible; the budget pill in the console lies |
| Guardrails | **Do not run** | `check_input`/`check_output` are on Aegis's agent path only |
| Risk gate | **Does not exist** on this path | No `interrupt()`, no `approvals` row |
| RLS | **Irrelevant** | The query never reaches an Aegis table |
| Code execution | **Arbitrary Python in the server process** | A Custom Component is unsandboxed |

The last row is not our characterisation. It is Langflow's own docstring on
`allow_custom_components` **[SOURCE-1.11.3]**:

> *Note: this is a beta feature. **For security in a multi-tenant environment, use
> hardware-level isolation** to restrict access.*

**The vendor is telling us their in-process code control is not a multi-tenant boundary.** We
will not run one VM per tenant before 30 August. The honest posture, and the words that go in the
security document: *custom code is off, the proxy denies the `custom_component` routes, and these
are defence-in-depth controls, not an isolation boundary.*

---

## The three things that decide the architecture

**1. There is no tenant.** `User` has no org or tenant column. Flows are scoped by
`Flow.user_id`. The OSS authorization service is a documented **pass-through that returns `True`
for every request** **[SOURCE-1.11.3]**. Cross-tenant isolation here is **by identity, not by
RLS** — and no document we write may imply otherwise.

**2. Component governance does not exist in the release we can pin.** "Catalog policy" is the
mechanism people point at. Langflow 1.11.3 was installed and `GET /api/v1/catalog-policy/components`
returns **404** **[MEASURED]**. It exists only on `main`, and there it is a *global,
superuser-owned blocklist* whose org/workspace scopes are labelled *"schema reservation … no P1
resolution semantics"* **[SOURCE-main]**. **No item in this plan may depend on 1.12 shipping.**

**3. The licence is clean.** Unqualified **MIT**, no enterprise carve-out directory
**[MEASURED]**. Embedding it in a commercial multi-tenant product is permitted; the obligation is
to ship the copyright notice. Trademark and third-party notices matter only if Aegis is ever
distributed.

---

## Build vs buy — decided

**Buy the canvas, build the governance.** Writing our own flow builder is weeks of work to
produce something worse than a mature MIT-licensed one. But every part of Langflow that touches
policy is either absent or a no-op, so none of it is reusable. The split is clean and the
research supports it on measurements rather than preference.

**Rejected — Langfuse.** Not because it is worse. On features it is clearly ahead: organisations,
projects, real RBAC, prompt management, datasets, cost tracking, MIT core. It is rejected because
self-hosting requires **Postgres + ClickHouse + Redis + an S3-compatible blob store + two app
processes** **[DOC]**, and **ClickHouse has no native Windows build** — the vendor's own answer is
WSL2 **[DOC]**. That is a Linux dependency on a no-Docker Windows box, for a subsystem that
`plans/04` already defines as the *ephemeral deep-dive* rather than the durable record. **Keep
Phoenix.**

**Rejected — LangSmith.** Proprietary SaaS; self-hosting is an **Enterprise-plan add-on requiring
a licence key from sales and a Kubernetes cluster** **[DOC]**. It would put tenant prompts on a
third party's servers and buys nothing that OpenInference's LangChain instrumentation into
Phoenix does not already buy.

**Two things about Phoenix to write down anyway**, because they are load-bearing and easy to
forget: **Phoenix is Elastic Licence 2.0, not open source** — which constrains what a Phase 7
screen may do with it — and the repo's `arize-phoenix>=14.6,<15` cap is **correct and verified**:
20.3.0 raises `ValueError: mutable default <class 'mappingproxy'>` on import under Python 3.11
**[MEASURED]**. Do not "modernise" that pin.

---

## Where it physically lives

**A second venv is forced, not preferred.** Langflow resolves 566 packages; the versions conflict
with Aegis's. It is a separate process with a separate interpreter.

**A separate Postgres database with its own role, and that is the tenancy answer.** Langflow's
tables are not Aegis tables, do not carry `tenant_id`, and are not covered by
`_TENANT_SCOPED_TABLES`. The one Aegis-side table this phase adds — `langflow_identities`,
mapping an Aegis user to a Langflow user — **is** registered there with a policy, like every other
tenant-scoped table.

**Bound to `127.0.0.1`.** The proxy is the only reachable path, and a proxy is a bypassable
boundary the moment the port is reachable from anywhere else. That gets a test, not a comment.

### The cost, measured

Measured on macOS/arm64 with a real install of 1.11.3 **[MEASURED]** — the numbers will differ on
Windows but not by an order of magnitude:

| Measurement | Value |
|---|---|
| Install | exit `0`, 566 packages, no build failures |
| On-disk | **1.8 GB** venv, 1,115 entries in `site-packages` |
| Cold `import langflow` | 2.7 s (+1.6 s for `create_app`) |
| Boot to `GET /health` 200 | under 90 s |
| **Idle resident set** | **≈1.09 GB** |
| Default palette | **374 components** across 103 categories |

Every one of the 566 packages has a Windows-compatible distribution — **0 unresolved**
**[MEASURED]**, resolved with `--python-platform windows`. `pywin32` resolves cleanly. Only
*Langflow Desktop* needs a C++ build tool **[DOC]**; we run the server. On Windows `langflow run`
is **one Uvicorn process** — Gunicorn multi-worker is Linux-only **[DOC]** — which is correct for
a demo.

**The one number that governs this phase: ≈1.1 GB resident, and it is unmeasured on the target
machine.** Postgres 17, Memurai, Neo4j, Temporal, the Aegis backend (with in-process Phoenix and
an ONNX reranker) and the Next.js dev server already share that box. **Measure resident set on
the Windows machine with everything else running before Phase 6 depends on this.** If it does not
fit, the lever is `langflow-base[complete]` plus the Aegis bundle — 190 fewer packages, ~160 MB
less download — at the cost of a less-travelled configuration that needs its own verification.

**No Docker at any point.**

---

## The tasks, in dependency order

### L0 — The second service (config)

Second venv. `langflow==1.11.3` pinned exactly, installed from a **local wheel cache**. Own
Postgres database and role. Bound to `127.0.0.1`.

`1.12.0.dev31` was published 2026-08-18 — **a dev build every day for two weeks** **[MEASURED]**.
Pin exactly, vendor the wheels, and freeze before rehearsal. A `pip install langflow` that
resolves differently on the morning of the demo is a self-inflicted failure.

**Depends on:** Phase 3 (tenants exist). **Test:** the service answers on loopback and is
refused from any other interface.

---

### L1 — Lock it down before anyone sees it (config)

All four flags exist in 1.11.3 **[MEASURED]**:

```
LANGFLOW_ALLOW_CUSTOM_COMPONENTS=false
LANGFLOW_CUSTOM_COMPONENT_ADMIN_ONLY=true
LANGFLOW_BLOCK_CODE_INTERPRETER_COMPONENTS=true
LANGFLOW_ALLOW_PUBLIC_CUSTOM_COMPONENTS=false   # already the default
LANGFLOW_STORE_ENVIRONMENT_VARIABLES=false      # the host env holds database credentials
LANGFLOW_AUTO_LOGIN=false
```

`BLOCK_CODE_INTERPRETER_COMPONENTS` blocks *"Python Interpreter/REPL/Function, Smart Transform,
CSV Agent, CodeAct, Cuga, OpenDsStar"* **[SOURCE-1.11.3]**. The superuser credential is rotated
and never issued to anyone.

**One subtlety that is actually the mechanism L6 needs:** `LANGFLOW_COMPONENTS_PATH` is
*"an admin-curated allow-list that remains executable even when `allow_custom_components` is
False"* **[SOURCE-1.11.3]**. Our components stay runnable; everyone else's arbitrary code does
not. Leave `allow_components_paths_override` at its default `true`.

**Test:** a flow containing a code-interpreter component is refused at run time, asserted against
the running service — not by reading the config back.

---

### L2 — SSO and the identity mapping (small)

Aegis JWKS plus `EXTERNAL_AUTH_*`; supported in 1.11.3 with **no fork** **[SOURCE-1.11.3]**. An
access ceiling so a Langflow session can never exceed the Aegis role that minted it.

`langflow_identities` is registered in `_TENANT_SCOPED_TABLES` with a policy — it is an Aegis
table and gets the same treatment as every other.

**Depends on:** L1, and Phase 3's `fine_role` on the wire (done).
**Test:** the two-tenant seed's users map to distinct Langflow identities, and one tenant's user
cannot open the other's flow.

---

### L3 — **The keystone.** An Aegis OpenAI-compatible endpoint (medium)

**This is the highest-leverage item in the plan and it must come before the grant UI.**

Aegis already has the right shape: `aegis/src/aegis/gateway/llm.py` routes everything through *"a
custom OpenAI-compatible provider — model string form `openai/<deployment_id>`, with `api_base` +
`api_key` supplied per call"*, with budget/rate governance and observability as **injected hooks**
**[SOURCE]**.

So: expose `POST /v1/chat/completions` and `POST /v1/embeddings` on the Aegis backend. They
authenticate the caller, **bind the `GovernanceContext` for that tenant and user**, and delegate
to `aegis.gateway.complete` / `embed`. Every Langflow model component is configured with
`base_url` pointing at it and a **per-user, short-lived key**.

What that one move buys, with no Langflow code change:

| Control | How it applies |
|---|---|
| Budgets | `enforce_governance` runs before spend, **fail-closed** on a database error |
| Usage ledger | `record_usage` writes a row per call in real billing units |
| Model allowlist | The endpoint resolves `ModelRole`, so a tenant gets their tier's models and cannot name a model directly |
| Guardrails | `check_input` / `check_output` on prompt and completion |
| Observability | The gateway's injected sink already emits `gen_ai.*` spans |
| Cancellation, rate limits, admission caps | One place — and Phase 3 already built them |

**Belt and braces:** the L4 proxy blocks the direct provider components outright, so a tenant user
cannot construct an ungoverned call even by typing a provider key into a node.

**Tests required:**
- A call through `/v1/chat/completions` without a resolvable tenant is **refused**, not silently
  ungoverned. This is the whole point: the failure mode being fixed is *silence*.
- A flow run against a tenant at its budget cap returns a visible refusal and writes **no**
  `usage_ledger` row for work it did not do.
- A request naming a model outside the tenant's tier is refused server-side, asserted by the
  absence of a provider call rather than by the status code alone.

---

### L4 — The Track A proxy (medium)

**Buildable today against 1.11.3, and it does not depend on Langflow's roadmap.** Default-deny
route allowlist; palette filter; validation on flow write and flow run; the two
`custom_component` routes denied outright.

Track A exists *precisely because* component governance does not exist in the pinned release.

**Test:** a route not on the allowlist is refused; a flow whose JSON names a denied component is
rejected on **write and on run** (write-only validation is bypassable by anyone who can POST a run).

---

### L5 — The tenant-admin grant UI (medium — the UI is the bulk)

Component grants are **`SettingSpec` entries with `merge: TIGHTEN_ONLY`** — the catalogue Phase 3
task 3.7 already built. A tenant cannot grant itself a component the platform withheld, by
arithmetic rather than by a check.

**Do L3 before L5.** A component-grant UI over ungoverned components is *a nice UI on the hole.*

**Depends on:** L4, and the Phase 3 settings catalogue (done).

---

### L6 — The Aegis component bundle (medium)

Thin Langflow components shipped via `LANGFLOW_COMPONENTS_PATH`, each calling Aegis's `/v1` API
with the user's token:

- **Aegis Retrieval** — hybrid retrieval, RLS-scoped in Aegis, so a tenant user *physically
  cannot* retrieve another tenant's chunks. Contrast the raw OpenSearch/Chroma nodes in the
  default palette, which have **no tenancy at all**.
- **Aegis Tool** — one node over the typed tool registry, allowlist resolved server-side.
- **Aegis Agent** — the real agent graph, guardrails and risk gate included, returning the answer
  and its `run_id`.
- **Aegis Memory** — per-subject memory, `session_id` bound server-side.

**This inverts the risk posture.** Instead of governing 374 third-party components, we govern the
~10 on the allowlist and everything else is denied by default.

**Depends on:** L3.

---

### L7 — Runs are recorded in `run_events`, or they did not happen (small)

Langflow can export OTLP to Phoenix via `PHOENIX_COLLECTOR_ENDPOINT` **[SOURCE-main]** — point it
at the local Phoenix so flow spans and Aegis spans share one trace tree.

But **Phoenix is not the record.** Every Aegis-mediated call from a flow (L3 and L6) writes
`run_events` rows carrying `tenant_id`, `user_id`, the flow id and the `trace_id`, so a tenant's
flow run is as replayable and auditable as a console run. This costs almost nothing extra *if L3
and L6 are the only paths that can reach a model or the data* — which is the point of doing them.

**Depends on:** Phase 3 `run_events` (done).

---

### L8 — Embed, and the cheap flow-level limits (small)

Iframe in Next.js with token handoff. Plus the mitigations for the residue below: **max node and
edge count per flow, a per-flow run timeout, and the Phase 3 per-tenant admission cap applied to
flow runs.**

---

### L9 — *Optional.* Track B, only if 1.12 releases and proves stable

A pluggable policy service moves the palette filter in-process. The proxy keeps its route
allowlist and validation as defence in depth. **Nothing in L0–L8 may depend on this.**

---

## The honest residue

Even with L3, L4, L6 and L7 all built, **the flow topology itself is not governed.** Aegis's risk
gate reasons about *actions*, not *graphs*. A user can wire allowed components into a loop or a
fan-out nobody reviewed. L8's node/edge caps and run timeout are cheap mitigations. Full
flow-level static analysis is not worth building before 30 August — **say so rather than implying
the gate covers it.**

---

## Definition of done

- [ ] Langflow 1.11.3 runs natively on the Windows box, no Docker, from a vendored wheel cache;
      **resident set measured with every other service running**.
- [ ] The service is unreachable from any interface but loopback — tested, not configured.
- [ ] A code-interpreter component is refused at run time on the running service.
- [ ] Two tenants' users map to distinct Langflow identities; neither can open the other's flow.
- [ ] A model call from inside a flow appears in `usage_ledger` against the right tenant.
- [ ] A flow run by a tenant at its budget cap is refused visibly, with no ledger row for work
      that did not happen.
- [ ] A call that cannot resolve a tenant is **refused**, not silently ungoverned.
- [ ] A flow naming a denied component is rejected on write **and** on run.
- [ ] Aegis Retrieval inside a flow returns only the calling tenant's chunks.
- [ ] A flow run produces `run_events` rows carrying flow id and `trace_id`, and the console links
      to the trace.
- [ ] No document anywhere claims RLS covers Langflow's own tables.

---

## The demo sentence this earns

> *"A tenant admin grants this user four components. The user builds a pipeline, hits Run, and the
> answer comes back — and every model call in it was budgeted, guard-railed, ledgered against
> their tenant, and retrieved only their tenant's documents, because the only components they were
> given are the governed ones."*

---

## Risks, stated plainly

1. **The release we can pin has no component governance at all.** Track A (L4) exists precisely
   for this. **No plan item may depend on 1.12.**
2. **Langflow's code-execution control is documented as beta and explicitly not a multi-tenant
   boundary.** We compensate with layered controls, not isolation. Those words go in the security
   posture document.
3. **Cross-tenant isolation here is by identity, not RLS.** No document may imply otherwise.
4. **≈1.1 GB resident, unmeasured on the target machine.** The tightest constraint in this plan.
5. **A fast release train** — a dev build every day for two weeks. Pin exactly, vendor the wheels,
   freeze before rehearsal.
6. **The proxy is a bypassable boundary if the port is reachable.** Loopback bind plus a test.
7. **Trademark and third-party notices** if Aegis is ever distributed.
