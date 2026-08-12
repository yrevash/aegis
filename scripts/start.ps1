<#
.SYNOPSIS
  Start the TAIF S2 platform (Windows). Opens the backend and console, each in
  its own window, and prints the URLs.
.PARAMETER Mode
  safe : console only, mock transport — no backend, no infra. Can't-fail demo.
  lite : backend with NO databases (STORES=off, SQLite audit) + live console.  [default]
  full : backend with all stores (Postgres/pgvector + Neo4j + Redis) + live console.
.EXAMPLE
  .\scripts\start.ps1 -Mode lite
#>
param([ValidateSet('safe','lite','full')][string]$Mode = 'lite')

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Write-Host "`n== Starting in '$Mode' mode ==" -f Cyan

# ── Console env (mock vs live) ───────────────────────────────────────────────
if ($Mode -eq 'safe') { $useMock = 'true' } else { $useMock = 'false' }

# ── Backend (skipped in safe mode) ───────────────────────────────────────────
if ($Mode -ne 'safe') {
  $env:DB_BOOTSTRAP = 'true'
  if ($Mode -eq 'lite') {
    $env:STORES = 'off'
    $env:POSTGRES_DSN = 'sqlite+aiosqlite:///./taif_lite.db'
    $env:PHOENIX_ENABLED = 'false'
  } else {
    $env:STORES = 'on'
  }
  $backendCmd = "Set-Location '$root\backend'; " +
    "`$env:STORES='$($env:STORES)'; `$env:DB_BOOTSTRAP='true'; " +
    "`$env:POSTGRES_DSN='$($env:POSTGRES_DSN)'; `$env:PHOENIX_ENABLED='$($env:PHOENIX_ENABLED)'; " +
    ".venv\Scripts\python -m uvicorn app.main:app --app-dir src --port 8000"
  Start-Process powershell -ArgumentList '-NoExit','-Command', $backendCmd
  Write-Host "  backend  -> http://localhost:8000  (docs at /docs)" -f Green
}

# ── Console (web\) ───────────────────────────────────────────────────────────
$consoleCmd = "Set-Location '$root\web'; " +
  "`$env:NEXT_PUBLIC_USE_MOCK='$useMock'; `$env:NEXT_PUBLIC_API_BASE='http://localhost:8000'; " +
  "`$env:NEXT_PUBLIC_HEALTH_PATH='/health'; " +
  "npm run dev -- --port 3000"
Start-Process powershell -ArgumentList '-NoExit','-Command', $consoleCmd
Write-Host "  console  -> http://localhost:3000  (login admin/admin)" -f Green

Write-Host "`nTwo windows opened. Close them to stop.`n" -f Yellow
if ($Mode -eq 'lite') { Write-Host "Lite mode: no databases needed. Only GENAILAB_API_KEY (in backend\.env) is required for real answers.`n" -f Cyan }
