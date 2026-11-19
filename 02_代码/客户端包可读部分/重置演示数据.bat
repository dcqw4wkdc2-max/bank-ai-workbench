@echo off
chcp 65001 >nul
title Reset demo data
cd /d "%~dp0"


echo This will delete the data folder (database, audit logs, archive).
set /p ok=Type Y and press Enter to confirm:
if /i not "%ok%"=="Y" (
  echo Cancelled.
  pause
  exit /b 0
)
if exist "data" rmdir /s /q "data"
echo Done. The next start will rebuild the database with demo seed data.
pause
