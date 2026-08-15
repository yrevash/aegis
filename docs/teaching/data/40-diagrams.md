# Data — the diagrams

Diagram 4 is the one to know. It is the clearest picture of how three defensible decisions
compose into an invisible failure.

---

## 1. Where the data layer sits

```mermaid
flowchart TB
    CORE["aegis.core<br/>pydantic + stdlib<br/><b>free — imported by everything</b>"]

    DATA["aegis.data<br/>sqlalchemy&#91;asyncio&#93;<br/><i>the aegis&#91;data&#93; extra</i>"]

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
    MEM --> CORE
    GOV --> CORE
    OPS --> CORE

    GR --> CORE
    VI --> CORE
    VO --> CORE
    FO --> CORE

    GR -.->|"NO ORM"| X1(["never touches aegis.data"])
    VI -.-> X1
    VO -.-> X1
    FO -.-> X1

    CORE -.->|"sqlalchemy is BANNED here"| BAN["the core is imported by everything,<br/>so its dependencies are<br/>everyone's dependencies"]
```

**Modules that persist things pay for the ORM. Modules that do not, do not.**

---

## 2. One schema, two dialects

```mermaid
flowchart TB
    DEF["one declarative definition"] --> D{"dialect at DDL time"}

    D -->|postgresql| PG["jsonb · timestamptz"]
    D -->|"sqlite (tests)"| SQ["JSON · naive-UTC DATETIME"]

    subgraph MECH["two mechanisms"]
        V["with_variant<br/><i>a different TYPE</i>"]
        T["TypeDecorator<br/><i>a different type AND<br/>a value transformation</i>"]
    end

    V --> JB["JsonB = JSON().with_variant(JSONB, 'postgresql')"]
    T --> UD["UtcDateTime — normalises on bind,<br/>returns aware UTC on result"]
    T --> VC["VectorColumn — jsonb / JSON"]

    DEF -.->|"the alternative"| BAD["if dialect == 'postgresql':<br/>scattered through calling code"]
    BAD -.-> WHY["scattered branches are where<br/>behaviour actually DIVERGES<br/>between test and production"]
```

**Express the difference once, at the type.** A dialect branch at the type is a
compile-time detail; a dialect branch at a call site is a behaviour difference.

---

## 3. The naive/aware datetime trap

```mermaid
flowchart TB
    APP["the application is uniformly aware<br/>datetime.now(UTC)"] --> COL{"the column type"}

    COL -->|"TIMESTAMP WITHOUT TIME ZONE<br/><i>the ORM default</i>"| N

    subgraph N["two failure modes"]
        F1["<b>LOUD</b>: asyncpg refuses to encode<br/>an aware datetime for a naive column<br/>-> every write and every<br/>WHERE ts &lt; :now blows up<br/><i>this killed the SLA sweeper</i>"]
        F2["<b>SILENT</b>: server_default=now()<br/>stores the SERVER'S LOCAL WALL CLOCK<br/>-> on TimeZone=Asia/Kolkata every<br/>created_at is +05:30 off,<br/>and the API relabels it +00:00"]
    end

    COL -->|"UtcDateTime"| OK

    subgraph OK["the fix"]
        A1["postgres -> timestamptz"]
        A2["sqlite -> naive UTC<br/><i>an offset would corrupt<br/>LEXICAL ordering</i>"]
        A3["bind: normalise either input form"]
        A4["result: ALWAYS aware UTC"]
    end

    OK --> BASE["applied via type_annotation_map<br/>ON THE BASE"]
    BASE --> CLASS["<b>every Mapped&#91;datetime&#93; on every table</b><br/>— including one written next year<br/>by someone who never heard of this"]
```

**Fixing each column is right today and wrong on the next one someone adds. Fixing the
base is right forever.**

---

## 4. How the ledger silently lost every row

```mermaid
flowchart TB
    ADD["add audio_seconds + images<br/>to the UsageLedger model<br/><i>the ALTER TABLE written<br/>only in a docstring</i>"] --> TEST{"tests"}

    TEST -->|"schema built from scratch<br/>every run"| PASS["all 8 columns · PASS"]

    ADD --> PROD["deploy to a LIVE database"]
    PROD --> CA["create_all runs"]
    CA --> NOOP["<b>CREATE TABLE IF NOT EXISTS</b><br/>the table exists -> does NOTHING"]

    NOOP --> OLD["the live table still has 6 columns"]
    OLD --> INS["every INSERT naming audio_seconds<br/>raises UndefinedColumn"]
    INS --> SWALLOW["_record_usage swallows it<br/><i>usage recording is best-effort<br/>by design — and that design<br/>is CORRECT</i>"]

    SWALLOW --> LOST["the row is lost"]
    LOST --> SUM["USD caps are computed by<br/>SUMMING those rows"]
    SUM --> ZERO["no rows -> no spend -><br/><b>the cap never binds</b>"]

    ZERO --> SILENT["no exception · no failing test ·<br/>no failing request · dashboard green ·<br/>$0.00 looks exactly like<br/>a quiet tenant"]
```

**Three defensible decisions** — no migration framework, best-effort usage recording,
additive defaulted columns — **compose into an invisible, security-relevant failure.**

---

## 5. The additive reconciler

