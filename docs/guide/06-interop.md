# Part 6 — Interoperability

A platform that only talks to its own frontend is a product. A platform that other
systems can call, inspect and trace is infrastructure. This part covers the four ways
something outside Aegis can talk to it or about it.

| Standard | Answers the question |
|---|---|
| **A2A** (Agent2Agent) | How does another *agent* delegate work to Aegis? |
| **MCP** (Model Context Protocol) | How does another *model* use Aegis's tools? |
| **CycloneDX** (SBOM, AgBOM) | What is inside this deployment? |
| **OpenTelemetry** | What happened during a run, in a format any tool can read? |

---

## 6.1 A2A — Agent2Agent 1.0

### What an agent-to-agent protocol is for

Two agents built by different teams, in different languages, on different frameworks,
need to work together. One wants to hand a task to the other and get a result back.

Without a shared protocol, every pair needs a bespoke integration: a private URL shape, a
private payload, a private way of saying "here is the answer" or "it failed". With *n*
agents you get *n²* integrations.

**A2A** is a protocol for that handoff. It answers three questions in a standard way:
*what can you do* (discovery), *do this for me* (task submission), and *is it done yet*
(task status). Aegis implements **A2A 1.0** in
`backend/src/app/a2a/` — a small hand-written implementation, deliberately, so the
protocol surface has no transitive dependency conflicts with the rest of the stack.

### Discovery: the agent card

An **agent card** is a JSON document describing what an agent is and how to talk to it.
Aegis serves it at:

```
GET /.well-known/agent-card.json
```

**Why a well-known path.** `/.well-known/` is a reserved URI prefix defined by RFC 8615
and administered by IANA — the same registry that governs port numbers and media types.
Anything under it is a *registered* location with a documented meaning, which is what
makes discovery need no configuration: given only an origin, a client knows exactly where
to look. It is the same mechanism `security.txt` and ACME certificate challenges use. The
alternative — every deployment choosing its own path — puts the burden back on
out-of-band configuration, which is what a discovery protocol exists to remove.

The card carries `name`, `description`, `version`, `protocolVersion` (`"1.0"`),
`supportedInterfaces` (each with a URL, a `protocolBinding` of `JSONRPC`, and the
protocol version), `provider`, `documentationUrl`, `capabilities`, `securitySchemes`,
`securityRequirements`, default input and output modes, and `skills`.

Two things the card deliberately does **not** do:

- **It does not list the tool registry.** It advertises two coarse skills —
  `answer-with-provenance` and `governed-action`. The card is unauthenticated, and
  publishing the tool list would tell an anonymous reader exactly which capabilities this
  deployment holds.
- **It does not claim capabilities it does not implement.** `streaming`,
  `pushNotifications` and `extendedAgentCard` are all `false`, because the corresponding
  methods are not served. A card is a contract; a card that overstates is worse than no
  card.

### Signing: JWKS and ES256

A card fetched over the network can be tampered with, so Aegis signs it.

The signature is **ES256** — ECDSA over the NIST P-256 curve with SHA-256. This is the
first asymmetric key in the platform; everything else uses a symmetric secret, which
cannot sign a *public* artefact because anyone able to verify could also forge.

The signing input is the card **canonicalised by RFC 8785 (JSON Canonicalisation
Scheme)** with the `signatures` array excluded: sorted keys, no insignificant whitespace.
Canonicalisation matters here for the same reason it matters in the audit chain — a
signature is evidence only if the verifier can reconstruct byte for byte what was signed.

The public half is published as a **JWKS** (JSON Web Key Set) at
`GET /.well-known/jwks.json`, returning the P-256 public key as `{kty: "EC", crv:
"P-256", use: "sig", alg: "ES256", kid, x, y}`. The signature's protected header carries
`kid` (which key) and `jku` (where to fetch it), so a verifier that has never seen this
deployment can complete the check from the card alone.

One deliberate constraint: the origin written into the card and into `jku` comes from
configuration, **never from the incoming request's `Host` header**. A signed document
whose contents are chosen by the person requesting it is a signed document that certifies
the attacker's URLs. With no configured origin the card is served unsigned with
`Cache-Control: no-store`; with one, signed and cacheable for five minutes with an
`ETag`.

