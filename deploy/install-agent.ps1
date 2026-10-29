<#
.SYNOPSIS
    Install or uninstall the WAMSS workstation activity agent (Windows).

.DESCRIPTION
    Installs the agent as an always-on, at-logon Windows Scheduled Task:
    copies the agent sources, creates a Python virtualenv, installs
    agent/requirements.txt, writes config.env from interactive prompts (or
    parameters), and registers the task "WAMSS Activity Agent" that runs
    pythonw.exe on a launcher which loads config.env and starts the agent.

    Employee notice and consent are required before installing this agent on
    any workstation (see agent/README.md "Privacy and consent"). On first
    run the agent shows a graphical consent dialog; nothing is logged until
    the employee answers Yes.

.PARAMETER Uninstall
    Remove the scheduled task and the install directory, then exit.

.PARAMETER Force
    Skip confirmation prompts (used with -Uninstall).

.PARAMETER InstallDir
    Install location. Default: C:\ProgramData\WAMSS\agent

.PARAMETER ServerUrl
    Backend base URL, e.g. http://192.0.2.10:8000

.PARAMETER MachineId
    Stable id for this workstation, e.g. workstation-042

.PARAMETER EmployeeId
    Employee id for this workstation's primary user, e.g. employee-042

.PARAMETER OfficeId
    Office/site id (backend filters dashboards by it). Default: hq

.EXAMPLE
    .\deploy\install-agent.ps1
    (run in an elevated PowerShell; prompts for server URL and ids)
.EXAMPLE
    .\deploy\install-agent.ps1 -ServerUrl http://192.0.2.10:8000 -MachineId ws-042 -EmployeeId emp-042
.EXAMPLE
    .\deploy\install-agent.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Force,
    [string]$InstallDir = "$env:ProgramData\WAMSS\agent",
    [string]$ServerUrl,
    [string]$MachineId,
    [string]$EmployeeId,
    [string]$OfficeId = "hq"
)

$ErrorActionPreference = "Stop"
$TaskName = "WAMSS Activity Agent"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-Python {
    foreach ($candidate in @("py", "python", "python3")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $verOut = & $candidate -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
            $major, $minor = $verOut.Trim().Split(".")
            if (([int]$major -gt 3) -or (([int]$major -eq 3) -and ([int]$minor -ge 10))) {
                return $candidate
            }
        } catch { }
    }
    return $null
}

if (-not (Test-Admin)) {
    Write-Host "[FAIL] this installer needs an elevated PowerShell (Run as administrator)" -ForegroundColor Red
    exit 1
}

# ---- Uninstall --------------------------------------------------------------
if ($Uninstall) {
    if (-not $Force) {
        $answer = Read-Host "Remove scheduled task '$TaskName' and delete '$InstallDir'? (y/N)"
        if ($answer -ne "y" -and $answer -ne "Y") { Write-Host "Cancelled."; exit 0 }
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[OK] removed scheduled task '$TaskName'" -ForegroundColor Green
    } else {
        Write-Host "[..] no scheduled task named '$TaskName' found"
    }
    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
        Write-Host "[OK] deleted $InstallDir" -ForegroundColor Green
    } else {
        Write-Host "[..] $InstallDir does not exist"
    }
    exit 0
}

# ---- Install ----------------------------------------------------------------
Write-Host "==> WAMSS workstation agent installer (Windows)" -ForegroundColor Cyan

if (-not $ServerUrl)  { $ServerUrl  = Read-Host "Backend server URL (e.g. http://192.0.2.10:8000)" }
if (-not $MachineId)  { $MachineId  = Read-Host "Machine id (e.g. workstation-042)" }
if (-not $EmployeeId) { $EmployeeId = Read-Host "Employee id (e.g. employee-042)" }
if (-not $OfficeId)   { $OfficeId   = Read-Host "Office id (default: hq)" }
if (-not $OfficeId)   { $OfficeId = "hq" }

$python = Find-Python
if (-not $python) {
    Write-Host "[FAIL] no Python >= 3.10 found in PATH (install from python.org, then re-run)" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] using Python launcher '$python'" -ForegroundColor Green

$srcDir = Join-Path $repoRoot "agent"
if (-not (Test-Path (Join-Path $srcDir "activity_agent.py"))) {
    Write-Host "[FAIL] agent sources not found at $srcDir" -ForegroundColor Red
    exit 1
}

Write-Host "==> Copying agent sources to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
foreach ($f in @("activity_agent.py", "consent.py", "notifier.py", "strain.py", "usb_monitor.py", "wearable_ble.py", "requirements.txt")) {
    $src = Join-Path $srcDir $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $InstallDir $f) -Force }
}
Write-Host "[OK] sources copied"

Write-Host "==> Creating virtualenv"
& $python -m venv (Join-Path $InstallDir "venv")
$venvPython = Join-Path $InstallDir "venv\Scripts\python.exe"
$venvPip = Join-Path $InstallDir "venv\Scripts\pip.exe"
Write-Host "==> Installing dependencies (this can take a few minutes)"
& $venvPip install --upgrade pip | Out-Null
& $venvPip install -r (Join-Path $InstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] pip install failed; see output above" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] dependencies installed" -ForegroundColor Green

Write-Host "==> Writing config.env"
$configLines = @(
    "WAMSS_AGENT_SERVER_URL=$ServerUrl",
    "WAMSS_MACHINE_ID=$MachineId",
    "WAMSS_EMPLOYEE_ID=$EmployeeId",
    "WAMSS_OFFICE_ID=$OfficeId",
    "WAMSS_LOG_INTERVAL=60",
    "WAMSS_SYNC_INTERVAL=300",
    "WAMSS_SPOOL_DIR=$($InstallDir -replace '\\', '/')/spool"
)
$configLines | Set-Content -Path (Join-Path $InstallDir "config.env") -Encoding UTF8
Write-Host "[OK] config written"

Write-Host "==> Writing launcher"
$launcher = @'
"""WAMSS agent launcher: loads config.env into os.environ, then runs the agent."""
import os
import runpy

BASE = os.path.dirname(os.path.abspath(__file__))

def load_env(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)

load_env(os.path.join(BASE, "config.env"))
os.chdir(BASE)
runpy.run_path(os.path.join(BASE, "activity_agent.py"), run_name="__main__")
'@
$launcher | Set-Content -Path (Join-Path $InstallDir "launcher.py") -Encoding UTF8

Write-Host "==> Registering scheduled task '$TaskName'"
$exe = Join-Path $InstallDir "venv\Scripts\pythonw.exe"
$action = New-ScheduledTaskAction -Execute $exe -Argument "`"$(Join-Path $InstallDir 'launcher.py')`"" -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "WAMSS workstation activity logger (employee notice and consent required)" | Out-Null
Write-Host "[OK] scheduled task registered (runs at logon, hidden, no console window)" -ForegroundColor Green

Write-Host ""
Write-Host "================ Agent installed ================" -ForegroundColor Green
Write-Host "  Install dir: $InstallDir"
Write-Host "  Task:        $TaskName (AtLogOn)"
Write-Host "  Server:      $ServerUrl"
Write-Host "================================================"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. The agent starts at the next logon. On first run it shows a consent"
Write-Host "     dialog; nothing is logged until the employee answers Yes."
Write-Host "  2. To start it now without logging off: open Task Scheduler, find"
Write-Host "     '$TaskName', and choose Run."
Write-Host "  3. Verify records are spooling: check $InstallDir\spool\pending.jsonl"
Write-Host "     and the dashboard for machine id '$MachineId'."
Write-Host "  4. To remove later: .\deploy\install-agent.ps1 -Uninstall"
