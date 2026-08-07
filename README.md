# AtoZ Voice Studio

Local Windows production workspace for faceless and AI-assisted YouTube videos.

## Version 0.7.5 - Clone samples in Documents

- Clone mic recordings saved to `Documents\AtoZ Voice Studio\Clone Samples`
- Browse WAV opens that folder; Samples folder button included
- Relink scene/full narration after YouTube AI Studio → AtoZ rename
- Validate existing WAVs on open; quieter playback when a file is missing
- Full narration warns if scenes are not complete yet
- Record microphone samples for voice cloning on the Voice tab
- Quick-guide steps that change with Kokoro / VibeVoice / Clone
- Full English Kokoro voice catalogue with quality grades
- Optional Clone engine via OpenAI-compatible `/v1/audio/speech` + reference WAV
- Ollama AI assist for Kling and image prompts
- Export validation, WAV copies for DaVinci, and import README
- Project, script and scene management
- Local Kokoro narration generation
- Optional VibeVoice Realtime 0.5B narration through a local OpenAI-compatible server
- Kling and ComfyUI prompt storage
- DaVinci Resolve production-pack export (CSV sheet, duration report, SRT)
- Standalone Windows application and Inno Setup installer
- Installer-based updates from GitHub Releases
- Built-in Windows playback with play, pause, stop, replay, seek and volume
- Consistent -19 dBFS narration mastering when scene WAVs are combined

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
