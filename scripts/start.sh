#!/usr/bin/env bash
# Start the TAIF S2 platform (macOS/Linux — rehearsal twin of start.ps1).
# Usage: ./scripts/start.sh [safe|lite|full]   (default: lite)
#   safe : console only, mock transport — no backend, no infra.
#   lite : backend with NO databases (STORES=off, SQLite audit) + live console.
#   full : backend with all stores (Postgres + Neo4j + Redis; vectors are embedded).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-lite}"
echo -e "\n== Starting in '$MODE' mode =="

pids=()
cleanup() { echo -e "\nstopping..."; for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup INT TERM EXIT

if [ "$MODE" = "safe" ]; then USE_MOCK=true; else USE_MOCK=false; fi

if [ "$MODE" != "safe" ]; then
  ( cd "$ROOT/backend"
    export DB_BOOTSTRAP=true
    if [ "$MODE" = "lite" ]; then
      export STORES=off POSTGRES_DSN='sqlite+aiosqlite:///./taif_lite.db' PHOENIX_ENABLED=false
    else
      export STORES=on
    fi
    exec .venv/bin/python -m uvicorn app.main:app --app-dir src --port 8000
  ) & pids+=($!)
  echo "  backend  -> http://localhost:8000  (docs at /docs)"
fi

( cd "$ROOT/web"
  NEXT_PUBLIC_USE_MOCK="$USE_MOCK" NEXT_PUBLIC_API_BASE='http://localhost:8000' \
    NEXT_PUBLIC_HEALTH_PATH='/health' exec npm run dev -- --port 3000
) & pids+=($!)
echo "  console  -> http://localhost:3000  (login admin/demo)"

[ "$MODE" = "lite" ] && echo -e "\nLite mode: no databases needed; only GENAILAB_API_KEY (backend/.env) for real answers."
echo -e "\nCtrl-C to stop both.\n"
wait
