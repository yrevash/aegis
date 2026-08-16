<#
.SYNOPSIS
  Provision the Aegis serving role - the Postgres login that CANNOT bypass tenant RLS.

.DESCRIPTION
  PostgreSQL skips row security entirely for a superuser or a BYPASSRLS role, and
  FORCE ROW LEVEL SECURITY only removes the table *owner's* exemption. Serving requests
  as `postgres` therefore leaves all 13 tenant_isolation policies installed and enforced
  against nobody - which is how this platform ran until the connection was split.

  This script runs scripts\sql\aegis-app-role.sql (create the role NOSUPERUSER
  NOBYPASSRLS, grant it only DML) and then repoints backend\.env:

    POSTGRES_DSN        -> the serving role      (every request; subject to RLS)
    POSTGRES_ADMIN_DSN  -> the superuser/owner   (create_all, RLS bootstrap; DDL only)

  Idempotent - re-running rotates the password and re-applies the same grants.

.PARAMETER Password
  The serving role's password. Generated (32 chars, cryptographically random) when
  omitted, which is the recommended path: nothing memorable ends up in a file.

.PARAMETER NoEnv
  Provision the role but leave backend\.env untouched; the DSNs are printed instead.

.EXAMPLE
  .\scripts\db-roles.ps1
  Creates aegis_app in the taif database and points backend\.env at it.
#>
[CmdletBinding()]
param(
  [string]$Role = 'aegis_app',
  [string]$Database = 'taif',
  [string]$DbHost = 'localhost',
  [int]$Port = 5432,
  [string]$Superuser = 'postgres',
  [string]$SuperuserPassword = 'postgres',
  [string]$Password,
  [switch]$NoEnv
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Ok($m)   { Write-Host "  [ OK ] $m" -f Green }
function Warn($m) { Write-Host "  [WARN] $m" -f Yellow }
function Bad($m)  { Write-Host "  [FAIL] $m" -f Red }

# psql is not on PATH after a default Windows PostgreSQL install, so look where the
# installer actually puts it before giving up.
$psql = Get-Command 'psql' -ErrorAction SilentlyContinue
if (-not $psql) {
  $found = Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin\psql.exe' -ErrorAction SilentlyContinue |
           Select-Object -Last 1
  if ($found) { $psql = $found.FullName } else { Bad 'psql.exe not found - install the PostgreSQL client tools'; exit 1 }
} else {
  $psql = $psql.Source
}

if (-not $Password) {
  $bytes = New-Object 'System.Byte[]' 24
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  # URL-safe: the password is going into a DSN, where '/', '+', '@' and ':' would need escaping.
  $Password = [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').Replace('=','')
}

Write-Host "`n== Provisioning serving role '$Role' in $Database ==" -f Cyan
$env:PGPASSWORD = $SuperuserPassword
& $psql -v ON_ERROR_STOP=1 -U $Superuser -h $DbHost -p $Port -d $Database `
        -v role=$Role -v pw=$Password -f "$root\scripts\sql\aegis-app-role.sql"
$rc = $LASTEXITCODE
Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
if ($rc -ne 0) { Bad "psql exited $rc - the role was NOT provisioned"; exit $rc }
Ok "role '$Role' is LOGIN NOSUPERUSER NOBYPASSRLS with DML grants only"

$dsn = "postgresql://${Role}:${Password}@${DbHost}:${Port}/${Database}"
$adminDsn = "postgresql://${Superuser}:${SuperuserPassword}@${DbHost}:${Port}/${Database}"

if ($NoEnv) {
  Write-Host "  POSTGRES_DSN=$dsn"
  Write-Host "  POSTGRES_ADMIN_DSN=$adminDsn"
} else {
  $envFile = "$root\backend\.env"
  # Seed backend\.env if it does not exist yet: install-windows.ps1 provisions the
  # stores (this script) before the app dependencies (bootstrap.ps1, which is what
  # normally creates .env), and writing DSNs into a file nobody created would lose them.
  if ((-not (Test-Path $envFile)) -and (Test-Path "$root\backend\.env.example")) {
    Copy-Item "$root\backend\.env.example" $envFile
  }
  if (-not (Test-Path $envFile)) {
    Warn "backend\.env not found - add these two lines by hand:"
    Write-Host "    POSTGRES_DSN=$dsn"
    Write-Host "    POSTGRES_ADMIN_DSN=$adminDsn"
  } else {
    $lines = Get-Content $envFile
    $wroteDsn = $false; $wroteAdmin = $false
    $updated = foreach ($line in $lines) {
      if ($line -like 'POSTGRES_DSN=*')            { $wroteDsn = $true;   "POSTGRES_DSN=$dsn" }
      elseif ($line -like 'POSTGRES_ADMIN_DSN=*')  { $wroteAdmin = $true; "POSTGRES_ADMIN_DSN=$adminDsn" }
      else { $line }
    }
    if (-not $wroteDsn)   { $updated += "POSTGRES_DSN=$dsn" }
    if (-not $wroteAdmin) { $updated += "POSTGRES_ADMIN_DSN=$adminDsn" }
    Set-Content -Path $envFile -Value $updated
    Ok 'backend\.env updated: POSTGRES_DSN -> serving role, POSTGRES_ADMIN_DSN -> owner'
  }
}

Write-Host "`nDone. Verify with .\scripts\preflight.ps1 (row 'RLS serving role').`n" -f Green
