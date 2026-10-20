param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

function Test-BotServer($botProcess) {
    if (-not $botProcess) { return $false }
    $command = [string]$botProcess.CommandLine
    return ($botProcess.Name -match '^python(?:w|\d+(?:\.\d+)?)?\.exe$' -and
        $command.IndexOf($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $command -match 'scripts[\\/]web\.py(?:\s|"|$)')
}

$listeners = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
$servers = @()
foreach ($listener in $listeners) {
    $botProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if (-not (Test-BotServer $botProcess)) {
        throw "Port 8765 belongs to another application (PID $($listener.OwningProcess)). Nothing was stopped."
    }
    $servers += $botProcess
}
if ($CheckOnly) {
    Write-Host "Verified bot listeners: $($servers.Count). Check only; nothing was stopped."
    exit 0
}

$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Bot Python environment not found. Nothing was stopped.' }
Write-Host 'Pausing publications and stopping the bot worker...'
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    @'
from bike_publisher.config import Settings
from bike_publisher.lifecycle import stop_all_workers
settings = Settings.from_root()
stop_all_workers(settings)
'@ | & $python -
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the worker safely. Server was not stopped.' }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}

foreach ($server in $servers) {
    # Recheck identity immediately before stopping: PIDs can be reused.
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($server.ProcessId)"
    if (Test-BotServer $current) {
        Stop-Process -Id $current.ProcessId -ErrorAction Stop
    }
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($server.ParentProcessId)"
    if (Test-BotServer $parent) {
        Stop-Process -Id $parent.ProcessId -ErrorAction Stop
    }
}
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8765 is still in use. No unrelated processes were stopped.'
}
Write-Host 'Bot stopped. Port 8765 is free. You can now run PC START.cmd or TAILSCALE START.cmd.' -ForegroundColor Green
