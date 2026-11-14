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
