$ErrorActionPreference = 'Stop'
$projectPath = Split-Path $PSScriptRoot -Parent
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
