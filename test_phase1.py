from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from connections import CredentialStore, ProcessRunner, detect_ffmpeg, detect_python, normalize_url, validate_workflow
from settings import SCHEMA_VERSION, SettingsStore, default_settings, redact, validate_directory


class SettingsTests(unittest.TestCase):
    def test_persistence_and_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"schema_version": 0, "general": {"auto_save": False}}), encoding="utf-8")
            store = SettingsStore(path)
            settings = store.load()
            self.assertFalse(settings["general"]["auto_save"])
            self.assertIn("kokoro", settings)
            store.save(settings)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], SCHEMA_VERSION)

    def test_corruption_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text("not json", encoding="utf-8")
            store = SettingsStore(path)
            self.assertEqual(store.load()["schema_version"], SCHEMA_VERSION)
            self.assertTrue(store.recovered)
            self.assertTrue(path.with_suffix(".corrupt.json").exists())

    def test_atomic_save_and_space_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "folder with spaces" / "settings.json"
            SettingsStore(path).save(default_settings())
            self.assertTrue(path.exists())

    def test_secret_redaction(self):
        report = redact("api_key=abc123 password: swordfish Authorization: Bearer token123")
        self.assertNotIn("abc123", report)
        self.assertNotIn("swordfish", report)
        self.assertNotIn("token123", report)

    def test_directory_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, _ = validate_directory(str(Path(folder) / "path with spaces"))
            self.assertTrue(ok)


class ConnectionTests(unittest.TestCase):
    def test_python_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            python = Path(folder) / "venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
            self.assertEqual(detect_python(folder), str(python))

    def test_comfyui_url_validation(self):
        self.assertEqual(normalize_url("http://127.0.0.1:8188/"), "http://127.0.0.1:8188")
        with self.assertRaises(ValueError):
            normalize_url("127.0.0.1:8188")

    def test_workflow_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "workflow.json"
            path.write_text(json.dumps({"1": {"class_type": "Test"}}), encoding="utf-8")
            ok, _, _ = validate_workflow(str(path), {"positive_node": "1", "output_node": "2"})
            self.assertFalse(ok)
            ok, _, _ = validate_workflow(str(path), {"positive_node": "1"})
            self.assertTrue(ok)

    def test_ffmpeg_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            ffmpeg = Path(folder) / "ffmpeg.exe"
            ffprobe = Path(folder) / "ffprobe.exe"
            ffmpeg.touch(); ffprobe.touch()
            self.assertEqual(detect_ffmpeg(str(ffmpeg)), (str(ffmpeg), str(ffprobe)))

    def test_process_failure_and_spaces(self):
        result = ProcessRunner().run([sys.executable, "-c", "import sys; print('hello'); print('bad', file=sys.stderr); sys.exit(3)"], 10)
        self.assertEqual(result.exit_code, 3)
        self.assertIn("hello", result.stdout)
        self.assertIn("bad", result.stderr)

    def test_process_timeout(self):
        result = ProcessRunner().run([sys.executable, "-c", "import time; time.sleep(10)"], 0.2)
        self.assertTrue(result.timed_out)

    def test_process_cancellation(self):
        runner = ProcessRunner()
        holder = []
        thread = threading.Thread(target=lambda: holder.append(runner.run([sys.executable, "-c", "import time; time.sleep(10)"], 20)))
        thread.start()
        deadline = time.monotonic() + 3
        while runner.process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        runner.cancel()
        thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_secure_credential_abstraction(self):
        values = {}
        fake = types.SimpleNamespace(
            set_password=lambda service, name, value: values.__setitem__((service, name), value),
            get_password=lambda service, name: values.get((service, name)),
        )
        with patch.dict(sys.modules, {"keyring": fake}):
            store = CredentialStore("test-service")
            store.set("api", "secret")
            self.assertEqual(store.get("api"), "secret")


if __name__ == "__main__":
    unittest.main()
