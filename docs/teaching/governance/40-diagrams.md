# Governance — the diagrams

Diagram 3 (the two isolation layers with both traps) and diagram 5 (the ledger failure
chain) are the two to be able to draw from memory.

---

## 1. The four controls, and where each sits in a request

```mermaid
flowchart TB
    REQ["HTTP request<br/>Authorization: Bearer ..."] --> AUTHN

    AUTHN["<b>1. Authentication</b><br/>decode + verify the JWT<br/><i>algorithms pinned explicitly</i>"]
    AUTHN --> CLAIMS["TokenClaims<br/>user_id, username,<br/>fine role, coarse role, tenant_id"]

    CLAIMS --> AUTHZ["<b>2. Authorisation</b><br/>role dependency on the route<br/>+ cross-tenant guard"]

    AUTHZ --> CTX["<b>resolve limits</b><br/>effective_limits(tenant, user)<br/><i>user cap clamped inward</i>"]
    CTX --> BIND["bind GovernanceContext<br/><i>contextvar, inside the task</i>"]

    BIND --> WORK["the agent run"]

    WORK --> ISO["<b>3. Tenant isolation</b><br/>app filter + Postgres RLS<br/><i>on every governed read</i>"]
    WORK --> BUD["<b>4. Budgets</b><br/>enforced at the gateway<br/>BEFORE every model call"]

    BUD --> LED["usage_ledger row"]
    LED -->|"summed by"| BUD

    WORK --> AUD["<b>audit_log row</b><br/>action, actor, model,<br/>trace_id, approved_by"]
```

The `LED → BUD` back-edge is the important one: the cap is computed from the ledger, so
the ledger's ability to accept rows *is* the cap.

---

## 2. The two role vocabularies

```mermaid
flowchart TB
    DB["users.role<br/><i>the stored coarse role</i>"] --> R{"which value?"}

    R -->|admin| T{"tenant_id set?"}
    R -->|ai_team| AI["fine = ai_team"]
    R -->|devops| DO["fine = devops"]
    R -->|client| CL["fine = client"]

    T -->|"no — global"| PA["fine = <b>platform_admin</b><br/>rank 4"]
    T -->|"yes — scoped"| TA["fine = <b>tenant_admin</b><br/>rank 3"]

    AI --> RK2["rank 2"]
    DO --> RK2
    CL --> RK1["rank 1"]

    PA --> TOK
    TA --> TOK
    RK2 --> TOK
    RK1 --> TOK

    TOK["JWT carries BOTH<br/>role (fine) + coarse_role"]
    TOK --> WHY["<i>carrying both avoids a LOSSY<br/>re-derivation: collapsing to coarse<br/>and back cannot recover the tier</i>"]
```

No fifth column. The admin split is **derived** from tenancy, so the two facts cannot
drift — they are the same fact. `ai_team` and `devops` deliberately share rank 2: neither
dominates the other.

---

## 3. Tenant isolation — two layers, and both traps

```mermaid
flowchart TB
    Q["a governed query"] --> L1["<b>Layer 1: application filter</b><br/>WHERE tenant_id = :ctx"]
    L1 --> BINDG["<b>bind the scope</b><br/>SELECT set_config('app.tenant_id', :tid, true)"]
    BINDG --> L2["<b>Layer 2: Postgres RLS policy</b><br/>tenant_isolation on<br/>users / usage_ledger / approvals"]
    L2 --> ROWS["only this tenant's rows"]

    T1["<b>Trap 1</b><br/>SET app.tenant_id = :tid<br/>SET takes a LITERAL<br/>-> syntax error at or near $1"] -.->|"broke every<br/>tenant-scoped path"| BINDG

    T2["<b>Trap 2</b><br/>session-scoped SET persists<br/>for the whole CONNECTION<br/>-> a pool leaks scope"] -.->|"fixed by is_local=true"| BINDG

    T3["<b>Trap 3</b><br/>ENABLE without FORCE<br/>the table OWNER bypasses<br/>every policy — and the app<br/>connects as the owner"] -.->|"policy enforced<br/>against nobody"| L2
```

All three were live in this codebase. None is visible in code review: the code said
"set the tenant" and "enable RLS", and both statements were true.

---