### The methods

Aegis serves JSON-RPC 2.0 over `POST /v1/a2a`, authenticated with a bearer token. Two
methods, in A2A 1.0's PascalCase spelling:

| Method | Behaviour |
|---|---|
| `SendMessage` | Extracts the text parts of the message, runs the agent, returns a task with `state: "TASK_STATE_COMPLETED"` and an artifact carrying the answer. A failure returns `"TASK_STATE_FAILED"` |
| `GetTask` | Returns `-32004`: task history is not retained by this agent; a task is observable only on the stream that created it |

`GetTask` is answered honestly rather than faked. Returning a plausible empty task would
make a client believe its task was lost; a specific error tells it the truth.

### The `tenant` field — the key security idea

This is the most important paragraph in Part 6.

The A2A protocol carries a `tenant` field. A client copies it from whichever interface it
selected in an agent card. Two facts about it decide everything:

1. **It arrives before authentication.** It is part of the request body, present whether
   or not the caller has a valid credential.
2. **It is entirely attacker-controlled.** Anyone can put any string there.

So the rule Aegis enforces is:

> **`tenant` selects which agent is being addressed. It never sets `app.tenant_id`.**

It is a **routing label**, in the same category as a hostname. The database scope — the
value that drives row-level security and every tenant predicate (Part 4, §4.2) — is
derived from the verified bearer token and from nothing else. The A2A security scheme in
the card says so in its own description, so the property is published, not merely
implemented.

Concretely, the routed value is passed to `resolve_addressed_tenant`, and **its return
value is discarded**. The function's only outputs are the token's own scope or an
exception; it can return `authenticated`, and it can raise. It can never return `routed`.
The governance context bound for the run comes from the token.

**A mismatch is refused, not reconciled.** If the routed tenant and the authenticated
tenant disagree, the request fails. Aegis does not "prefer the token" and continue, and
it does not narrow one to the other. A caller addressing a tenant its credential does not
cover is making a request nobody should serve, and serving a *different* request than the
one asked for is its own kind of wrong answer. A platform-scoped principal — one with no
tenant at all — likewise cannot be routed into a tenant.

**The error is identical every time.** Five distinct failures — a non-string value, a
non-ASCII or non-decimal value, a non-canonical spelling such as `"07"`, an unparseable
integer, and a genuine mismatch — all produce the same JSON-RPC error `-32602` (invalid
params) with the same message:

> `the addressed tenant is not the tenant this credential belongs to`

The message names no tenant id. This matters because an error that *varies* is an
**oracle**: a caller who cannot read another tenant's data can still probe which tenant
ids exist by watching whether the refusal changes shape. Even the *type* of failure is a
signal — which is why a non-string value returns `-32602` rather than crashing into a
500, since a caller who cannot tell a wrong tenant from a right one can still tell a 500
from a `-32602`.

The value is also validated narrowly before comparison: ASCII decimal digits only (so
that non-ASCII digit forms, which some integer parsers accept, cannot be used as a filter
bypass), and canonical spelling only (`"07"` is refused rather than normalised to `7`).

`-32602` rather than HTTP 403 is the right code because the request *is* well-formed and
*is* authenticated. What is wrong is the parameter.

### Cross-questions

**Q. Why implement A2A by hand instead of using the official SDK?**
Dependency reality. The SDK's version pins conflict with pins the rest of the platform
needs. The protocol surface is two methods and a card; hand-writing it costs less than
resolving a dependency graph downgrade, and it keeps the wire format under direct test.

**Q. Your signing key is generated per process. Is that not useless?**
It is honest rather than useless: verification works within a process lifetime, the JWKS
is served live, and the limitation is logged at startup rather than hidden. Persisting the
key is a deployment concern (a key store), not a protocol one — the code path that signs
and publishes is the same either way.

**Q. If `tenant` is ignored, why accept the field at all?**
Because the protocol defines it and clients send it. Silently ignoring it would let a
caller believe it had addressed tenant 7 while being served tenant 3 — a wrong answer
delivered confidently. Comparing and refusing turns a silent divergence into a loud one.

