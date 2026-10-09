@echo off
rem DoMe test kit: stop the test server. See README.md in this folder.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-server.ps1" %*
echo.
pause
