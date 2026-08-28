# SOTA 01 — A2A 1.0 (Agent2Agent, Linux Foundation)

> **STATUS: PLAN. Nothing here is implemented.** Written 2026-08-27 against A2A spec
> `1.0.0` and the repo at `docs/teaching-modules`.
>
> **Evidence marks.** Every claim carries one: **[MEASURED]** — I ran it on this machine
> and pasted the result; **[SOURCE]** — I read the code, with `file:line`; **[DOC]** —
> vendor documentation, with the URL. Where I could not establish something, the sentence
> that would have claimed it says so instead.

---

## What this is, and why it matters for Aegis, in one paragraph

A2A is the wire protocol by which one agent system hands a turn to another agent system it
does not own, discovering it through a JSON document at a well-known URL and talking to it
over JSON-RPC 2.0 or gRPC or HTTP+JSON **[DOC]**
(https://a2a-protocol.org/latest/specification/). It reached `1.0.0` **[DOC]**, is governed
by the Linux Foundation **[DOC]**
(https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year),
and 1.0 adds two things Aegis specifically needs: **signed Agent Cards** (JWS over an
RFC 8785-canonicalised card) and a **`tenant` routing field** that lets one endpoint host
many logically distinct agents. It matters here because Aegis's differentiator is
governance — budgets, RLS, guardrails, a human risk gate — and A2A is the only standard
surface on which a *third party's* agent can consume that governance without being inside
our process. Today Aegis has **no A2A implementation whatsoever**: the only four
occurrences of the string in the product are OpenTelemetry span attribute *names*, and one
of them is a literal `"a2a"` used as a label, not a protocol **[SOURCE]**
(`aegis/src/aegis/observability/semconv.py:113-119`, emitted at
`aegis/src/aegis/agent/graph.py:468-471` and `aegis/src/aegis/agent/subagent.py:541-544`).

---

## Verification of the premises I was handed

I was told four things. Three held; one was wrong in a way that changes the plan.

| Premise | Verdict |
|---|---|
| A2A reached 1.0 in April 2026 under the Linux Foundation | **Holds.** Spec says "Latest Released Version `1.0.0`" **[DOC]**; the Python SDK's first 1.0 pre-releases are dated `2026-04-17` and `1.0.1` `2026-04-22` **[MEASURED]**, from `https://pypi.org/pypi/a2a-sdk/json` |
| Signed Agent Cards, multi-tenancy | **Holds.** §8.4 JWS + RFC 8785; §8.3.2 rule 4 requires the client to "Set the `tenant` field in every request message to exactly the value declared in the selected `AgentInterface` entry" **[DOC]** |
| Aegis has no A2A implementation; only four span attributes | **Holds exactly.** `grep -rn "A2A_\|a2a"` over `aegis/src`, `backend/src`, `frontend/src` returns only `semconv.py` (definitions + `__all__` at lines 26-29), `graph.py:468-471`, `subagent.py:541-544`, and the re-export at `backend/src/app/observability/semconv.py:11-14,54-57` **[MEASURED]**. **No test anywhere references `A2A_`** **[MEASURED]** — `grep -rln "A2A_" backend/tests aegis/src/aegis/conformance` is empty |
| "a minimal A2A JSON-RPC endpoint (`message/send`, `tasks/get`)" | **WRONG for 1.0.** Those are the 0.x method names. **A2A 1.0's JSON-RPC binding uses PascalCase operation names**: `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask`, `GetExtendedAgentCard`, plus the four `*TaskPushNotificationConfig` methods **[DOC]** (spec §9.4, read from `https://raw.githubusercontent.com/a2aproject/A2A/main/docs/specification.md` lines 2273-2450). Any plan step, test or curl written against `message/send` would be testing a protocol nobody speaks |

**Do not write `message/send` anywhere.** The whole point of implementing a standard is
that an off-the-shelf client connects; one that speaks 1.0 will POST `SendMessage`.

---

## The exact 1.0 spec surface this plan targets

Only the parts we would actually serve. All **[DOC]**, from the spec markdown cited above.

**Discovery.** `GET https://{server_domain}/.well-known/agent-card.json` (§8.2, and the
IANA well-known URI registration template at §14.3 names the same path). Servers **SHOULD**
send `Cache-Control: max-age=…` and an `ETag` derived from the card `version` or a content
hash (§8.6.1).

**Agent Card fields** we would emit, taken verbatim from the §8.5 sample: `name`,
`description`, `version`, `supportedInterfaces` (array of `{url, protocolBinding,
protocolVersion}` — `protocolBinding` is one of `JSONRPC` / `GRPC` / `HTTP+JSON`),
`provider`, `documentationUrl`, `capabilities` (`streaming`, `pushNotifications`,
`extendedAgentCard`), `securitySchemes`, `securityRequirements`, `defaultInputModes`,
`defaultOutputModes`, `skills` (each `{id, name, description, tags, examples, inputModes,
outputModes}`), `signatures`. Note **`supportedInterfaces`**, not the 0.x `url` +
`additionalInterfaces` pair — §13's migration notes call the polymorphic reshaping a
breaking change in 1.0.

**Version negotiation.** The protocol version is `Major.Minor` only, e.g. `1.0`; patch
numbers "SHOULD NOT be used in requests, responses and Agent Cards" (§7). It travels as the
`A2A-Version` HTTP header, and may also be a query parameter (§9.2, spec lines 708-729).

**Signing** (§8.4). Canonicalise the card with **RFC 8785 (JCS)** after applying protobuf
field-presence rules — omit unset `optional` fields, omit empty repeated fields that are
not `REQUIRED`, **always exclude `signatures`**. Sign
`ASCII(BASE64URL(header) || '.' || BASE64URL(payload))`. The `AgentCardSignature` object is
`{protected, signature, header?}`; the protected header **MUST** carry `alg`, **SHOULD**
carry `typ: "JOSE"`, **MUST** carry `kid`, and **MAY** carry `jku` pointing at a JWKS.

**Task states** (§9.4.6 and the terminal-state list): `TASK_STATE_SUBMITTED`,
`TASK_STATE_WORKING`, `TASK_STATE_INPUT_REQUIRED`, `TASK_STATE_AUTH_REQUIRED`,
`TASK_STATE_COMPLETED`, `TASK_STATE_FAILED`, `TASK_STATE_CANCELED`, `TASK_STATE_REJECTED`.
`SubscribeToTask` returns `UnsupportedOperationError` on a terminal task.

**Streaming.** `SendStreamingMessage` answers HTTP 200 `Content-Type: text/event-stream`
with frames of the form `data: {"jsonrpc":"2.0","id":1,"result":{ /* StreamResponse */ }}`
(§9.4.2).

---

## SDK maturity — honestly

**The Python SDK is real and current, and this plan uses it.** `a2a-sdk` is at **1.1.2,
uploaded 2026-07-22**, and the release train runs 1.0.0a2 (2026-04-17) → 1.0.1 → 1.0.2 →
1.0.3 → 1.1.0 → 1.1.1 → 1.1.2 **[MEASURED]** (PyPI JSON API). Its own summary says it
implements spec 1.0 with a 0.3 compatibility mode **[DOC]**
(https://pypi.org/project/a2a-sdk/). **I did not verify the compatibility-mode claim by
reading SDK source** — treat it as vendor marketing until someone does.

**It resolves into this repo's backend venv, with exactly one displacement** **[MEASURED]**:

```
$ uv pip install --python backend/.venv/bin/python --dry-run "a2a-sdk[http-server,signing,telemetry]"
Resolved 37 packages in 192ms
Would uninstall 1 package
Would install 5 packages
 + a2a-sdk==1.1.2   + aiologic==0.17.1   + culsans==0.11.0   + json-rpc==1.15.0
 - protobuf==7.35.1
 + protobuf==6.33.6
```

`a2a-sdk` pins `protobuf<7,>=5.29.5` **[MEASURED]**, and the venv currently holds
`protobuf 7.35.1` **[MEASURED]**. I enumerated every installed distribution that declares a
protobuf constraint **[MEASURED]** — `google-api-core` (`<8,>=6.33.5`),
`googleapis-common-protos` (same), `proto-plus` (same), `temporalio` (`>=3.20,<8`),
`opentelemetry-proto` (`>=5,<8`), `onnxruntime` (`>=4.25.8`), `nemoguardrails` (`>=5.29.5`),
`qdrant-client` (`>=3.20.0`) — and **6.33.6 satisfies all of them**. So the downgrade is
resolvable on paper. **It is not verified at runtime.** Temporal and ONNX Runtime both link
generated protobuf code; nothing here proves they import cleanly on 6.x. That check is
task **A0** below and it gates everything.

The three extras matter: `http-server` pulls `starlette` + `sse-starlette` (both already
installed **[MEASURED]**: starlette 1.6.0, sse-starlette 3.4.8), `signing` pulls `pyjwt`
(installed, 2.13.0 **[MEASURED]**), `telemetry` pulls the OTel API/SDK Aegis already has.
Notably **grpc is NOT pulled** unless you ask for `[grpc]` **[MEASURED]** — we would declare
`JSONRPC` only, so we never take that dependency.

**Where hand-rolling is still required:** I did not establish that the SDK's server side can
be driven with Aegis's `AuthContext` / `GovernanceContext` binding, nor that its task store
can be backed by Aegis Postgres under RLS. Task **A4** is written so that the *card* and the
*JSON-RPC method dispatch* come from the SDK's types, but the **request handler is ours** —
a thin FastAPI route, not the SDK's app factory. That is the same posture the MCP front door
already takes toward its SDK, and for the same reason.

---

## The three things that decide the architecture

### 1. Aegis has no asymmetric key. Card signing needs one, and it is a new secret.

`jwt_algorithm` defaults to `"HS256"` and `jwt_secret` is a symmetric string
**[SOURCE]** (`backend/src/app/config.py:399-400`). A grep for `RSA`, `jwks`, `JWKS`,
`private_key`, `ES256`, `RS256` across `backend/src/app` returns **nothing**
**[MEASURED]**. An HS256 secret cannot sign an Agent Card in any useful sense — verification
would require handing every peer the key that mints Aegis access tokens.

So **A2A card signing introduces the first asymmetric keypair in the product.** `cryptography
48.0.1` and `pyjwt 2.13.0` are already installed **[MEASURED]**, so generating and using an
ES256 key needs no new dependency. The private key must live wherever `jwt_secret` lives and
be subject to the same production strength check that `app.config` already applies to
`jwt_secret` **[SOURCE]** (`backend/src/app/config.py:699,728`) — **I did not read that
check's implementation**, so task A2 says "extend it", not "it will just work".

### 2. `GET /v1/platform/capabilities` is the wrong source for `skills`, and the brief is wrong about it.

The brief says to build the card "from the EXISTING `GET /v1/platform/capabilities` and the
tool registry". I read the endpoint. It returns `{product, tagline, module_count, modules[]}`
where each module is a *branded Aegis subsystem name paired with its implementing
`module_path`* **[SOURCE]** (`backend/src/app/api/routes.py:1418-1444`, schema at
`backend/src/app/api/schemas.py:585-599`). It is a marketing/architecture manifest for the
landing page — it is **unauthenticated by design** and carries "no tenant, user, usage or
credential data" **[SOURCE]** (`routes.py:1432-1437`).

That is a good source for the card's `name`, `description`, `provider` and
`documentationUrl`. It is a **bad** source for `skills`, which in A2A are things a caller can
*ask for*. The real skill catalogue is the tool registry —
`app.adapter.tools.TOOL_REGISTRY` **[SOURCE]** (`backend/src/app/adapter/tools.py:694`) —
filtered per persona by `tool_definitions_for(persona_id)` **[SOURCE]**
(`tools.py:791-793`), which is exactly what the MCP front door already lists **[SOURCE]**
(`backend/src/app/mcp/server.py:1333-1340`).

**And that creates the central tension of this plan:** the tool list is *per persona*, and
the well-known card is *unauthenticated*. See §3.

### 3. A2A `tenant` is a routing string. Aegis tenancy is a Postgres GUC. They are not the same thing and must not be conflated.

Aegis's tenancy is `set_config('app.tenant_id', …, is_local => true)` on the connection, with
a second GUC `app.tenant_all` because "`app.tenant_id` cannot express the difference between
the two" (platform-wide vs. no-tenant) **[SOURCE]** (`aegis/src/aegis/governance/rls.py:281`,
`:286`, `:302-305`, `:314`, and the table registry at `:182`). The scope is derived from the
authenticated principal by `principal_tenant_scope` **[SOURCE]**
(`aegis/src/aegis/retrieval/types.py:210`), with `AllTenants` as a distinct sentinel type
(`types.py:74`).

A2A's `tenant` is described by the spec as an **opaque routing identifier** the client copies
from the `AgentInterface` it selected (§8.3.2 rule 4) **[DOC]**. It arrives **before**
authentication and is attacker-controlled.

**Therefore: `tenant` selects which Agent Card / which agent identity is being addressed. It
NEVER sets `app.tenant_id`.** The RLS scope continues to come only from the verified bearer
token, exactly as `/v1/query` does today **[SOURCE]** (`backend/src/app/api/routes.py:1750`
resolves governance from `auth`). If the `tenant` in the request disagrees with the tenant
the token resolves to, the request is **refused**, not reconciled. That refusal gets a test
(V6 below) and it is the single most important test in this document.

---

## What we would actually serve

**Two Agent Cards, not one**, because of §2's tension:

* **The public card** at `GET /.well-known/agent-card.json`. Unauthenticated. Carries
  identity, `supportedInterfaces`, `capabilities`, `securitySchemes`, and a **small fixed
  set of skills that are true for every caller** — `answer-question` (the governed agent
  run) and nothing else. It does **not** enumerate `TOOL_REGISTRY`, because doing so would
  publish the domain's action surface to anyone who can curl the host. It is signed.
* **The extended card** via the JSON-RPC method `GetExtendedAgentCard`, which 1.0 gates
  behind `capabilities.extendedAgentCard: true` (§13 notes 1.0 "relocates the extended agent
  card capability from a top-level field to the capabilities object") **[DOC]**. This one is
  authenticated, and its `skills` **are** `tool_definitions_for(persona_for(caller.role))` —
  the same per-persona allowlist MCP serves. A tool a persona may not call is never listed,
  by construction, because we reuse the function rather than re-deriving it.

**One JSON-RPC endpoint**, `POST /v1/a2a`, declared in `supportedInterfaces` as
`{"url": "https://…/v1/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}`.

---

## Exact files to create and modify

### Create

| File | What |
|---|---|
| `backend/src/app/a2a/__init__.py` | Package marker; lazy re-exports only, mirroring `backend/src/app/mcp/__init__.py` (21 lines **[MEASURED]**) so importing the app never requires `a2a-sdk` |
| `backend/src/app/a2a/card.py` | Builds the public and extended `AgentCard` dicts; owns the RFC 8785 canonicalisation and the JWS signing/verifying helpers |
| `backend/src/app/a2a/keys.py` | Loads the ES256 private key, exposes the JWKS document, and refuses to start in `PROD` on a default/absent key |
| `backend/src/app/a2a/server.py` | The JSON-RPC dispatcher: `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, `GetExtendedAgentCard`. Everything else answers JSON-RPC `-32601` |
| `backend/src/app/a2a/tasks.py` | Task lifecycle over the existing Postgres, mapping Aegis run state → `TASK_STATE_*` |
| `backend/src/app/api/routes_a2a.py` | The FastAPI routes, attached to `router` the way the other control planes are **[SOURCE]** (`backend/src/app/main.py:765-770` describes that convention) |
| `backend/tests/a2a/test_card.py`, `test_signing.py`, `test_jsonrpc.py`, `test_tenant_confusion.py`, `test_streaming.py` | See Verification |
| `backend/src/app/migrations/…` (new revision) | The `a2a_tasks` table |

### Modify

| File:line | Change |
|---|---|
| `backend/src/app/config.py:399-400` | Add `a2a_signing_key_pem`, `a2a_signing_kid`, `a2a_public_base_url`, `a2a_enabled`. Extend the PROD strength check at `config.py:699,728` to cover the new key |
| `backend/src/app/main.py:775-791` | Serve `/.well-known/agent-card.json` and `/.well-known/a2a-jwks.json` at the **root**, not under `/v1`. See the trap below |
| `backend/tests/api/test_route_coverage.py:569+` | Add `UNREACHABLE_BY_DESIGN` entries for every `/v1/a2a*` route. See the trap below |
| `aegis/src/aegis/observability/semconv.py:113-119` | Rename or justify. See §"The existing span attributes" |
| `backend/pyproject.toml` | `a2a-sdk[http-server,signing,telemetry]==1.1.2`, pinned exactly |

### Two traps in this repo that will bite a naive implementation

**Trap 1 — `/v1` is applied at the composition root, so you cannot serve a well-known path
from `router`.** `_split_infra_probes` puts every route on `app.api.routes.router` under
`API_PREFIX = "/v1"` except the three paths in `INFRA_PATHS = {"/health","/ready","/readyz"}`,
and it **raises at import time** if an `INFRA_PATHS` entry stops being served **[SOURCE]**
(`backend/src/app/main.py:771-772` and the `_split_infra_probes` body at `main.py:798-830`;
`INFRA_PATHS` at `backend/src/app/api/openapi.py:68`). A2A's discovery path is fixed by spec
at the root. So the card is served either by adding it to `INFRA_PATHS` (which changes the
meaning of a set documented as "the probes a load balancer is configured with") or by
`app.mount`/`app.add_route` directly on the FastAPI app, the way the MCP transport is mounted
**[SOURCE]** (`main.py:791`). **Prefer the direct route on the app**; leave `INFRA_PATHS`
alone.

**Trap 2 — `tests/api/test_route_coverage.py` fails any new `router` route no browser
reaches.** Every non-public route must be reachable from a portal or carry an explicit
`UNREACHABLE_BY_DESIGN` entry, and a second test fails entries that are stale *or* that name
a route which has since been wired up **[SOURCE]** (`backend/tests/api/test_route_coverage.py:38-43`,
allowlist at `:569`). A2A routes are protocol endpoints with no console surface — the same
situation MCP is in, which is why the MCP transport is an `app.mount` and "carries no
allowlist entry … because it is not a portal route at all" **[SOURCE]** (`main.py:779-781`).
**Decide this consciously**: mount, or allowlist with a reason.

---

## Data / schema changes

One table, `a2a_tasks`, and it **must** be registered in `_TENANT_SCOPED_TABLES`
**[SOURCE]** (`aegis/src/aegis/governance/rls.py:182`) with a policy, like every other
tenant-scoped table — otherwise a `GetTask` for another tenant's task id returns that task.

| Column | Type | Note |
|---|---|---|
| `id` | uuid pk | The A2A task id handed back to the peer |
| `tenant_id` | int not null | **The RLS column.** From the token, never from the request's `tenant` field |
| `user_id` | int not null | The principal that created it |
| `context_id` | uuid | A2A's `contextId`, the conversation grouping |
| `state` | text not null | One of the eight `TASK_STATE_*` values |
| `run_id` | text | The Aegis run this wraps, so a task links to `run_events` and to the trace |
| `agent_tenant` | text | The A2A `tenant` routing string the peer sent, recorded for audit — **never** used for filtering |
| `message_history` | jsonb | Bounded; `GetTask` takes `historyLength` |
| `artifacts` | jsonb | Output parts |
| `created_at`, `updated_at` | timestamptz | |

I did **not** read the migration tooling, so the revision's exact shape is unspecified here
beyond "follow the neighbouring revision".

---

## Tasks, in dependency order

### A0 — Prove the protobuf downgrade is survivable (**gate**)

Install `a2a-sdk[http-server,signing,telemetry]==1.1.2` into a **copy** of the backend venv,
then run the Temporal, ONNX/ML and OTel-touching suites. The resolver says 6.33.6 satisfies
every declared constraint **[MEASURED]**; a resolver has never been evidence that generated
protobuf code imports.

**Abandon condition:** if Temporal or ONNX Runtime breaks on protobuf 6.x, this whole plan
collapses to A1 (hand-rolled, no SDK) or to nothing. Decide that before writing A2.

**Checkable:** the existing suite is green on the copy, and `python -c "import temporalio,
onnxruntime, opentelemetry.proto"` exits 0.

### A1 — The card builder, with no signing and no HTTP

Pure function: `build_public_card(settings) -> dict` and
`build_extended_card(auth) -> dict`. Public skills are the fixed pair; extended skills come
from `tool_definitions_for(_persona_for(auth.role))` **[SOURCE]**
(`backend/src/app/adapter/tools.py:791`, `backend/src/app/api/routes.py:311`). Identity
fields come from `app.capabilities` (`PRODUCT_NAME`, `PRODUCT_TAGLINE`, `AEGIS_MODULES`)
**[SOURCE]** (`backend/src/app/api/routes.py:1439-1443`).

**Checkable:** the produced dict validates against `a2a_sdk`'s `AgentCard` model, and its
`supportedInterfaces[0]` is `{"url": …, "protocolBinding": "JSONRPC", "protocolVersion":
"1.0"}`.

### A2 — The key, and the JWKS

Generate an ES256 keypair with `cryptography` (48.0.1, installed **[MEASURED]**). Private
key from settings; public key served at a stable URL used as `jku`. Extend the PROD config
check so a default key is a hard startup error, matching how `jwt_secret` is treated
**[SOURCE]** (`backend/src/app/config.py:699`).

**Checkable:** `Settings(env="PROD", a2a_signing_key_pem=<default>)` raises at construction.

### A3 — Canonicalise and sign

RFC 8785 canonicalisation with the protobuf field-presence pre-pass, `signatures` excluded,
then JWS with `{alg: "ES256", typ: "JOSE", kid, jku}` **[DOC]** (§8.4.1-8.4.2). Ship a
verifier too — not because we need it, but because a signer without a round-trip test is a
signer nobody has checked.

**I have not verified that `a2a-sdk[signing]` exposes a canonicaliser.** If it does, use it;
if it does not, `rfc8785` is a small pure-Python package and hand-rolling JCS is a known
correctness trap (number formatting, surrogate pairs) that should not be attempted casually.

**Checkable:** sign → mutate one byte of `description` → verification fails. Sign → reorder
every key in the JSON → verification **passes** (that is what canonicalisation buys, and it
is the assertion that proves JCS is really running).

### A4 — `POST /v1/a2a`, the JSON-RPC surface

Five methods, PascalCase, and `-32601` for everything else. `SendMessage` authenticates with
the same `require_auth` dependency the rest of `/v1` uses, resolves governance with
`_resolve_governance(auth)` exactly as `/v1/query` does **[SOURCE]**
(`backend/src/app/api/routes.py:1750`), creates an `a2a_tasks` row, invokes the same
LangGraph run `/query` invokes, and returns a `Task`. `GetTask` reads the row under RLS.
`CancelTask` sets `TASK_STATE_CANCELED`.

**The `tenant` check lives here and runs before anything else**, per §3.

**Checkable:** each method independently, with curl payloads in Verification.

### A5 — `SendStreamingMessage` over the existing SSE

`/v1/query` already returns an `EventSourceResponse` **[SOURCE]**
(`backend/src/app/api/routes.py:1709-1716`), and the repo already depends on
`sse-starlette 3.4.8` **[MEASURED]**. The work is a **translation layer**, not a new
transport: Aegis `StreamEvent` frames → A2A `StreamResponse` frames wrapped as
`data: {"jsonrpc":"2.0","id":<same id>,"result":{…}}` **[DOC]** (§9.4.2).

**The frame shapes do not match and I have not mapped them.** Aegis's union is published
into the OpenAPI document as `StreamEvent` **[SOURCE]**
(`backend/src/app/api/openapi.py:74`); A2A's is `StreamResponse`. Somebody has to write that
table by hand, and until they do, A5 is not costed.

### A6 — Deal with the existing `app.a2a.*` span attributes

See the next section. This is a **rename**, and it is small, and it should happen in A0's
commit so nobody builds on the ambiguity.

### A7 — Record A2A runs in `run_events`

An A2A-originated run must be as auditable as a console run: same `run_events` rows, same
`trace_id`, plus the task id. **I did not read the `run_events` writer**, so this task is
named but not specified.

---

## The existing `app.a2a.*` span attributes — rename, and here is the exact case

Today four attributes describe the **supervisor → specialist handoff inside one Aegis
process**. The code says so itself: "A2A-**style** labelled agent handoff … Emitted as a
dedicated span so the trace tree reads as an explicit agent-to-agent handoff" and
`A2A_PROTOCOL = "app.a2a.protocol"  # labelling convention ("a2a")` **[SOURCE]**
(`aegis/src/aegis/observability/semconv.py:113-119`). The emitted value is the literal string
`"a2a"` **[SOURCE]** (`graph.py:471`, `subagent.py:544`).

**That is an in-process LangGraph handoff, not the Agent2Agent protocol.** Once a real A2A
endpoint exists, a trace containing `app.a2a.protocol = "a2a"` on a purely internal handoff
is actively misleading to the one audience that matters — an operator debugging whether a
turn crossed a trust boundary. This is the "never claim one tech while silently running
another" rule applied to telemetry.

**Rename to `app.handoff.*`** (`from`, `to`, `reason`) and **delete `A2A_PROTOCOL`
entirely** — an attribute whose only value is a constant carries no information. Reserve
`app.a2a.*` for spans that genuinely cross the A2A wire, and give it real fields when A4
lands: `app.a2a.method`, `app.a2a.task_id`, `app.a2a.peer`.

**The rename is cheap and provably safe**: six call sites total
(`semconv.py:26-29,116-119`, `graph.py:468-471`, `subagent.py:541-544`,
`backend/src/app/observability/semconv.py:11-14,54-57`) and **zero tests reference `A2A_`**
**[MEASURED]**. The only cost is that a saved Phoenix query would stop matching, which is not
a cost anyone in this repo has paid for yet.

**The alternative — justify and keep — is available but weaker.** It requires a comment
saying "this is not the A2A protocol" on an attribute literally named `a2a.protocol`, which
is a documentation patch over a naming bug.

---

## VERIFICATION

Nothing below is optional, and none of it has been run — the feature does not exist. Every
expected response is derived from the spec section cited, not from an implementation.

### Preconditions to establish first (these are *not* assumed)

```bash
# The backend answers at all, and the version prefix is what this document assumes.
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health          # expect 200
curl -s http://127.0.0.1:8000/v1/platform/capabilities | head -c 200           # expect {"product":"Aegis",...
# There is no A2A surface yet — this is the baseline the plan changes.
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/.well-known/agent-card.json  # expect 404 today
```

### V1 — The card is served, cacheable, and valid

```bash
curl -si http://127.0.0.1:8000/.well-known/agent-card.json
```

Expect `200`; `Content-Type: application/json`; an `ETag` header and a `Cache-Control` with
`max-age` (§8.6.1 says SHOULD for both — if we omit them, say so, do not pretend);
`supportedInterfaces[0].protocolBinding == "JSONRPC"`;
`supportedInterfaces[0].protocolVersion == "1.0"` (**not** `"1.0.0"` — §7 forbids the patch
component); a non-empty `signatures` array.

**No bearer token.** If this returns 401, the card is not discoverable and the feature is
inert.

### V2 — The public card leaks no domain surface

```bash
curl -s http://127.0.0.1:8000/.well-known/agent-card.json | python3 -c \
 "import json,sys; c=json.load(sys.stdin); print(sorted(s['id'] for s in c['skills']))"
```

Expect exactly the fixed public skill ids. **Assert the absence of every `TOOL_REGISTRY`
key** — this is the test that stops a later refactor from quietly publishing the action
surface. Test file: `backend/tests/a2a/test_card.py`.

### V3 — The signature round-trips, and canonicalisation is really running

`backend/tests/a2a/test_signing.py`, three assertions:

1. verify(sign(card)) is true;
2. verify fails after flipping one character of `description`;
3. verify **still passes** after `json.loads`/`json.dumps` with `sort_keys=False` and keys
   shuffled — proving JCS, not byte-comparison, is doing the work.

### V4 — `SendMessage` runs the governed agent

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"<seed user>","password":"<seed pw>"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST http://127.0.0.1:8000/v1/a2a \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -H 'A2A-Version: 1.0' \
  -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{
        "message":{"role":"user","parts":[{"text":"What is the status of my request?"}]}}}'
```

Expect `{"jsonrpc":"2.0","id":1,"result":{"task":{"id":"…","status":{"state":"TASK_STATE_…"},…}}}`.
**I have not read `SendMessageRequest`'s exact required fields in the SDK models**, so treat
the `params` shape above as the spec's prose (§9.4.1 references `SendMessageRequest` and
`Message`) and pin it against `a2a_sdk`'s pydantic model before writing the test.

The login route path is an assumption I did **not** verify — confirm it against
`backend/src/app/api/routes.py` before pasting this into a runbook.

### V5 — `GetTask` under RLS

```bash
curl -s -X POST http://127.0.0.1:8000/v1/a2a -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"GetTask","params":{"id":"<task id from V4>","historyLength":10}}'
```

Expect the task. Then repeat with **tenant B's** token from the two-tenant seed and expect a
task-not-found error, **not** an empty task and **not** a 403 that reveals existence.
Test: `backend/tests/a2a/test_jsonrpc.py`, alongside the existing two-tenant fixtures — mirror
the harness in `backend/tests/mcp/test_streamable_http.py:42-70`, which runs a real uvicorn on
an ephemeral port in the test's own event loop precisely because the asyncpg pool is bound to
that loop **[SOURCE]**.

### V6 — **Tenant confusion. The most important test in this document.**

`backend/tests/a2a/test_tenant_confusion.py`:

* Tenant A's token + `"tenant": "<tenant B's routing string>"` in the request → **refused**,
  and the assertion is on the *absence of a database read under B's scope*, not merely on the
  status code.
* Tenant A's token + no `tenant` field, where the selected interface declares one → refused
  (§8.3.2 rule 4 makes the field mandatory when declared).
* An `a2a_tasks` row is never written with a `tenant_id` derived from the request body.

### V7 — Streaming

```bash
curl -N -s -X POST http://127.0.0.1:8000/v1/a2a \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -H 'Accept: text/event-stream' -H 'A2A-Version: 1.0' \
  -d '{"jsonrpc":"2.0","id":3,"method":"SendStreamingMessage","params":{
        "message":{"role":"user","parts":[{"text":"hello"}]}}}'
