@echo off
setlocal
cd /d "%~dp0"

echo =============================================
echo       YouTube AI Studio - Installer
echo =============================================
echo.

where py >nul 2>&1
if errorlevel 1 (
  echo Python launcher was not found.
  echo Install Python 3.12, then run this file again.
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo Creating Python 3.12 environment...
  py -3.12 -m venv venv
  if errorlevel 1 goto :failed
)

call venv\Scripts\activate.bat

echo Updating Python installer tools...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :failed

echo Installing YouTube AI Studio...
python -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Installation completed.
echo Double-click run_app.bat to open YouTube AI Studio.
pause
exit /b 0

:failed
echo.
echo Installation failed. Copy the error shown above.
pause
exit /b 1

