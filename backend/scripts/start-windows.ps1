#Requires -Version 5.1
<#
.SYNOPSIS
  Start Aegis on Windows: check the stores, bring up what can be brought up, then
  run the API and the console.

.DESCRIPTION
  The Windows install is native -- no Docker, no WSL, no compose file -- so "start
  the stack" is four services with four different ways of being started, and this
  script knows which is which:

    * PostgreSQL and Memurai are Windows SERVICES. They start on boot, and when
      they are not running the fix is `Start-Service`, which this script does.
    * Qdrant is a BARE BINARY from a zip. Nothing supervises it, so it is launched
      in its own window and dies with that window.
    * Temporal is the CLI's dev server, same story.
    * Neo4j Desktop is a GUI that cannot be scripted. An instance is created by
      hand, once, and the password is chosen in that dialog. This script can only
      look at 7687 and tell you.

  What it will not do is pretend. A store that is down is reported as down; the
  API is only started when the things it *requires* are actually answering, and
  the one store that is genuinely optional (Neo4j) is called optional rather than
  quietly skipped.

  There is deliberately no SQLite mode. `scripts\start.ps1 -Mode lite` used to
  repoint POSTGRES_DSN at `sqlite+aiosqlite:///./taif_lite.db`, which is the exact
  shape of the failure this project refuses: the platform reports "running" while
  serving from a different database than the one it names, with none of the RLS
  the tenant isolation story depends on. If Postgres is down, that is a fact to
  fix, not a fact to route around.

.PARAMETER Skip
  Do not start the console (-Skip Web) or the API (-Skip Api). Both still get
  their readiness reported. Use -Skip Web when you only want the API for tests.

.PARAMETER NoInfra
  Never touch the stores -- only check them. For when Postgres and Memurai are
  managed outside this repo and you do not want a script starting services.

.EXAMPLE
  .\backend\scripts\start-windows.ps1
  Start everything: services, Qdrant, Temporal, API, console.

.EXAMPLE
  .\backend\scripts\start-windows.ps1 -Skip Web
  API only -- the console is not launched.

.NOTES
  Stop it again with .\backend\scripts\stop-windows.ps1
