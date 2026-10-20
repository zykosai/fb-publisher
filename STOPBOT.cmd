@echo off
title Stop FB Publisher
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-bot.ps1"
echo.
pause
