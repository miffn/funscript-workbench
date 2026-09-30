param(
    [string]$LanHost = '192.0.2.6',
    [string]$PythonPath = 'C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe',
    [string]$ProjectPath = (Split-Path $PSScriptRoot -Parent)
)
$ErrorActionPreference = 'Stop'
$projectPath = $ProjectPath
$pathParts = $projectPath.TrimStart([char]92).Split([char]92)
if ($pathParts.Count -lt 6 -or $pathParts[0] -notin @('wsl.localhost','wsl$') -or $pathParts[2] -ne 'home' -or $pathParts[4] -ne 'projects') {
    throw 'ProjectPath must identify the selected WSL checkout under /home/<user>/projects.'
}
$executor = Join-Path $env:USERPROFILE '.codex\bin\Invoke-WslProject.ps1'
if (-not (Test-Path -LiteralPath $executor)) { throw 'WSL project executor is missing.' }
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Windows Python executable does not exist.' }
$taskName = 'ScriptWorkbench'
$runtimeDir = Join-Path $env:ProgramData 'ScriptWorkbench'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$directoryAcl = Get-Acl -LiteralPath $runtimeDir
$allowedSids = @('S-1-5-18','S-1-5-32-544',[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
$needsAcl = -not $directoryAcl.AreAccessRulesProtected -or $directoryAcl.Access.Count -ne 3
foreach ($existingRule in $directoryAcl.Access) {
    $ruleSid = $existingRule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    if ($ruleSid -notin $allowedSids -or $existingRule.AccessControlType -ne 'Allow' -or $existingRule.FileSystemRights -ne 'FullControl') { $needsAcl = $true }
}
if ($needsAcl) {
$directoryAcl = New-Object System.Security.AccessControl.DirectorySecurity
$directoryAcl.SetAccessRuleProtection($true, $false)
$inheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
foreach ($sid in $allowedSids) {
    $identity = New-Object System.Security.Principal.SecurityIdentifier($sid)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', $inheritance, 'None', 'Allow')
    $directoryAcl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $runtimeDir -AclObject $directoryAcl
}
$previousTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($previousTask -and $previousTask.State -eq 'Running') { Stop-ScheduledTask -TaskName $taskName }
$keeperPath = Join-Path $runtimeDir 'keeper.pid'
if (Test-Path -LiteralPath $keeperPath) {
    $keeperPid = [int](Get-Content -LiteralPath $keeperPath -Raw)
    $keeperProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$keeperPid" -ErrorAction SilentlyContinue
    if ($keeperProcess -and $keeperProcess.Name -eq 'wsl.exe' -and $keeperProcess.CommandLine -match '/usr/bin/sleep infinity') {
        & "$env:WINDIR\System32\taskkill.exe" /PID $keeperPid /T /F | Out-Null
    }
    Remove-Item -LiteralPath $keeperPath -ErrorAction SilentlyContinue
}
foreach ($name in @('Run-Workbench.ps1','Start-Workbench.ps1','Stop-Workbench.ps1')) {
    Copy-Item -LiteralPath (Join-Path $projectPath "scripts\$name") -Destination (Join-Path $runtimeDir $name) -Force
}
$configPath = Join-Path $runtimeDir 'config.json'
@{ProjectPath=$projectPath;PythonPath=$PythonPath;LanHost=$LanHost;Distro=$pathParts[1];WslUser=$pathParts[3]} | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
$powerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -ConfigPath "{1}"' -f (Join-Path $runtimeDir 'Run-Workbench.ps1'), $configPath
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $userId
$logon.Delay = 'PT15S'
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $logon -Principal $principal `
    -Settings $settings -Description 'Start the workbench at Windows login and recover failures independently of Codex.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output ('Autostart installed: task={0}, mode=Logon, log={1}' -f $taskName,(Join-Path $runtimeDir 'autostart.log'))