**Q. Is an identical error message not unhelpful to legitimate callers?**
A legitimate caller has exactly one correct value: the tenant their own credential
belongs to. They do not need the error to tell them what it is. The information the
message withholds is only useful to someone who does not already know it.

---

## 6.2 MCP — the Model Context Protocol

**MCP** is a standard way to expose tools, resources and prompts to any model-driven
client. Where A2A is agent-to-agent, MCP is client-to-tools: an MCP client (Claude
Desktop, an IDE, another agent framework) connects, asks what tools exist, and calls them.

Aegis serves MCP over **Streamable HTTP** — the modern HTTP transport, where a single
endpoint carries requests and can stream responses over the same connection. The stdio
transport is deliberately not offered: a stdio process has no caller identity to
authorise against, so one process could only ever serve one tenant.

What a caller sees is computed per caller:

- **Domain tools** from the tool registry, filtered by the persona attached to the
  caller's role.
- **Platform tools** filtered by role.
- **Two resources**: a platform capabilities document and a tool-policy document, the
  latter rendered with the caller's own role and tenant scope substituted in.

Risk tiers from Part 4 carry over unchanged. Low and medium-risk tools execute. A
high-risk tool is **listed but not executed**: the call writes a pending approval row and
returns its id. An MCP client is a proposer, not an approver.

### Authority is re-read on every call

This is the property worth memorising.

A bearer token carries claims — a username, a role, a tenant. Those claims were true when
the token was minted. They are not evidence of what the holder may do *now*. A user can
be deactivated, moved to another tenant, or demoted, and their token keeps saying
otherwise until it expires.

So on **every inbound MCP message**, Aegis:

1. Reads the HTTP request bound to that message. No request means no caller identity, and
   the call is refused.
2. Decodes the bearer token — from *this* call, not only from the call that opened the
   session.
3. Uses **only the username** from the token, as a lookup key.
4. Reads the live `users` row from PostgreSQL, through the same lookup the login path
   uses.
5. Builds the authority — role, persona, fine-grained role, tenant, user id — entirely
   from that row.

If the row is missing or inactive, the call fails. It fails **closed**: falling back to
the token's own claims is precisely how a revoked principal keeps working.

Nothing is cached between calls — no persona, no tenant, no principal. And the
connection-level scope check that MCP allows is deliberately left empty, because a scope
verified once at connect time is exactly the "authenticate once, trust every call"
mistake this design exists to avoid.

The consequence, stated plainly: **a token claiming a higher role does not get one.** The
token is a key to the door. The database decides what is behind it.

### Cross-questions

**Q. A database read on every tool call — is that not slow?**
It is one indexed lookup by username against a table that is small by construction and
hot in cache, against a tool call that is already doing network I/O. The cost is
rounding error next to the correctness it buys.

**Q. Why not just use short-lived tokens instead?**
Short expiry shrinks the window; it does not close it. Between minting and expiry the
claims are still stale, and shrinking the window trades security for a re-authentication
storm. Re-reading removes the window entirely.

**Q. Can an MCP client trigger a high-risk action?**
It can *propose* one. The call creates a pending approval and returns the id; a human
decides through the same inbox as any other gate.

---

## 6.3 CycloneDX — SBOM and AgBOM

### What a bill of materials is

A **bill of materials** (BOM) is a manufacturing idea: a list of every part in a product,
with enough identity to trace each one. A **software** bill of materials (SBOM) does the
same for a build — every package, its version, its licence, its identifier.

It matters because of inverted lookups. When a vulnerability is announced in some
library, the question is not "is that library good?" It is "**do I have it, and where?**"
Without an SBOM that is answered by reading build files by hand, at a moment when speed
is the whole point.

Aegis emits two documents, both **CycloneDX 1.6**.

### The dependency SBOM — what packages

`GET /v1/stack/sbom?format=cyclonedx` (also `format=spdx`), admin or devops only, served
with the media type `application/vnd.cyclonedx+json`.

