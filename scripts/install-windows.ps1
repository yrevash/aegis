<#
.SYNOPSIS
  Full machine setup for Aegis on Windows - toolchain, native stores, app deps.
  No Docker, no GPU, no WSL.

.DESCRIPTION
  bootstrap.ps1 installs the Python and Node dependencies but assumes the
  toolchain already exists and installs none of the three data stores - yet
  `start.ps1 -Mode full` needs all three. This script fills that gap:

    1. toolchain  - Python 3.11, Node LTS, uv, Git (via winget)
    2. stores     - PostgreSQL, Redis, Neo4j (native, never Docker)
    3. app deps   - delegates to bootstrap.ps1 (single source of truth for extras)
    4. verify     - reports what actually answers on its port

  There is deliberately NO vector-database step. The vector store is EMBEDDED:
  it runs inside the Python process against a local directory, so it needs no
  server binary, no service registration and no open port - which is what makes
  Aegis installable on a locked-down machine.

  Idempotent: every step checks before it installs, so re-running is safe and
  fast. Each store is independent - one failing does not stop the others, and
  the summary at the end tells you exactly what is missing and how to fix it.

.PARAMETER SkipStores
  Toolchain + app dependencies only. Use this if you intend to run
  `start.ps1 -Mode lite`, which needs no databases at all.

.PARAMETER StoresOnly
  Install just the three data stores; skip toolchain and app dependencies.

.EXAMPLE
  .\scripts\install-windows.ps1
  Full setup, then: .\scripts\start.ps1 -Mode full

.EXAMPLE
  .\scripts\install-windows.ps1 -SkipStores
  Enough to run: .\scripts\start.ps1 -Mode lite

.NOTES
  Run in an ELEVATED PowerShell - PostgreSQL, Redis and Neo4j register Windows
  services. Authored on macOS and NOT yet executed on a Windows box; treat the
  first run as a rehearsal and keep the manual fallbacks in the summary handy.
