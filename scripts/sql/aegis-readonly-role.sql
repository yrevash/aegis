-- Provision the Aegis DATABASE CONSOLE's role: the connection that CANNOT WRITE.
--
-- WHY THIS FILE EXISTS
--   §7.9 needed a way to look at the data without dropping out of the product into
--   `psql`. The naive implementation of that — `await conn.execute(user_sql)` on the
--   application connection — is unsafe in five measured ways, and the first and largest
--   is that the application connection holds INSERT, UPDATE and DELETE. So the console
--   gets a third role, beside the owner/DDL role and `aegis_app`: SELECT and nothing
--   else, owning nothing, NOSUPERUSER NOBYPASSRLS, with `users.password_hash` withheld
--   by a COLUMN grant rather than by a denylist in application code.
--
--   The column grant is the interesting part. `information_schema` respects column-level
--   privileges, so withholding the column removes it from the schema browser, from every
--   generated projection and from every predicate at once — the permission model IS the
--   browser's source of truth, and there is nothing left to drift.
--
--   `SET ROLE` on the application connection would have been simpler and is not a
--   boundary: `RESET ROLE` is one legal statement, and the §7.9 probe used it to walk
--   from the read-only role back to the superuser and read `pg_authid`. Hence a
--   separately authenticated role with its own DSN and its own pool.
--
-- THIS FILE IS GENERATED. The source of truth is `aegis/src/aegis/dbadmin/provision.py`,
-- and it is checked in so a DBA can read the DDL before it runs:
--
--   python -m aegis.dbadmin --role aegis_readonly --password '<strong-password>'
--
-- HOW TO RUN IT (as the OWNER of the Aegis tables — granting requires ownership, which
-- is the property that makes the split meaningful)
--
--   psql -U postgres -d taif -f scripts/sql/aegis-readonly-role.sql
--
-- Replace REPLACE-ME below with a real password first, or regenerate with your own.
--
-- IDEMPOTENT, and expected to be RE-RUN AFTER EVERY SCHEMA CHANGE: a table created since
-- the last run carries no grant for this role, so the console simply does not list it —
-- which is correct, and easy to misread as a missing table.
--
-- AFTERWARDS, in backend/.env:
--   AEGIS_DB_CONSOLE_ENABLED=true
--   AEGIS_DB_CONSOLE_DSN=postgresql://aegis_readonly:<pw>@localhost:5432/taif
--
-- The backend re-reads this role's privileges over the same connection before every
-- request (`aegis.dbadmin.runner.verify_posture`) and refuses to serve one that can
-- write, so a DSN pointed at the wrong role is a refusal, not a hole.

\set ON_ERROR_STOP on

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aegis_readonly') THEN
    CREATE ROLE aegis_readonly LOGIN PASSWORD 'REPLACE-ME';
  END IF;
END $$;

ALTER ROLE aegis_readonly LOGIN PASSWORD 'REPLACE-ME'
  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;

ALTER ROLE aegis_readonly SET statement_timeout = '10s';

ALTER ROLE aegis_readonly SET idle_in_transaction_session_timeout = '30s';

-- A guard rail, not the boundary: this role can turn it off itself (finding 4).
ALTER ROLE aegis_readonly SET default_transaction_read_only = on;

GRANT USAGE ON SCHEMA public TO aegis_readonly;

-- No CREATE on the schema: this role owns nothing and can make nothing.
REVOKE CREATE ON SCHEMA public FROM aegis_readonly;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO aegis_readonly;

-- Withhold users.password_hash: revoke the table-wide SELECT and re-grant
-- every other column. information_schema then stops listing it, so the
-- schema browser and every generated projection lose it with nothing to
-- drift (finding 5).
REVOKE SELECT ON TABLE users FROM aegis_readonly;

DO $$
DECLARE cols text;
BEGIN
  SELECT string_agg(format('%I', attname), ', ' ORDER BY attnum)
    INTO cols
    FROM pg_attribute
   WHERE attrelid = 'users'::regclass
     AND attnum > 0 AND NOT attisdropped
     AND attname <> ALL (ARRAY['password_hash']);
  EXECUTE format('GRANT SELECT (%s) ON TABLE users TO aegis_readonly', cols);
END $$;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO aegis_readonly;

-- Belt: take back anything a broader default privilege may have granted.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON ALL TABLES IN SCHEMA public FROM aegis_readonly;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES
  FROM aegis_readonly;

-- Prove it. `t` in either flag column means the console will refuse to run:
-- aegis.dbadmin.runner.verify_posture re-reads exactly this before every query.
SELECT rolname AS console_role,
       rolsuper     AS is_superuser_MUST_BE_f,
       rolbypassrls AS bypasses_rls_MUST_BE_f
  FROM pg_roles WHERE rolname = 'aegis_readonly';
