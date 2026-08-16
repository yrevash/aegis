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

# Is tenant isolation actually ON? An open 5432 says nothing about it: Postgres skips
# row security entirely for a superuser, so serving as `postgres` leaves all 13
# tenant_isolation policies installed and enforced against nobody. This row asks the
# database the same question the backend asks at boot (app.data.rls_check), so the
# day-of board and the running platform cannot disagree.
rls_note='backend/.venv missing — run scripts/bootstrap.sh'
rls=1
if [ -x "$ROOT/backend/.venv/bin/python" ]; then
  rls_note="$( cd "$ROOT/backend" && PYTHONPATH="$ROOT/aegis/src:src" \
                 .venv/bin/python -m app.data.rls_check 2>/dev/null )"
  [ $? -eq 0 ] && rls=0
  [ -n "$rls_note" ] || rls_note='rls_check produced no output'
fi
row "$rls" "RLS serving role" "$rls_note"

# Regression gate (DeepEval-pattern) — offline CI gate over the seed corpus + the
# agentic router case. No network, no keys, no stores; a nonzero exit FAILS preflight.
gate=1
if [ -x "$ROOT/backend/.venv/bin/python" ]; then
  ( cd "$ROOT/backend" && PYTHONPATH=src .venv/bin/python -m app.eval.regression ) >/dev/null 2>&1 && gate=0
  row "$gate" "Regression gate" "DeepEval-pattern eval (offline)"
else
  row 1 "Regression gate" "backend/.venv missing — run scripts/bootstrap.sh"
fi

echo -e "\nGuide:"
echo "  gateway UP -> lite works (real agent, no databases):  ./scripts/start.sh lite"
echo "  all UP     -> full mode:                               ./scripts/start.sh full"
echo "  nothing    -> demo-safe (mock, always works):          ./scripts/start.sh safe"
echo

# Service probes above are informational (which run mode is available); the
# regression gate is a real pass/fail bar, so its result is preflight's exit code.
exit "$gate"
