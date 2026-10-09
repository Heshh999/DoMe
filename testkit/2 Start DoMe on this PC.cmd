@echo off
rem DoMe test kit, step 2. See README.md in this folder.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-agent.ps1" %*
echo.
pause
