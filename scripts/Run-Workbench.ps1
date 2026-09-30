param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$runtimeDir = Split-Path $ConfigPath -Parent
$wslExe = Join-Path $env:WINDIR 'System32\wsl.exe'
$logPath = Join-Path $runtimeDir 'autostart.log'
function Write-RunnerLog([string]$Message) {
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value ('{0:o} {1}' -f (Get-Date), $Message)
}
function Test-WorkbenchReady([string]$Address) {
    try {
        $local = Invoke-RestMethod 'http://localhost:8788/api/health' -TimeoutSec 3
        $lan = Invoke-RestMethod "http://${Address}:8787/api/health" -TimeoutSec 3
        return ($local.status -eq 'ok' -and $local.service -eq 'script-workbench' -and $lan.status -eq 'ok' -and $lan.service -eq 'script-workbench')
    } catch { return $false }
}
$mutexName = 'Global\ScriptWorkbench-' + [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$ownsMutex = $false
$keeper = $null
try {
    try { $ownsMutex = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { exit 0 }
    Set-Content -LiteralPath (Join-Path $runtimeDir 'runner.pid') -Value $PID -NoNewline
    Write-RunnerLog ('Runner started, pid={0}' -f $PID)
    $wasReady = $false
    while ($true) {
        try {
            # An attached WSL command keeps the distro alive independently of Codex.
            if (-not $keeper -or $keeper.HasExited) {
                $keeperArgs = @('-d',$config.Distro,'--user',$config.WslUser,'--exec','/usr/bin/sleep','infinity')
                $keeper = Start-Process -FilePath $wslExe -ArgumentList $keeperArgs -WindowStyle Hidden -PassThru `
                    -RedirectStandardOutput (Join-Path $runtimeDir 'keeper.stdout.log') `
                    -RedirectStandardError (Join-Path $runtimeDir 'keeper.stderr.log')
                Set-Content -LiteralPath (Join-Path $runtimeDir 'keeper.pid') -Value $keeper.Id -NoNewline
                Write-RunnerLog ('WSL keeper started, pid={0}' -f $keeper.Id)
            }
            if (-not (Test-WorkbenchReady $config.LanHost)) {
                & (Join-Path $runtimeDir 'Start-Workbench.ps1') -ProjectPath $config.ProjectPath -LanHost $config.LanHost -PythonPath $config.PythonPath -Direct
                if ($LASTEXITCODE -ne 0) { throw 'Workbench startup failed.' }
                if (-not (Test-WorkbenchReady $config.LanHost)) { throw 'Workbench health check failed.' }
                $wasReady = $false
            }
            if (-not $wasReady) {
                Write-RunnerLog ('Workbench ready: http://localhost:8788/ | http://{0}:8787/' -f $config.LanHost)
                $wasReady = $true
            }
        } catch {
            Write-RunnerLog ('Startup/recovery retry: ' + $_.Exception.Message)
            $wasReady = $false
        }
        Start-Sleep -Seconds 15
    }
} finally {
    if ($keeper -and -not $keeper.HasExited) { Stop-Process -Id $keeper.Id -ErrorAction SilentlyContinue }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
