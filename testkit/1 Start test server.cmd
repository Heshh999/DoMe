@echo off
rem DoMe test kit, step 1. See README.md in this folder.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-server.ps1" %*
echo.
pause
