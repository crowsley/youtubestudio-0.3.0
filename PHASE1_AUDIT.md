# Phase 1 audit and release notes

## Before Phase 1

- Working: project creation/loading/saving, scripts, scenes, prompt storage, production-pack export and updater.
- Partial: Kokoro narration existed without environment validation or useful diagnostics.
- Placeholder: Kling and ComfyUI only stored prompt text.
- Missing: settings, secure credentials, connection tests, FFmpeg, diagnostics, first-launch checks and process management.

## Phase 1 architecture

- `app.py`: existing CustomTkinter project editor plus Settings UI and first-launch report.
- `settings.py`: versioned application settings, safe defaults, atomic writes, migration and corruption recovery.
- `connections.py`: cancellable process runner and real Kokoro, ComfyUI, Kling API and FFmpeg checks.
- `test_phase1.py`: standard-library automated tests with external services mocked where appropriate.

Settings are stored outside projects at `%LOCALAPPDATA%\YouTube AI Studio\settings.json`. Secrets are stored through the Windows credential backend and are never written to settings, project files or logs.

## Scope

Phase 1 configures and validates connections. ComfyUI image generation and Kokoro speech generation are used only for short connection tests here. Full production generation and video rendering remain future phases.
