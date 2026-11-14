<#
.SYNOPSIS
    Install or uninstall the WAMSS workstation activity agent (Windows).

.DESCRIPTION
    Installs the collector as an explicitly employee-bound at-logon Scheduled
    Task. Secrets are entered through a masked prompt and stored only as
    machine-scope DPAPI ciphertext in a separately ACL-protected directory.
    The optional LocalSystem spool service is explicitly installed with its
    per-service SID enabled; that SID and the assigned interactive user's SID
    are the only principals granted read access to the protected token file.

.PARAMETER Uninstall
    Remove the scheduled task, sync service, and installation directory.

.PARAMETER Force
    Skip uninstall confirmation.

.PARAMETER UseWindowsService
    Run ordered batch upload in a boot-start service; keep collection in the
    consent-gated interactive user session.

.PARAMETER InstallDir
    Secure installation location. For a defensible ACL boundary this must be
    %ProgramData%\WAMSS\agent.

.PARAMETER InteractiveUser
    Optional assertion for the intended interactive Windows account. The
    account must be the currently signed-in console user; if omitted, that
    console user is used.

.PARAMETER ServerUrl
    Backend base URL, e.g. https://wamss.example.com

.PARAMETER MachineId
    Stable id for this workstation.

.PARAMETER EmployeeId
    Employee id mapped to the employee's personal token on the backend.

.PARAMETER OfficeId
    Office/site id (default: hq).
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Force,
    [switch]$UseWindowsService,
    [string]$InstallDir = "$env:ProgramData\WAMSS\agent",
    [string]$InteractiveUser,
    [string]$ServerUrl,
    [string]$MachineId,
    [string]$EmployeeId,
    [string]$OfficeId = "hq"
)

$ErrorActionPreference = "Stop"
$TaskName = "WAMSS Activity Agent"
$ServiceName = "WAMSSActivitySync"
$repoRoot = Split-Path -Parent $PSScriptRoot
$adminSid = [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
$systemSid = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = [Security.Principal.WindowsPrincipal]::new($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-Sid([string]$Identity) {
    try {
        if ($Identity -match '^S-\d(-\d+)+$') {
            return [Security.Principal.SecurityIdentifier]::new($Identity)
        }
        return ([Security.Principal.NTAccount]::new($Identity)).Translate(
            [Security.Principal.SecurityIdentifier]
        )
    } catch {
        throw "Could not resolve the Windows account '$Identity' to a SID."
    }
}

function Assert-NoReparsePath([string]$Path) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    $pathRoot = [IO.Path]::GetPathRoot($fullPath)
    $cursor = $pathRoot
    $tail = $fullPath.Substring($pathRoot.Length)
    foreach ($component in $tail.Split([char[]]@('\', '/'), [StringSplitOptions]::RemoveEmptyEntries)) {
        $cursor = Join-Path $cursor $component
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($item) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse points are not allowed in protected path '$cursor'."
            }
        } elseif (Test-Path -LiteralPath $cursor) {
            throw "Could not safely inspect protected path '$cursor'."
        } else {
            break
        }
    }
}

function Assert-NoReparseTree([string]$Root) {
    Assert-NoReparsePath $Root
    if (-not (Test-Path -LiteralPath $Root)) { return }
    foreach ($item in Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction Stop) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Reparse points are not allowed inside the WAMSS installation directory."
        }
    }
}

function New-AclSpec($Sid, [string]$Rights, [string]$Inheritance = $null, [string]$Propagation = $null) {
    return [pscustomobject]@{
        Sid = $Sid; Rights = $Rights; Inheritance = $Inheritance; Propagation = $Propagation
    }
}

