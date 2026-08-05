from __future__ import annotations

import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path

APP_NAME = "YouTube AI Studio"
SCHEMA_VERSION = 1
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
SETTINGS_FILE = DATA_DIR / "settings.json"


def default_settings() -> dict:
    kokoro_dir = Path.home() / "Downloads" / "Kokoro-TTS"
    return {
        "schema_version": SCHEMA_VERSION,
        "general": {
            "app_data_dir": str(DATA_DIR),
            "project_dir": str(DATA_DIR / "projects"),
            "logs_dir": str(DATA_DIR / "logs"),
            "temp_dir": str(DATA_DIR / "temp"),
            "auto_save": True,
            "auto_save_interval": 60,
            "open_last_project": False,
            "check_updates_on_startup": False,
        },
        "kokoro": {
            "install_dir": str(kokoro_dir),
            "python": str(kokoro_dir / "venv" / "Scripts" / "python.exe"),
            "model_repo": "hexgrad/Kokoro-82M",
            "language": "b",
            "voice": "bf_emma",
            "speed": 1.0,
            "timeout": 180,
        },
        "comfyui": {
            "base_url": "http://127.0.0.1:8188",
            "workflow_file": "",
            "positive_node": "",
            "negative_node": "",
            "seed_node": "",
            "width_node": "",
            "height_node": "",
            "output_node": "",
            "timeout": 180,
            "output_dir": str(DATA_DIR / "comfyui"),
        },
        "kling": {
            "mode": "Manual Import",
            "project_url": "https://klingai.com/",
            "import_dir": str(DATA_DIR / "kling"),
            "api_base_url": "",
            "workspace": "",
            "model": "",
            "duration": 5,
            "aspect_ratio": "16:9",
        },
        "ffmpeg": {"ffmpeg": "", "ffprobe": ""},
        "output": {
            "project_root": str(DATA_DIR / "projects"),
            "export_dir": str(DATA_DIR / "exports"),
            "cache_dir": str(DATA_DIR / "cache"),
            "temp_dir": str(DATA_DIR / "temp"),
            "resolution": "1920x1080",
            "frame_rate": 30,
            "transition_duration": 0.5,
            "video_codec": "libx264",
            "hardware_encoder": "Auto",
            "audio_codec": "aac",
            "quality": "High",
            "overwrite": "Ask",
        },
        "status": {},
    }


def _merge(default: dict, saved: dict) -> dict:
    result = deepcopy(default)
    for key, value in saved.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key].update(value)
        else:
            result[key] = value
    result["schema_version"] = SCHEMA_VERSION
    return result


class SettingsStore:
    def __init__(self, path: Path = SETTINGS_FILE):
        self.path = Path(path)
        self.recovered = False

    def load(self) -> dict:
        defaults = default_settings()
        if not self.path.exists():
            return defaults
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("settings root must be an object")
            return _merge(defaults, saved)
        except (OSError, ValueError, json.JSONDecodeError):
            self.recovered = True
            backup = self.path.with_suffix(".corrupt.json")
            try:
                shutil.copy2(self.path, backup)
            except OSError:
                pass
            return defaults

    def save(self, settings: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = deepcopy(settings)
        payload["schema_version"] = SCHEMA_VERSION
        fd, temp_name = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def validate_directory(value: str, create: bool = True) -> tuple[bool, str]:
    try:
        path = Path(value).expanduser()
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            return False, f"Not a directory: {path}"
        probe = path / ".youtube-ai-studio-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, f"Writable: {path}"
    except OSError as exc:
        return False, f"Not writable: {value} ({exc})"


def redact(value: str) -> str:
    import re

    value = re.sub(r"(?i)(token|api[_ -]?key|password)(\s*[:=]\s*)\S+", r"\1\2[REDACTED]", value)
    value = re.sub(r"(?i)(authorization:\s*(?:bearer\s+)?)\S+", r"\1[REDACTED]", value)
    return value

