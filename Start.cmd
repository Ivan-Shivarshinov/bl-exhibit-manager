@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Follow README.md to install Python dependencies first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m exhibit.launch %*
if errorlevel 1 pause
