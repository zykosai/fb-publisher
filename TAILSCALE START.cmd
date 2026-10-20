@echo off
title FB Publisher - Phone and Tailscale
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-web.ps1" -Phone
echo.
echo Bot server has stopped. The message above explains why.
pause
