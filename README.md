# YouTube AI Studio

Local Windows production workspace for faceless and AI-assisted YouTube videos.

## Version 0.5.1 - Phase 2 Narration

- Project, script and scene management
- Local Kokoro narration generation
- Kling and ComfyUI prompt storage
- DaVinci Resolve production-pack export
- Standalone Windows application and Inno Setup installer
- Start Menu, optional desktop shortcut and uninstaller
- Projects stored safely in `%LOCALAPPDATA%\YouTube AI Studio`
- Installer-based updates from GitHub Releases
- Persistent Settings and first-launch setup report
- Real Kokoro WAV, ComfyUI image and FFmpeg MP4 connection tests
- Manual/API Kling configuration with secure credential storage
- Diagnostics with secret redaction and visible failure reasons
- Cancellable Kokoro subprocess generation with live structured progress
- Preview, individual scene, sequential all-scenes and combined narration WAVs
- Validated atomic audio output and resume/retry state in project files
- Built-in Windows playback with play, pause, stop, replay, seek and volume

## Development

Run `install.bat`, then `run_app.bat`.

## Windows installer

Install Inno Setup 6, then run `build_exe.bat`. The finished installer is written to:

    installer-output\YouTube-AI-Studio-Setup.exe

Users of the installer do not need Python.
