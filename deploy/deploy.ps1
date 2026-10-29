<#
.SYNOPSIS
    WAMSS one-command deployment (Windows).

.DESCRIPTION
    1. Checks prerequisites (docker, docker compose plugin, ports 8000/8080 free).
    2. Creates .env from .env.example on FIRST run only, generating a random
       WAMSS_API_KEY (and POSTGRES_PASSWORD when the postgres block is active).
       An existing .env is NEVER overwritten and secrets are NEVER committed.
    3. Builds and starts the compose stack in the background.
    4. Waits for the backend health endpoint (GET /health) with a timeout.
    5. Prints the dashboard URL and next steps.

    Idempotent: safe to re-run for updates. Re-runs rebuild and restart the
    stack but preserve .env and the named data volumes.

.PARAMETER NoBuild
    Skip the build step; restart existing images only.

.PARAMETER Profile
    Extra compose profiles to enable, e.g. -Profile postgres.

.EXAMPLE
    .\deploy\deploy.ps1
.EXAMPLE
    .\deploy\deploy.ps1 -Profile postgres
#>
[CmdletBinding()]
param(
    [switch]$NoBuild,
    [string[]]$Profile = @(),
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$healthTimeoutSec = 180

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

if ($Help) {
    Write-Host @"
Usage: .\deploy\deploy.ps1 [-NoBuild] [-Profile NAME ...]

One-command WAMSS deployment (FastAPI backend + React dashboard via Docker).

First run:  creates .env from .env.example and generates a random
            WAMSS_API_KEY (printed once; keep it safe). Existing .env and
            data volumes are preserved on every run.

After a successful deploy:
  Dashboard:  http://localhost:8080
  Backend:    http://localhost:8000  (health: /health, API docs: /docs)

Idempotent: re-running rebuilds and restarts the stack without touching
.env or the backend-data / pgdata volumes.
"@
    exit 0
}

function New-RandomToken([int]$Bytes = 32) {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $buf = New-Object byte[] $Bytes
    $rng.GetBytes($buf)
    $rng.Dispose()
    return [Convert]::ToBase64String($buf).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Test-PortFree([int]$Port) {
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { $listener.Stop() }
    }
}

# 1. Prerequisites ------------------------------------------------------------
Write-Step "Checking prerequisites"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[FAIL] docker not found in PATH" -ForegroundColor Red; exit 1
}
docker info 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] docker daemon not reachable (start Docker Desktop)" -ForegroundColor Red; exit 1
}
docker compose version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] docker compose plugin missing (update Docker Desktop)" -ForegroundColor Red; exit 1
}
foreach ($port in @(8000, 8080)) {
    if (-not (Test-PortFree $port)) {
        Write-Host "[FAIL] port $port is already in use; stop the conflicting service or remap it in docker-compose.yml" -ForegroundColor Red
        exit 1
    }
}
Write-Host "[OK] docker, compose plugin, and ports 8000/8080 available" -ForegroundColor Green

# 2. .env (first run only) -----------------------------------------------------
Write-Step "Preparing .env"
if (Test-Path ".env") {
    Write-Host "[OK] .env already exists; keeping it untouched (secrets preserved)" -ForegroundColor Green
} else {
    $generatedKey = New-RandomToken 32
    $lines = Get-Content ".env.example"
    $out = foreach ($line in $lines) {
        if ($line -match "^WAMSS_API_KEY=\s*$") { "WAMSS_API_KEY=$generatedKey" }
        elseif ($line -match "^POSTGRES_PASSWORD=changeme\s*$") { "POSTGRES_PASSWORD=$(New-RandomToken 24)" }
        else { $line }
    }
    $out | Set-Content -Path ".env" -Encoding UTF8
    Write-Host "[OK] created .env from .env.example with a freshly generated WAMSS_API_KEY" -ForegroundColor Green
    Write-Host "      API key (shown once, saved in .env as WAMSS_API_KEY):"
    Write-Host "        $generatedKey"
    Write-Host "      Show it any time with: (Get-Content .env | Select-String '^WAMSS_API_KEY=')"
}

# 3. Build and start -----------------------------------------------------------
Write-Step "Starting the compose stack"
$composeArgs = @()
foreach ($p in $Profile) { $composeArgs += "--profile"; $composeArgs += $p }
if ($NoBuild) {
    & docker compose @composeArgs up -d
} else {
    & docker compose @composeArgs up --build -d
}
if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] docker compose up failed" -ForegroundColor Red; exit 1 }

# 4. Wait for backend health ---------------------------------------------------
Write-Step "Waiting for backend health (GET /health)"
$deadline = (Get-Date).AddSeconds($healthTimeoutSec)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    Write-Host "      ... still waiting (backend starting up)"
    Start-Sleep -Seconds 5
}
if (-not $healthy) {
    Write-Host "[FAIL] backend did not become healthy within ${healthTimeoutSec}s" -ForegroundColor Red
    Write-Host "       Inspect with: docker compose logs backend"
    exit 1
}
Write-Host "[OK] backend is healthy" -ForegroundColor Green

# 5. Report --------------------------------------------------------------------
Write-Host ""
Write-Host "================ WAMSS is up ================" -ForegroundColor Green
Write-Host "  Dashboard:  http://localhost:8080"
Write-Host "  Backend:    http://localhost:8000"
Write-Host "  API docs:   http://localhost:8000/docs"
Write-Host "============================================="
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Seed demo data:  python shared\simulate.py --api-url http://localhost:8000"
Write-Host '     (with the API key:  $key = (Get-Content .env | Select-String "^WAMSS_API_KEY=").ToString().Split("=")[1]; python shared\simulate.py --api-url http://localhost:8000 --api-key $key)'
Write-Host "  2. Install the workstation agent (run as admin):  .\deploy\install-agent.ps1"
Write-Host "  3. Open the dashboard at http://localhost:8080 and verify the seeded data."
Write-Host "  4. To update later, just re-run:  .\deploy\deploy.ps1"
