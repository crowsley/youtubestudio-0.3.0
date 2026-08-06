@echo off
setlocal
cd /d "%~dp0"
if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist installer-output rmdir /s /q installer-output
python -m PyInstaller --noconfirm --clean --windowed --onedir --name AtoZVoiceStudio --add-data "narration_adapter.py;." --collect-all customtkinter --collect-all kokoro --collect-all misaki --collect-all phonemizer --collect-all espeakng_loader --collect-all segments --collect-all csvw --collect-all language_tags --collect-all keyring --collect-all spacy --collect-all soundfile app.py
if errorlevel 1 goto :failed
rem The bundled Codex Python can run tkinter but PyInstaller cannot discover its Tcl/Tk files.
for /f "delims=" %%I in ('python -c "import sys; print(sys.base_prefix)"') do set "PYBASE=%%I"
xcopy /e /i /y "%PYBASE%\Lib\tkinter" "dist\AtoZVoiceStudio\_internal\tkinter" >nul
xcopy /e /i /y "%PYBASE%\tcl\tcl8.6" "dist\AtoZVoiceStudio\_internal\_tcl_data" >nul
xcopy /e /i /y "%PYBASE%\tcl\tk8.6" "dist\AtoZVoiceStudio\_internal\_tk_data" >nul
copy /y "%PYBASE%\DLLs\_tkinter.pyd" "dist\AtoZVoiceStudio\_internal\" >nul
copy /y "%PYBASE%\DLLs\tcl86t.dll" "dist\AtoZVoiceStudio\_internal\" >nul
copy /y "%PYBASE%\DLLs\tk86t.dll" "dist\AtoZVoiceStudio\_internal\" >nul
python -m PyInstaller --noconfirm --clean --console --onefile --name AtoZVoiceStudioUpdater updater.py
if errorlevel 1 goto :failed
copy /y "dist\AtoZVoiceStudioUpdater.exe" "dist\AtoZVoiceStudio\"
copy /y version.json "dist\AtoZVoiceStudio\"
copy /y update_config.json "dist\AtoZVoiceStudio\"
copy /y LICENSE "dist\AtoZVoiceStudio\"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%~dp0.tools\InnoSetup6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 was not found. Install it from https://jrsoftware.org/isinfo.php
  exit /b 2
)
"%ISCC%" installer.iss
if errorlevel 1 goto :failed
copy /y "installer-output\AtoZ-Voice-Studio-Setup.exe" "installer-output\YouTube-AI-Studio-Setup.exe" >nul
echo Installer ready in installer-output\AtoZ-Voice-Studio-Setup.exe
exit /b 0
:failed
echo Build failed.
exit /b 1
