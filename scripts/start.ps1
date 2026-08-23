#Requires -Version 5.1
<#
.SYNOPSIS
  Superseded. Forwards to backend\scripts\start-windows.ps1.

.DESCRIPTION
  This script used to offer three modes, and one of them was a lie worth
  describing so it does not come back.

  `-Mode lite` repointed POSTGRES_DSN at `sqlite+aiosqlite:///./taif_lite.db` and
  announced "no databases needed". The platform came up, the console rendered,
  and every tenant boundary was gone -- because those boundaries are Postgres
  row-level-security policies and SQLite has no such thing. A demo that starts
  cleanly while the isolation story it is demonstrating is not running is worse
  than one that refuses to start, and it took a cross-tenant leak to notice.

  `-Mode safe` served the console against a mock transport with no backend, which
  is a different product wearing this one's UI.

  The replacement has no modes. It checks each store, starts the ones it can,
  names the ones it cannot, and refuses to start the API when Postgres is down --
  the one case where continuing would produce a running platform that is quietly
  wrong. Its companion is `backend\scripts\stop-windows.ps1`.

  This file stays because six documents and a README point at it. It forwards,
  and tells you where it went.
#>
[CmdletBinding()]
param([string]$Mode)

$ErrorActionPreference = 'Stop'
$target = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend\scripts\start-windows.ps1'

if ($Mode) {
  Write-Host ''
  Write-Host "  -Mode '$Mode' no longer exists." -ForegroundColor Yellow
  Write-Host '  There is one way to start now: the real stores, or an honest refusal.' -ForegroundColor Yellow
  Write-Host '  (`lite` used to swap Postgres for SQLite, which silently disabled every' -ForegroundColor DarkGray
  Write-Host '   tenant isolation policy in the platform. See this file for the history.)' -ForegroundColor DarkGray
  Write-Host ''
}

Write-Host "  -> backend\scripts\start-windows.ps1" -ForegroundColor Cyan
Write-Host ''
& $target
