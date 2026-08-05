@echo off
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" updater.py
) else (
  py -3.12 updater.py
)
echo.
pause

