@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -Stop
if errorlevel 1 (
  echo Clicker could not stop.
  pause
  exit /b 1
)
