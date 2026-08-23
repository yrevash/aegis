-- Provision the Aegis serving role: the connection that CANNOT bypass tenant RLS.
--
-- WHY THIS FILE EXISTS
--   PostgreSQL skips row security entirely for a superuser and for any role holding
--   BYPASSRLS. `FORCE ROW LEVEL SECURITY` removes the table *owner's* exemption, not
--   that one. So a platform that connects as `postgres` installs its tenant_isolation
--   policies, sees them in pg_policies, and is filtered by none of them. Aegis shipped
--   in exactly that state; this role is the fix, and the split between it and the
--   owner/DDL connection is what makes bypass a property of the CONNECTION rather than
--   something application code is trusted to avoid.
--
-- WHAT IT CREATES
--   A LOGIN role (default name `aegis_app`) that is NOSUPERUSER, NOBYPASSRLS,
--   NOCREATEDB, NOCREATEROLE and NOINHERIT; owns nothing; and holds only
--   SELECT/INSERT/UPDATE/DELETE on the application's tables plus USAGE on their
--   sequences. It cannot ALTER TABLE ... DISABLE ROW LEVEL SECURITY, cannot DROP a
--   policy, and cannot create objects — the isolation it is subject to is not its to
--   remove.
--
-- HOW TO RUN IT (as a superuser, against the application database)
--   psql -U postgres -d taif -v role=aegis_app -v pw='<strong-password>' \
--        -f scripts/sql/aegis-app-role.sql
--   or use the wrappers: scripts/db-roles.sh / scripts/db-roles.ps1
--
-- IDEMPOTENT: safe to re-run. CREATE ROLE is guarded; every other statement is a
-- GRANT or an ALTER that converges on the same state.
--
-- AFTERWARDS, in backend/.env:
--   POSTGRES_DSN=postgresql://aegis_app:<pw>@localhost:5432/taif     <- serves requests
--   POSTGRES_ADMIN_DSN=postgresql://postgres:...@localhost:5432/taif <- DDL only
--
-- NOTE ON NEW TABLES: `ALTER DEFAULT PRIVILEGES` below covers tables created *later*
-- by the role running this script. The backend also re-grants on every bootstrap
-- (aegis.governance.rls.grant_serving_role), which covers tables created by a
-- different owner or before this file was ever run. The two are complementary; both
-- are idempotent.

\set ON_ERROR_STOP on

-- 1. The role itself. `pw` is passed with -v so the password never lives in this file.
--    `\gexec` runs the generated statement only when the SELECT returns a row, which is
--    how CREATE ROLE is made idempotent without a DO block — psql does not interpolate
--    its variables inside dollar-quoted bodies, so a DO block here would try to create
--    a role literally named `:'role'`.
SELECT format('CREATE ROLE %I', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role')
\gexec

-- 2. Password plus the attributes that make RLS apply. Stated explicitly (not assumed
--    from the CREATE defaults) and re-applied on every run, so this file also *repairs*
--    a role that was granted SUPERUSER or BYPASSRLS by hand at some point.
SELECT format(
         'ALTER ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEDB '
         'NOCREATEROLE NOREPLICATION NOINHERIT',
         :'role', :'pw')
\gexec

-- 3. Reach the database and its schema, and nothing beyond it.
--    CREATE is granted deliberately: LightRAG's PGKVStorage / PGDocStatusStorage create
--    their own bookkeeping tables at runtime as the *connecting* role, so without it
--    full-stores retrieval fails on PostgreSQL 15+ (where PUBLIC no longer carries
--    schema CREATE). It is not an RLS escape - DISABLE ROW LEVEL SECURITY and DROP
--    POLICY require ownership of the target table, and the governed tables are owned by
--    the DDL role. The alternative, running LightRAG on the owner connection, would put
--    ingested content through a role that bypasses every policy.
GRANT CONNECT ON DATABASE :"DBNAME" TO :"role";
GRANT USAGE, CREATE ON SCHEMA public TO :"role";

-- 4. DML on what exists today. No TRUNCATE, no REFERENCES, no TRIGGER.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"role";

-- 5. The same DML on what this script's runner creates in future (i.e. what the
--    owner/DDL connection's create_all will make on the next boot).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"role";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"role";

-- 5a. Take UPDATE and DELETE back off the three append-only ledgers, and off every
--     partition of them. Step 4 granted `arwd` on ALL TABLES and step 5 will do the
--     same for future ones — both instruments are per-schema and cannot exempt a
--     table, so the only way to end at SELECT+INSERT on these three is to grant
--     everything and take two privileges back afterwards. Without this, re-running
--     this file would silently restore DELETE on the audit trail that the backend's
--     bootstrap (aegis.governance.rls.grant_serving_role) had removed.
--
--     `run_events` is PARTITIONED BY RANGE (ts): PostgreSQL checks privileges on the
--     relation NAMED in the statement, so revoking on the parent alone leaves
--     `DELETE FROM run_events_2026_08` working. The pg_inherits arm covers the months.
--
--     These three and no others: `runs`, `approvals`, `job_runs`, `notifications` and
--     the three `checkpoint*` tables are legitimately rewritten in place, and a revoke
--     there is a `permission denied` in the middle of a request, not a security gain.
--     `memory_write_log` looks append-only and is deliberately excluded: the
--     DPDP/GDPR erasure route (POST /v1/memory/forget) must delete from it, and it is
--     served from a request handler, so the alternative is an RLS-bypassing connection
--     in the request path. See aegis.governance.rls._APPEND_ONLY_TABLES.
SELECT format('REVOKE UPDATE, DELETE ON public.%I FROM %I', c.relname, :'role')
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relkind IN ('r', 'p')
   AND (
        c.relname IN ('audit_log', 'run_events', 'usage_ledger')
     OR EXISTS (
            SELECT 1
              FROM pg_inherits i
              JOIN pg_class p ON p.oid = i.inhparent
             WHERE i.inhrelid = c.oid
               AND p.relname IN ('audit_log', 'run_events', 'usage_ledger')
        )
   )
 ORDER BY c.relname
\gexec

-- 6. Prove it. A row here that says `t` for either column means RLS is still inert.
SELECT rolname       AS serving_role,
       rolsuper      AS is_superuser_MUST_BE_f,
       rolbypassrls  AS bypasses_rls_MUST_BE_f
  FROM pg_roles
 WHERE rolname = :'role';
