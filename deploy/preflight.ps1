<#
.SYNOPSIS
    WAMSS preflight checklist (Windows).

.DESCRIPTION
    Verifies the host is ready for deployment before running deploy/deploy.ps1.
    Critical checks: Python >= 3.10, docker present and reachable, docker
    compose plugin, >= 5 GB free disk space, ports 8000 and 8080 free.
    Warning-only (never fails the run): BLE adapter presence for the optional
    smartwatch heart-rate module.

.EXAMPLE
    .\deploy\preflight.ps1
#>
[CmdletBinding()]
param(
    [switch]$Help
)

$ErrorActionPreference = "Continue"
$script:Fail = 0
$script:Warn = 0

function Pass-Check([string]$Message) { Write-Host "[PASS] $Message" -ForegroundColor Green }
function Fail-Check([string]$Message) { Write-Host "[FAIL] $Message" -ForegroundColor Red; $script:Fail++ }
function Warn-Check([string]$Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow; $script:Warn++ }

if ($Help) {
    Write-Host @"
Usage: .\deploy\preflight.ps1

Runs the WAMSS deployment preflight checklist on the local host.

Critical checks (exit code 1 when any fails):
  Python >= 3.10, docker present and reachable, docker compose plugin,
  >= 5 GB free disk space, ports 8000 and 8080 free.

Warning-only checks (never fail the run):
  BLE adapter presence (needed only for the optional smartwatch module).
"@
    exit 0
}

Write-Host "=== WAMSS preflight checklist ==="
Write-Host ""

# 1. Python >= 3.10 ----------------------------------------------------------
$py = $null
foreach ($candidate in @("py", "python", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { $py = $candidate; break }
}
if ($py) {
    try {
        $verOut = & $py -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
        $major, $minor = $verOut.Trim().Split(".")
        if (([int]$major -gt 3) -or (([int]$major -eq 3) -and ([int]$minor -ge 10))) {
            Pass-Check "Python $verOut (>= 3.10) via '$py'"
        } else {
            Fail-Check "Python $verOut found but version < 3.10"
        }
    } catch {
        Fail-Check "could not determine Python version ('$py')"
    }
} else {
    Fail-Check "no Python launcher found in PATH (need python >= 3.10; install from python.org)"
}

# 2. Docker ------------------------------------------------------------------
if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Pass-Check "docker present and reachable"
        } else {
            Fail-Check "docker installed but the daemon is not reachable (start Docker Desktop)"
        }
    } catch {
        Fail-Check "docker info failed; start Docker Desktop and retry"
    }
} else {
    Fail-Check "docker not found in PATH (install Docker Desktop)"
}

# 3. Docker compose plugin ---------------------------------------------------
try {
    docker compose version 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $cv = (docker compose version 2>$null) -join " "
        Pass-Check "docker compose plugin: $cv"
    } else {
        Fail-Check "docker compose plugin missing (update Docker Desktop)"
    }
} catch {
    Fail-Check "docker compose plugin missing (update Docker Desktop)"
}

# 4. Disk space >= 5 GB free -------------------------------------------------
$drive = (Get-Location).Drive
if (-not $drive) { $drive = "C" }
try {
    $freeBytes = (Get-PSDrive $drive.Name -ErrorAction Stop).Free
    if ($freeBytes -ge 5GB) {
        $freeGB = [math]::Round($freeBytes / 1GB, 1)
        Pass-Check "free disk space on $($drive.Name): ($freeGB GB >= 5 GB)"
    } else {
        $freeGB = [math]::Round($freeBytes / 1GB, 2)
        Fail-Check "free disk space on $($drive.Name): only $freeGB GB (need >= 5 GB)"
    }
} catch {
    Fail-Check "could not read free disk space: $($_.Exception.Message)"
}

# 5. Ports 8000 and 8080 free ------------------------------------------------
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
foreach ($port in @(8000, 8080)) {
    if (Test-PortFree $port) {
        Pass-Check "port $port is free"
    } else {
        Fail-Check "port $port is already in use (stop the conflicting service or remap the port in docker-compose.yml)"
    }
}

# 6. BLE adapter (warning only) ----------------------------------------------
$ble = $false
try {
    $devs = Get-PnpDevice -Class Bluetooth -Status OK -ErrorAction SilentlyContinue
    if ($devs) { $ble = $true }
} catch { $ble = $false }
if ($ble) {
    Pass-Check "BLE adapter detected (smartwatch module available)"
} else {
    Warn-Check "no BLE adapter detected; the agent will run with heuristic-only strain scoring (smartwatch heart-rate disabled)"
}

Write-Host ""
Write-Host "=== Result: $($script:Fail) critical failure(s), $($script:Warn) warning(s) ==="
if ($script:Fail -gt 0) {
    Write-Host "Preflight FAILED. Fix the items above and re-run." -ForegroundColor Red
    exit 1
}
Write-Host "Preflight OK. Next: .\deploy\deploy.ps1" -ForegroundColor Green
