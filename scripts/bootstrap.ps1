<#
.SYNOPSIS
  One-shot installer for the TAIF S2 platform (Windows). Installs every backend
  and console (web\) dependency, sets up the .env files, and reports what's ready.
.DESCRIPTION
  Idempotent - safe to re-run. Does NOT need Docker, a GPU, or any database.
  After this, use scripts\start.ps1 to run the app. See docs\RUNBOOK.md.
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Line($ok, $msg) { if ($ok) { Write-Host "  [ OK ] $msg" -f Green } else { Write-Host "  [FAIL] $msg" -f Red } }

Write-Host "`n== Prerequisites ==" -f Cyan
$py = Have 'python'; Line $py "python  (need >= 3.11)"
$node = Have 'node';  Line $node "node    (need >= 18.18)"
$npm = Have 'npm';    Line $npm  "npm     (ships with Node)"
$uv = Have 'uv';      Line $uv   "uv      (pip install uv)"
if (-not ($py -and $node -and $npm -and $uv)) {
  Write-Host "`nInstall the missing tools above, then re-run.`n" -f Yellow; exit 1
}

# -- Backend ------------------------------------------------------------------
Write-Host "`n== Backend ==" -f Cyan
Set-Location "$root\backend"
if (-not (Test-Path '.venv')) { uv venv }
# EVERY extra the backend actually imports. `auth` (JWT/argon2 login + RBAC) and
# `mcp` (the MCP tool-server facade, which needs mcp>=2.0) were missing here, so a
# fresh box came up without them and their tests failed at import.
$extras = 'data,auth,observability,agent,retrieval,ingestion,ml,guardrails,mcp,dev'
Write-Host "  installing backend + all extras ($extras) ..."
uv pip install -e ".[$extras]" | Out-Null
if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env'; Write-Host "  created backend\.env (fill in GENAILAB_API_KEY)" -f Yellow }

# Train the ML spine once, offline, on the real domain frame. NOT optional: the
# train-on-demand fallback was removed because, when the domain adapter was
# unimportable, it fitted the built-in NOISE synthesiser and served that as domain
# evidence. /ml/explain and /ml/model-card now answer 503 until this artifact
# exists. Needs no API key and no database.
if (Test-Path '.artifacts\ml_spine.joblib') {
  Line $true "ML spine already trained"
} else {
  Write-Host "  training the ML spine (offline, ~10s) ..."
  $env:PYTHONPATH = "$root\aegis\src;src"
  .venv\Scripts\python -m app.ml 2>&1 | Out-Null
  if (Test-Path '.artifacts\ml_spine.joblib') { Line $true "ML spine trained" }
  else { Write-Host "  [WARN] ML training failed - /ml/explain will 503. Run by hand: .venv\Scripts\python -m app.ml" -f Yellow }
}
Line $true "backend ready"

# -- Console (web\) -----------------------------------------------------------
Write-Host "`n== Console (web\) ==" -f Cyan
Set-Location "$root\web"
npm install | Out-Null
if (-not (Test-Path '.env.local')) { Copy-Item '.env.example' '.env.local'; Write-Host "  created web\.env.local" -f Yellow }
Line $true "console ready"

Set-Location $root
Write-Host "`nDone. Next:" -f Green
Write-Host "  1) put your key in backend\.env  (GENAILAB_API_KEY=...)"
Write-Host "  2) .\scripts\preflight.ps1        # see what services are up"
Write-Host "  3) .\scripts\start.ps1 -Mode lite # run it (no databases needed)"
Write-Host ""
Write-Host "  Full mode (Postgres) also needs the serving role, or tenant RLS is bypassed:"
Write-Host "     .\scripts\db-roles.ps1         # creates aegis_app, repoints backend\.env`n"
