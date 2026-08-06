<#
.SYNOPSIS
  Day-of readiness board (Windows). Checks each service and prints GREEN/RED so
  you instantly know which run mode is available. Read-only — changes nothing.
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
Row (Port 'localhost' 6379) 'Redis (6379)'     'full mode only'

Write-Host "`nGuide:" -f Cyan
Write-Host "  gateway UP  -> lite mode works (real agent, no databases): .\scripts\start.ps1 -Mode lite"
Write-Host "  all UP      -> full mode:                                    .\scripts\start.ps1 -Mode full"
Write-Host "  nothing UP  -> demo-safe (mock, always works):               .\scripts\start.ps1 -Mode safe`n"
