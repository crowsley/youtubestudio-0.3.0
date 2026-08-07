# AtoZ Voice Studio

Local Windows production workspace for faceless and AI-assisted YouTube videos.

## Version 0.8.0 - Per-scene voices & story SFX

- Per-scene Kokoro voice / character presets (male, female, British, etc.)
- Import story SFX (mp3/wav/…) → convert to studio WAV via FFmpeg
- Attach SFX to scenes; library in `Documents\AtoZ Voice Studio\Sound Effects`
- Full narration can mix timed SFX under voice when FFmpeg is configured
- Export copies voice + SFX into project folders for DaVinci
- Clone mic recordings saved to `Documents\AtoZ Voice Studio\Clone Samples`
- Relink narration after YouTube AI Studio → AtoZ rename
- Ollama AI assist for Kling and image prompts
- DaVinci Resolve production-pack export (CSV, duration report, SRT)
- Local Kokoro / VibeVoice / Clone narration engines
- Installer-based updates from GitHub Releases

## VibeVoice Realtime

Run `run_vibevoice_realtime.bat`, then select **VibeVoice Realtime** in the Voice tab. The server URL, model, default voice and timeout are configurable in Settings. Kokoro remains the fast default and works without the VibeVoice server.

## License

AtoZ Voice Studio is free community software released under the [MIT License](LICENSE). You may use, modify and redistribute it, including commercially, while retaining the copyright and licence notice. Third-party models and components retain their own licences.

## Development

Run `install.bat`, then `run_app.bat`.

## Windows installer

Install Inno Setup 6, then run `build_exe.bat`. The finished installer is written to:

    installer-output\AtoZ-Voice-Studio-Setup.exe

Users of the installer do not need Python.
