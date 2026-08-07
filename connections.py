from __future__ import annotations

import ctypes
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from settings import redact, validate_directory

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class ProcessResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


class ProcessRunner:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        if not self.process or self.process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        else:
            self.process.kill()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def run(self, command: list[str], timeout: float = 60, on_output: Callable[[str, str], None] | None = None, log_file: Path | None = None) -> ProcessResult:
        self.cancelled = False
        output: dict[str, list[str]] = {"stdout": [], "stderr": []}
        events: queue.Queue[tuple[str, str]] = queue.Queue()
        try:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        except OSError as exc:
            return ProcessResult(command, -1, "", str(exc))

        def read_stream(name: str, stream) -> None:
            for line in iter(stream.readline, ""):
                events.put((name, line))
            stream.close()

        threads = [threading.Thread(target=read_stream, args=(name, stream), daemon=True) for name, stream in (("stdout", self.process.stdout), ("stderr", self.process.stderr))]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        while self.process.poll() is None:
            try:
                name, line = events.get(timeout=0.05)
                output[name].append(line)
                if on_output:
                    on_output(name, line)
            except queue.Empty:
                pass
            if time.monotonic() >= deadline:
                timed_out = True
                self.cancel()
                break
        for thread in threads:
            thread.join(timeout=1)
        while not events.empty():
            name, line = events.get_nowait()
            output[name].append(line)
        result = ProcessResult(command, self.process.poll() if self.process.poll() is not None else -1, "".join(output["stdout"]), "".join(output["stderr"]), timed_out, self.cancelled and not timed_out)
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text(redact(f"Command: {command!r}\nExit: {result.exit_code}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"), encoding="utf-8")
        return result


def detect_python(kokoro_dir: str = "") -> str:
    candidates = []
    if kokoro_dir:
        candidates.append(Path(kokoro_dir) / "venv" / "Scripts" / "python.exe")
    for root in (Path.home() / "Downloads" / "Kokoro-TTS", Path.home() / "Kokoro-TTS"):
        candidates.append(root / "venv" / "Scripts" / "python.exe")
    found = shutil.which("python")
    if found:
        candidates.append(Path(found))
    return str(next((path for path in candidates if path.is_file()), ""))


def test_kokoro(config: dict, output_dir: Path, generate: bool = True) -> dict:
    python = Path(config.get("python", ""))
    install_dir = Path(config.get("install_dir", ""))
    if not python.is_file():
        return {"ok": False, "reason": f"Python executable not found: {python}"}
    if not install_dir.is_dir():
        return {"ok": False, "reason": f"Kokoro directory not found: {install_dir}"}
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / "kokoro_connection_test.wav"
    code = "import sys; from kokoro import KPipeline; import numpy as np, soundfile as sf; "
    if generate:
        code += f"p=KPipeline(lang_code={config.get('language', 'b')!r}, repo_id={config.get('model_repo', 'hexgrad/Kokoro-82M')!r}); parts=[np.asarray(a,dtype=np.float32) for _,_,a in p('Testing AtoZ Voice Studio generation.', voice={config.get('voice', 'bf_emma')!r}, speed={float(config.get('speed', 1.0))!r})]; sf.write({str(wav_path)!r}, np.concatenate(parts), 24000); print({str(wav_path)!r})"
    else:
        code += "import numpy, soundfile, misaki, phonemizer; print('Kokoro imports OK')"
    command = [str(python), "-c", code]
    log = output_dir / "kokoro-test.log"
    result = ProcessRunner().run(command, float(config.get("timeout", 180)), log_file=log)
    valid_wav = False
    if generate and result.ok and wav_path.exists() and wav_path.stat().st_size > 44:
        try:
            with wave.open(str(wav_path), "rb") as audio:
                valid_wav = audio.getnframes() > 0 and audio.getframerate() > 0
        except (OSError, wave.Error):
            pass
    ok = result.ok and (valid_wav if generate else True)
    return {"ok": ok, "connected": ok and generate, "reason": "Playable WAV generated." if ok and generate else ("Imports succeeded; generate a playable test voice to connect." if ok else f"Kokoro test failed (exit {result.exit_code})."), "command": command, "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.exit_code, "wav": str(wav_path) if valid_wav else "", "log": str(log)}


def normalize_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must start with http:// or https:// and include a host")
    return value.rstrip("/")


def request_json(url: str, data: dict | None = None, timeout: float = 10) -> dict:
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"} if payload else {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def validate_workflow(path: str, config: dict) -> tuple[bool, str, dict | None]:
    try:
        workflow = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(workflow, dict):
            raise ValueError("Workflow root must be an object")
        missing = [config[key] for key in ("positive_node", "negative_node", "seed_node", "width_node", "height_node", "output_node") if config.get(key) and str(config[key]) not in workflow]
        if missing:
            return False, f"Workflow node IDs not found: {', '.join(missing)}", None
        return True, f"Valid workflow with {len(workflow)} nodes.", workflow
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"Invalid workflow: {exc}", None


def test_comfyui(config: dict, generate: bool = False) -> dict:
    try:
        base = normalize_url(config.get("base_url", ""))
        timeout = float(config.get("timeout", 10))
        system = request_json(f"{base}/system_stats", timeout=timeout)
        queue_info = request_json(f"{base}/queue", timeout=timeout)
        if not generate:
            return {"ok": False, "reason": "Server responded; test generation is still required.", "system": system, "queue": queue_info}
        valid, reason, workflow = validate_workflow(config.get("workflow_file", ""), config)
        if not valid:
            return {"ok": False, "reason": reason}
        workflow = json.loads(json.dumps(workflow))
        test_values = {"positive_node": ("text", "A simple blue circle on a white background"), "negative_node": ("text", "text, watermark"), "seed_node": ("seed", 1), "width_node": ("width", 256), "height_node": ("height", 256)}
        for setting, (field, value) in test_values.items():
            node_id = str(config.get(setting, ""))
            if node_id:
                workflow[node_id].setdefault("inputs", {})[field] = value
        client_id = str(uuid.uuid4())
        submitted = request_json(f"{base}/prompt", {"prompt": workflow, "client_id": client_id}, timeout)
        prompt_id = submitted["prompt_id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            history = request_json(f"{base}/history/{prompt_id}", timeout=min(timeout, 10))
            if prompt_id in history:
                outputs = history[prompt_id].get("outputs", {})
                for node in outputs.values():
                    for image in node.get("images", []):
                        query = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image.get("type", "output")})
                        target_dir = Path(config["output_dir"])
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target = target_dir / image["filename"]
                        with urllib.request.urlopen(f"{base}/view?{query}", timeout=timeout) as response:
                            target.write_bytes(response.read())
                        signature = target.read_bytes()[:12]
                        if signature.startswith(b"\x89PNG\r\n\x1a\n") or signature.startswith(b"\xff\xd8\xff") or signature[:4] in {b"RIFF", b"II*\x00", b"MM\x00*"}:
                            return {"ok": True, "reason": "Test image generated.", "image": str(target)}
                return {"ok": False, "reason": "ComfyUI completed without an image output."}
            time.sleep(1)
        return {"ok": False, "reason": "ComfyUI generation timed out."}
    except Exception as exc:
        return {"ok": False, "reason": f"ComfyUI test failed: {exc}"}


