param(
    [string]$LanHost,
    [string]$PythonPath,
    [switch]$Direct,
    [string]$ProjectPath = (Split-Path $PSScriptRoot -Parent)
)
$ErrorActionPreference = 'Stop'
function Resolve-WorkbenchLanHost {
    param([string]$Value, [string]$ConfigPath = (Join-Path $env:ProgramData 'ScriptWorkbench\config.json'))
    if (-not [string]::IsNullOrWhiteSpace($Value)) { return $Value }
    if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
        try {
            $configured = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
            if ($configured.LanHost -is [string] -and -not [string]::IsNullOrWhiteSpace($configured.LanHost)) {
                return $configured.LanHost
            }
        } catch { throw 'Unable to read existing workbench configuration; provide -LanHost explicitly.' }
    }
    throw 'Provide -LanHost explicitly. A first-time setup has no default LAN address.'
}
function Resolve-WorkbenchPython {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if (-not $launcher) { throw 'Python 3.12 launcher is unavailable; provide -PythonPath explicitly.' }
        $discovered = @(& $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null)
        if ($LASTEXITCODE -ne 0 -or $discovered.Count -ne 1) {
            throw 'Python 3.12 could not be discovered; provide -PythonPath explicitly.'
        }
        $Value = [string]$discovered[0]
    }
    if ([string]::IsNullOrWhiteSpace($Value) -or -not (Test-Path -LiteralPath $Value -PathType Leaf)) {
        throw 'Windows Python executable is unavailable; provide a valid -PythonPath.'
    }
    return $Value
}
$LanHost = Resolve-WorkbenchLanHost -Value $LanHost
function Test-GatewayHealth {
    param([string]$CurrentLanHost)
    try {
        $localHealth = Invoke-RestMethod 'http://localhost:8788/api/health' -TimeoutSec 3
        $lanHealth = Invoke-RestMethod "http://${CurrentLanHost}:8787/api/health" -TimeoutSec 3
        $localCapabilities = Invoke-RestMethod 'http://localhost:8788/api/capabilities' -TimeoutSec 3
        $lanCapabilities = Invoke-RestMethod "http://${CurrentLanHost}:8787/api/capabilities" -TimeoutSec 3
        return ($localHealth.service -eq 'script-workbench' -and $lanHealth.service -eq 'script-workbench' -and $localCapabilities.can_open_folder -eq $true -and $lanCapabilities.can_open_folder -eq $false)
    } catch { return $false }
}
$autostartTask = Get-ScheduledTask -TaskName 'ScriptWorkbench' -ErrorAction SilentlyContinue
if (-not $Direct -and $autostartTask) {
    Enable-ScheduledTask -TaskName 'ScriptWorkbench' | Out-Null
    if ($autostartTask.State -ne 'Running') { Start-ScheduledTask -TaskName 'ScriptWorkbench' }
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-GatewayHealth $LanHost) {
            Write-Output "本机入口：http://localhost:8788/"
            Write-Output "局域网入口：http://${LanHost}:8787/"
            exit 0
        }
        Start-Sleep -Seconds 1
    }
    throw 'Autostart task has not become healthy; inspect %ProgramData%\ScriptWorkbench\autostart.log'
}
$PythonPath = Resolve-WorkbenchPython -Value $PythonPath
$projectPath = $ProjectPath
$executor = "$env:USERPROFILE\.codex\bin\Invoke-WslProject.ps1"
& $executor -WorkingDirectory $projectPath -FilePath systemctl -ArgumentList @('--user', 'start', 'script-workbench.service')
if ($LASTEXITCODE -ne 0) { throw 'WSL service start failed' }
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8789/api/health' -TimeoutSec 2
        if ($health.service -eq 'script-workbench' -and $health.status -eq 'ok') {
            $ready = $true
            break
        }
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { throw 'WSL service did not become ready on port 8789' }
$pidPath = Join-Path $projectPath 'data\gateway.pid'
if (Test-Path -LiteralPath $pidPath) {
    $gatewayPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$gatewayPid" -ErrorAction SilentlyContinue
    if ($existing -and $existing.CommandLine -and $existing.CommandLine.Contains('windows_gateway.py') -and $existing.CommandLine.Contains($projectPath)) {
        if ($existing.CommandLine.Contains("--lan-host $LanHost ") -and (Test-GatewayHealth $LanHost)) {
            Write-Output "Workbench already running: http://localhost:8788/ | http://${LanHost}:8787/"
            exit 0
        }
        Stop-Process -Id $gatewayPid
    }
}
$gatewayPath = Join-Path $projectPath 'scripts\windows_gateway.py'
$keyPath = Join-Path $projectPath 'data\host.key'
$arguments = @(('"' + $gatewayPath + '"'), '--lan-host', $LanHost, '--host-key-file', ('"' + $keyPath + '"'))
$process = Start-Process -FilePath $PythonPath -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $projectPath 'data\gateway.stdout.log') -RedirectStandardError (Join-Path $projectPath 'data\gateway.stderr.log')
Set-Content -LiteralPath $pidPath -Value $process.Id -NoNewline
Start-Sleep -Milliseconds 500
if ($process.HasExited) { throw 'Windows gateway exited; inspect data/gateway.stderr.log' }
if (-not (Test-GatewayHealth $LanHost)) {
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue
    throw 'Gateway readiness or host/LAN capability verification failed; inspect data/gateway.stderr.log'
}
Write-Output "本机入口：http://localhost:8788/"
Write-Output "局域网入口：http://${LanHost}:8787/"
