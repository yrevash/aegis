# Governance — the diagrams

Five diagrams. The two to be able to draw from memory are **the two isolation layers with
their traps** and **the ledger failure chain** — the first is how isolation is meant to
work, the second is how a control switches itself off.

Everything else is explained in [`10-guide.md`](10-guide.md); a picture is only here when
it shows something prose cannot.

---

## 1. The four controls, and where each sits in a request

*Look at the arrow that goes backwards, from the ledger into the budget check.*

```mermaid
flowchart TB
    REQ["HTTP request<br/>Authorization: Bearer ..."] --> AUTHN["<b>1. Authentication</b><br/>decode and verify the JWT<br/>algorithms pinned explicitly"]
    AUTHN --> CLAIMS["TokenClaims<br/>user, fine role, coarse role, tenant"]

    CLAIMS --> AUTHZ["<b>2. Authorisation</b><br/>role dependency on the route<br/>plus the cross-tenant guard"]
    AUTHZ --> CTX["resolve effective limits<br/>then bind a GovernanceContext<br/>on a contextvar, inside the task"]
    CTX --> WORK["the agent run"]

    WORK --> ISO["<b>3. Tenant isolation</b><br/>application filter plus Postgres RLS<br/>on every governed read"]
    WORK --> BUD["<b>4. Budgets</b><br/>checked at the gateway,<br/>BEFORE every model call"]
    WORK --> AUD["<b>audit_log row</b><br/>action, actor, model,<br/>trace_id, approved_by"]

    BUD --> LED["usage_ledger row"]
    LED -->|"the cap is a SUM over these rows"| BUD
```

That back-edge is why §12 happens. The cap is computed from the ledger, so **the ledger's
ability to accept a row *is* the cap** — and a ledger write that silently fails is a
spend ceiling that silently lifts.

The context is bound *inside* the task, not around it. An async generator runs in its own
context, so binding outside it would not be visible at the gateway where the model call
finally happens.

---

## 2. Tenant isolation — two layers, and the three traps

*Look at the annotations, not the spine. The spine was never wrong; all three traps were
live at once.*

```mermaid
flowchart TB
    Q["a governed query"] --> L1["<b>Layer 1: application filter</b><br/>WHERE tenant_id = the bound tenant"]
    L1 --> BINDG["<b>bind the scope</b><br/>SELECT set_config with app.tenant_id,<br/>the value, and is_local true"]
    BINDG --> L2["<b>Layer 2: Postgres RLS policy</b><br/>tenant_isolation on<br/>users, usage_ledger, approvals"]
    L2 --> ROWS(["only this tenant's rows"])

    T1["<b>Trap 1</b><br/>SET app.tenant_id = :tid<br/>SET takes a LITERAL, not a bind —<br/>syntax error at or near dollar-one"] -.->|"broke every<br/>tenant-scoped path"| BINDG

    T2["<b>Trap 2</b><br/>a session-scoped SET lives as long<br/>as the CONNECTION, so a pool<br/>hands tenant 7's scope to tenant 12"] -.->|"fixed by is_local"| BINDG

    T3["<b>Trap 3</b><br/>ENABLE without FORCE — the table<br/>OWNER bypasses every policy,<br/>and the app connects as the owner"] -.->|"enforced<br/>against nobody"| L2
```

None of the three is visible in code review. The code said "set the tenant" and "enable
RLS", and both statements were true.

Layer 1 is not redundant: it is the only layer that works on SQLite, which is what the
tests run on — which is also why the suite passed while trap 1 was live.

---

## 3. The RLS predicate, branch by branch

*Look at the NULL branch. It does not restrict, and that is deliberate.*

```mermaid
flowchart TB
    P["current_setting app.tenant_id"] --> S["substring, digits only<br/>can NEVER raise"]

    S --> N{"result is NULL?"}
    N -->|"yes — unset, empty,<br/>or non-numeric"| OPEN["<b>FAILS OPEN</b><br/>the policy does not restrict"]
    N -->|no| MATCH["a row is visible only when<br/>tenant_id equals the scope"]

    MATCH --> NULLROW["a row whose tenant_id is NULL<br/>matches nothing, so it is invisible<br/>to every bound scope — <b>that</b> row<br/>fails CLOSED"]
    MATCH --> W["no explicit WITH CHECK, so Postgres<br/>reuses USING for writes: an INSERT<br/>stamping another tenant is REJECTED,<br/>not merely hidden"]

    BAD["a bare empty-string cast RAISES,<br/>and an OR guard cannot save it —<br/>SQL gives no evaluation order"] -.->|"why substring,<br/>not a cast"| S
```

