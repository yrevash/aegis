#Requires -Version 5.1
<#
.SYNOPSIS
  Stop Aegis on Windows. The API and the console by default; the stores only if
  you ask.

.DESCRIPTION
  The default is deliberately narrow: it stops the two things this repo starts
  and leaves everything else alone. PostgreSQL and Memurai are Windows services
  that other work on the machine may depend on, and a stop script that helpfully
  shut down a developer's database because they wanted to restart an API is a
  script nobody runs twice.

  So:

    * default        -- the API and the console, and nothing else.
    * -Stores        -- also Qdrant and Temporal, which this repo launched as bare
                       processes and which nothing else supervises.
    * -Services      -- also the PostgreSQL and Memurai Windows services. Needs an
                       elevated shell, and asks before each one unless -Force.

  Neo4j Desktop is never touched under any flag. It is a GUI application with its
  own lifecycle and killing its JVM from underneath it is how an instance ends up
  needing repair.

.PARAMETER Stores
  Also stop Qdrant and Temporal -- the bare binaries start-windows.ps1 launched.

.PARAMETER Services
  Also stop the PostgreSQL and Memurai services. Prompts per service.

.PARAMETER Force
  Do not prompt. With -Services this stops your database without asking, so it is
  opt-in twice on purpose.

.EXAMPLE
  .\backend\scripts\stop-windows.ps1
  Stop the API and the console. Postgres, Memurai, Qdrant, Temporal keep running.

.EXAMPLE
  .\backend\scripts\stop-windows.ps1 -Stores
  Also stop Qdrant and Temporal -- the full local stack except the services.
#>
[CmdletBinding()]
param(
  [switch]$Stores,
  [switch]$Services,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'

$PORT_API = 8000
$PORT_WEB = 3000
$PORT_QDRANT = 6333
$PORT_TEMPORAL = 7233

function Head([string]$m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok([string]$m) { Write-Host "  [ ok ] $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Info([string]$m) { Write-Host "  $m" -ForegroundColor Gray }

<#
  The owning PIDs of whatever is LISTENING on a port.

  By port and not by image name, deliberately. `Stop-Process -Name python` on a
  developer's machine is indiscriminate -- it takes out the Jupyter kernel, the
  other checkout, and the script doing the killing. The listener on 8000 is the
  API by definition; nothing else can be.

  Get-NetTCPConnection is Windows 8+/Server 2012+. On anything older, netstat
  parsing is the fallback, which is why this reads a little defensively.
#>
function Get-ListenerPids([int]$Port) {
  $pids = @()
  try {
    $pids = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique
  } catch {
    $pids = netstat -ano -p tcp 2>$null |
            Select-String -Pattern "LISTENING" |
            Where-Object { $_ -match "[:\.]$Port\s" } |
            ForEach-Object { ($_ -split '\s+')[-1] } |
            Select-Object -Unique
  }
  return @($pids | Where-Object { $_ -and $_ -ne 0 } | ForEach-Object { [int]$_ })
}

<#
  Stop whatever holds a port, and say what it was.

  The process is named before it is killed, because "stopped 3 processes" is not
  a sentence anyone can check and a stop script's whole job is being trustworthy
  about what it did. Child processes go too: `npm run dev` is a shim that spawns
  the real Next server, and stopping only the shim leaves the port held.
#>
function Stop-Port([int]$Port, [string]$Label) {
  $pids = Get-ListenerPids $Port
  if ($pids.Count -eq 0) { Info "$Label -- nothing listening on $Port"; return }

  foreach ($processId in $pids) {
    $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
    $name = if ($proc) { "$($proc.ProcessName) (pid $processId)" } else { "pid $processId" }
    try {
      # /T takes the process tree; npm -> node is the case that needs it.
      & taskkill.exe /PID $processId /T /F *> $null
      if ($LASTEXITCODE -ne 0) { throw "taskkill exit $LASTEXITCODE" }
      Ok "$Label -- stopped $name"
    } catch {
      Warn "$Label -- could not stop $name : $($_.Exception.Message)"
    }
  }

  foreach ($i in 1..12) {
    if ((Get-ListenerPids $Port).Count -eq 0) { return }
    Start-Sleep -Milliseconds 250
  }
  Warn "$Label -- port $Port is still held after 3s"
}

function Stop-StoreService([string]$Pattern, [string]$Label) {
  $svc = Get-Service -Name $Pattern -ErrorAction SilentlyContinue |
         Where-Object { $_.Status -eq 'Running' } | Select-Object -First 1
  if (-not $svc) { Info "$Label -- no running service matches '$Pattern'"; return }

  if (-not $Force) {
    $answer = Read-Host "  Stop the $Label service '$($svc.Name)'? Other work on this machine may be using it. [y/N]"
    if ($answer -notmatch '^[Yy]') { Info "$Label -- left running"; return }
  }
  try {
    Stop-Service -Name $svc.Name -ErrorAction Stop
    Ok "$Label -- service '$($svc.Name)' stopped"
  } catch {
    Warn "$Label -- could not stop '$($svc.Name)': $($_.Exception.Message)"
    Warn '  stopping a service usually needs an elevated PowerShell'
  }
}

# -----------------------------------------------------------------------------
Head 'Application'
Stop-Port $PORT_WEB 'console'
Stop-Port $PORT_API 'API'

if ($Stores) {
  Head 'Local stores (bare processes)'
  Stop-Port $PORT_QDRANT 'Qdrant'
  Stop-Port $PORT_TEMPORAL 'Temporal'
  Info 'Neo4j Desktop is never stopped from here -- close it from its own window.'
}

if ($Services) {
  Head 'Windows services'
  Warn 'These are shared with the rest of the machine.'
  Stop-StoreService 'Memurai*' 'Memurai (Redis)'
  Stop-StoreService 'postgresql*' 'PostgreSQL'
}

Head 'Now'
foreach ($row in @(
  @{ Port = $PORT_WEB; Name = 'console ' },
  @{ Port = $PORT_API; Name = 'API     ' },
  @{ Port = $PORT_QDRANT; Name = 'Qdrant  ' },
  @{ Port = $PORT_TEMPORAL; Name = 'Temporal' }
)) {
  $held = (Get-ListenerPids $row.Port).Count -gt 0
  Info "$($row.Name)  $($row.Port)  $(if ($held) { 'still up' } else { 'stopped' })"
}
Write-Host ''
if (-not $Stores -and -not $Services) {
  Info 'Stores left running on purpose. -Stores also stops Qdrant and Temporal;'
  Info '-Services also stops the Postgres and Memurai Windows services.'
  Write-Host ''
}