```

Expect `Content-Type: text/event-stream` and frames literally shaped
`data: {"jsonrpc": "2.0", "id": 3, "result": {…}}` — **the `id` must equal the request's**,
which is the detail §9.4.2's example makes explicit and which a naive SSE translation drops.
Expect a terminal frame carrying one of the four terminal `TASK_STATE_*` values.
Test: `backend/tests/a2a/test_streaming.py`.

### V8 — Interoperability with something that is not us

Point the real `a2a-sdk` client at the endpoint and call `SendMessage` + `GetTask`. A
protocol implementation verified only by its own author's tests is a private format with a
public name. This is the acceptance gate for the phase.

### V9 — The span rename is complete

```bash
grep -rn "app\.a2a\." aegis/src backend/src   # expect: only genuinely-over-the-wire spans
grep -rn "A2A_PROTOCOL" aegis/src backend/src # expect: nothing
```

### Frontend

**Nothing in `web/` changes for A0–A5.** I checked: the only protocol-adjacent console
surface is the MCP one (`web/src/components/mcp/*`, `web/src/lib/api/mcp.ts`) **[MEASURED]**,
and it renders MCP peers, not A2A. If a later task adds an A2A peer console, it inherits
`test_route_coverage.py`'s reachability rule and the `portal.ts` catalogue — see Trap 2.

The one frontend-visible consequence, **if** the public card is served from the app root: the
landing page's origin now returns a JSON document at a well-known path. Confirm it does not
collide with anything Next.js serves and that the CORS middleware
(`backend/src/app/main.py:750-757`) does not gate it, since discovery is by definition
cross-origin.

---

## Risks, and what would make me abandon or reduce this

1. **The protobuf downgrade (7.35.1 → 6.33.6) is unproven at runtime** **[MEASURED]** for
   the resolution, unverified for the import. Temporal and ONNX Runtime are the exposure.
   *Abandon trigger:* either breaks → drop the SDK and hand-roll the JSON-RPC surface
   (five methods; the card is just a dict), or drop A2A entirely.
2. **Card signing introduces the first asymmetric private key in the product** and there is
   no key-management story here **[MEASURED]** — no JWKS, no rotation, no HSM. `signatures`
   is a MAY in §8.4 **[DOC]**. *Reduce trigger:* if key handling is not solved, **ship the
   card unsigned and say so in the security document**, rather than shipping a key checked
   into a config file. An unsigned card is honest; a leaked signing key is not recoverable.
3. **The `tenant` field is attacker-controlled and arrives pre-auth.** Every other tenancy
   bug in this codebase's history has this shape. V6 exists for exactly this and should be
   written before A4, not after.
4. **The Aegis→A2A stream frame mapping is not designed.** A5 is not costed. If it proves
   expensive, ship `SendMessage` only and set `capabilities.streaming: false` — a card that
   truthfully declares no streaming is fine; one that declares streaming and drops frames is
   not.
5. **`a2a-sdk` ships fast**: six releases between 2026-04-17 and 2026-07-22 **[MEASURED]**.
   Pin exactly, vendor the wheel, freeze before any demo.
6. **The 1.0 method names are PascalCase and 0.x's were not** **[DOC]**. Anything written
   from memory or from a pre-1.0 tutorial will be wrong on the wire and will pass its own
   tests.
7. **`GET /v1/platform/capabilities` is unauthenticated** **[SOURCE]** (`routes.py:1432`).
   If the card ever draws `skills` from something that route can reach, the public card
   grows a domain surface silently. V2 is the guard.

---

## What this plan does NOT cover

* **gRPC and HTTP+JSON bindings.** `JSONRPC` only. `supportedInterfaces` will declare one
  entry, honestly.
* **Push notifications.** The four `*TaskPushNotificationConfig` methods are unimplemented
  and `capabilities.pushNotifications` will be `false`.
* **`ListTasks` and `SubscribeToTask`.** Named in the spec surface above so the omission is
  visible, not planned.
* **Aegis as an A2A *client*** — calling out to other agents' cards. That is a separate
  document with a separate threat model (SSRF, card-signature trust, egress policy) and
  nothing here prepares for it.
* **A2A extensions** (`AgentExtension`, the `A2A-Extensions` header).
* **`AUTH_REQUIRED` / `INPUT_REQUIRED` task states as a real human-in-the-loop path.** Aegis
  has an approvals gate; wiring A2A's interrupted states onto it is obvious and unspecified
  here.
* **Key rotation, revocation, and the multiple-signature case** §8.4.3 allows.
* **Any claim that the `a2a-sdk` 0.3 compatibility mode works.** I read the vendor's
  sentence and did not test it.
* **`run_events` integration (A7) is named, not specified** — I did not read that writer.
