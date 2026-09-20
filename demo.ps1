<#
    demo.ps1 - start Firebreak and open it.

        .\demo.ps1              start serve.py, check LANDFIRE, open Chrome
        .\demo.ps1 -Offline     skip the LANDFIRE check (venue wifi is gone)
        .\demo.ps1 -NoBrowser   just serve

    Everything exported already works offline; the LANDFIRE check only tells you
    whether building NEW free-play regions will work at the venue.
#>
[CmdletBinding()]
param(
    [switch]$Offline,
    [switch]$NoBrowser,
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "python is not on PATH." }

# Fall back to the plain static server if FastAPI is missing, so the demo always runs.
& $python -c "import fastapi, uvicorn" 2>$null
$haveApi = $?
if ($haveApi) {
    Write-Host "Starting serve.py (static page + free-play API) on port $Port" -ForegroundColor Green
    $server = Start-Process -FilePath $python -ArgumentList @('serve.py', '--port', $Port) -PassThru -NoNewWindow
} else {
    Write-Host "fastapi/uvicorn not installed - falling back to the static server." -ForegroundColor Yellow
    Write-Host "  (pip install -r requirements.txt to get free-play region building)" -ForegroundColor Yellow
    $server = Start-Process -FilePath $python -ArgumentList @('-m', 'http.server', '-d', 'web', $Port) -PassThru -NoNewWindow
}

$url = "http://localhost:$Port"
$ready = $false
foreach ($i in 1..40) {
    Start-Sleep -Milliseconds 250
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}
if (-not $ready) { throw "Server did not answer on $url" }

if ($Offline) {
    Write-Host "Offline mode: skipping the LANDFIRE check." -ForegroundColor Yellow
} elseif ($haveApi) {
    try {
        $h = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 8
        $towns = ($h.towns | ForEach-Object { $_.id }) -join ', '
        if ($h.landfire_reachable) {
            Write-Host "LANDFIRE reachable. Free-play regions can be built." -ForegroundColor Green
        } else {
            Write-Host "LANDFIRE unreachable. Existing towns work; new regions will not build." -ForegroundColor Yellow
        }
        Write-Host "Towns: $towns"
    } catch {
        Write-Host "Health check failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

if (-not $NoBrowser) {
    $chrome = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($chrome) { Start-Process $chrome $url } else { Start-Process $url }
}

Write-Host ""
Write-Host "Firebreak is running at $url  (Ctrl+C to stop)" -ForegroundColor Green
try { Wait-Process -Id $server.Id } catch { }
