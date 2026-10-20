$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
& .\.venv\Scripts\python.exe scripts\worker.py
