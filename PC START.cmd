@echo off
title FB Publisher
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-web.ps1"
echo.
echo Bot server has stopped. The message above explains why.
pause
