# YouTube AI Studio

Local Windows production workspace for faceless and AI-assisted YouTube videos.

## Version 0.3.2

- Project, script and scene management
- Local Kokoro narration generation
- Kling and ComfyUI prompt storage
- DaVinci Resolve production-pack export
- Standalone Windows application and Inno Setup installer
- Start Menu, optional desktop shortcut and uninstaller
- Projects stored safely in `%LOCALAPPDATA%\YouTube AI Studio`
- Installer-based updates from GitHub Releases

## Development

Run `install.bat`, then `run_app.bat`.

## Windows installer

Install Inno Setup 6, then run `build_exe.bat`. The finished installer is written to:

    installer-output\YouTube-AI-Studio-Setup.exe

Users of the installer do not need Python.
