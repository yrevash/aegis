#!/usr/bin/env bash
# Start the Aegis backend against the REAL native stores (no Docker):
#   Postgres (taif db) · Redis · Neo4j — all NATIVE, never Docker.
# The vector store is EMBEDDED (in-process, file-backed): no server, nothing to start.
# Runs the backend in the background, waits for /health, and prints status.
# The web app is started separately (Node runs fine everywhere).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG=/tmp/aegis-backend.log

echo "== Aegis dev-native bring-up =="

# ── Redis ────────────────────────────────────────────────────────────────────
redis-cli ping >/dev/null 2>&1 || redis-server --daemonize yes --port 6379 >/dev/null 2>&1
printf '  redis      : %s\n' "$(redis-cli ping 2>/dev/null || echo DOWN)"

# ── Neo4j (best-effort — the backend boots even if this is down) ──────────────
if command -v neo4j >/dev/null 2>&1; then
  neo4j-admin dbms set-initial-password aegisdev1 >/dev/null 2>&1 || true
  neo4j start >/dev/null 2>&1 || true
elif command -v brew >/dev/null 2>&1 && brew services list 2>/dev/null | grep -qi neo4j; then
  brew services start neo4j >/dev/null 2>&1 || true
fi
if (exec 3<>/dev/tcp/localhost/7687) 2>/dev/null; then exec 3>&- 3<&-; echo "  neo4j      : UP (7687)"; else
  echo "  neo4j      : down (non-blocking — graph retrieval degrades, dashboards fine)"; fi

# ── Vector store (EMBEDDED — nothing to start) ────────────────────────────────
# The ANN engine behind retrieval + memory recall runs in-process against a local
# directory (Chroma PersistentClient; LightRAG's own vectors sit in NanoVectorDB under
# its working_dir). There is no server binary and no port, so there is nothing to launch
# here — only a directory to make sure exists and is writable.
VECTOR_STORE_PATH="${VECTOR_STORE_PATH:-$ROOT/backend/vector_storage}"
export VECTOR_STORE_PATH
if mkdir -p "$VECTOR_STORE_PATH" 2>/dev/null && [ -w "$VECTOR_STORE_PATH" ]; then
  echo "  vectors    : embedded, writable ($VECTOR_STORE_PATH)"
else
  echo "  vectors    : NOT WRITABLE ($VECTOR_STORE_PATH) — the backend will refuse to boot"
fi

# ── Postgres check (started/prepared already) ────────────────────────────────
if psql -h 127.0.0.1 -p 5432 -U postgres -d taif -tAc 'select 1' >/dev/null 2>&1; then
  echo "  postgres   : UP (taif)"
else
  echo "  postgres   : DOWN — start it, then: createdb + role postgres"
fi

# ── Backend ──────────────────────────────────────────────────────────────────
echo "  starting backend (uvicorn :8000) → $LOG"
cd "$ROOT/backend"
# One-time: strip the macOS quarantine flag so Gatekeeper doesn't do a (blocking)
# network verification on each compiled wheel the first time it loads.
xattr -dr com.apple.quarantine .venv 2>/dev/null || true
# The importable `aegis` core is a src-layout sibling package — put it on the path.
export PYTHONPATH="$ROOT/aegis/src${PYTHONPATH:+:$PYTHONPATH}"
# Offline-friendly: use LiteLLM's bundled price map and skip HF/telemetry network
# calls at import (they otherwise stall a network-restricted box).
export LITELLM_LOCAL_MODEL_COST_MAP=True HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
# Kill any stale instance on 8000.
pkill -f "uvicorn app.main:app" 2>/dev/null || true
# Redirect stdin from /dev/null — some deep imports block on a stdin read otherwise.
nohup .venv/bin/python -m uvicorn app.main:app --app-dir src --port 8000 --host 127.0.0.1 > "$LOG" 2>&1 < /dev/null &
echo "  backend pid: $!"

# ── Wait for health ──────────────────────────────────────────────────────────
for i in $(seq 1 40); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8000/health 2>/dev/null)" = "200" ]; then
    echo "  health     : 200 (up in ${i}s)"; break
  fi
  sleep 1
done
if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8000/health 2>/dev/null)" != "200" ]; then
  echo "  health     : NOT UP — last log lines:"; tail -20 "$LOG"
else
  echo
  echo "Backend ready → http://localhost:8000  (docs: /docs)"
  echo "Now start the web app (in another terminal or let Claude do it):"
  echo "  cd $ROOT/web && NEXT_PUBLIC_API_BASE=http://localhost:8000 NEXT_PUBLIC_HEALTH_PATH=/health npx next dev -p 3000"
fi
