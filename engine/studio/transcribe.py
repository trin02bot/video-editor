"""Word-level transcription with faster-whisper, primed with names from the project."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from . import media
from .config import Project

MODELS = Path(os.environ.get("STUDIO_MODELS", Path.home() / ".cache" / "studio-models"))


def _model(name: str):
    from faster_whisper import WhisperModel
    local = MODELS / f"faster-whisper-{name}"
    if not (local / "model.bin").exists():
        from huggingface_hub import snapshot_download
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        snapshot_download(f"Systran/faster-whisper-{name}", local_dir=str(local))
    threads = int(os.environ.get("STUDIO_WHISPER_THREADS", os.cpu_count() or 4))
    device = "cuda" if os.environ.get("STUDIO_CUDA") == "1" else "cpu"
    return WhisperModel(str(local), device=device, compute_type="float16" if device == "cuda" else "int8", cpu_threads=threads)


def prompt_for(proj: Project) -> str:
    names = ", ".join(p.name for p in proj.people)
    b = proj.brand
    return f"Podcast: {b.show_name}. {b.guest_subline}. Speakers: {names}. Conversational English, y'all."


def transcribe_array(x: np.ndarray, model, prompt: str, offset: float = 0.0) -> list[dict]:
    segs, _ = model.transcribe(x, language="en", beam_size=5, word_timestamps=True, vad_filter=True,
                               initial_prompt=prompt, condition_on_previous_text=False)
    out = []
    for s in segs:
        out.append({"start": s.start + offset, "end": s.end + offset, "text": s.text.strip(),
                    "words": [[w.start + offset, w.end + offset, w.word.strip()] for w in s.words]})
    return out


def run(proj: Project, log=print) -> list[dict]:
    out = proj.work_dir / "transcript.json"
    if out.exists():
        return json.loads(out.read_text())
    m16 = proj.work_dir / "audio" / "master16.wav"
    _, x = media.read_wav(m16)
    a, b = proj.limit or (0, len(x) / 16000)
    log(f"transcribe: {b - a:.0f}s with whisper {proj.whisper_model}")
    model = _model(proj.whisper_model)
    segs = transcribe_array(x[int(a * 16000):int(b * 16000)], model, prompt_for(proj), offset=a)
    out.write_text(json.dumps(segs))
    return segs


def words(segs: list[dict]) -> list[tuple[float, float, str]]:
    return [(w[0], w[1], w[2]) for s in segs for w in s["words"]]
