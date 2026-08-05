# Phase 2 audit and release notes

## Previous narration state

Narration used the real Kokoro library, but model loading and generation lived inside the desktop process. The background worker read Tk widgets directly, had no timeout or cancellation, wrote final WAV files without validation, did not persist audio state, and exposed no playback or detailed errors. A model download, dependency failure, or cross-thread Tk error could therefore leave the interface displaying `Generating narration...` indefinitely.

## Phase 2 architecture

- `narration_adapter.py` runs under the configured Kokoro virtual-environment Python and emits JSON-line stages.
- `narration.py` owns process execution, temporary files, WAV validation, atomic replacement, combination, logging and Windows MCI playback.
- `app.py` owns validation, sequential scene orchestration, progress, cancellation, retry and additive project state.
- Existing project fields and folders remain readable. Audio metadata is added through project schema version 2.

Phase 2 does not implement ComfyUI production, Kling automation or final video rendering.