Two different NULLs, opposite outcomes, and people reverse them. An unset **scope** fails
open. A NULL **column** fails closed.

The open branch exists because login reads `users` by username *before* any tenant is
known — under a fail-closed branch with FORCE on, nobody could ever log in. It is a named
gap, not airtight isolation, and `effective_config()` reports it as `fail_closed=False`
rather than putting a green badge on a security page.

---

## 4. Budget resolution and enforcement

*Look at the order of the two checks at the bottom: user rows are read first.*

```mermaid
flowchart TB
    subgraph RESOLVE["once per request"]
        TB["tenant budget row"] --> CL["clamp inward<br/>the min of the caps that exist,<br/>None meaning uncapped"]
        UB["user budget row"] --> CL
        CL --> LIM["GovernanceLimits<br/>token_cap, usd_cap, rpm, tpm"]
    end

    subgraph ENFORCE["before EVERY model call"]
        E0["enforce_governance"] --> E1{"a tenant is bound?"}
        E1 -->|no| NOOPE(["return — ungoverned is a clean no-op"])
        E1 -->|yes| E2["bind the RLS scope"]
        E2 --> E3["load the budget rows,<br/><b>user-scoped first</b>"]
        E3 --> E4["sum the ledger over each<br/>row's own rolling window"]
        E4 --> C1{"any cap reached?<br/>tokens, USD, rpm, tpm"}
        C1 -->|yes| X1(["BudgetExceededError naming<br/>the scope, limit and usage"])
        C1 -->|no| PASS(["call the provider"])
    end

    LIM -.->|"same rows,<br/>same summation"| E3
```

**User rows first**, because if both are breached, "tenant 7 is over budget" is true and
useless. Naming the narrowest breached scope is the same refusal with a better diagnosis.

The comparison is `>=`, so consumption *at* the cap blocks.

And read step 4 again: it is a read, and the ledger row is written after the provider
call. Twelve concurrent requests can all pass before any of them records a cent. The
overshoot is bounded by in-flight concurrency — say that plainly rather than claiming a
hard cap.

---

## 5. The ledger failure chain — why a schema change is a security control

*Follow it top to bottom, and notice that no box on the way down is a mistake.*

```mermaid
flowchart TB
    ADD["two columns added to the<br/>UsageLedger model"] --> DOC["the ALTER TABLE written<br/>only in a docstring"]
    DOC --> CA["create_all is CREATE TABLE IF NOT EXISTS<br/>and never alters an existing table"]
    CA --> MISS["the live database lacks<br/>audio_seconds and images"]
    MISS --> INS["every record_usage INSERT<br/>raises UndefinedColumn"]
    INS --> SWAL["the gateway swallows it —<br/>usage recording is best-effort<br/>by design, and correct on its own"]
    SWAL --> LOST["<b>rows vanish, silently</b>"]
    LOST --> SUM["the USD cap sums the ledger,<br/>so the sum stays at zero"]
    SUM --> NOCAP["<b>every USD cap stops binding</b><br/>paid calls, no ceiling, no record"]

    FIX["<b>reconcile_additive_columns</b><br/>runs at bootstrap, right after create_all"] --> NOCAP
    FIX --> P1["additive only — it has no<br/>statement that could destroy data"]
    FIX --> P2["idempotent — planned from<br/>information_schema, DDL carries<br/>IF NOT EXISTS"]
    FIX --> P3["<b>LOUD</b> — SchemaDriftError on drift<br/>it cannot fix safely, and the API<br/>refuses to serve"]
```

Every individual layer is defensible. The bug lives entirely in the seam, which is the one
place no layer's tests look.

The last property is the one that takes an argument: most schema drift should not stop a
boot. This table should, because a spend control is computed from it.

**Next:** [`50-interview.md`](50-interview.md).
