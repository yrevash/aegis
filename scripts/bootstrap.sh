#!/usr/bin/env bash
# One-shot installer for the TAIF S2 platform (macOS/Linux — rehearsal twin of
# bootstrap.ps1). Installs every backend + console (web/) dependency and the .env
# files. Idempotent. No Docker, GPU, or database required. See docs/operations/runbook.md.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ok()   { printf '  \033[32m[ OK ]\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m[FAIL]\033[0m %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

echo -e "\n== Prerequisites =="
miss=0
for t in python3:need-3.11+ node:need-18.18+ npm:ships-with-node uv:pip-install-uv; do
  cmd="${t%%:*}"; hint="${t##*:}"
  if have "$cmd"; then ok "$cmd"; else fail "$cmd ($hint)"; miss=1; fi
done
[ "$miss" -eq 0 ] || { echo -e "\nInstall the missing tools, then re-run.\n"; exit 1; }

echo -e "\n== Backend =="
cd "$ROOT/backend"
[ -d .venv ] || uv venv
# EVERY extra the backend actually imports. `auth` (JWT/argon2 login + RBAC) and
# `mcp` (the MCP tool-server facade, which needs mcp>=2.0) were missing here, so a
# fresh box came up without them and their tests failed at import.
extras='data,auth,observability,agent,retrieval,ml,guardrails,mcp,dev'
echo "  installing backend + all extras ($extras) ..."
uv pip install -e ".[$extras]" >/dev/null
[ -f .env ] || { cp .env.example .env; echo "  created backend/.env (fill in GENAILAB_API_KEY)"; }

# Train the ML spine once, offline, on the real domain frame.
#
# This is NOT optional any more. `get_model()` used to train on demand inside
# whatever request arrived first, and when the domain adapter was unimportable it
# trained on the built-in NOISE synthesiser and served that as domain evidence —
# a point prediction, a "90% coverage" interval and feature_0..3 drivers, with no
# way for a caller to tell it apart from a real model. That fallback is gone, so
# /ml/explain and /ml/model-card now answer 503 until the artifact exists.
#
# Needs no API key and no database: it fits locally from the adapter's frame.
if [ -f ".artifacts/ml_spine.joblib" ]; then
  ok "ML spine already trained (.artifacts/ml_spine.joblib)"
else
  echo "  training the ML spine (offline, ~10s) ..."
  if PYTHONPATH="$ROOT/aegis/src:src" .venv/bin/python -m app.ml >/dev/null 2>&1; then
    ok "ML spine trained"
  else
    echo "  [WARN] ML spine training failed - /ml/explain and /ml/model-card will 503."
    echo "         Re-run by hand to see why:  cd backend && .venv/bin/python -m app.ml"
  fi
fi
ok "backend ready"

echo -e "\n== Console (web/) =="
cd "$ROOT/web"
npm install >/dev/null 2>&1
[ -f .env.local ] || { cp .env.example .env.local; echo "  created web/.env.local"; }
ok "console ready"

cd "$ROOT"
echo -e "\n\033[32mDone.\033[0m Next:"
echo "  1) put your key in backend/.env  (GENAILAB_API_KEY=...)"
echo "  2) ./scripts/preflight.sh"
echo "  3) ./scripts/start.sh lite"
echo
