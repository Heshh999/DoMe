@echo off
rem DoMe test kit, step 4 (optional): switch the AI assistant on or off. See README.md in this folder.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\apply-ai-settings.ps1" %*
echo.
pause
