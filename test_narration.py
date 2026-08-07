from __future__ import annotations

import json
import io
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from connections import ProcessResult
from narration import KokoroNarrationProvider, VibeVoiceNarrationProvider, clean_text, combine_wavs, normalize_wav, validate_wav
from narration_adapter import parser
from unittest.mock import patch


def make_wav(path: Path, frames: int = 2400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * frames)


def wav_bytes(frames: int = 2400) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\1\0" * frames)
    return stream.getvalue()


class FakeRunner:
    def __init__(self, outcome="success"):
        self.outcome, self.cancelled = outcome, False

    def run(self, command, timeout, on_output):
        on_output("stdout", json.dumps({"event": "stage", "stage": "loading_model"}) + "\n")
        if "--batch-file" in command:
            jobs = json.loads(Path(command[command.index("--batch-file") + 1]).read_text(encoding="utf-8"))
            for current, job in enumerate(jobs, 1):
                on_output("stdout", json.dumps({"event": "item_start", "index": job["index"], "current": current, "total": len(jobs)}) + "\n")
                make_wav(Path(job["output"]))
                on_output("stdout", json.dumps({"event": "item_complete", "index": job["index"], "current": current, "total": len(jobs)}) + "\n")
            return ProcessResult(command, 0, "ok", "")
        output = Path(command[command.index("--output") + 1])
        if self.outcome == "success":
            make_wav(output)
            on_output("stdout", json.dumps({"event": "complete", "output": str(output), "duration": .1}) + "\n")
            return ProcessResult(command, 0, "ok", "")
        if self.outcome == "cancel":
            return ProcessResult(command, 1, "", "", cancelled=True)
        return ProcessResult(command, 2, "", "missing dependency")

    def cancel(self): self.cancelled = True


