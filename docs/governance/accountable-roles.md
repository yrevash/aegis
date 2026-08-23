# Accountable-role register

- **Owner:** Platform owner (`platform_admin`).
- **Reviewed:** quarterly, and on any change to `web/src/lib/portal.ts` or to a `require_*`
  guard in `backend/src/app/api/routes.py`. See [`review-cadence.md`](review-cadence.md).
- **Satisfies:** NIST AI RMF **GOVERN** 2.1–2.3 (roles, responsibilities and lines of
  communication documented and enforced). ISO/IEC 42001 A.3 stays `not_implemented`, and
  §5 below says why that is the honest reading of the same facts.

> **This register describes the roles the software enforces.** It is not an org chart and
> it does not invent one. Every row maps to a real `fine_role`, a real portal in
> `web/src/lib/portal.ts`, and a real guard function that returns 403 to everyone else.
> §4 says who actually holds each role today, which on a two-person team is the part an
> assessor most needs to hear.

---

## 1. The five roles

The five values of `fine_role` are minted onto the JWT at `POST /v1/auth/login` and the
browser keys the whole portal on them. The coarse `Role` (`admin` / `ai_team` / `devops` /
`client`) is what the data layer scopes by; `platform_admin` and `tenant_admin` are both
coarse `admin`, which is exactly why the fine tier exists.

| Role | Accountable for | Tenant scope | Enforced by |
|---|---|---|---|
| **`platform_admin`** | The deployment itself: tenants, users, budgets, MCP peers, the database console, and every decision no single tenant may take. Owns this register, the AI policy and the review cadence. | None — sees every tenant. | `require_platform_admin` (fine role, not coarse) |
| **`tenant_admin`** | One tenant: its seats, its settings, its approvals, its audit trail, its documents. Accountable to its own end customers for what the agent does in that tenant. | Exactly one tenant, applied server-side. | `require_tenant_admin` (admits `platform_admin`, then scopes to the caller's tenant) |
| **`ai_team`** | How the agent behaves: prompts, evals, guardrail configuration, retrieval, the ML signal, the improvement loop up to but not including the release decision. | Un-tenanted platform staff. | `require_ai_team`, `require_admin_or_ai_team` |
| **`devops`** | Whether the deployment is running and defensible: stack, patches, security posture, compliance, red-team runs, latency, cache. | Un-tenanted platform staff. | `require_devops`, `require_platform_security_reader` |
| **`client`** | Their own work: their runs, their documents, their approvals, their memory, their spend. Accountable for the decisions they take on the agent's output. | Their own tenant and their own records. | `require_client`, `require_admin_or_client` |

Sections per role are declared once, in `ROLE_SECTIONS` (`web/src/lib/portal.ts`), and
`backend/tests/api/test_route_coverage.py` reads that file and asserts every listed section
has a live surface behind it. A role's navigation cannot drift from what it can actually do.

---

## 2. The capability boundaries that matter

These are the ones a reviewer should check, because each is a place where "who is
accountable" is decided by code rather than by a sentence.

| Boundary | Rule | Where |
|---|---|---|
| **Platform vs tenant admin** | A tenant admin may not run the red-team battery, read the serving role's RLS attributes, or read the process-wide cache counters. Those are facts about the *deployment*, and no tenant filter would make them safe. | `require_infra_reader` / `require_platform_security_reader` (`backend/src/app/api/routes_health.py`) |
| **Compliance map** | The control-by-control gap map is platform-staff only; the public landing band reads a summary projection with no gaps and no evidence paths. | `GET /v1/compliance` vs `GET /v1/platform/standards` (`backend/src/app/api/routes_standards.py`) |
| **Who may approve** | A paused gate is resolved by an admin tier, narrowed further by the revoke-only `seat.can_approve`. A tenant can take approval authority away from a seat; nothing can hand it to a seat the coarse guard already refused. | `aegis/src/aegis/settings/spec.py` |
| **Un-tenanted principals** | An authenticated principal bound to no tenant and holding no platform role is refused once, by the type, rather than falling through into an unscoped query. | `_require_scope` (`backend/src/app/api/routes.py`) |
| **Token consistency** | The fine and coarse role claims must agree; a token presenting fine `client` with coarse `admin` is rejected as tampered rather than trusted at the elevated tier. | `require_auth` (`backend/src/app/api/routes.py`) |
| **Tenant isolation under all of the above** | Postgres RLS with `FORCE ROW LEVEL SECURITY` and a `NOSUPERUSER NOBYPASSRLS` serving role, audited at boot; a bypassing role stops the process when the check is fatal. | `aegis/src/aegis/governance/rls.py` |

---

## 3. Accountability for the AI specifically

Mapping the four NIST functions to the role that owns them in this deployment:

| Function | Owner | What owning it means here |
|---|---|---|
| **GOVERN** | `platform_admin` | Keeps this register, the AI policy and the review cadence current; approves any change to the platform prompt floor, the default gate tier, or the model gateway. |
| **MAP** | `platform_admin`, with `ai_team` | Owns [`context-and-impact.md`](context-and-impact.md) and redoes it on a domain swap. `ai_team` supplies the capability and limitation half (`GET /v1/ml/model-card`, the eval results). |
| **MEASURE** | `ai_team` | Owns the eval regression gate and the red-team battery, including the benign control suites that measure over-blocking. |
| **MANAGE** | `devops`, with `tenant_admin` | `devops` owns incident response, release and rollback; `tenant_admin` owns the approval queue for its own tenant and is the human in "human oversight". |

---

## 4. Who holds these roles today — stated plainly

Aegis is built and operated by a **two-person team**. One person holds `platform_admin`,
`devops` and `ai_team`; the second reviews. There is no separation of duties between the
person who writes a control and the person who checks it, and no independent assurance
function.

This matters and is not softened here:

- The **compensating control is mechanical, not organisational**: `enforced` in the
  compliance table requires a file *and* a test, every evidence reference is resolved
  against the real filesystem, route table and pytest node ids on each run, and the
  security posture is derived from live wiring rather than declared. A control this team
  turns off cannot keep reporting green, whoever wrote it.
- What that does **not** cover is judgement — deciding that a risk is acceptable, or that a
  gap may stay open. Those decisions have one signature on them.
- `tenant_admin` and `client` are held by the customer, not by this team. That boundary is
  real and is enforced by the guards in §2.

---

## 5. Why ISO/IEC 42001 A.3 is still `not_implemented`

A.3 ("Internal organization") asks for an organisational structure and a reporting line for
AI concerns. What this document describes is an **access-control** structure with the AI
functions mapped onto it. On a two-person team those coincide, and saying so is more useful
than claiming a governance body that does not meet. When a third person joins, A.3 becomes
a real question and this register becomes its starting point — not its answer.
