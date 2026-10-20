$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path '.venv\fb-publisher-ready')) {
    & (Join-Path $projectRoot 'setup.ps1')
}
& .\.venv\Scripts\python.exe scripts\facebook_login.py
if ($LASTEXITCODE -ne 0) { throw 'Facebook login failed. See the error above.' }
