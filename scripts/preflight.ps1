<#
.SYNOPSIS
  Day-of readiness board (Windows). Checks each service and prints GREEN/RED so
  you instantly know which run mode is available. Read-only - changes nothing.
#>
$root = Split-Path -Parent $PSScriptRoot
function Port($h,$p) { try { (Test-NetConnection -ComputerName $h -Port $p -WarningAction SilentlyContinue -InformationLevel Quiet) } catch { $false } }
function Row($ok,$name,$note) { $c = if($ok){'Green'}else{'Red'}; $s = if($ok){'UP  '}else{'DOWN'}; Write-Host ("  [{0}] {1,-20} {2}" -f $s,$name,$note) -f $c }

Write-Host "`n== Preflight ==" -f Cyan

# Gateway (the one thing lite mode needs). Reads backend\.env if present.
$key = $null; $base = 'https://genailab.tcs.in'
$envFile = "$root\backend\.env"
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^GENAILAB_API_KEY=(.+)$') { $key = $Matches[1].Trim() }
    if ($_ -match '^GENAILAB_BASE_URL=(.+)$') { $base = $Matches[1].Trim() }
  }
}
$gwOk = $false
if ($key -and $key -ne 'replace-me') {
  try {
    add-type @"
using System.Net; using System.Security.Cryptography.X509Certificates;
public class TrustAll : ICertificatePolicy { public bool CheckValidationResult(ServicePoint s, X509Certificate c, WebRequest r, int p){return true;} }
"@ -ErrorAction SilentlyContinue
    [System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAll
    $r = Invoke-WebRequest -Uri "$base/models" -Headers @{Authorization="Bearer $key"} -TimeoutSec 10 -UseBasicParsing
    $gwOk = ($r.StatusCode -eq 200)
  } catch { $gwOk = $false }
}
Row $gwOk 'Model gateway' $(if($key){"$base"}else{'no key in backend\.env'})

# Local stores (only needed for full mode).
Row (Port 'localhost' 5432) 'Postgres (5432)'  'full mode only'
Row (Port 'localhost' 7687) 'Neo4j (7687)'     'full mode only'

# Is tenant isolation actually ON? An open 5432 says nothing about it: Postgres skips
# row security ENTIRELY for a superuser, so serving as `postgres` leaves all 13
# tenant_isolation policies installed and enforced against nobody. This row asks the
# database the same question the backend asks at boot (app.data.rls_check), so the
# day-of board and the running platform cannot disagree. Read-only.
$rlsOk = $false
$rlsNote = 'backend\.venv missing - run scripts\bootstrap.ps1'
$pyExe = "$root\backend\.venv\Scripts\python.exe"
if (Test-Path $pyExe) {
  Push-Location "$root\backend"
  $env:PYTHONPATH = "$root\aegis\src;src"
  $rlsNote = (& $pyExe -m app.data.rls_check 2>$null | Select-Object -Last 1)
  $rlsOk = ($LASTEXITCODE -eq 0)
  Pop-Location
  if (-not $rlsNote) { $rlsNote = 'rls_check produced no output' }
}
Row $rlsOk 'RLS serving role' $rlsNote

# The vector store is EMBEDDED (in-process, file-backed) - no server, no port. What
# can fail is the directory, so that is what we check: it must exist and be writable,
# otherwise full mode refuses to boot rather than degrading to a RAM index.
$vecPath = $env:VECTOR_STORE_PATH
if (-not $vecPath) { $vecPath = "$root\backend\vector_storage" }
$vecOk = $false
try {
  New-Item -ItemType Directory -Force -Path $vecPath -ErrorAction Stop | Out-Null
  $probe = Join-Path $vecPath '.preflight'
  Set-Content -Path $probe -Value 'ok' -ErrorAction Stop
  Remove-Item $probe -ErrorAction SilentlyContinue
  $vecOk = $true
} catch { $vecOk = $false }
Row $vecOk 'Vector store (local)' $vecPath

# Redis on Windows is Memurai: same wire protocol, same port, different CLI
# (`memurai-cli`, not `redis-cli`) - the app needs no change either way. A PING
# is worth more than an open port here, since the cache depends on the protocol.
$redisNote = 'full mode only'
$redisCli = @('memurai-cli','redis-cli') | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
if ($redisCli) {
  $pong = & $redisCli ping 2>$null
  if ($pong -match 'PONG') { $redisNote = "PONG via $redisCli" }
}
Row (Port 'localhost' 6379) 'Redis/Memurai (6379)' $redisNote

# Regression gate (DeepEval-pattern) - offline CI gate over the seed corpus + the
# agentic router case. No network, no keys, no stores; a nonzero exit FAILS preflight.
$gateOk = $false
$py = "$root\backend\.venv\Scripts\python.exe"
if (Test-Path $py) {
  Push-Location "$root\backend"
  $env:PYTHONPATH = 'src'
  & $py -m app.eval.regression *> $null
  $gateOk = ($LASTEXITCODE -eq 0)
  Pop-Location
  Row $gateOk 'Regression gate' 'DeepEval-pattern eval (offline)'
} else {
  Row $false 'Regression gate' 'backend\.venv missing - run scripts\bootstrap.ps1'
}

Write-Host "`nGuide:" -f Cyan
Write-Host "  gateway UP  -> lite mode works (real agent, no databases): .\scripts\start.ps1 -Mode lite"
Write-Host "  all UP      -> full mode:                                    .\scripts\start.ps1 -Mode full"
Write-Host "  nothing UP  -> demo-safe (mock, always works):               .\scripts\start.ps1 -Mode safe`n"

# Service probes above are informational (which run mode is available); the
# regression gate is a real pass/fail bar, so its result is preflight's exit code.
if (-not $gateOk) { exit 1 }
exit 0
