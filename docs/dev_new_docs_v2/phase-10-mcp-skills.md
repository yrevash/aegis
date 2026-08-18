# Phase 10 — MCP and skills

**The two capabilities the user asked for twice that had no home in phases 3–9.**

Research: [`plans/02-agentic-core-console.md`](plans/02-agentic-core-console.md) §4 (MCP) and §3
(skills)

Both were deferred out of phases 5 and 6 as *"excellent, not required to win a blind problem"* —
a judgement made when timing was the constraint. **Timing is no longer the constraint**, and the
user named both as major additions, so they get a phase rather than a backlog line.

---

## What is actually wrong

### 1. An MCP server exists and is single-tenant by construction

`aegis` ships a real MCP server — 593 lines, with an honest HIGH-risk policy. But it is
**stdio-only**, and the persona is **pinned by an environment variable**. One process serves one
persona. There is no caller identity, so there is nothing to enforce RBAC against.

The user asked for the opposite: *"we need to have real MCP for our platform connecting agents
to do the work with proper role based access."*

### 2. There is no MCP client

*"for aegis admin there should be console and mcp client of aegis itself to ask questions and
get data."* Nothing consumes MCP today, so Aegis cannot use an external tool server, and the
admin cannot query the platform through one.

### 3. Skills half-exist, and the half that exists is the crude half

`aegis/src/aegis/memory/recall.py:300` reads markdown from a `SKILLS_DIR` and **injects whole
bodies into the prompt**. That is a skills *mechanism* with none of the properties that make
skills useful: no per-tenant or per-user scoping, no authoring surface, no progressive
disclosure, and no visibility that a skill was used.

The user: *"those users should have the option to add skill to them on how they can work… a
major addition of skill is needs to be there… user should have autonomy to work with agents."*

---

## Tasks

| # | Task | Days |
|---|---|---|
| 10.1 | Skills as data: table, scoping, resolver | 1.0 |
| 10.2 | Progressive disclosure via a `load_skill` tool | 0.75 |
| 10.3 | Skill authoring UI + visible activation | 0.75 |
| 10.4 | MCP server: Streamable HTTP + per-caller identity | 1.0 |
| 10.5 | MCP server: RBAC and tenant scope per call | 1.0 |
| 10.6 | MCP client: external servers as gated tools | 1.0 |
| 10.7 | The admin MCP console | 0.5 |

**Total: 6.0 days.**

### 10.1 — Skills as data, not files on disk

Adopt the **`SKILL.md` open standard** (agentskills.io — 25+ compatible implementations) as the
authoring format, stored in Postgres rather than a directory, scoped `platform | tenant | user`
through the **Phase 3 settings resolver**. One resolution mechanism, not a second one.

A skill is: a name, a description, a body, and a trigger condition. Resolution is
`platform ∪ tenant ∪ user`, and a tenant skill cannot shadow a platform safety skill — the same
`tighten_only` discipline the guardrails use.

### 10.2 — Progressive disclosure

Today the whole body goes into the prompt. That does not scale past a handful of skills and it
burns context on skills the query never needed.

**Three tiers:** name and description always visible in the system prompt · the body loaded on
demand via a `load_skill` tool call · attachments fetched only if the body asks for them.

The load is a **real tool call**, which means it appears in the trace like any other. That is
the mechanism *and* the visibility — the user can see the agent decide it needed a skill.

### 10.3 — Authoring and activation

A tenant admin writes a skill; a user writes their own. The console shows an activation chip
when one loads, so *"see how self-improving prompts help them"* is observable rather than
asserted.

**Validation on write, not on use:** a skill whose body fails the input rail is rejected at
authoring time. A skill is stored text that reaches a prompt — it is the same attack surface as
uploaded memory, and Phase 7 already names that as the most patient attack in the product.

### 10.4 / 10.5 — The MCP server, made multi-tenant

**Streamable HTTP transport**, replacing stdio, so there is a connection to attach identity to.
Then the part that matters: **every call carries a caller identity, and the existing RBAC and
tenant scope run per call** — the same `GovernanceContext` the HTTP API binds, not a parallel
permission model.

The HIGH-risk policy already in the server stays. An MCP caller proposing a HIGH-risk action
lands in the **same human gate** as everything else. That is the sentence worth being able to
say: *the protocol does not get its own back door.*

The obvious failure to avoid: an MCP server that authenticates the connection once and then
trusts every call on it. Scope is per call, because a long-lived connection outlives the
context that opened it.

### 10.6 — The MCP client

Aegis consuming external MCP servers turns any compliant tool into an Aegis tool.

**External tools default to HIGH risk.** They are code we did not write, reached over a network,
returning content into an agent's context — so they land at the human gate until a platform
admin explicitly lowers the tier for a named tool. That is the honest default, and it composes
with the Phase 5 `TOOL_RESULT` rail, which already exists to screen exactly this kind of
untrusted return value.

### 10.7 — The admin MCP console

The platform admin querying Aegis through its own MCP interface. Small, because 10.4–10.6 do the
work; this is a client surface over an already-governed protocol.

---

## Definition of done

- [ ] A skill resolves `platform ∪ tenant ∪ user`, and a tenant skill cannot shadow a platform safety skill — tested.
- [ ] Loading a skill is a visible tool call in the trace.
- [ ] A skill body that fails the input rail is rejected at authoring time.
- [ ] Two MCP callers from different tenants see different data over the same server — tested against live Postgres, as a tenant-isolation case.
- [ ] An MCP caller proposing a HIGH-risk action lands in the human gate, not around it.
- [ ] An external MCP tool defaults to HIGH risk and its return value passes through the `TOOL_RESULT` rail.
- [ ] Full suites green, ruff clean, `next build` green.

## Demo at the end of this phase

Write a skill as a tenant user, ask a question, and watch the agent decide it needs the skill and
load it as a visible tool call. Then connect Claude Desktop to Aegis over MCP as two different
tenants and show each seeing only its own data — with a HIGH-risk action from MCP stopping at the
same gate the console uses.

## Risks

**The MCP server is the largest new attack surface in the roadmap.** It is a second front door to
the same data, and the mistake to avoid is authenticating a connection rather than a call.

**Skills are stored text that reaches a prompt.** Treat authoring as an untrusted-input path.
Phase 7's memory-upload reasoning applies unchanged.

**Progressive disclosure adds a tool call to the hot path.** Measure the added latency; if a
skill loads on most queries, the tiering has been drawn in the wrong place.
