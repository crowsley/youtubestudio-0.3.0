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
    result.add_argument("--text-file")
    result.add_argument("--output")
    result.add_argument("--batch-file")
    result.add_argument("--voice", required=True)
    result.add_argument("--language", required=True)
    result.add_argument("--speed", required=True, type=float)
    result.add_argument("--model-repo", default="hexgrad/Kokoro-82M")
    return result


def synthesize(pipeline, text: str, output: Path, voice: str, speed: float, np, sf) -> float:
    if not text.strip():
        raise ValueError("Narration text is empty")
    parts = []
    for current, (_, _, audio) in enumerate(pipeline(text, voice=voice, speed=speed), 1):
        parts.append(np.asarray(audio, dtype=np.float32))
        event("progress", current=current, total=0)
    if not parts:
        raise RuntimeError("Kokoro returned no audio")
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, np.concatenate(parts), 24000)
    with wave.open(str(output), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if not 0.5 <= args.speed <= 2.0:
            raise ValueError("Speech speed must be between 0.5 and 2.0")
        event("stage", stage="loading_model")
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline

        pipeline = KPipeline(lang_code=args.language, repo_id=args.model_repo)
        if args.batch_file:
            jobs = json.loads(Path(args.batch_file).read_text(encoding="utf-8"))
            failures = 0
            for position, job in enumerate(jobs, 1):
                try:
                    event("item_start", index=job["index"], current=position, total=len(jobs))
                    duration = synthesize(pipeline, job["text"], Path(job["output"]), args.voice, args.speed, np, sf)
                    event("item_complete", index=job["index"], output=job["output"], duration=duration, current=position, total=len(jobs))
                except Exception as exc:
                    failures += 1
                    event("item_error", index=job.get("index"), type=type(exc).__name__, message=str(exc), current=position, total=len(jobs))
            event("batch_complete", total=len(jobs), failed=failures)
            return int(bool(failures))
        if not args.text_file or not args.output:
            raise ValueError("--text-file and --output are required for single generation")
        event("stage", stage="generating_audio")
        output = Path(args.output)
        duration = synthesize(pipeline, Path(args.text_file).read_text(encoding="utf-8"), output, args.voice, args.speed, np, sf)
        event("complete", output=str(output), duration=duration)
        return 0
    except Exception as exc:
        event("error", type=type(exc).__name__, message=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
