# Start (or restart) the SLIM Fire API using the local virtual environment.
# Usage:
#   .\run.ps1            # start the server
#   .\run.ps1 -Restart   # kill anything on port 8005 first, then start

param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# The local API connects directly to FIRMS and GWIS. Ignore any stale shell proxy.
foreach ($proxyVariable in "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy") {
    Remove-Item "Env:$proxyVariable" -ErrorAction SilentlyContinue
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Virtual environment not found at .venv. Create it first (see README)."
}

$port = 8005

if ($Restart) {
    $owners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($pid_ in $owners) {
        Write-Host "Stopping process $pid_ using port $port ..."
        & taskkill.exe /PID $pid_ /T /F | Out-Null
    }
}

Write-Host "Starting SLIM Fire API on http://127.0.0.1:$port (Ctrl+C to stop) ..."
& $python -m uvicorn fire_api:app --host 127.0.0.1 --port $port
