[CmdletBinding()]
param(
    [string]$Executable,
    [ValidateSet("Install", "Remove")]
    [string]$Action = "Install"
)

$ErrorActionPreference = "Stop"
$TaskName = "WAMSS Work Monitor"
$StartupDir = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupDir "$TaskName.lnk"

if ($Action -eq "Remove") {
    if (Test-Path -LiteralPath $ShortcutPath) {
        Remove-Item -LiteralPath $ShortcutPath -Force
        Write-Host "Removed current-user startup shortcut: $ShortcutPath"
    } else {
        Write-Host "No current-user startup shortcut was installed."
    }
    exit 0
}

if (-not $Executable) {
    throw "Specify -Executable when installing the startup shortcut."
}
$ExecutablePath = (Resolve-Path -LiteralPath $Executable).Path
if ([IO.Path]::GetExtension($ExecutablePath) -ne ".exe") {
    throw "Executable must be the packaged WAMSSWorkMonitor .exe."
}
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $ExecutablePath
$Shortcut.WorkingDirectory = Split-Path -Parent $ExecutablePath
$Shortcut.Description = "Starts the consent-gated WAMSS Work Monitor for the signed-in user."
$Shortcut.Save()
Write-Host "Installed current-user startup shortcut: $ShortcutPath"
Write-Host "The monitor still requires its config.env and employee consent before collection."
