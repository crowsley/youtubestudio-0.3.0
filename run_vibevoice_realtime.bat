@echo off
setlocal
cd /d "%~dp0vibevoice-realtime-openai-api"
if not exist "..\vibevoice\.venv\Scripts\python.exe" (
  echo VibeVoice Python environment was not found.
  pause
  exit /b 1
)
"..\vibevoice\.venv\Scripts\python.exe" vibevoice_realtime_openai_api.py --model-path models\VibeVoice-Realtime-0.5B --port 8880
