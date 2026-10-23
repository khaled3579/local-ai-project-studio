@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-All.ps1" %*
set "START_EXIT=%ERRORLEVEL%"
if not "%START_EXIT%"=="0" pause
exit /b %START_EXIT%