## 4. The RLS predicate, branch by branch

```mermaid
flowchart TB
    P["current_setting('app.tenant_id', true)"] --> S["substring(... from '^[0-9]+$')<br/><i>digits only — can NEVER raise</i>"]

    S --> N{"result is NULL?"}
    N -->|"yes — unset, empty,<br/>or non-numeric"| OPEN["<b>policy does not restrict</b><br/><i>needed by the login lookup and<br/>the platform-admin listings</i>"]
    N -->|no| MATCH["row visible iff<br/>tenant_id = scope::int"]

    MATCH --> W["no explicit WITH CHECK<br/>-> Postgres reuses USING for writes<br/><i>an INSERT stamping another tenant<br/>is REJECTED, not merely hidden</i>"]

    BAD["a bare ''::int cast<br/>RAISES — and an OR guard<br/>cannot save it, because SQL<br/>gives no evaluation order"] -.->|"why substring, not cast"| S
```

**Describe the NULL branch honestly.** A bound numeric scope is strictly enforced; an
unbound request is not restricted. That is strictly more enforcement than before FORCE
(when the policy was inert for the owning role in every case), and it is a named gap, not
a claim of airtight isolation.

---

## 5. The ledger failure chain — why a schema change is a security control

```mermaid
flowchart TB
    ADD["columns added to the<br/>UsageLedger model"] --> DOC["the ALTER TABLE written<br/>only in a docstring"]
    DOC --> CA["create_all is<br/>CREATE TABLE IF NOT EXISTS<br/><i>never alters an existing table</i>"]
    CA --> MISS["live database lacks<br/>audio_seconds / images"]
    MISS --> INS["every record_usage INSERT<br/>raises UndefinedColumn"]
    INS --> SWAL["the gateway swallows it<br/><i>usage recording is best-effort<br/>by design — correct on its own</i>"]
    SWAL --> LOST["<b>rows vanish, silently</b>"]
    LOST --> SUM["the USD cap sums the ledger<br/>-> the sum stays flat"]
    SUM --> NOCAP["<b>every USD cap stops binding</b><br/>paid calls, no ceiling, no record"]

    FIX["<b>reconcile_additive_columns</b><br/>at bootstrap"] --> NOCAP
    FIX --> P1["additive only — cannot destroy data"]
    FIX --> P2["idempotent — plan from information_schema,<br/>DDL carries IF NOT EXISTS"]
    FIX --> P3["Postgres only"]
    FIX --> P4["<b>LOUD</b> — SchemaDriftError on<br/>drift it cannot fix, and the API<br/>REFUSES TO SERVE"]
```

Every individual layer is defensible. The bug lives entirely in the seam.

---

## 6. Additive reconciliation — the decision

```mermaid
flowchart TB
    BOOT["bootstrap, after create_all"] --> DIA{"dialect is postgresql?"}
    DIA -->|no| NOOP([return — SQLite recreates<br/>its schema every run])
    DIA -->|yes| READ["read information_schema.columns"]

    READ --> PLAN["plan_additive_columns<br/><i>pure: no database needed</i>"]
    PLAN --> SPLIT{"for each missing column"}

    SPLIT -->|"table absent entirely"| SKIP["skip — create_all owns<br/>brand-new tables"]
    SPLIT -->|"nullable OR has a<br/>server_default"| OK["addable"]
    SPLIT -->|"primary key, or NOT NULL<br/>with no server default"| UNSAFE["unsafe"]

    UNSAFE --> RAISE["<b>SchemaDriftError</b><br/>CRITICAL log + raise<br/><i>no correct value exists for<br/>the rows already present</i>"]
    RAISE --> REFUSE([the API refuses to boot])

    OK --> DDL["render the DDL with SQLAlchemy's<br/>own CreateColumn compiler<br/><i>identical to what create_all<br/>would have produced</i>"]
    DDL --> ALTER["ALTER TABLE ... ADD COLUMN IF NOT EXISTS"]
    ALTER --> IDX["create indexes declared solely<br/>on the newly added columns"]
    IDX --> DONE([logged at INFO])
```

---

## 7. Budget resolution and enforcement

