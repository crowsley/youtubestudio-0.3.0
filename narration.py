from __future__ import annotations

import ctypes
import json
import os
import re
import tempfile
import time
import unicodedata
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from connections import ProcessRunner
from settings import redact


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", "".join(ch for ch in value if ch in "\n\t" or unicodedata.category(ch)[0] != "C")).strip()


def validate_wav(path: Path, minimum_bytes: int = 128) -> dict:
    try:
        if not path.is_file() or path.stat().st_size < minimum_bytes:
            raise ValueError("WAV is missing, empty, or too small")
        with path.open("rb") as stream:
            header = stream.read(12)
        if header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError("Invalid RIFF/WAVE header")
        with wave.open(str(path), "rb") as wav:
            rate, channels, frames, width = wav.getframerate(), wav.getnchannels(), wav.getnframes(), wav.getsampwidth()
        if rate <= 0 or channels <= 0 or frames <= 0:
            raise ValueError("WAV contains no playable audio")
        return {"path": str(path), "duration": frames / rate, "file_size": path.stat().st_size, "sample_rate": rate, "channels": channels, "sample_width": width}
    except (OSError, wave.Error, ValueError) as exc:
        raise ValueError(f"Invalid WAV: {exc}") from exc


@dataclass
class NarrationResult:
    success: bool
    output: str = ""
    duration: float = 0
    file_size: int = 0
    sample_rate: int = 0
    channels: int = 0
    exit_code: int = -1
    elapsed: float = 0
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    cancelled: bool = False
    timed_out: bool = False


class KokoroNarrationProvider:
    def __init__(self, config: dict, log_file: Path, runner: ProcessRunner | None = None):
        self.config, self.log_file, self.runner = config, log_file, runner or ProcessRunner()

    def cancel(self) -> None:
        self.runner.cancel()

    def command(self, text_file: Path, output: Path, voice: str, language: str, speed: float) -> list[str]:
        adapter = Path(__file__).with_name("narration_adapter.py")
        return [str(self.config.get("python", "")), str(adapter), "--text-file", str(text_file), "--output", str(output), "--voice", voice, "--language", language, "--speed", str(speed), "--model-repo", self.config.get("model_repo", "hexgrad/Kokoro-82M")]

    def generate_batch(self, jobs: list[dict], voice: str, language: str, speed: float, on_event: Callable[[dict], None] | None = None) -> dict[int, NarrationResult]:
        started = time.monotonic()
        python, install = Path(self.config.get("python", "")), Path(self.config.get("install_dir", ""))
        if not python.is_file() or not install.is_dir():
            error = f"Kokoro configuration is missing: Python={python}, installation={install}"
            return {job["index"]: NarrationResult(False, error=error) for job in jobs}
        batch, temporary = [], {}
        for job in jobs:
            output = Path(job["output"]); output.parent.mkdir(parents=True, exist_ok=True)
            temp = output.with_name(output.stem + ".tmp.wav"); temp.unlink(missing_ok=True)
            temporary[job["index"]] = (temp, output)
            batch.append({"index": job["index"], "text": clean_text(job["text"]), "output": str(temp)})
        fd, manifest_name = tempfile.mkstemp(prefix="narration-batch-", suffix=".json", dir=Path(jobs[0]["output"]).parent)
        os.close(fd); manifest = Path(manifest_name); manifest.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        adapter = Path(__file__).with_name("narration_adapter.py")
        command = [str(python), str(adapter), "--batch-file", str(manifest), "--voice", voice, "--language", language, "--speed", str(speed), "--model-repo", self.config.get("model_repo", "hexgrad/Kokoro-82M")]
        errors = {}
        def receive(stream: str, line: str) -> None:
            try:
                item = json.loads(line) if stream == "stdout" else {"event": "log", "message": line.rstrip(), "stream": stream}
                if item.get("event") == "item_error": errors[item.get("index")] = item.get("message", "Generation failed")
                if on_event: on_event(item)
            except json.JSONDecodeError:
                if on_event: on_event({"event": "log", "message": line.rstrip(), "stream": stream})
        try:
            timeout = float(self.config.get("timeout", 180)) * max(1, len(jobs))
            process = self.runner.run(command, timeout, receive)
            results = {}
            for job in jobs:
                index = job["index"]; temp, output = temporary[index]
                try:
                    info = validate_wav(temp)
                    os.replace(temp, output)
                    elapsed = time.monotonic() - started
                    self._log(command, voice, speed, process, info, elapsed)
                    results[index] = NarrationResult(True, output=str(output), duration=info["duration"], file_size=info["file_size"], sample_rate=info["sample_rate"], channels=info["channels"], exit_code=process.exit_code, elapsed=elapsed, stdout=process.stdout, stderr=process.stderr)
                except Exception as exc:
                    error = errors.get(index) or ("Generation cancelled" if process.cancelled else str(exc))
                    results[index] = NarrationResult(False, exit_code=process.exit_code, elapsed=time.monotonic()-started, stdout=process.stdout, stderr=process.stderr, error=error, cancelled=process.cancelled, timed_out=process.timed_out)
            return results
        finally:
            manifest.unlink(missing_ok=True)
            for temp, _ in temporary.values(): temp.unlink(missing_ok=True)

    def generate(self, text: str, voice: str, language: str, speed: float, output: Path, on_event: Callable[[dict], None] | None = None) -> NarrationResult:
        started = time.monotonic()
        text = clean_text(text)
        python, install = Path(self.config.get("python", "")), Path(self.config.get("install_dir", ""))
        if not text: return NarrationResult(False, error="Narration text is empty")
        if not python.is_file(): return NarrationResult(False, error=f"Configured Kokoro Python is missing: {python}")
        if not install.is_dir(): return NarrationResult(False, error=f"Kokoro installation is missing: {install}")
        if not 0.5 <= speed <= 2.0: return NarrationResult(False, error="Speech speed must be between 0.5 and 2.0")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.stem + ".tmp.wav")
        temporary.unlink(missing_ok=True)
        fd, text_name = tempfile.mkstemp(prefix="narration-", suffix=".txt", dir=output.parent)
        os.close(fd)
        text_file = Path(text_name)
        text_file.write_text(text, encoding="utf-8")
        command = self.command(text_file, temporary, voice, language, speed)
        events = []
        def receive(stream: str, line: str) -> None:
            if stream == "stdout":
                try:
                    item = json.loads(line)
                    events.append(item)
                    if on_event: on_event(item)
                except json.JSONDecodeError:
                    if on_event: on_event({"event": "log", "message": line.rstrip()})
            elif on_event:
                on_event({"event": "log", "message": line.rstrip(), "stream": "stderr"})
        try:
            result = self.runner.run(command, float(self.config.get("timeout", 180)), receive)
            if not result.ok:
                error = next((e.get("message") for e in reversed(events) if e.get("event") == "error"), "") or result.stderr.strip() or f"Kokoro exited with code {result.exit_code}"
                return NarrationResult(False, exit_code=result.exit_code, elapsed=time.monotonic()-started, stdout=result.stdout, stderr=result.stderr, error=error, cancelled=result.cancelled, timed_out=result.timed_out)
            info = validate_wav(temporary)
            os.replace(temporary, output)
            self._log(command, voice, speed, result, info, time.monotonic()-started)
            return NarrationResult(True, exit_code=result.exit_code, elapsed=time.monotonic()-started, stdout=result.stdout, stderr=result.stderr, **{k: info[k] for k in ("duration", "file_size", "sample_rate", "channels")}, output=str(output))
        except Exception as exc:
            return NarrationResult(False, elapsed=time.monotonic()-started, error=str(exc))
        finally:
            text_file.unlink(missing_ok=True)
            if temporary.exists() and not output.exists(): temporary.unlink(missing_ok=True)

    def _log(self, command, voice, speed, result, info, elapsed) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        record = f"[{datetime.now().isoformat(timespec='seconds')}] voice={voice} speed={speed} elapsed={elapsed:.2f}s exit={result.exit_code}\ncommand={redact(repr(command))}\noutput={info}\nstdout={redact(result.stdout)}\nstderr={redact(result.stderr)}\n\n"
        with self.log_file.open("a", encoding="utf-8") as log: log.write(record)


