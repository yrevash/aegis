#!/usr/bin/env bash
# Day-of readiness board (macOS/Linux — rehearsal twin of preflight.ps1).
# Read-only: probes each service and prints UP/DOWN. Changes nothing.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

row() { # $1=ok(0/1) $2=name $3=note
  if [ "$1" -eq 0 ]; then printf '  \033[32m[UP  ]\033[0m %-20s %s\n' "$2" "$3";
  else printf '  \033[31m[DOWN]\033[0m %-20s %s\n' "$2" "$3"; fi
}
port() { # returns 0 if host:port open
  (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null && { exec 3>&- 3<&-; return 0; } || return 1
}

echo -e "\n== Preflight =="

# Gateway — the only thing lite mode needs. Read key/base from backend/.env.
KEY=""; BASE="https://genailab.tcs.in"
if [ -f "$ROOT/backend/.env" ]; then
  KEY="$(grep -E '^GENAILAB_API_KEY=' "$ROOT/backend/.env" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
  b="$(grep -E '^GENAILAB_BASE_URL=' "$ROOT/backend/.env" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
  [ -n "$b" ] && BASE="$b"
fi
gw=1
if [ -n "$KEY" ] && [ "$KEY" != "replace-me" ]; then
  code="$(curl -sk -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KEY" --max-time 10 "$BASE/models" 2>/dev/null || echo 000)"
  [ "$code" = "200" ] && gw=0
  row "$gw" "Model gateway" "$BASE (HTTP $code)"
else
  row 1 "Model gateway" "no key in backend/.env"
fi

port localhost 5432; row $? "Postgres (5432)" "full mode only"
port localhost 7687; row $? "Neo4j (7687)"    "full mode only"
port localhost 6379; row $? "Redis (6379)"    "full mode only"

echo -e "\nGuide:"
echo "  gateway UP -> lite works (real agent, no databases):  ./scripts/start.sh lite"
echo "  all UP     -> full mode:                               ./scripts/start.sh full"
echo "  nothing    -> demo-safe (mock, always works):          ./scripts/start.sh safe"
echo
