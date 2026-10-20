param([switch]$Phone)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'run-web.ps1') -Phone:$Phone