def combine_wavs(inputs: list[Path], output: Path, silence_seconds: float = 0.25) -> dict:
    if not inputs: raise ValueError("No scene narration files are available")
    details = [validate_wav(path) for path in inputs]
    rate, channels, width = details[0]["sample_rate"], details[0]["channels"], details[0]["sample_width"]
    temporary = output.with_name(output.stem + ".tmp.wav")
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(temporary), "wb") as target:
        target.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        silence = b"\0" * int(rate * channels * width * max(0, silence_seconds))
        for index, path in enumerate(inputs):
            with wave.open(str(path), "rb") as source:
                if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (rate, channels, width):
                    raise ValueError("Scene WAV formats do not match")
                target.writeframes(source.readframes(source.getnframes()))
            if index + 1 < len(inputs): target.writeframes(silence)
    info = validate_wav(temporary)
    os.replace(temporary, output)
    info["path"] = str(output)
    return info


class WindowsAudioPlayer:
    def __init__(self):
        self.alias = "youtube_ai_studio_audio"
        self.path = ""

    def _send(self, command: str) -> str:
        if os.name != "nt": raise RuntimeError("Built-in playback is available on Windows")
        buffer = ctypes.create_unicode_buffer(255)
        code = ctypes.windll.winmm.mciSendStringW(command, buffer, 255, 0)
        if code:
            error = ctypes.create_unicode_buffer(255); ctypes.windll.winmm.mciGetErrorStringW(code, error, 255)
            raise RuntimeError(error.value)
        return buffer.value

    def load(self, path: Path) -> None:
        validate_wav(path); self.close(); self._send(f'open "{path}" type waveaudio alias {self.alias}'); self.path = str(path)

    def play(self) -> None: self._send(f"play {self.alias}")
    def pause(self) -> None: self._send(f"pause {self.alias}")
    def stop(self) -> None: self._send(f"stop {self.alias}"); self._send(f"seek {self.alias} to start")
    def seek(self, milliseconds: int) -> None: self._send(f"seek {self.alias} to {max(0, milliseconds)}")
    def volume(self, value: float) -> None: self._send(f"setaudio {self.alias} volume to {int(max(0, min(1, value))*1000)}")
    def position(self) -> int: return int(self._send(f"status {self.alias} position") or 0)
    def length(self) -> int: return int(self._send(f"status {self.alias} length") or 0)
    def close(self) -> None:
        try: self._send(f"close {self.alias}")
        except RuntimeError: pass
