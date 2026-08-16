#!/usr/bin/env bash
# Provision the Aegis serving role (macOS/Linux — rehearsal twin of db-roles.ps1).
#
# WHY: PostgreSQL skips row security entirely for a superuser or a BYPASSRLS role, and
# FORCE ROW LEVEL SECURITY only removes the table *owner's* exemption. Serving requests
# as `postgres` therefore leaves all 13 tenant_isolation policies installed and enforced
# against nobody. This script creates the non-superuser role that the policies do apply
# to, and repoints backend/.env at it — keeping the superuser DSN as POSTGRES_ADMIN_DSN
# so DDL (create_all, the schema reconciler, the RLS bootstrap) still has its own,
# separate connection.
#
# Idempotent: re-running rotates the password and re-applies the same grants.
#
#   ./scripts/db-roles.sh                          # defaults, generated password
#   ./scripts/db-roles.sh --password 'hunter2'     # choose the password
#   ./scripts/db-roles.sh --no-env                 # provision only; leave .env alone
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ROLE=aegis_app
DB=taif
PGHOST=localhost
PGPORT=5432
SUPERUSER=postgres
PASSWORD=""
WRITE_ENV=1

while [ $# -gt 0 ]; do
  case "$1" in
    --role)       ROLE="$2"; shift 2 ;;
    --database)   DB="$2"; shift 2 ;;
    --host)       PGHOST="$2"; shift 2 ;;
    --port)       PGPORT="$2"; shift 2 ;;
    --superuser)  SUPERUSER="$2"; shift 2 ;;
    --password)   PASSWORD="$2"; shift 2 ;;
    --no-env)     WRITE_ENV=0; shift ;;
    -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

command -v psql >/dev/null 2>&1 || { echo "psql not found — install the PostgreSQL client tools" >&2; exit 1; }

# A generated password beats a documented one: nothing memorable ever ends up in git.
[ -n "$PASSWORD" ] || PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"

echo "== Provisioning serving role '$ROLE' in $DB =="
psql -v ON_ERROR_STOP=1 -U "$SUPERUSER" -h "$PGHOST" -p "$PGPORT" -d "$DB" \
     -v role="$ROLE" -v pw="$PASSWORD" \
     -f "$ROOT/scripts/sql/aegis-app-role.sql"

DSN="postgresql://$ROLE:$PASSWORD@$PGHOST:$PGPORT/$DB"
ADMIN_DSN="postgresql://$SUPERUSER@$PGHOST:$PGPORT/$DB"

if [ "$WRITE_ENV" -eq 1 ]; then
  # Seed backend/.env if it does not exist yet: this script can legitimately run before
  # bootstrap.sh (the installer provisions stores before app dependencies), and writing
  # the DSNs into a file nobody created would lose them.
  [ -f "$ROOT/backend/.env" ] || cp "$ROOT/backend/.env.example" "$ROOT/backend/.env"
  # Rewrite POSTGRES_DSN in place and add POSTGRES_ADMIN_DSN if absent. Done in Python
  # so a generated password containing regex/sed metacharacters cannot corrupt the file.
  ENV_FILE="$ROOT/backend/.env" ENV_DSN="$DSN" ENV_ADMIN="$ADMIN_DSN" python3 - <<'PY'
import os, pathlib
path = pathlib.Path(os.environ["ENV_FILE"])
if not path.exists():
    print(f"  backend/.env not found — add these lines by hand:\n"
          f"    POSTGRES_DSN={os.environ['ENV_DSN']}\n"
          f"    POSTGRES_ADMIN_DSN={os.environ['ENV_ADMIN']}")
    raise SystemExit(0)
lines = path.read_text().splitlines()
out, seen_dsn, seen_admin = [], False, False
for line in lines:
    if line.startswith("POSTGRES_DSN="):
        out.append("POSTGRES_DSN=" + os.environ["ENV_DSN"]); seen_dsn = True
    elif line.startswith("POSTGRES_ADMIN_DSN="):
        out.append("POSTGRES_ADMIN_DSN=" + os.environ["ENV_ADMIN"]); seen_admin = True
    else:
        out.append(line)
if not seen_dsn:
    out.append("POSTGRES_DSN=" + os.environ["ENV_DSN"])
if not seen_admin:
    out.append("POSTGRES_ADMIN_DSN=" + os.environ["ENV_ADMIN"])
path.write_text("\n".join(out) + "\n")
print("  backend/.env updated: POSTGRES_DSN -> serving role, POSTGRES_ADMIN_DSN -> owner")
PY
else
  echo "  POSTGRES_DSN=$DSN"
  echo "  POSTGRES_ADMIN_DSN=$ADMIN_DSN"
fi

echo
echo "Done. The serving role cannot bypass RLS; the owner DSN is for DDL only."
echo "Verify with:  ./scripts/preflight.sh   (row 'RLS serving role')"
