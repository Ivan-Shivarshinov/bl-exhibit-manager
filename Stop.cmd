@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m exhibit.launch --stop
if errorlevel 1 pause