function Assert-TrustedInstallerDirectory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $acl = Get-Acl -LiteralPath $Path
    $ownerSid = Resolve-Sid $acl.Owner
    $trustedSids = @($adminSid.Value, $systemSid.Value, $installerSid.Value)
    if ($ownerSid.Value -notin $trustedSids) {
        throw "The existing protected directory has an untrusted owner: '$Path'."
    }
    $writeRights = [Security.AccessControl.FileSystemRights]::Write -bor `
        [Security.AccessControl.FileSystemRights]::Modify -bor `
        [Security.AccessControl.FileSystemRights]::Delete -bor `
        [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor `
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor `
        [Security.AccessControl.FileSystemRights]::TakeOwnership -bor `
        [Security.AccessControl.FileSystemRights]::CreateFiles -bor `
        [Security.AccessControl.FileSystemRights]::CreateDirectories
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
            $trustedSids -contains $rule.IdentityReference.Value -or
            (($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0)) {
            continue
        }
        if (([int]$rule.FileSystemRights -band [int]$writeRights) -ne 0) {
            throw "An untrusted account can modify the protected directory '$Path'."
        }
    }
}

function Set-TrustedOwner([string]$Path) {
    $acl = Get-Acl -LiteralPath $Path
    $acl.SetOwner($adminSid)
    Set-Acl -LiteralPath $Path -AclObject $acl
    $ownerSid = Resolve-Sid (Get-Acl -LiteralPath $Path).Owner
    if ($ownerSid.Value -ne $adminSid.Value) {
        throw "Could not set the trusted owner for '$Path'."
    }
}

function Set-ExactAcl {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object[]]$Rules,
        [switch]$Directory,
        [switch]$Propagate
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        if ($Directory) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
        else { throw "Expected protected file is missing: $Path" }
    }
    $acl = Get-Acl -LiteralPath $Path
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($oldRule in @($acl.Access)) {
        $acl.RemoveAccessRuleSpecific($oldRule) | Out-Null
    }
    foreach ($spec in $Rules) {
        $rights = [Enum]::Parse([Security.AccessControl.FileSystemRights], [string]$spec.Rights)
        if ($Directory) {
            $inheritance = [Security.AccessControl.InheritanceFlags]::None
            $propagation = [Security.AccessControl.PropagationFlags]::None
            if ($null -ne $spec.Inheritance) {
                $inheritance = [Enum]::Parse([Security.AccessControl.InheritanceFlags], [string]$spec.Inheritance)
                if ($null -ne $spec.Propagation) {
                    $propagation = [Enum]::Parse([Security.AccessControl.PropagationFlags], [string]$spec.Propagation)
                }
            } elseif ($Propagate) {
                $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
                    [Security.AccessControl.InheritanceFlags]::ObjectInherit
            }
            $rule = [Security.AccessControl.FileSystemAccessRule]::new(
                $spec.Sid, $rights, $inheritance, $propagation,
                [Security.AccessControl.AccessControlType]::Allow
            )
        } else {
            $rule = [Security.AccessControl.FileSystemAccessRule]::new(
                $spec.Sid, $rights, [Security.AccessControl.AccessControlType]::Allow
            )
        }
        $acl.AddAccessRule($rule) | Out-Null
    }
    $acl.SetOwner($adminSid)
    Set-Acl -LiteralPath $Path -AclObject $acl

    $verified = Get-Acl -LiteralPath $Path
    if ((Resolve-Sid $verified.Owner).Value -ne $adminSid.Value) {
        throw "ACL owner verification failed for '$Path'."
    }
    if (-not $verified.AreAccessRulesProtected) {
        throw "ACL inheritance is not disabled for '$Path'."
    }
    $actualRules = @($verified.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($actualRules.Count -ne $Rules.Count) {
        throw "Unexpected ACL entries remain on '$Path'."
    }
    foreach ($actual in $actualRules) {
        $matches = @()
        foreach ($spec in $Rules) {
            if ($spec.Sid.Value -ne $actual.IdentityReference.Value) { continue }
            $expectedRights = [Enum]::Parse([Security.AccessControl.FileSystemRights], [string]$spec.Rights)
            if ([int]$actual.FileSystemRights -ne [int]$expectedRights) { continue }
            if ($Directory) {
                $expectedInheritance = [Security.AccessControl.InheritanceFlags]::None
                $expectedPropagation = [Security.AccessControl.PropagationFlags]::None
                if ($null -ne $spec.Inheritance) {
                    $expectedInheritance = [Enum]::Parse([Security.AccessControl.InheritanceFlags], [string]$spec.Inheritance)
                    if ($null -ne $spec.Propagation) {
                        $expectedPropagation = [Enum]::Parse([Security.AccessControl.PropagationFlags], [string]$spec.Propagation)
                    }
                } elseif ($Propagate) {
                    $expectedInheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
                        [Security.AccessControl.InheritanceFlags]::ObjectInherit
                }
                if ([int]$actual.InheritanceFlags -ne [int]$expectedInheritance -or
                    [int]$actual.PropagationFlags -ne [int]$expectedPropagation) { continue }
            }
            $matches += $spec
        }
        if ($matches.Count -ne 1 -or $actual.IsInherited -or
            $actual.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
            throw "ACL identity verification failed for '$Path'."
        }
    }
}

function Invoke-IcaclsReset([string]$Path) {
    $icacls = Join-Path $env:SystemRoot "System32\icacls.exe"
    & $icacls $Path /reset | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not reset an existing installation ACL." }
}

function Reset-ChildAcls([string]$Root) {
    if (-not (Test-Path -LiteralPath $Root)) { return }
    Assert-NoReparseTree $Root
    $items = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction Stop)
    # Reset directories top-down so every later child inherits from its final parent.
    $directories = @($items | Where-Object { $_.PSIsContainer } | Sort-Object { $_.FullName.Length })
    foreach ($item in $directories) {
        Invoke-IcaclsReset $item.FullName
        Set-TrustedOwner $item.FullName
    }
    foreach ($item in @($items | Where-Object { -not $_.PSIsContainer })) {
        Invoke-IcaclsReset $item.FullName
        Set-TrustedOwner $item.FullName
    }
}

function Remove-AgentTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        if ($task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
}

function Remove-AgentService {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service) {
        if ($service.Status -ne "Stopped") {
            Stop-Service -Name $ServiceName -Force -ErrorAction Stop
            $service.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::Stopped, [TimeSpan]::FromSeconds(30))
        }
        & sc.exe delete $ServiceName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not remove the existing WAMSS sync service." }
        for ($i = 0; $i -lt 30 -and (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue); $i++) {
            Start-Sleep -Seconds 1
        }
        if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
            throw "The existing WAMSS sync service has not been removed."
        }
    }
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
[Environment]::SetEnvironmentVariable("WAMSS_EMPLOYEE_TOKEN", $null, "Process")
[Environment]::SetEnvironmentVariable("WAMSS_API_KEY", $null, "Process")
$installerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User

# Use the system known-folder path instead of trusting a caller-controlled
# ProgramData environment variable.
$programData = [IO.Path]::GetFullPath(
    [Environment]::GetFolderPath([Environment+SpecialFolder]::CommonApplicationData)
).TrimEnd('\')
if (-not $programData) { throw "Windows CommonApplicationData could not be resolved." }
$secureInstallDir = [IO.Path]::GetFullPath((Join-Path $programData "WAMSS\agent")).TrimEnd('\')
$requestedInstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
if ($requestedInstallDir -ine $secureInstallDir) {
    throw "For secure Windows ACLs, InstallDir must be '$secureInstallDir'."
}
$InstallDir = $secureInstallDir
$installParent = Split-Path -Parent $InstallDir
$secretDir = Join-Path $InstallDir "secrets"
$spoolDir = Join-Path $InstallDir "spool"
foreach ($protectedPath in @($programData, $installParent, $InstallDir, $secretDir, $spoolDir)) {
    Assert-NoReparsePath $protectedPath
}
Assert-TrustedInstallerDirectory $installParent
if (Test-Path -LiteralPath $InstallDir) {
    Assert-NoReparseTree $InstallDir
}

# ---- Uninstall --------------------------------------------------------------
if ($Uninstall) {
    if (-not $Force) {
        $answer = Read-Host "Remove task '$TaskName', service '$ServiceName', and delete '$InstallDir' including its local spool? (y/N)"
        if ($answer -ne "y" -and $answer -ne "Y") { Write-Host "Cancelled."; exit 0 }
    }
    if (Test-Path -LiteralPath $InstallDir) {
        $parentEntries = @(Get-ChildItem -LiteralPath $installParent -Force -ErrorAction Stop)
        if ($parentEntries.Count -ne 1 -or $parentEntries[0].FullName -ine $InstallDir) {
            throw "The WAMSS ProgramData directory contains sibling data; refusing to alter or remove it."
        }
    }
    Remove-AgentTask
    Remove-AgentService
    if (Test-Path -LiteralPath $InstallDir) {
        $uninstallParentRules = @(
            (New-AclSpec $adminSid "FullControl"),
            (New-AclSpec $systemSid "FullControl")
        )
        if (Test-Path -LiteralPath $installParent) {
            Set-ExactAcl -Path $installParent -Directory -Rules $uninstallParentRules
        }
        Set-ExactAcl -Path $InstallDir -Directory -Propagate -Rules @(
            (New-AclSpec $adminSid "FullControl"),
            (New-AclSpec $systemSid "FullControl")
        )
        Reset-ChildAcls $InstallDir
        Remove-Item -LiteralPath $InstallDir -Recurse -Force
        Write-Host "[OK] deleted $InstallDir" -ForegroundColor Green
    } else {
        Write-Host "[..] $InstallDir does not exist"
    }
    exit 0
}

# ---- Install ----------------------------------------------------------------
Write-Host "==> WAMSS Windows agent secure installer" -ForegroundColor Cyan

# The sync service resolves the active console user. Bind the collector task and
# credential ACL to that same account, not to the elevated installer identity.
$consoleUser = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).UserName
if (-not $consoleUser) {
    throw "Sign in to the intended employee's Windows console account before installing."
}
if ($InteractiveUser -and $InteractiveUser.Trim() -ine $consoleUser.Trim()) {
    throw "InteractiveUser must match the currently signed-in console account '$consoleUser'."
}
$interactiveUser = $consoleUser.Trim()
$userSid = Resolve-Sid $interactiveUser
$interactiveSid = $userSid.Value
$interactiveUserConfig = $interactiveUser

if (-not $ServerUrl)  { $ServerUrl  = Read-Host "Backend server URL (use HTTPS except for loopback)" }
if (-not $MachineId)  { $MachineId  = Read-Host "Machine id (e.g. workstation-042)" }
if (-not $EmployeeId) { $EmployeeId = Read-Host "Employee id mapped to this user's personal token" }
if (-not $OfficeId)   { $OfficeId   = Read-Host "Office id (default: hq)" }
if (-not $OfficeId)   { $OfficeId = "hq" }
$ServerUrl = $ServerUrl.Trim()
$MachineId = $MachineId.Trim()
$EmployeeId = $EmployeeId.Trim()
$OfficeId = $OfficeId.Trim()
if ([string]::IsNullOrWhiteSpace($ServerUrl) -or
    [string]::IsNullOrWhiteSpace($MachineId) -or
    [string]::IsNullOrWhiteSpace($EmployeeId)) {
    throw "Server URL, machine id, and employee id are required and cannot be blank."
}
foreach ($value in @($ServerUrl, $MachineId, $EmployeeId, $OfficeId)) {
    if ($value -match '[\r\n]') { throw "Configuration values cannot contain line breaks." }
}

# Reassignment of a ProgramData install could expose old ciphertext or local
# spool data to a different employee. Require explicit uninstall first.
$configPath = Join-Path $InstallDir "config.env"
if (Test-Path -LiteralPath $InstallDir) {
    throw "An existing installation must be removed with -Uninstall before secure installation or reassignment."
}
if (Test-Path -LiteralPath $installParent) {
    $parentEntries = @(Get-ChildItem -LiteralPath $installParent -Force -ErrorAction Stop)
    if ($parentEntries.Count -gt 0) {
        throw "The WAMSS ProgramData directory contains unmanaged entries; remove or relocate them before installing."
    }
}

$python = Find-Python
if (-not $python) { throw "No Python >= 3.10 found in PATH (install Python, then re-run)." }
$srcDir = Join-Path $repoRoot "agent"
if (-not (Test-Path (Join-Path $srcDir "activity_agent.py"))) {
    throw "Agent sources not found at $srcDir."
}

# Stop any stale principals before creating the new installation.
Remove-AgentTask
Remove-AgentService

# Restrict the parent and install tree before copying executable code. The
# service SID is added after the service is registered below.
New-Item -ItemType Directory -Force -Path $installParent | Out-Null
Assert-TrustedInstallerDirectory $installParent
Set-ExactAcl -Path $installParent -Directory -Rules @(
    (New-AclSpec $adminSid "FullControl"),
    (New-AclSpec $systemSid "FullControl"),
    (New-AclSpec $userSid "ReadAndExecute")
)
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Set-ExactAcl -Path $InstallDir -Directory -Propagate -Rules @(
    (New-AclSpec $adminSid "FullControl"),
    (New-AclSpec $userSid "ReadAndExecute")
)

Write-Host "==> Copying agent sources to $InstallDir"
foreach ($f in @("activity_agent.py", "consent.py", "notifier.py", "strain.py", "usb_monitor.py", "wearable_ble.py", "windows_sync_service.py", "secret_store.py", "requirements.txt")) {
    $src = Join-Path $srcDir $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $InstallDir $f) -Force }
}

Write-Host "==> Creating virtualenv"
& $python -m venv (Join-Path $InstallDir "venv")
if ($LASTEXITCODE -ne 0) { throw "Could not create the agent virtual environment." }
$venvPython = Join-Path $InstallDir "venv\Scripts\python.exe"
$venvPip = Join-Path $InstallDir "venv\Scripts\pip.exe"
Write-Host "==> Installing dependencies"
& $venvPip install --upgrade pip | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not update pip in the agent environment." }
& $venvPip install -r (Join-Path $InstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not install agent dependencies." }

# The optional sync service is created under LocalSystem explicitly, then
# configured with its unrestricted per-service SID before any secret is stored.
$serviceSid = $null
if ($UseWindowsService) {
    $serviceScript = Join-Path $InstallDir "windows_sync_service.py"
    Write-Host "==> Installing the optional sync service as LocalSystem (manual start until ACL setup)"
    & $venvPython $serviceScript install --startup manual --username LocalSystem
    if ($LASTEXITCODE -ne 0) { throw "Could not install the WAMSS sync service." }
    $serviceInfo = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
    if (-not $serviceInfo -or $serviceInfo.StartName.Trim().ToUpperInvariant() -notin @("LOCALSYSTEM", "NT AUTHORITY\SYSTEM")) {
        throw "The WAMSS sync service is not configured to run as LocalSystem."
    }
    & sc.exe sidtype $ServiceName unrestricted | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not enable the WAMSS per-service SID." }
    $sidStatus = (& sc.exe qsidtype $ServiceName 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $sidStatus -notmatch "(?i)UNRESTRICTED") {
        throw "The WAMSS service SID type could not be verified as unrestricted."
    }
    $serviceSid = Resolve-Sid "NT SERVICE\$ServiceName"
}

# Add the exact service SID to the install boundary, if selected.
$parentRules = @(
    (New-AclSpec $adminSid "FullControl"),
    (New-AclSpec $systemSid "FullControl"),
    (New-AclSpec $userSid "ReadAndExecute")
)
$installRules = @(
    (New-AclSpec $adminSid "FullControl"),
    (New-AclSpec $userSid "ReadAndExecute")
)
if ($serviceSid) {
    $parentRules += New-AclSpec $serviceSid "ReadAndExecute"
    $installRules += New-AclSpec $serviceSid "ReadAndExecute"
}
Set-ExactAcl -Path $installParent -Directory -Rules $parentRules
Set-ExactAcl -Path $InstallDir -Directory -Propagate -Rules $installRules
Reset-ChildAcls $InstallDir

# In service mode, the employee can append only to a precreated queue file:
# they cannot create/replace spool entries that a LocalSystem service might
# otherwise follow as a reparse point. The service SID alone can rotate/upload.
if ($serviceSid) {
    Set-ExactAcl -Path $spoolDir -Directory -Rules @(
        (New-AclSpec $adminSid "FullControl"),
        (New-AclSpec $userSid "ReadAndExecute"),
        (New-AclSpec $serviceSid "ReadAndExecute")
    )
    $spoolScopeScript = "import hashlib,sys; print(hashlib.sha256((sys.argv[1] + chr(0) + sys.argv[2].casefold()).encode('utf-8')).hexdigest()[:24])"
    $spoolScope = (& $venvPython -c $spoolScopeScript $EmployeeId $interactiveUser).Trim()
    if ($LASTEXITCODE -ne 0 -or $spoolScope -notmatch '^[0-9a-f]{24}$') {
        throw "Could not derive the bound employee spool path."
    }
    $identitySpoolDir = Join-Path $spoolDir $spoolScope
    Assert-NoReparsePath $identitySpoolDir
    New-Item -ItemType Directory -Path $identitySpoolDir | Out-Null
    $identitySpoolRules = @(
        (New-AclSpec $adminSid "FullControl"),
        (New-AclSpec $userSid "ReadAndExecute"),
        (New-AclSpec $userSid "AppendData" "ObjectInherit" "InheritOnly"),
        (New-AclSpec $serviceSid "Modify")
    )
    Set-ExactAcl -Path $identitySpoolDir -Directory -Propagate -Rules $identitySpoolRules
    $pendingFile = Join-Path $identitySpoolDir "pending.jsonl"
    $lockFile = Join-Path $identitySpoolDir ".spool.lock"
    New-Item -ItemType File -Path $pendingFile | Out-Null
    New-Item -ItemType File -Path $lockFile | Out-Null
    Set-ExactAcl -Path $pendingFile -Rules @(
        (New-AclSpec $adminSid "FullControl"),
        (New-AclSpec $userSid "ReadAndExecute, AppendData"),
        (New-AclSpec $serviceSid "Modify")
    )
    Set-ExactAcl -Path $lockFile -Rules @(
        (New-AclSpec $adminSid "FullControl"),
        (New-AclSpec $userSid "ReadAndExecute, WriteData, AppendData"),
        (New-AclSpec $serviceSid "Modify")
    )
} else {
    Set-ExactAcl -Path $spoolDir -Directory -Propagate -Rules @(
        (New-AclSpec $adminSid "FullControl"),
        (New-AclSpec $userSid "Modify")
    )
}

Write-Host "==> Writing nonsecret config.env"
$secretDirConfig = $secretDir -replace '\\', '/'
$configLines = @(
    "WAMSS_AGENT_SERVER_URL=$ServerUrl",
    "WAMSS_MACHINE_ID=$MachineId",
    "WAMSS_EMPLOYEE_ID=$EmployeeId",
    "WAMSS_INTERACTIVE_USER=$interactiveUserConfig",
    "WAMSS_INTERACTIVE_SID=$interactiveSid",
    "WAMSS_SECRET_DIR=$secretDirConfig",
    "WAMSS_OFFICE_ID=$OfficeId",
    "WAMSS_LOG_INTERVAL=60",
    "WAMSS_SYNC_INTERVAL=300",
    "WAMSS_SYNC_ENABLED=$(if ($UseWindowsService) { '0' } else { '1' })",
    "WAMSS_SPOOL_DIR=$($InstallDir -replace '\\', '/')/spool"
)
$configLines | Set-Content -Path $configPath -Encoding UTF8

# Temporary setup ACL: only the elevated installer can write; the intended
# task user and (if selected) exact service SID can read/traverse.
$secretDirRules = @(
    (New-AclSpec $adminSid "FullControl"),
    (New-AclSpec $userSid "ReadAndExecute")
)
if ($serviceSid) { $secretDirRules += New-AclSpec $serviceSid "ReadAndExecute" }
Set-ExactAcl -Path $secretDir -Directory -Propagate -Rules $secretDirRules

Write-Host "==> Provisioning the employee's personal token"
Write-Host "The assigned employee should enter their own employee-scoped token at the masked prompt. The deployment-wide API key is not distributed to this workstation." -ForegroundColor Yellow
$secretStoreScript = Join-Path $InstallDir "secret_store.py"
& $venvPython $secretStoreScript --secret-dir $secretDir
if ($LASTEXITCODE -ne 0) { throw "Protected employee-token provisioning failed; the agent will not be scheduled or started." }

# The install directory was required to be fresh; fail if provisioning left
# anything except the single expected encrypted token blob.
$secretEntries = @(Get-ChildItem -LiteralPath $secretDir -Force -ErrorAction Stop)
if ($secretEntries.Count -ne 1 -or $secretEntries[0].Name -cne "employee-token.dpapi" -or
    $secretEntries[0].PSIsContainer -or
    (($secretEntries[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
    throw "Credential storage contains unexpected files or directories."
}
$secretReadRules = @((New-AclSpec $userSid "Read"))
if ($serviceSid) { $secretReadRules += New-AclSpec $serviceSid "Read" }
$secretDirFinalRules = @((New-AclSpec $userSid "ReadAndExecute"))
if ($serviceSid) { $secretDirFinalRules += New-AclSpec $serviceSid "ReadAndExecute" }
Set-ExactAcl -Path $secretDir -Directory -Rules $secretDirFinalRules
Set-ExactAcl -Path (Join-Path $secretDir "employee-token.dpapi") -Rules $secretReadRules

# The normal collector receives no deployment API key. Its employee token is
# mapped server-side to one employee; any service-ingest exception still needs
# API-key middleware validation and cannot use this employee-token exemption.
$launcher = @'
"""WAMSS agent launcher: load nonsecret config and DPAPI credentials, then run."""
import os
import runpy
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SECRET_CONFIG_KEYS = {"WAMSS_EMPLOYEE_TOKEN", "WAMSS_API_KEY"}

def load_env(path):
    if not path.exists():
        raise SystemExit("WAMSS agent configuration is missing; refusing to start.")
    with path.open(encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key in SECRET_CONFIG_KEYS:
                continue
            os.environ[key] = value.strip().strip('"').strip("'")

load_env(BASE / "config.env")
try:
    from secret_store import current_user_sid, load_into_environment
    secret_dir = os.environ.get("WAMSS_SECRET_DIR")
    if not secret_dir:
        raise RuntimeError("credential directory is not configured")
    load_into_environment(secret_dir, required=True)
    import consent
    expected_user = os.environ.get("WAMSS_INTERACTIVE_USER", "").strip().casefold()
    expected_sid = os.environ.get("WAMSS_INTERACTIVE_SID", "").strip().casefold()
    if (not expected_user or consent.current_os_user() != expected_user
            or not expected_sid or current_user_sid().casefold() != expected_sid):
        raise RuntimeError("interactive Windows account does not match the configured account")
except Exception as exc:
    print(f"WAMSS protected startup failed ({type(exc).__name__}); refusing to start.", file=sys.stderr)
    raise SystemExit(1)

os.chdir(BASE)
runpy.run_path(str(BASE / "activity_agent.py"), run_name="__main__")
'@
$launcher | Set-Content -Path (Join-Path $InstallDir "launcher.py") -Encoding UTF8

Write-Host "==> Registering the scheduled task for '$interactiveUser'"
$exe = Join-Path $InstallDir "venv\Scripts\pythonw.exe"
$launcherPath = Join-Path $InstallDir "launcher.py"
$action = New-ScheduledTaskAction -Execute $exe -Argument ('"{0}"' -f $launcherPath) -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $interactiveUser -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "WAMSS workstation activity logger (employee notice and consent required)" | Out-Null
$registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ((Resolve-Sid $registered.Principal.UserId).Value -ne $userSid.Value -or
    $registered.Principal.LogonType.ToString() -ine "Interactive") {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    throw "Scheduled-task identity verification failed."
}
Write-Host "[OK] task is bound to the intended interactive account with limited privileges" -ForegroundColor Green

if ($UseWindowsService) {
    # Start only after its identity, SID, and protected credential ACL are set.
    & sc.exe config $ServiceName start= auto | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not configure automatic service startup." }
    & $venvPython $serviceScript start
    if ($LASTEXITCODE -ne 0) { throw "Could not start the WAMSS sync service." }
    $running = Get-Service -Name $ServiceName -ErrorAction Stop
    if ($running.Status -ne "Running") { throw "The WAMSS sync service did not reach Running state." }
    Write-Host "[OK] LocalSystem sync service is running with its verified service SID" -ForegroundColor Green
}

Write-Host ""
Write-Host "================ Agent installed ================" -ForegroundColor Green
Write-Host "  Install dir: $InstallDir"
Write-Host "  Task:        $TaskName (AtLogOn as $interactiveUser)"
if ($UseWindowsService) { Write-Host "  Service:     $ServiceName (LocalSystem, service SID ACL, Automatic)" }
Write-Host "  Server:      $ServerUrl"
Write-Host "  Credentials: employee token encrypted by machine-scope DPAPI; no deployment API key stored"
Write-Host "================================================"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. The agent starts at the next logon. On first run it shows a consent dialog; nothing is logged until the employee answers Yes."
if ($UseWindowsService) {
    Write-Host "  2. The collector runs only as '$interactiveUser'; the LocalSystem service syncs only that account's consented spool."
}
Write-Host "  3. To start the collector now without logging off, open Task Scheduler and choose Run for '$TaskName'."
Write-Host "  4. To remove later: .\deploy\install-agent.ps1 -Uninstall"
