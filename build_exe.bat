@echo off
setlocal
cd /d "%~dp0"
if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist installer-output rmdir /s /q installer-output
python -m PyInstaller --noconfirm --clean --windowed --onedir --name YouTubeAIStudio --collect-all customtkinter --collect-all kokoro --collect-all misaki --collect-all phonemizer --collect-all espeakng_loader --collect-all segments --collect-all csvw --collect-all language_tags --collect-all spacy --collect-all soundfile app.py
if errorlevel 1 goto :failed
python -m PyInstaller --noconfirm --clean --console --onefile --name YouTubeAIStudioUpdater updater.py
if errorlevel 1 goto :failed
copy /y "dist\YouTubeAIStudioUpdater.exe" "dist\YouTubeAIStudio\"
copy /y version.json "dist\YouTubeAIStudio\"
copy /y update_config.json "dist\YouTubeAIStudio\"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 was not found. Install it from https://jrsoftware.org/isinfo.php
  exit /b 2
)
"%ISCC%" installer.iss
if errorlevel 1 goto :failed
echo Installer ready in installer-output\YouTube-AI-Studio-Setup.exe
exit /b 0
:failed
echo Build failed.
exit /b 1