#>
[CmdletBinding()]
param(
  [ValidateSet('Web', 'Api')][string[]]$Skip = @(),
  [switch]$NoInfra
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # ...\aegis
$Backend = Join-Path $Root 'backend'
$Web = Join-Path $Root 'web'

# The ports every message in this file refers to. Changing a port here changes
# nothing else -- these are what the scripts *check*; the API's own port is passed
# on its command line below and the console reads NEXT_PUBLIC_API_BASE.
$PORT_API = 8000
$PORT_WEB = 3000
$PORT_PG = 5432
$PORT_REDIS = 6379
$PORT_NEO4J = 7687
$PORT_QDRANT = 6333
$PORT_TEMPORAL = 7233

function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Head([string]$m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok([string]$m) { Write-Host "  [ ok ] $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Bad([string]$m) { Write-Host "  [FAIL] $m" -ForegroundColor Red }

<#
  Is something listening on this port?

  `Test-NetConnection` is the obvious call and the wrong one: it is slow, it
  writes a progress bar into automation output, and on a closed port it waits out
  a full TCP timeout. A raw socket with an explicit deadline answers in
  milliseconds and is the same question.
#>
function Test-Port([int]$Port, [int]$TimeoutMs = 400) {
  $client = New-Object Net.Sockets.TcpClient
  try {
    $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
    if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
    $client.EndConnect($async)
    return $true
  } catch { return $false } finally { $client.Close() }
}

<#
  Start a Windows service by name pattern, if it is installed and not running.

  Returns $true when the port answers afterwards. Memurai and PostgreSQL both
  register services whose exact names vary by version -- "Memurai", "postgresql-x64-16"
  -- so the caller passes a pattern rather than a name.
#>
function Start-StoreService([string]$Pattern, [int]$Port, [string]$Label) {
  if (Test-Port $Port) { Ok "$Label already up on $Port"; return $true }
  if ($NoInfra) { Bad "$Label is down on $Port (-NoInfra, so not starting it)"; return $false }

  $svc = Get-Service -Name $Pattern -ErrorAction SilentlyContinue |
         Where-Object { $_.Status -ne 'Running' } | Select-Object -First 1
  if (-not $svc) {
    Bad "$Label is not answering on $Port and no stopped service matches '$Pattern'"
    return $false
  }

  Say "  starting service $($svc.Name)..."
  try {
    Start-Service -Name $svc.Name -ErrorAction Stop
  } catch {
    Bad "$Label service '$($svc.Name)' would not start: $($_.Exception.Message)"
    Warn '  an elevated PowerShell is usually what this needs'
    return $false
  }

  # A service reports Running before it is accepting connections.
  foreach ($i in 1..20) { if (Test-Port $Port) { break }; Start-Sleep -Milliseconds 250 }
  if (Test-Port $Port) { Ok "$Label up on $Port"; return $true }
  Bad "$Label service started but nothing is listening on $Port"
  return $false
}

<#
  Launch a bare binary in its own window, if its port is not already answering.

  Qdrant and Temporal have no service wrapper: they are processes that live in a
  window. That is why `stop-windows.ps1` has to find them by port rather than by
  asking a service manager.
#>
function Start-Detached([string]$Exe, [string]$Arguments, [int]$Port, [string]$Label, [string]$WorkDir = $Root) {
  if (Test-Port $Port) { Ok "$Label already up on $Port"; return $true }
  if ($NoInfra) { Bad "$Label is down on $Port (-NoInfra, so not starting it)"; return $false }

  $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
  if (-not $cmd) {
    Bad "$Label is down on $Port and '$Exe' is not on PATH"
    return $false
  }

  Say "  launching $Exe..."
  Start-Process -FilePath $cmd.Source -ArgumentList $Arguments -WorkingDirectory $WorkDir | Out-Null
  foreach ($i in 1..40) { if (Test-Port $Port) { break }; Start-Sleep -Milliseconds 250 }
  if (Test-Port $Port) { Ok "$Label up on $Port"; return $true }
  Bad "$Label did not come up on $Port within 10s -- check the window it opened"
  return $false
}

# -----------------------------------------------------------------------------
Head 'Stores'

$pgOk = Start-StoreService 'postgresql*' $PORT_PG 'PostgreSQL'

# Memurai is the Redis-compatible server for Windows. Its service is "Memurai",
# its CLI is `memurai-cli`, and nothing about it answers to the name redis -- but
# it speaks the same wire protocol on the same port, so every REDIS_URL in this
# repo points at it unchanged.
$redisOk = Start-StoreService 'Memurai*' $PORT_REDIS 'Memurai (Redis)'
if (-not $redisOk -and -not $NoInfra) {
  Warn '  no Memurai service found. Install: winget install Memurai.MemuraiDeveloper'
  Warn '  https://www.memurai.com/get-memurai  -- then: Start-Service Memurai'
}

$qdrantOk = Start-Detached 'qdrant' '' $PORT_QDRANT 'Qdrant'
$temporalOk = Start-Detached 'temporal' 'server start-dev' $PORT_TEMPORAL 'Temporal'

# Neo4j Desktop: look, report, never claim. An instance is created by hand in the
# GUI and the password is chosen in that dialog, so there is nothing here to
# automate -- and a down graph degrades `/v1/graph` without stopping the API.
if (Test-Port $PORT_NEO4J) {
  Ok "Neo4j up on $PORT_NEO4J"
} else {
  Warn "Neo4j is not listening on $PORT_NEO4J -- graph retrieval will degrade, everything else is fine"
  Warn '  Neo4j Desktop cannot be scripted. Open it, Local instances -> Create instance,'
  Warn "  set a password, Start it, and put that same password in backend\.env as NEO4J_PASSWORD."
}

# -----------------------------------------------------------------------------
Head 'Preconditions'

if (-not $pgOk) {
  Bad 'PostgreSQL is down, and the API cannot run without it.'
  Say ''
  Say '  Every tenant boundary in this platform is a Postgres row-level-security' Yellow
  Say '  policy. There is deliberately no SQLite fallback: a stack that "starts"' Yellow
  Say '  against a different database is one where the isolation story is not' Yellow
  Say '  running and nothing says so.' Yellow
  Say ''
  Say '  Fix Postgres, then run this again.' Yellow
  exit 1
}

$venvPython = Join-Path $Backend '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
  Bad "no virtualenv at $venvPython"
  Say '  run: .\scripts\install-windows.ps1   (or .\scripts\bootstrap.ps1 if the stores are already installed)' Yellow
  exit 1
}

$envFile = Join-Path $Backend '.env'
if (-not (Test-Path $envFile)) {
  Bad "backend\.env is missing -- the API has no gateway key, no DSN and no JWT secret"
  Say '  run: .\scripts\bootstrap.ps1   (it seeds .env from the template)' Yellow
  exit 1
}
Ok 'virtualenv and backend\.env present'

# -----------------------------------------------------------------------------
Head 'Application'

if ($Skip -notcontains 'Api') {
  if (Test-Port $PORT_API) {
    Warn "something is already listening on $PORT_API -- not starting a second API"
  } else {
    # --app-dir src because the package lives at backend/src/app. Started in its
    # own window so its log is readable and Ctrl-C there stops only the API.
    $apiCmd = "Set-Location '$Backend'; " +
              ".venv\Scripts\python -m uvicorn app.main:app --app-dir src " +
              "--host 127.0.0.1 --port $PORT_API"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $apiCmd | Out-Null
    foreach ($i in 1..60) { if (Test-Port $PORT_API) { break }; Start-Sleep -Milliseconds 500 }
    if (Test-Port $PORT_API) { Ok "API on http://127.0.0.1:$PORT_API  (docs at /docs)" }
    else { Bad "API did not come up on $PORT_API within 30s -- read the window it opened" }
  }
}

if ($Skip -notcontains 'Web') {
  if (Test-Port $PORT_WEB) {
    Warn "something is already listening on $PORT_WEB -- not starting a second console"
  } else {
    # NEXT_PUBLIC_API_BASE is set here and nowhere else. docs\install tells you
    # never to put it in web\.env.local, because a stale value there outlives
    # every port change and is invisible from the browser.
    $webCmd = "Set-Location '$Web'; " +
              "`$env:NEXT_PUBLIC_API_BASE='http://127.0.0.1:$PORT_API'; " +
              "npm run dev -- --port $PORT_WEB"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $webCmd | Out-Null
    foreach ($i in 1..60) { if (Test-Port $PORT_WEB) { break }; Start-Sleep -Milliseconds 500 }
    if (Test-Port $PORT_WEB) { Ok "console on http://localhost:$PORT_WEB" }
    else { Warn "console not up yet on $PORT_WEB -- Next's first compile is slow; check its window" }
  }
}

# -----------------------------------------------------------------------------
Head 'Where things are'
Say "  console    http://localhost:$PORT_WEB"
Say "  API        http://127.0.0.1:$PORT_API        docs: /docs"
Say "  Postgres   127.0.0.1:$PORT_PG      $(if ($pgOk) { 'up' } else { 'DOWN' })"
Say "  Memurai    127.0.0.1:$PORT_REDIS      $(if ($redisOk) { 'up' } else { 'DOWN' })"
Say "  Qdrant     127.0.0.1:$PORT_QDRANT      $(if ($qdrantOk) { 'up' } else { 'DOWN' })"
Say "  Temporal   127.0.0.1:$PORT_TEMPORAL      $(if ($temporalOk) { 'up' } else { 'DOWN' })"
Say "  Neo4j      127.0.0.1:$PORT_NEO4J      $(if (Test-Port $PORT_NEO4J) { 'up' } else { 'DOWN (optional)' })"
Say ''
Say "  stop it:   .\backend\scripts\stop-windows.ps1" Cyan
Say ''