```mermaid
flowchart TB
    subgraph RESOLVE["once per request"]
        TB["tenant budget row"] --> CL["_clamp_inward<br/><i>min of the present caps;<br/>None means uncapped</i>"]
        UB["user budget row"] --> CL
        CL --> LIM["GovernanceLimits<br/>token_cap, usd_cap, rpm, tpm"]
    end

    subgraph ENFORCE["before EVERY model call"]
        E0["enforce_governance"] --> E1{"tenant bound?"}
        E1 -->|no| NOOPE([return — ungoverned])
        E1 -->|yes| E2["bind the RLS scope"]
        E2 --> E3["load budget rows<br/><i>USER-scoped first</i>"]
        E3 --> E4["sum the ledger over<br/>this row's window"]
        E4 --> C1{"tokens >= token_cap?"}
        C1 -->|yes| X1([BudgetExceededError])
        C1 -->|no| C2{"cost >= usd_cap?"}
        C2 -->|yes| X2([BudgetExceededError])
        C2 -->|no| C3{"rpm or tpm set?"}
        C3 -->|yes| C4["re-sum over the last 60s"]
        C4 --> C5{"calls >= rpm<br/>or tokens >= tpm?"}
        C5 -->|yes| X3([BudgetExceededError])
        C5 -->|no| PASS([proceed to the provider])
        C3 -->|no| PASS
    end

    LIM -.->|"same rows,<br/>same summation"| E3
```

**User rows first** so a user breach is attributed to the user. Comparison is `>=`, so
consumption *at* the cap blocks.

---

## 8. The cross-tenant budget takeover, and why the obvious fix is worse

```mermaid
flowchart TB
    K["natural key<br/>(scope_type, scope_id, window)<br/><b>contains no tenant</b>"] --> COLL["two tenants can produce<br/>the same triple"]

    COLL --> OLD["<b>the bug</b><br/>lookup with no tenant predicate,<br/>then tenant_id = caller unconditionally"]
    OLD --> TAKE["tenant B overwrites tenant A's caps<br/>AND re-stamps the row as B's<br/><i>silent takeover</i>"]

    COLL --> NAIVE["<b>the tempting fix</b><br/>add tenant_id to the lookup"]
    NAIVE --> DUP["finds nothing -> INSERTS a<br/>second row for the same<br/>scope+window"]
    DUP --> ARB["the enforcement reader picks<br/>between duplicates ARBITRARILY<br/><i>takeover becomes non-determinism</i>"]

    COLL --> GOOD["<b>the actual fix</b><br/>keep the FULL natural-key lookup,<br/>then check ownership"]
    GOOD --> REF["different, non-null tenant<br/>-> CrossTenantBudgetError -> 403"]
    GOOD --> ALLOW["platform-admin may write any row;<br/>an unowned row may be claimed;<br/>an admin write never erases<br/>an existing owner stamp"]
```

---

## 9. Authentication, end to end

```mermaid
flowchart LR
    L["POST /login<br/>username + password"] --> LOOK["read users by username<br/><i>runs BEFORE any tenant is known —<br/>this is why the unset-GUC branch<br/>must not fail closed</i>"]
    LOOK --> V{"verify_password"}
    V -->|"no hash stored"| F1([False — fail closed])
    V -->|"any exception"| F2([False — never raises])
    V -->|match| MINT["create_access_token"]

    MINT --> P["payload:<br/>sub (string), username,<br/>role (fine), coarse_role,<br/>tenant_id, iat, exp"]
    P --> SIG["HMAC-SHA256 with the injected secret"]
    SIG --> TOK([JWT])

    TOK --> D["decode_access_token"]
    D --> ALG["algorithms=[...] passed EXPLICITLY<br/><i>defeats alg:none and<br/>algorithm confusion</i>"]
    ALG --> REQ{"username and role present?"}
    REQ -->|no| REJ([InvalidTokenError])
    REQ -->|yes| CLAIMS([TokenClaims])

    GUARD["startup guard:<br/>a non-dev deployment REFUSES<br/>to boot on a default or<br/>too-short JWT_SECRET"] -.-> SIG
```

The payload is **encoded, not encrypted** — anyone holding the token reads every claim.
The signature buys integrity, which makes the signing secret the single thing standing
between a user and any tenant.

---

**Next:** [`50-interview.md`](50-interview.md).
