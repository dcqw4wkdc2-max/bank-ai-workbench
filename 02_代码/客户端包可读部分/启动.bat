@echo off
chcp 65001 >nul
title Bank AI Workbench
cd /d "%~dp0"

rem MODEL_IMPL=real: call the private GPU endpoint; auto-degrades to mock if unreachable
set MODEL_IMPL=real

"%~dp0BankAIWorkbench.exe"
if errorlevel 1 (
  echo.
  echo Startup failed. Please screenshot this window and send it to the maintainer.
  pause
)
