@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -OpenBrowser
if errorlevel 1 (
  echo Clicker could not start.
  pause
  exit /b 1
)
