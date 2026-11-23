"""Frame-accurate rendering: per-shot segments -> concat -> mux, plus color match and clean-joining parts."""
from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import media
from .config import Project
from .media import FPS, SR, run
from .sync import file_at

ENC = ["-an", "-c:v", "libx264", "-preset", os.environ.get("STUDIO_X264_PRESET", "medium"), "-crf", "17",
       "-pix_fmt", "yuv420p", "-profile:v", "high", "-color_primaries", "bt709", "-color_trc", "bt709",
       "-colorspace", "bt709", "-color_range", "tv", "-r", str(FPS), "-video_track_timescale", "30000",
       "-x264-params", "keyint=60:min-keyint=1:scenecut=0"]
WORKERS = int(os.environ.get("STUDIO_WORKERS", max(1, (os.cpu_count() or 4) // 2)))


def color_match(proj: Project, sync: dict, log=print) -> dict[str, list[float]]:
    """Per-camera RGB gains that make neutral surfaces match the master camera."""
    out_f = proj.work_dir / "color.json"
    if out_f.exists():
        return json.loads(out_f.read_text())
    import cv2
    tmp = proj.work_dir / "color"; tmp.mkdir(exist_ok=True)

    def neutral_mean(cam) -> np.ndarray:
        acc = []
        for k, f in enumerate(cam.files[:3]):
            d = media.probe(f)["duration"]
            for j, frac in enumerate((0.2, 0.5, 0.8)):
                png = tmp / f"{cam.id}_{k}_{j}.png"
                rng = "scale=in_range=full:out_range=tv," if media.probe(f)["full_range"] else ""
                run(["ffmpeg", "-v", "error", "-y", "-ss", f"{d*frac:.1f}", "-i", f, "-frames:v", "1",
                     "-vf", f"{rng}scale=960:-2,format=rgb24", str(png)])
                im = cv2.imread(str(png))[:, :, ::-1].astype(np.float32) / 255
                hsv = cv2.cvtColor((im * 255).astype(np.uint8), cv2.COLOR_RGB2HSV)
                m = (hsv[..., 1] < 40) & (hsv[..., 2] > 70) & (hsv[..., 2] < 235)
                if m.sum() > 500:
                    acc.append(im[m].mean(0))
        return np.mean(acc, 0) if acc else np.array([0.5, 0.5, 0.5])
    ref = neutral_mean(proj.master)
    gains = {}
    for c in proj.cameras:
        if c.master_audio:
            gains[c.id] = [1.0, 1.0, 1.0]; continue
        g = ref / np.maximum(neutral_mean(c), 1e-3)
        wb = g / g[1]; lum = 1 + (g[1] - 1) * 0.5
        gains[c.id] = [float(np.clip(wb[i] * lum, 0.8, 1.25)) for i in range(3)]
        log(f"color: camera {c.id} gains R{gains[c.id][0]:.3f} G{gains[c.id][1]:.3f} B{gains[c.id][2]:.3f}")
    out_f.write_text(json.dumps(gains))
    return gains


def source_args(proj: Project, sync: dict, cam_id: str, k0: int, k1: int) -> tuple[list[str], str]:
    """ffmpeg input args + pre-filter that yield frames for master frames [k0,k1) of a camera."""
    t0, t1 = k0 / FPS, k1 / FPS
    fa = file_at(sync, cam_id, t0, t1, margin=0.0)
    if fa is None:
        raise ValueError(f"camera {cam_id} has no footage for {t0:.2f}-{t1:.2f}")
    f, ft = fa
    info = media.probe(f)
    pre = ""
    if info["full_range"]:
        pre += "scale=in_range=full:out_range=tv,format=yuv420p,"
    if abs(info["fps"] - FPS) > 0.01:
        pre += f"fps={FPS},"
        ss = ft
    else:
        ss = ft - 0.5 / FPS
    return ["-ss", f"{max(0, ss):.4f}", "-i", f], pre


def render_segments(proj: Project, sync: dict, gains: dict, edl: list[dict], seg_dir: Path,
                    out_w=1920, out_h=1080, extra_vf: str = "", progress=None) -> list[Path]:
    seg_dir.mkdir(parents=True, exist_ok=True)

    def one(i):
        s = edl[i]; f = seg_dir / f"{i:05d}.mp4"; n = s["k1"] - s["k0"]
        if f.exists() and f.stat().st_size > 0 and _frames(f) == n:
            return f
        cam = s["shot"].split(":")[0]
        inp, pre = source_args(proj, sync, cam, s["k0"], s["k1"])
        x, y, w, h = s["crop"]
        g = gains.get(cam, [1, 1, 1])
        col = "" if g == [1.0, 1.0, 1.0] else f"format=gbrp,colorchannelmixer=rr={g[0]:.3f}:gg={g[1]:.3f}:bb={g[2]:.3f},"
        vf = f"{pre}crop={w}:{h}:{x}:{y},{col}scale={out_w}:{out_h}:flags=lanczos,setsar=1,{extra_vf}format=yuv420p"
        run(["ffmpeg", "-v", "error", "-y", "-threads", "2"] + inp + ["-frames:v", str(n), "-vf", vf] + ENC + [str(f)])
        return f
    done = 0; files = [None] * len(edl)
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, f in zip(range(len(edl)), ex.map(one, range(len(edl)))):
            files[i] = f; done += 1
            if progress:
                progress(done / len(edl))
    return files


def _frames(f: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(f)],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return -1


def concat(files: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{Path(f).resolve()}'\n" for f in files))
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out


def mux(video: Path, audio_wav: Path, out: Path, title: str = "", aac: bool = True) -> Path:
    a = ["-c:a", "aac", "-b:a", "320k"] if aac else ["-c:a", "pcm_s16le"]
    run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio_wav), "-map", "0:v", "-map", "1:a", "-c:v", "copy"]
        + a + ["-shortest", "-movflags", "+faststart"] + (["-metadata", f"title={title}"] if title else []) + [str(out)])
    return out


def parts(proj: Project, edl_tl: list[dict], video: Path, audio_wav: Path, out_dir: Path, max_s: float) -> list[Path]:
    """Split into parts that join back sample-exactly: every part starts on a camera change (an IDR frame)."""
    starts, t = [], 0
    for s in edl_tl:
        starts.append(t); t += s["k1"] - s["k0"]
    total = t; bounds = [0]; target = int(max_s * FPS)
    while total - bounds[-1] > target * 1.3:
        want = bounds[-1] + target
        bounds.append(min((x for x in starts if x > bounds[-1] + target * 2 // 3), key=lambda x: abs(x - want)))
    bounds.append(total)
    n = len(bounds) - 1; files = []
    for i, (k0, k1) in enumerate(zip(bounds, bounds[1:]), 1):
        f = out_dir / f"{proj.name}_Part{i:02d}of{n}.mov"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{k0/FPS:.6f}", "-i", str(video), "-i", str(audio_wav), "-map", "0:v", "-map", "1:a",
             "-frames:v", str(k1 - k0), "-c:v", "copy",
             "-af", f"atrim=start_sample={k0*SR//FPS}:end_sample={k1*SR//FPS},asetpts=PTS-STARTPTS",
             "-c:a", "pcm_s16le", "-ar", str(SR), "-movflags", "+faststart", str(f)])
        files.append(f)
    return files
