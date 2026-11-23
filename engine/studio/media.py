"""ffmpeg / ffprobe helpers."""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile

FPS = 30            # output frame rate (timeline is frame-accurate at 30 fps)
SR = 48000          # audio sample rate


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(cmd[:8])} ...\n{r.stderr[-2000:]}")


@lru_cache(maxsize=None)
def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=index,codec_type,width,height,r_frame_rate,pix_fmt,color_range,sample_rate,channels",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    return {
        "duration": float(d["format"]["duration"]),
        "width": int(v.get("width", 0)), "height": int(v.get("height", 0)),
        "fps": eval(v.get("r_frame_rate", "30/1")) if v else 0,
        "full_range": v.get("pix_fmt", "").startswith("yuvj") or v.get("color_range") == "pc",
    }


def extract_audio(src: str, dst: Path, sr: int = 16000, mono: bool = True, fmt: str = "pcm_s16le") -> Path:
    if not dst.exists():
        tmp = dst.with_name(dst.stem + ".part" + dst.suffix)      # never leave a half-written file under the real name
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", src, "-vn"]
        if mono:
            cmd += ["-ac", "1"]
        run(cmd + ["-ar", str(sr), "-c:a", fmt, str(tmp)])
        tmp.replace(dst)
    return dst


def read_wav(path: Path) -> tuple[int, np.ndarray]:
    sr, x = wavfile.read(str(path))
    if x.dtype == np.int16:
        x = x.astype(np.float32) / 32768
    elif x.dtype == np.int32:
        x = x.astype(np.float32) / 2147483648
    return sr, x.astype(np.float32)


def write_wav(path: Path, sr: int, x: np.ndarray) -> None:
    wavfile.write(str(path), sr, x.astype(np.float32))


def frame_grab(src: str, t: float, out: Path, width: int = 960) -> Path:
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)])
    return out
