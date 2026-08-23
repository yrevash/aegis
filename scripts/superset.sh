#!/usr/bin/env bash
# Build, seed and run the Aegis Superset instance.
#
# WHY THIS EXISTS: the first instance was installed into a temp directory and a
# restart destroyed it — every registered dataset, every chart, and the embedded
# dashboard. Only `docs/operations/superset/` survived, because that is in git.
# This script rebuilds the whole thing from that bundle, into `.superset/` inside
# the project, where a reboot cannot reach it.
#
#   scripts/superset.sh install   # venv + metadata DB + admin + roles  (~5 min)
#   scripts/superset.sh import    # load the asset bundle from docs/
#   scripts/superset.sh start     # run it on :8088
#   scripts/superset.sh stop
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="$ROOT/.superset"
VENV="$HOME_DIR/venv"
export SUPERSET_CONFIG_PATH="$HOME_DIR/superset_config.py"
export FLASK_APP=superset
SUPERSET="$VENV/bin/superset"

install() {
  mkdir -p "$HOME_DIR"
  uv venv --python 3.11 "$VENV"
  # psycopg2 is NOT bundled: without it every dataset call 500s with
  # "No module named 'psycopg2'", while the dataset *list* still renders fine
  # because listing reads Superset's own metadata DB and never touches Postgres.
  #  is imported by superset/cli/test_db.py but is not declared as a
  # dependency of the 6.1.0 wheel, so every CLI invocation dies on
  # ModuleNotFoundError before doing anything.
  VIRTUAL_ENV="$VENV" uv pip install "apache-superset==6.1.0" psycopg2-binary rich cachetools
  "$SUPERSET" db upgrade
  "$SUPERSET" fab create-admin --username admin --firstname A --lastname D \
      --email admin@aegis.local --password admin || true
  # `superset init` syncs role<->permission tables. Skipping it is what produces
  # a bare "Forbidden" on every dashboard with no other clue.
  "$SUPERSET" init
  echo "installed → $VENV"
}

import_assets() {
  local pw="${AEGIS_SUPERSET_DB_PASSWORD:?set AEGIS_SUPERSET_DB_PASSWORD to the aegis_superset role password}"
  local staged; staged="$(mktemp -d)"
  cp -R "$ROOT/docs/operations/superset/"* "$staged/"
  rm -f "$staged/aegis-boards.json"
  # Which Postgres the boards read. It MUST be the database the backend serves from:
  # Superset pointed at a different one is not a broken chart, it is a chart that
  # renders confidently with another deployment's numbers. That happened — analytics
  # reported 88.11 spend and 9 red-team runs against a real 4.16 and 7, because this
  # bundle hardcoded a database name the backend had since moved off. Defaults to the
  # database in POSTGRES_DSN when it is set, so the two cannot drift by default.
  local dbname="${AEGIS_SUPERSET_DB_NAME:-}"
  if [ -z "$dbname" ]; then
    dbname="$(printf '%s' "${POSTGRES_DSN:-}" | sed -n 's#.*/\([^/?]*\).*#\1#p')"
  fi
  : "${dbname:?set AEGIS_SUPERSET_DB_NAME (or POSTGRES_DSN) to the database the backend serves from}"
  echo "superset boards will read database: $dbname"
  # The committed bundle ships REPLACE-ME / REPLACE-DB rather than real values.
  "$VENV/bin/python" - "$staged" "$pw" "$dbname" <<'PY'
import pathlib, re, sys
staged, pw, dbname = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
f = staged / "databases" / "Aegis.yaml"
text = re.sub(r"(postgresql\+psycopg2://aegis_superset:)[^@]+@", rf"\1{pw}@", f.read_text())
f.write_text(text.replace("/REPLACE-DB'", f"/{dbname}'"))
PY
  "$SUPERSET" import-directory -o "$staged"
  rm -rf "$staged"
  echo "imported. Now paste the numeric dataset + dashboard ids into"
  echo "  docs/operations/superset/aegis-boards.json"
  echo "and register the dashboard for embedding:"
  echo "  POST /api/v1/dashboard/<id>/embedded  {\"allowed_domains\": [...]}"
}

start() {
  [ -x "$SUPERSET" ] || { echo "not installed; run: scripts/superset.sh install" >&2; exit 1; }
  nohup "$SUPERSET" run -p 8088 --host 127.0.0.1 --with-threads \
      > "$HOME_DIR/superset.log" 2>&1 &
  echo "starting on :8088 (log: $HOME_DIR/superset.log)"
}

stop() { pkill -f "$VENV/bin/superset" || true; echo stopped; }

case "${1:-}" in
  install) install ;;
  import)  import_assets ;;
  start)   start ;;
  stop)    stop ;;
  *) sed -n '2,14p' "$0"; exit 1 ;;
esac
