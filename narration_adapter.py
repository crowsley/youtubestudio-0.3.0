from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path


def event(name: str, **values) -> None:
    print(json.dumps({"event": name, **values}), flush=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--text-file", required=True)
    result.add_argument("--output", required=True)
    result.add_argument("--voice", required=True)
    result.add_argument("--language", required=True)
    result.add_argument("--speed", required=True, type=float)
    result.add_argument("--model-repo", default="hexgrad/Kokoro-82M")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        text = Path(args.text_file).read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("Narration text is empty")
        if not 0.5 <= args.speed <= 2.0:
            raise ValueError("Speech speed must be between 0.5 and 2.0")
        event("stage", stage="loading_model")
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline

        pipeline = KPipeline(lang_code=args.language, repo_id=args.model_repo)
        event("stage", stage="generating_audio")
        parts = []
        for current, (_, _, audio) in enumerate(pipeline(text, voice=args.voice, speed=args.speed), 1):
            parts.append(np.asarray(audio, dtype=np.float32))
            event("progress", current=current, total=0)
        if not parts:
            raise RuntimeError("Kokoro returned no audio")
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, np.concatenate(parts), 24000)
        with wave.open(str(output), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
        event("complete", output=str(output), duration=duration)
        return 0
    except Exception as exc:
        event("error", type=type(exc).__name__, message=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
