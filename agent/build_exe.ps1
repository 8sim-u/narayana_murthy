[CmdletBinding()]
param(
    [string]$Python = "py",
    [string]$Name = "WAMSSWorkMonitor"
)

$ErrorActionPreference = "Stop"
$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildVenv = Join-Path $AgentDir ".venv-build"
$PythonExe = Join-Path $BuildVenv "Scripts\python.exe"
$Requirements = Join-Path $AgentDir "requirements-exe.txt"
$EntryPoint = Join-Path $AgentDir "work_monitor.py"

if (-not (Test-Path $BuildVenv)) {
    & $Python -3 -m venv $BuildVenv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the build virtual environment." }
}

& $PythonExe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not update pip in the build environment." }
& $PythonExe -m pip install -r $Requirements
if ($LASTEXITCODE -ne 0) { throw "Could not install the executable build dependencies." }

Push-Location $AgentDir
try {
    & $PythonExe -m PyInstaller --noconfirm --clean --onefile --windowed `
        --hidden-import consent --hidden-import secret_store --name $Name --distpath (Join-Path $AgentDir "dist") `
        --workpath (Join-Path $AgentDir "build") $EntryPoint
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed to build $Name.exe." }
} finally {
    Pop-Location
}

$Output = Join-Path $AgentDir "dist\$Name.exe"
if (-not (Test-Path $Output)) { throw "Expected executable was not created: $Output" }
Write-Host "Built $Output" -ForegroundColor Green

# Ship the monitor config beside the executable. A real config.env is
# copied as-is; otherwise the starter template is copied so the operator
# can see what to fill in instead of hitting a bare exit at startup.
$ConfigSource = Join-Path $AgentDir "config.env"
$ConfigTarget = Join-Path $AgentDir "dist\config.env"
if (Test-Path $ConfigSource) {
    Copy-Item $ConfigSource $ConfigTarget -Force
    Write-Host "Copied config.env beside the executable." -ForegroundColor Green
} else {
    Copy-Item (Join-Path $AgentDir "config.env.example") (Join-Path $AgentDir "dist\config.env.example") -Force
    Write-Host "No config.env found; copied config.env.example beside the executable. Copy it to config.env and fill in the server URL before running." -ForegroundColor Yellow
}