def test_kling_api(config: dict, api_key: str) -> dict:
    try:
        base = normalize_url(config.get("api_base_url", ""))
        if not api_key:
            return {"ok": False, "reason": "Kling API key is not stored."}
        request = urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            if 200 <= response.status < 300:
                return {"ok": True, "reason": f"Authenticated Kling API request succeeded ({response.status})."}
        return {"ok": False, "reason": "Kling API authentication failed."}
    except Exception as exc:
        return {"ok": False, "reason": f"Kling API test failed: {exc}"}


def detect_ffmpeg(configured: str = "") -> tuple[str, str]:
    downloads = Path.home() / "Downloads"
    downloaded = list(downloads.glob("ffmpeg*/bin/ffmpeg.exe")) + list(downloads.glob("ComfyUI*/ffmpeg*/bin/ffmpeg.exe"))
    candidates = [
        configured,
        shutil.which("ffmpeg") or "",
        str(Path(sys.executable).resolve().parent / "tools" / "ffmpeg.exe"),
        str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ffmpeg" / "bin" / "ffmpeg.exe"),
        str(Path(os.environ.get("ProgramData", "C:/ProgramData")) / "chocolatey" / "bin" / "ffmpeg.exe"),
        *map(str, downloaded),
    ]
    ffmpeg = next((str(Path(item)) for item in candidates if item and Path(item).is_file()), "")
    if not ffmpeg:
        return "", ""
    sibling = Path(ffmpeg).with_name("ffprobe.exe")
    return ffmpeg, str(sibling) if sibling.is_file() else (shutil.which("ffprobe") or "")


def test_ffmpeg(config: dict, temp_dir: Path) -> dict:
    ffmpeg, ffprobe = detect_ffmpeg(config.get("ffmpeg", ""))
    if not ffmpeg or not ffprobe:
        return {"ok": False, "reason": "FFmpeg and FFprobe executables were not found."}
    temp_dir.mkdir(parents=True, exist_ok=True)
    target = temp_dir / "ffmpeg_connection_test.mp4"
    runner = ProcessRunner()
    version = runner.run([ffmpeg, "-version"], 20)
    encoders = runner.run([ffmpeg, "-hide_banner", "-encoders"], 30)
    create = runner.run([ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=0.5", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(target)], 60)
    probe = runner.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(target)], 30) if create.ok else ProcessResult([], -1, "", "Creation failed")
    ok = version.ok and create.ok and probe.ok and target.exists() and target.stat().st_size > 0
    text = encoders.stdout
    return {"ok": ok, "reason": "Sample MP4 created and verified." if ok else "FFmpeg sample creation or FFprobe validation failed.", "ffmpeg": ffmpeg, "ffprobe": ffprobe, "version": version.stdout.splitlines()[0] if version.stdout else version.stderr, "nvidia": "h264_nvenc" in text, "intel": "h264_qsv" in text, "amd": "h264_amf" in text, "sample": str(target) if ok else "", "stderr": create.stderr + probe.stderr}


class CredentialStore:
    """Windows Credential Manager wrapper; tests can replace this class."""

    def __init__(self, service: str = "AtoZ Voice Studio") -> None:
        self.service = service
        self.legacy_service = "YouTube AI Studio" if service == "AtoZ Voice Studio" else None

    def set(self, name: str, secret: str) -> None:
        import keyring

        keyring.set_password(self.service, name, secret)

    def get(self, name: str) -> str:
        import keyring

        return keyring.get_password(self.service, name) or (keyring.get_password(self.legacy_service, name) if self.legacy_service else "") or ""

    def delete(self, name: str) -> None:
        import keyring

        try:
            keyring.delete_password(self.service, name)
        except keyring.errors.PasswordDeleteError:
            pass
