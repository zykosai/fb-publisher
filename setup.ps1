param([switch]$Dev, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (Test-Path -LiteralPath $pythonExe) {
    $bootstrap = $pythonExe
    $bootstrapArgs = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $bootstrap = 'py'
    $bootstrapArgs = @('-3')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $bootstrap = 'python'
    $bootstrapArgs = @()
} else {
    throw 'Python 3.11+ was not found. Install it from python.org, then reopen this launcher.'
}
& $bootstrap @bootstrapArgs -c 'import sys; print(sys.version); sys.exit(0 if sys.version_info >= (3,11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
if ($CheckOnly) {
    Write-Host 'Setup check passed. No environment, packages or browsers were changed.'
    exit 0
}
if (-not (Test-Path -LiteralPath $pythonExe)) {
    & $bootstrap @bootstrapArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create .venv. Check Python and folder write permissions.' }
}
$requirements = if ($Dev) { 'requirements-dev.txt' } else { 'requirements.txt' }
& $pythonExe -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the error and your internet connection.' }
& $pythonExe -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Browser installation failed. Check your internet connection and run SETUP.cmd again.' }
New-Item -ItemType File -Path '.venv\fb-publisher-ready' -Force | Out-Null
Write-Host 'FB Publisher is ready. Run PC START.cmd.' -ForegroundColor Green