The components are **resolved, not authored**: they come from the installed distributions
of the *running interpreter*, so the document describes the machine that is actually
serving, not a list maintained beside it. Each component carries a name, version, licence
identifier, a PURL (`pkg:pypi/<name>@<version>`, also used as the `bom-ref`), author and
homepage.

The metadata records what the integrity evidence actually is: the count of `sha256`
digest pins in the lock files (the backend's alone carries 4,219), and an explicit
property stating the document is **unsigned, with no in-toto or SLSA provenance**. Saying
what evidence you *do not* have is part of making the evidence you *do* have believable.

### The AgBOM — what this agent can do

An SBOM answers "what packages". It does not answer the question a reviewer of an
*agentic* system actually asks: **what can this thing do?** A list of Python packages
does not tell you that the agent can update a record, or which models it may route to, or
what it remembers.

The **AgBOM** answers that. `GET /v1/platform/agbom`, admin or devops only. It is
CycloneDX 1.6 as well, because the OWASP Agent Observability Standard extends CycloneDX
rather than inventing a fourth format.

Four sections, 23 components on a default deployment:

| Section | CycloneDX type | Count | What each entry carries |
|---|---|---|---|
| Tools | `application` | 4 | Risk tier, read-only flag, which personas may call it |
| Model deployments | `machine-learning-model` | 12 | Role, whether it is fleet-`declared` or `undeclared`, tenant-selectable, whether routing is currently in force |
| Guard stages | `application` | 4 | `INPUT`, `OUTPUT`, `TOOL_RESULT`, `MEMORY_WRITE` |
| Knowledge sources | `data` | 3 | Vector store, configured state, tenant-scoped flag |

The metadata component describes the agent itself and cross-links to
`/.well-known/agent-card.json`, so the inventory and the A2A identity point at each
other.

Two honest details are visible in the document. Tools are emitted with `type:
"application"` because `"tool"` is not a member of the CycloneDX 1.6 component-type
enumeration, and the divergence is recorded rather than silently made. And a model that
is routed but not declared in the fleet appears as `aegis:state=undeclared` rather than
being hidden: an inventory that omits what it cannot explain is not an inventory.

### The content-derived serial number

Every CycloneDX document has a `serialNumber`. The obvious implementation is a random
UUID. Aegis instead derives it:

```python
digest = hashlib.sha256(
    json.dumps(components, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
serial = f"urn:uuid:{digest[:8]}-{digest[8:12]}-..."
```

SHA-256 over the canonically serialised **components list only** — not the metadata,
because the metadata carries a timestamp and a timestamp would change on every call.

The property this buys: **two builds of an unchanged deployment produce the same serial.**
So a *changed* serial means the agent actually changed — a tool added, a model
deployment altered, a knowledge source configured. With a random UUID, every poll looks
like a new release, and a signal that fires every time is not a signal. It also makes the
document trivially diffable: two AgBOMs are identical or they are not, and you can tell
by comparing 36 characters.

### Cross-questions

**Q. Why CycloneDX rather than SPDX?**
Both are real, mature standards, and Aegis emits SPDX for the dependency SBOM precisely
because some consumers require it. For the *agent* inventory, CycloneDX wins on one
concrete ground: it has a component-type vocabulary that already includes
`machine-learning-model` and `data`, and a `properties` mechanism for namespaced
extensions like `aegis:riskTier`. SPDX is built around software packages and licence
provenance, which is what it is excellent at; expressing "this tool is high-risk and
callable by these two personas" would mean bending it. The OWASP Agent Observability
Standard made the same call, so following it also means being readable by the tools built
around it.

**Q. An SBOM you generate from the running process — can it be trusted?**
It is more trustworthy than one generated from a manifest, because a manifest describes
what was requested and the interpreter describes what is present. Its limits are stated
in the document itself: unsigned, no SLSA provenance, integrity evidence is the lock-file
digest pins.

**Q. Why does the AgBOM include guard stages? They are not "parts".**
Because the question the AgBOM answers is "what can this agent do", and "what screens
it" is the other half of that answer. An inventory listing four tools and no rails
describes a strictly more dangerous system than the one that exists.

---

## 6.4 OpenTelemetry

**OpenTelemetry** (OTel) is the vendor-neutral standard for traces, metrics and logs. A
**span** is one timed operation with attributes; spans nest into a **trace** covering one
request end to end.

Aegis emits its runs as spans using the **OpenTelemetry GenAI semantic conventions** — the
agreed attribute names for model calls. Using the standard names is the whole point: any
collector, dashboard or analysis tool that understands GenAI spans understands an Aegis
trace with no adapter.

Real attribute keys, from `aegis/src/aegis/observability/semconv.py`:

```
gen_ai.operation.name        gen_ai.request.model       gen_ai.usage.input_tokens
gen_ai.provider.name         gen_ai.response.model      gen_ai.usage.output_tokens
gen_ai.system  (alias)       gen_ai.request.temperature gen_ai.usage.cost
```

The conventions moved `gen_ai.system` to `gen_ai.provider.name` in semconv v1.37.0, and
renamed the token counters. Aegis emits the **new** keys and also sets the deprecated
`gen_ai.system` alias, so tooling on either side of the rename reads the traces
correctly. `gen_ai.usage.cost` is a named extension — the conventions do not define a cost
attribute — which is why it is documented as an extension rather than passed off as
standard.

Alongside these, spans carry **OpenInference**'s `openinference.span.kind`, the attribute
LLM-observability tools use to classify a span. Aegis sets it as a plain string across
nine kinds — `LLM`, `EMBEDDING`, `RETRIEVER`, `RERANKER`, `TOOL`, `GUARDRAIL`, `AGENT`,
`CHAIN`, `EVALUATOR` — without taking the OpenInference instrumentation packages as
dependencies. Compatibility with a convention costs a string; compatibility with a
library costs a dependency.

Platform-specific facts that have no standard key live under an `app.*` namespace:
`app.graph.node`, `app.retrieval.result_count`, `app.retrieval.cache_hit`,
`app.guardrail.stage`, `app.guardrail.verdict`, `app.tool.risk`, `app.handoff.to`, and
others. Namespacing them keeps them from colliding with a future standard key of the same
name.

What becomes a span:

| Span name | Kind |
|---|---|
| `chat <model>`, `embeddings <model>`, `transcription <model>` | LLM / embedding |
| `node.<name>` | Graph node |
| `tool.<name>` | Tool call, with risk tier |
| `handoff → <role>`, `subagent.<role>` | Agent |
| `retrieve`, `guardrail.input` | Retriever, guardrail |

Export goes to a locally launched **Arize Phoenix** instance when enabled, and otherwise
falls back to a plain OTel `TracerProvider` writing to the console. The tracer resolves
against OTel's global no-op provider when nothing is initialised, so every span call is
harmless offline and in tests. The gateway itself has **no OTel dependency**: it takes an
`ObservabilitySink` protocol with three methods, and the OTel implementation is injected.

### Cross-questions

**Q. Why OTel rather than a bespoke trace format?**
Three reasons. Portability — a customer already running a collector gets Aegis traces
with no integration work. Longevity — a bespoke format needs its own viewer, its own
storage and its own maintenance forever. And credibility — "we export OTel GenAI spans"
is checkable in five minutes; "we have detailed internal tracing" is not.

**Q. Why an open protocol at all, instead of a private API you fully control?**
A private API means every integration is a negotiation and every client is bespoke. An
open protocol means a client that has never heard of Aegis can discover it, call it and
read its traces. It also makes the security properties *auditable against a specification*
rather than against our own documentation — which is why the `tenant` rule in §6.1 is a
stronger claim than a private API's equivalent could be.

**Q. Standards constrain you. What did they cost?**
Real things, and they are recorded rather than hidden: tools are emitted as
`application` because CycloneDX 1.6 has no `tool` type; `transcription` is an Aegis
extension because the GenAI conventions have no audio operation yet; `gen_ai.usage.cost`
is not a standard attribute. Each is a documented divergence. A divergence you can name
is a smaller problem than a format nobody else can read.