class NarrationTests(unittest.TestCase):
    def config(self, folder: Path):
        python = folder / "venv with spaces" / "python.exe"
        python.parent.mkdir(); python.touch()
        return {"python": str(python), "install_dir": str(folder), "timeout": 3, "model_repo": "test/model"}

    def test_text_validation_preserves_punctuation(self):
        self.assertEqual(clean_text("  Hello,\n  world! \x00"), "Hello, world!")
        self.assertEqual(clean_text(""), "")

    def test_valid_wav(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "valid.wav"; make_wav(path)
            info = validate_wav(path)
            self.assertEqual(info["sample_rate"], 24000)
            self.assertAlmostEqual(info["duration"], .1)

    def test_zero_and_corrupt_wav_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            zero = Path(folder) / "zero.wav"; zero.touch()
            corrupt = Path(folder) / "bad.wav"; corrupt.write_bytes(b"not a wave" * 30)
            for path in (zero, corrupt):
                with self.assertRaises(ValueError): validate_wav(path)

    def test_adapter_argument_parsing(self):
        args = parser().parse_args(["--text-file", "a b.txt", "--output", "c d.wav", "--voice", "bm_lewis", "--language", "b", "--speed", "0.95"])
        self.assertEqual((args.voice, args.speed), ("bm_lewis", .95))

    def test_command_uses_configured_python_and_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); provider = KokoroNarrationProvider(self.config(root), root / "log.txt", FakeRunner())
            command = provider.command(root / "text file.txt", root / "output file.wav", "bm_lewis", "b", .95)
            self.assertEqual(command[0], str(root / "venv with spaces" / "python.exe"))
            self.assertTrue(command[command.index("--output") + 1].endswith("output file.wav"))

    def test_generation_success_and_streamed_event(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); events = []
            result = KokoroNarrationProvider(self.config(root), root / "log.txt", FakeRunner()).generate("Hello.", "bm_lewis", "b", .95, root / "scene.wav", events.append)
            self.assertTrue(result.success)
            self.assertEqual(events[0]["stage"], "loading_model")
            self.assertTrue(Path(result.output).is_file())

    def test_batch_generation_uses_one_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); runner = FakeRunner(); provider = KokoroNarrationProvider(self.config(root), root/"log.txt", runner); events = []
            jobs = [{"index": index, "text": f"Scene {index}", "output": str(root/f"scene-{index}.wav")} for index in (1, 2, 3)]
            results = provider.generate_batch(jobs, "bm_lewis", "b", .95, events.append)
            self.assertTrue(all(result.success for result in results.values()))
            self.assertEqual(sum(event.get("stage") == "loading_model" for event in events), 1)
            self.assertEqual(sum(event.get("event") == "item_complete" for event in events), 3)

    def test_subprocess_failure_preserves_stderr(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = KokoroNarrationProvider(self.config(root), root / "log.txt", FakeRunner("fail")).generate("Hello", "bm_lewis", "b", 1, root / "scene.wav")
            self.assertFalse(result.success); self.assertIn("missing dependency", result.error)

    def test_cancellation_and_provider_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); runner = FakeRunner("cancel"); provider = KokoroNarrationProvider(self.config(root), root / "log.txt", runner)
            result = provider.generate("Hello", "bm_lewis", "b", 1, root / "scene.wav")
            self.assertTrue(result.cancelled)
            provider.cancel(); self.assertTrue(runner.cancelled)

    def test_empty_scene_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = KokoroNarrationProvider(self.config(root), root / "log.txt", FakeRunner()).generate(" \n", "bm_lewis", "b", 1, root / "scene.wav")
            self.assertFalse(result.success); self.assertIn("empty", result.error)

    def test_combined_narration_order_and_silence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); one, two, output = root/"1.wav", root/"2.wav", root/"narration.wav"
            make_wav(one, 2400); make_wav(two, 4800)
            info = combine_wavs([one, two], output, .25)
            self.assertAlmostEqual(info["duration"], .55, places=2)
            self.assertTrue(output.is_file())

    def test_vibevoice_provider_uses_local_speech_api(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self): return wav_bytes()
        with tempfile.TemporaryDirectory() as folder, patch("urllib.request.urlopen", return_value=Response()) as request:
            root = Path(folder)
            result = VibeVoiceNarrationProvider({"base_url": "http://127.0.0.1:8880", "model": "test", "timeout": 2}, root/"log.txt").generate("Hello", "nova", "", 1, root/"voice.wav")
            self.assertTrue(result.success)
            self.assertIn("/v1/audio/speech", request.call_args.args[0].full_url)

    def test_normalize_wav_keeps_playable_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"quiet.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 24000, 0, "NONE", "not compressed")); wav.writeframes((b"\x10\0\xf0\xff") * 1200)
            before = path.read_bytes()
            normalize_wav(path)
            self.assertNotEqual(path.read_bytes(), before)
            self.assertGreater(validate_wav(path)["duration"], 0)

    def test_atomic_final_does_not_leave_tmp(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); output = root/"scene.wav"
            result = KokoroNarrationProvider(self.config(root), root/"log.txt", FakeRunner()).generate("Hello", "bm_lewis", "b", 1, output)
            self.assertTrue(result.success); self.assertFalse((root/"scene.tmp.wav").exists())

    def test_old_project_scene_migration(self):
        from app import Studio
        scene = Studio.migrate_scene(SimpleNamespace(), {"title": "Old", "narration": "Preserved"})
        self.assertEqual(scene["narration"], "Preserved")
        self.assertEqual(scene["audioStatus"], "Not generated")

    def test_stale_audio_path_recovery(self):
        from app import Studio
        owner = SimpleNamespace(
            scenes=[{"audioStatus": "Complete", "audioPath": "Z:/missing.wav"}],
            project_narration={"narrationStatus": "Complete", "combinedNarrationPath": "Z:/missing-all.wav"},
            current_project_path=None,
        )
        owner.migrate_audio_path = lambda value: Studio.migrate_audio_path(owner, value)
        Studio.repair_audio_state(owner)
        self.assertEqual(owner.scenes[0]["audioStatus"], "Not generated")
        self.assertEqual(owner.project_narration["narrationStatus"], "Not generated")


if __name__ == "__main__": unittest.main()
