param([switch]$Phone, [string]$Certificate, [string]$KeyFile)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path '.venv\fb-publisher-ready')) {
    & (Join-Path $projectRoot 'setup.ps1')
}
$launchArgs = @('scripts\web.py', '--open-browser')
if ($Phone) { $launchArgs += '--lan' }
if ($Certificate -and $KeyFile) { $launchArgs += @('--cert', $Certificate, '--key-file', $KeyFile) }
if ([bool]$Certificate -ne [bool]$KeyFile) { throw 'Specify both Certificate and KeyFile for HTTPS.' }
& .\.venv\Scripts\python.exe @launchArgs
if ($LASTEXITCODE -ne 0) { throw "Bot server exited with code $LASTEXITCODE. See the error above." }