```mermaid
flowchart TB
    B["bootstrap()"] --> CA["create_all — both metadatas"]
    CA --> REC["reconcile_additive_columns"]

    REC --> D{"postgresql?"}
    D -->|no| SKIP["return &#91;&#93; — SQLite rebuilds<br/>its schema every run"]

    D -->|yes| READ["read information_schema<br/>-> every (table, column) that EXISTS"]
    READ --> PLAN["plan_additive_columns<br/><i>pure, database-free, testable</i>"]

    PLAN --> SPLIT{"for each missing column"}
    SPLIT -->|"nullable, or has a server_default"| SAFE["ADDABLE"]
    SPLIT -->|"NOT NULL with no default,<br/>or a primary key"| UNSAFE["UNSAFE — no correct value<br/>exists for the rows already there"]

    UNSAFE --> RAISE["log CRITICAL <b>and</b> raise SchemaDriftError"]
    RAISE --> MAIN["main.py re-raises ahead of the<br/>blanket startup except<br/><b>-> refuses to serve</b>"]

    SAFE --> DDL["render via SQLAlchemy's<br/>CreateColumn compiler"]
    DDL --> ALTER["ALTER TABLE ... ADD COLUMN IF NOT EXISTS"]
    ALTER --> LOG["log at INFO"]
    ALTER --> IDX["create an index only if ALL<br/>its columns were added in this pass"]

    REC --> ALIGN["_align_timestamp_columns<br/>naive -> timestamptz<br/>USING c AT TIME ZONE 'UTC'"]
    ALIGN --> RLS["bootstrap_rls"]
```

**Why the ORM's own compiler renders the DDL:** so a reconciled database and a fresh one
converge. Hand-written SQL would create a second schema that exists only in production —
which is the original bug wearing a different hat.

---

## 6. Why refusing to boot is right, and how the refusal survives

```mermaid
flowchart LR
    DRIFT["unreconcilable drift"] --> C{"boot anyway?"}

    C -->|yes| RUN["serve with a table whose<br/>writes are failing right now"]
    RUN --> INV["ledger unwritable -><br/>no cost attribution -><br/>no binding caps -><br/><b>indefinitely, silently</b>"]

    C -->|no| REF["refuse to serve"]
    REF --> FIX["loud · immediate · fixed in an hour"]

    REF --> P1["defence 1: logged at CRITICAL<br/><i>as well as</i> raised"]
    REF --> P2["defence 2: re-raised ahead of<br/>main.py's blanket startup except"]

    P1 -.->|without it| SWAL["a host wrapping bootstrap in<br/>'the database is optional'<br/>reduces it to a traceback<br/>nobody reads"]
    P2 -.-> SWAL
```

**A loud failure caught by a broad handler is a quiet failure again.**

---

## 7. Vector storage — index or record?

```mermaid
flowchart TB
    Q{"what is this column FOR?"}

    Q -->|"a search INDEX"| IDX["pgvector: vector(n),<br/>distance operators, IVFFlat/HNSW"]
    IDX --> COST1["a Postgres EXTENSION ·<br/>does not exist on SQLite ·<br/>the test schema cannot be created"]

    Q -->|"a source of RECORD"| REC["a list of floats,<br/>stored as JSON"]
    REC --> WIN["jsonb on Postgres · JSON on SQLite ·<br/>NO extension · the schema<br/>materialises identically"]

    REC --> MIRROR["ANN search runs in the embedded<br/>vector store the SQL row mirrors into"]

    REC --> CAVEAT["<b>JSON enforces no dimensionality</b><br/>dim is DOCUMENTATION ·<br/>the mirror skips off-dim rows"]

    CAVEAT -.-> HON["keeping a parameter you cannot<br/>enforce is fine —<br/>letting a reader assume it is<br/>a constraint is not"]
```

---

## 8. RLS — the statement that made the policy real

```mermaid
flowchart TB
    T["a tenant-scoped table"] --> E["ENABLE ROW LEVEL SECURITY"]
    E --> POL["CREATE POLICY tenant_isolation<br/>USING tenant_id = current GUC"]

    POL --> CHECK{"who is connecting?"}
    CHECK -->|"a non-owner role"| ENF["the policy applies"]
    CHECK -->|"the table OWNER"| BYPASS["<b>exempt</b> — every row returned"]

    BYPASS --> REAL["and the app connects with the SAME<br/>role that ran create_all"]
    REAL --> DEC["the policy was DECORATIVE:<br/>enabled, visible in pg_policies,<br/>and enforced against nobody"]

    T --> F["<b>FORCE ROW LEVEL SECURITY</b>"]
    F --> FIXED["the owner is no longer exempt"]

    POL --> NOCHECK["no explicit WITH CHECK<br/>-> Postgres reuses USING for WRITES<br/>-> a cross-tenant INSERT is<br/>REJECTED, not merely hidden"]
```

**Every inspection said "isolation is on."** `pg_policies` showed the policy. The code
bound the scope. And every query returned every tenant's rows.

---

## 9. Registration side effects

```mermaid
flowchart TB
    B["bootstrap()"] --> IMP["import aegis.governance.models<br/>import aegis.ops.models<br/>import app.memory.stores<br/><i>for the side effect ONLY</i>"]

    IMP --> META["their mapped classes register<br/>on AegisBase.metadata"]
    META --> CA["create_all walks the metadata"]
    CA --> TBL["the tables exist"]

    IMP -.->|"forget one import"| MISS["the class is not in the metadata"]
    MISS --> NOTBL["<b>its table is simply never created</b><br/>— no error, no warning"]
    NOTBL --> LATE["the first query against it fails<br/>much later, somewhere else"]
```

The ORM form of the decorator-registry trap: **a registration in an unimported module is
invisible.**

---

**Next:** [`50-interview.md`](50-interview.md).
