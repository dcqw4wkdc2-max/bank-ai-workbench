@echo off
chcp 65001 >nul
title Bank AI Workbench (LAN mode)
cd /d "%~dp0"
set MODEL_IMPL=real
rem Bind 0.0.0.0 so phones on the same Wi-Fi can reach port 8103
set ZONE_HOST=0.0.0.0

"%~dp0BankAIWorkbench.exe"
if errorlevel 1 (
  echo.
  echo Startup failed. Please screenshot this window and send it to the maintainer.
  pause
)
