# Data — the diagrams

Five diagrams. The one to know is **diagram 2** — it is the clearest picture of how three
defensible decisions compose into an invisible failure.

Everything else about this module is explained in [`10-guide.md`](10-guide.md); a picture is
only here when it shows something prose cannot.

---

## 1. Where the data layer sits

*Look at which modules have an arrow into `aegis.data`, and which stop at the core.*

```mermaid
flowchart TB
    CORE["<b>aegis.core</b><br/>pydantic + stdlib<br/><i>imported by everything</i>"]
    DATA["<b>aegis.data</b><br/>sqlalchemy&#91;asyncio&#93;<br/><i>the aegis&#91;data&#93; extra</i>"]

    MEM["aegis.memory"]
    GOV["aegis.governance"]
    OPS["aegis.ops"]

    GR["aegis.guardrails"]
    VI["aegis.vision"]
    VO["aegis.voice"]
    FO["aegis.forecast"]

    DATA --> CORE
    MEM --> DATA
    GOV --> DATA
    OPS --> DATA

    GR --> CORE
    VI --> CORE
    VO --> CORE
    FO --> CORE
```

The four modules on the right never open a database, so they never carry an ORM. The three on
the left do, and they pay for it.

That split only exists because SQLAlchemy is **banned from the core** by a guard test. The core
is imported by everything, so a dependency there is everyone's dependency.

---

## 2. How the ledger silently lost every row

*Follow the right-hand branch. Every step on it is correct, and the end of it is a spend cap
that no longer binds.*

```mermaid
flowchart TB
    ADD["add audio_seconds + images<br/>to the UsageLedger model<br/><i>the ALTER TABLE written<br/>only in a docstring</i>"]

    ADD --> TEST["tests: the schema is built<br/>from scratch every run"]
    TEST --> PASS(["all 11 columns · PASS"])

    ADD --> PROD["deploy to a LIVE database"]
    PROD --> CA["create_all runs"]
    CA --> NOOP["<b>CREATE TABLE IF NOT EXISTS</b><br/>the table exists, so it does NOTHING"]

    NOOP --> OLD["the live table still has its<br/>original 9 columns"]
    OLD --> INS["every INSERT naming audio_seconds<br/>raises UndefinedColumn"]
    INS --> SWALLOW["_record_usage swallows it<br/><i>usage recording is best-effort<br/>by design — and that design is right</i>"]

    SWALLOW --> LOST["the row is lost"]
    LOST --> SUM["USD caps are computed by<br/>SUMMING those rows"]
    SUM --> ZERO["no rows -> no spend -><br/><b>the cap never binds</b>"]

    ZERO --> SILENT["no exception · no failing test ·<br/>no failing request · dashboard green ·<br/>and $0.00 looks exactly like<br/>a quiet tenant"]
```

Three defensible decisions — no migration framework, best-effort usage recording, additive
defaulted columns — compose into an invisible, security-relevant failure.

The two branches out of `ADD` are the whole story: drift can only exist on a long-lived
database, and the only long-lived database is production.

---

## 3. Bootstrap, in order

*Look at the three imports at the top. Nothing below them can create a table they did not
register.*

```mermaid
flowchart TB
    IMP["import aegis.governance.models<br/>import aegis.ops.models<br/>import app.memory.stores<br/><i>for the side effect only</i>"]
    IMP --> META["their mapped classes are now<br/>registered on AegisBase.metadata"]

    META --> CA["<b>create_all</b> — both metadatas<br/><i>CREATE TABLE IF NOT EXISTS</i>"]
    CA --> REC["<b>reconcile_additive_columns</b><br/><i>columns the models declare<br/>and the database lacks</i>"]
    REC --> AL["<b>_align_timestamp_columns</b><br/><i>naive -> timestamptz,<br/>USING c AT TIME ZONE 'UTC'</i>"]
    AL --> COMMIT["one transaction commits"]
    COMMIT --> RLS["<b>bootstrap_rls</b><br/><i>its own transaction</i>"]

    IMP -.->|"forget one import"| MISS["that class is not in the metadata<br/>-> its table is simply never created<br/><b>no error, no warning</b>"]
    MISS -.-> LATE["the first query against it fails<br/>much later, somewhere else"]
```

DDL is transactional in Postgres, so the four steps in the box either all land or none do —
never a half-migrated schema.

Every step is idempotent, because every worker runs all of it on startup.

The dotted branch is the decorator-registry trap in its ORM form: **a registration in an
unimported module is invisible.**

---

## 4. What the reconciler will and will not do

*Look at the `yes` branch. When it fires, nothing is added at all — not even the safe columns.*

```mermaid
flowchart TB
    START["reconcile_additive_columns"] --> DIA{"postgresql?"}
    DIA -->|no| SKIP(["return an empty list<br/><i>SQLite rebuilds its schema every run</i>"])

    DIA -->|yes| EX["read information_schema<br/><i>what the live database actually has</i>"]
    EX --> PLAN["<b>plan_additive_columns</b><br/><i>pure and database-free,<br/>so it is testable with no Postgres</i>"]

    PLAN --> Q{"any missing column that is a<br/>primary key, or NOT NULL with<br/>no server default?"}

    Q -->|yes| CRIT["log CRITICAL <b>and</b> raise SchemaDriftError<br/><i>no correct value exists for the<br/>rows already in the table</i>"]
    CRIT --> MAIN["main.py re-raises it ahead of the<br/>blanket startup except"]
    MAIN --> REFUSE(["refuse to serve"])

    Q -->|no| DDL["render each column with SQLAlchemy's<br/>own CreateColumn compiler"]
    DDL --> ALTER["ALTER TABLE ... ADD COLUMN IF NOT EXISTS<br/><i>logged at INFO</i>"]
    ALTER --> IDX["create a declared index only if<br/><b>all</b> its columns were added in this pass"]
```

The compiler on the `DDL` edge is the detail worth stealing: a reconciled database and a fresh
one converge, because both went through the same renderer. Hand-written SQL would leave you
with a second schema that exists only in production.

The refusal is right because booting means serving with a ledger whose writes are failing right
now — uncapped, unattributed spend, indefinitely, with every dashboard green.

And the refusal needs both defences on it. **A loud failure caught by a broad handler is a
quiet failure again.**

---

## 5. Why the RLS policy was enforced against nobody

*Follow the `table OWNER` branch — that is the role the application actually connects with.*

```mermaid
flowchart TB
    T["a tenant-scoped table"] --> E["ENABLE ROW LEVEL SECURITY"]
    E --> POL["CREATE POLICY tenant_isolation<br/><i>a numeric scope is bound<br/>-> tenant_id must equal it</i>"]

    POL --> WHO{"which role is querying?"}
    WHO -->|"a non-owner role"| ENF["the policy filters the query"]
    WHO -->|"the table OWNER"| BYP["<b>exempt</b> — every tenant's rows returned"]

    BYP --> SAME["and the app connects with the same<br/>role that ran create_all"]
    SAME --> DEC["the policy was <b>decorative</b>:<br/>visible in pg_policies,<br/>enforced against nobody"]

    DEC --> F["<b>ALTER TABLE ... FORCE ROW LEVEL SECURITY</b>"]
    F --> ENF
```

Every inspection said isolation was on. `pg_policies` showed the policy, the code bound the
scope, and every query returned every tenant's rows.

`FORCE` made the policy real for scoped requests. Requests that bind **no** numeric scope —
login-by-username, platform-admin surfaces — are still unrestricted by design, so read this as
a documented gap rather than a complete control.

**Next:** [`50-interview.md`](50-interview.md).