#>
[CmdletBinding()]
param(
  [switch]$SkipStores,
  [switch]$StoresOnly
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$Failures = [System.Collections.Generic.List[string]]::new()

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Ok($msg)   { Write-Host "  [ OK ] $msg" -f Green }
function Warn($msg) { Write-Host "  [WARN] $msg" -f Yellow }
function Bad($msg)  { Write-Host "  [FAIL] $msg" -f Red; $Failures.Add($msg) }
function Head($msg) { Write-Host "`n== $msg ==" -f Cyan }

function Test-Port([int]$Port) {
  try {
    $c = New-Object Net.Sockets.TcpClient
    $c.Connect('127.0.0.1', $Port); $c.Close(); return $true
  } catch { return $false }
}

<# Install a winget package unless the command it provides already exists. #>
function Install-WingetPackage([string]$Id, [string]$Probe, [string]$Label) {
  if ($Probe -and (Have $Probe)) { Ok "$Label (already installed)"; return $true }
  if (-not (Have 'winget')) { Bad "$Label - winget unavailable; install manually"; return $false }
  Write-Host "  installing $Label ..."
  winget install --id $Id --silent --accept-source-agreements --accept-package-agreements | Out-Null
  # winget puts new tools on PATH for *new* shells; refresh this one so later
  # steps can use what we just installed.
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
              [Environment]::GetEnvironmentVariable('Path','User')
  if ($Probe -and -not (Have $Probe)) { Warn "$Label installed but '$Probe' is not on PATH yet - reopen PowerShell"; return $true }
  Ok $Label
  return $true
}

Write-Host "`nAegis - Windows setup" -f White
Write-Host "no Docker * no GPU * native services only" -f DarkGray

# -----------------------------------------------------------------------------
# 1 * Toolchain
# -----------------------------------------------------------------------------
if (-not $StoresOnly) {
  Head 'Toolchain'
  Install-WingetPackage 'Python.Python.3.11'  'python' 'Python 3.11' | Out-Null
  Install-WingetPackage 'OpenJS.NodeJS.LTS'   'node'   'Node LTS'    | Out-Null
  Install-WingetPackage 'Git.Git'             'git'    'Git'         | Out-Null

  if (Have 'uv') {
    Ok 'uv (already installed)'
  } elseif (Have 'python') {
    Write-Host '  installing uv ...'
    python -m pip install --quiet --upgrade uv
    if (Have 'uv') { Ok 'uv' } else { Warn 'uv installed but not on PATH - reopen PowerShell' }
  } else {
    Bad 'uv - needs Python first'
  }
}

# -----------------------------------------------------------------------------
# 2 * Native stores
# -----------------------------------------------------------------------------
if (-not $SkipStores) {

  # -- PostgreSQL - tenants, users, budgets, ledger, approvals, checkpoints ----
  Head 'PostgreSQL'
  if (Test-Port 5432) {
    Ok 'already listening on 5432'
  } else {
    Install-WingetPackage 'PostgreSQL.PostgreSQL.16' $null 'PostgreSQL 16' | Out-Null
    Start-Sleep -Seconds 5
    if (Test-Port 5432) { Ok 'PostgreSQL up on 5432' } else { Bad 'PostgreSQL not answering on 5432' }
  }

  # The app expects a `taif` database, created and owned by the `postgres` superuser.
  # It is then served by a SEPARATE, non-superuser role - see the block below.
  if (Test-Port 5432) {
    $psql = Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin\psql.exe' -ErrorAction SilentlyContinue |
            Select-Object -Last 1
    if ($psql) {
      $env:PGPASSWORD = 'postgres'
      $exists = & $psql.FullName -U postgres -h 127.0.0.1 -tAc "SELECT 1 FROM pg_database WHERE datname='taif'" 2>$null
      if ($exists -match '1') {
        Ok "database 'taif' exists"
      } else {
        & $psql.FullName -U postgres -h 127.0.0.1 -c 'CREATE DATABASE taif' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok "created database 'taif'" }
        else { Warn "could not create 'taif' - create it manually, then re-run" }
      }
      Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    } else {
      Warn "psql.exe not found - create the 'taif' database manually"
    }
  }

  # -- The serving role - the half of tenant isolation that is not code --------
  # Postgres skips row security ENTIRELY for a superuser, and FORCE ROW LEVEL
  # SECURITY only removes the table owner's exemption. Connecting as `postgres`
  # therefore installs 13 tenant policies and is filtered by none of them. So a
  # fresh machine gets a second, non-superuser role (`aegis_app`) that requests are
  # served as, while `postgres` stays behind POSTGRES_ADMIN_DSN for DDL only.
  #
  # Only provisioned when it is missing: db-roles.ps1 rotates the password and
  # rewrites backend\.env, which should not happen on every re-run of the installer.
  Head 'Database roles (RLS enforcement)'
  if ((Test-Port 5432) -and $psql) {
    $env:PGPASSWORD = 'postgres'
    $roleExists = & $psql.FullName -U postgres -h 127.0.0.1 -tAc "SELECT 1 FROM pg_roles WHERE rolname='aegis_app'" 2>$null
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    if ($roleExists -match '1') {
      Ok "serving role 'aegis_app' exists (re-run scripts\db-roles.ps1 to rotate its password)"
    } else {
      & (Join-Path $PSScriptRoot 'db-roles.ps1')
      if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) { Ok "serving role 'aegis_app' provisioned" }
      else { Bad 'could not provision the aegis_app role - run scripts\db-roles.ps1 by hand' }
    }
  } else {
    Warn 'skipped - Postgres or psql.exe unavailable; run scripts\db-roles.ps1 once both are'
    Warn '  until then the backend serves as a superuser and every tenant RLS policy is bypassed'
  }

  # -- Redis / Memurai - near-exact retrieval cache + answer cache ------------
  # Redis ships no official Windows build, and enterprise Windows images have no
  # Docker or WSL to fall back on. Memurai is the maintained Redis-compatible
  # Windows service, speaks the same wire protocol on the same port, and needs
  # NO application change: REDIS_URL=redis://localhost:6379/0 and redis-py drive
  # it exactly as they drive Redis. Its CLI is `memurai-cli`, not `redis-cli`.
  Head 'Redis / Memurai'
  $redisCli = @('memurai-cli','redis-cli') | Where-Object { Have $_ } | Select-Object -First 1

  if (-not (Test-Port 6379)) {
    # Installed but not running is the common case after a reboot - starting the
    # existing service beats reinstalling it.
    $svc = Get-Service -Name 'Memurai*' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($svc -and $svc.Status -ne 'Running') {
      Write-Host "  starting the $($svc.Name) service ..."
      try { Start-Service $svc.Name; Start-Sleep -Seconds 3 } catch { Warn "could not start $($svc.Name): $($_.Exception.Message)" }
    } elseif (-not $svc -and -not $redisCli) {
      Install-WingetPackage 'Memurai.MemuraiDeveloper' 'memurai-cli' 'Memurai (Redis for Windows)' | Out-Null
      Start-Sleep -Seconds 4
      $redisCli = @('memurai-cli','redis-cli') | Where-Object { Have $_ } | Select-Object -First 1
    }
  }

  if (Test-Port 6379) {
    # Port-open is necessary but not sufficient - PING proves it actually speaks
    # the protocol, which is the thing the cache depends on.
    if ($redisCli) {
      $pong = & $redisCli ping 2>$null
      if ($pong -match 'PONG') { Ok "6379 answering PONG (via $redisCli)" }
      else { Warn "6379 open but $redisCli did not return PONG" }
    } else {
      Ok '6379 listening (no CLI found to PING with)'
    }
  } else {
    Warn 'Redis/Memurai not answering on 6379'
    Warn '  non-fatal: the semantic cache degrades to no-cache, every other surface is fine'
    Warn '  fix: install Memurai (https://www.memurai.com/get-memurai), then Start-Service Memurai'
  }

  # -- Vector store - EMBEDDED, so there is nothing to install ----------------
  # The ANN engine behind retrieval + memory recall runs in-process against a
  # local directory (installed as a Python package by bootstrap.ps1). No binary,
  # no service, no port. All this step does is prove the directory is writable,
  # because that is the only way the vector tier can fail on this machine.
  Head 'Vector store (embedded)'
  $vecPath = $env:VECTOR_STORE_PATH
  if (-not $vecPath) { $vecPath = Join-Path $root 'backend\vector_storage' }
  try {
    New-Item -ItemType Directory -Force -Path $vecPath -ErrorAction Stop | Out-Null
    $probe = Join-Path $vecPath '.install-check'
    Set-Content -Path $probe -Value 'ok' -ErrorAction Stop
    Remove-Item $probe -ErrorAction SilentlyContinue
    Ok "writable: $vecPath (no server needed)"
  } catch {
    Bad "vector store directory not writable: $vecPath - $($_.Exception.Message)"
  }

  # -- Neo4j - the knowledge graph behind GET /graph --------------------------
  # Neo4j Desktop cannot be fully automated: it ships its own JDK, but an
  # *instance* must be created interactively and the password is chosen in that
  # dialog. Installing Desktop is therefore only half the job, and a fresh
  # Desktop shows "Create your first instance" with nothing on 7687.
  #
  # Best-effort by design: a down Neo4j degrades graph retrieval but never blocks
  # the API, so this warns and prints the manual steps rather than failing.
  Head 'Neo4j'
  if (Test-Port 7687) {
    Ok 'already listening on 7687'
  } else {
    $desktop = Get-ChildItem "$env:LOCALAPPDATA\Programs","$env:ProgramFiles" -Filter 'Neo4j Desktop*' `
                 -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $desktop) {
      Install-WingetPackage 'Neo4j.Neo4jDesktop' $null 'Neo4j Desktop' | Out-Null
      $desktop = $true
    } else {
      Ok "Neo4j Desktop installed ($($desktop.Name))"
    }

    if (-not (Test-Port 7687)) {
      Warn 'Neo4j Desktop is installed but no instance is running on 7687.'
      Warn '  Desktop needs an instance created by hand - it cannot be scripted:'
      Warn '    1. open Neo4j Desktop -> Local instances -> Create instance'
      Warn '    2. set the password, and use that SAME value for NEO4J_PASSWORD'
      Warn '       in backend\.env  (user stays neo4j, bolt stays 7687)'
      Warn '    3. Start the instance, then re-run preflight.ps1'
      Warn '  Until then: graph retrieval degrades, every other surface is fine.'
    }
  }
}

# -----------------------------------------------------------------------------
# 3 * Application dependencies - delegated to bootstrap.ps1
# -----------------------------------------------------------------------------
if (-not $StoresOnly) {
  Head 'Application dependencies'
  $bootstrap = Join-Path $PSScriptRoot 'bootstrap.ps1'
  if (Test-Path $bootstrap) {
    # bootstrap.ps1 owns the extras list and the .env seeding; calling it keeps
    # one source of truth rather than duplicating a list that has drifted before.
    & $bootstrap
  } else {
    Bad "bootstrap.ps1 not found at $bootstrap"
  }
}

# -----------------------------------------------------------------------------
# 4 * Verify
# -----------------------------------------------------------------------------
Head 'Verification'
$ports = [ordered]@{
  'PostgreSQL 5432' = 5432
  'Redis      6379' = 6379
  'Neo4j      7687' = 7687
}
foreach ($k in $ports.Keys) {
  if (Test-Port $ports[$k]) { Ok $k } else { Warn "$k - not answering" }
}

$envFile = Join-Path $root 'backend\.env'
if (Test-Path $envFile) {
  if ((Get-Content $envFile -Raw) -match 'GENAILAB_API_KEY\s*=\s*replace-me') {
    Warn 'backend\.env still has GENAILAB_API_KEY=replace-me - no model calls will work'
  } else {
    Ok 'GENAILAB_API_KEY is set'
  }
} else {
  Warn 'backend\.env missing - copy backend\.env.example and fill in the key'
}

Head 'Next'
if ($Failures.Count -gt 0) {
  Write-Host "  $($Failures.Count) step(s) failed:" -f Red
  $Failures | ForEach-Object { Write-Host "    - $_" -f Red }
  Write-Host "  Everything else is usable. Lite mode needs no stores at all:" -f Yellow
}
Write-Host "  1) put your key in backend\.env   (GENAILAB_API_KEY=...)"
Write-Host "  2) .\scripts\preflight.ps1        # what is up"
Write-Host "  3) .\scripts\start.ps1 -Mode full # all stores"
Write-Host "     .\scripts\start.ps1 -Mode lite # no databases needed"
Write-Host "     .\scripts\start.ps1 -Mode safe # console only, mock data`n"
