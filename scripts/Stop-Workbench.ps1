param([string]$ProjectPath = (Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference = 'Stop'
$autostartTask = Get-ScheduledTask -TaskName 'ScriptWorkbench' -ErrorAction SilentlyContinue
if ($autostartTask -and $autostartTask.State -eq 'Running') { Stop-ScheduledTask -TaskName 'ScriptWorkbench' }
$keeperPath = Join-Path $env:ProgramData 'ScriptWorkbench\keeper.pid'
if (Test-Path -LiteralPath $keeperPath) {
    $keeperPid = [int](Get-Content -LiteralPath $keeperPath -Raw)
    $keeperProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$keeperPid" -ErrorAction SilentlyContinue
    if ($keeperProcess -and $keeperProcess.Name -eq 'wsl.exe' -and $keeperProcess.CommandLine -match '/usr/bin/sleep infinity') {
        & "$env:WINDIR\System32\taskkill.exe" /PID $keeperPid /T /F | Out-Null
    }
    Remove-Item -LiteralPath $keeperPath -ErrorAction SilentlyContinue
}
$projectPath = $ProjectPath
$pidPath = Join-Path $projectPath 'data\gateway.pid'
if (Test-Path -LiteralPath $pidPath) {
    $gatewayPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$gatewayPid" -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine -and $process.CommandLine.Contains('windows_gateway.py') -and $process.CommandLine.Contains($projectPath)) {
        Stop-Process -Id $gatewayPid
    }
    Remove-Item -LiteralPath $pidPath
}
& "$env:USERPROFILE\.codex\bin\Invoke-WslProject.ps1" -WorkingDirectory $projectPath -FilePath systemctl -ArgumentList @('--user', 'stop', 'script-workbench.service')
if ($LASTEXITCODE -ne 0) { throw 'WSL service stop failed' }
Write-Output '工作台已停止，库存数据保留。'
