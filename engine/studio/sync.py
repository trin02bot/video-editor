"""Audio-based multicam sync.

Every camera file is aligned to the master-audio camera with GCC-PHAT
cross-correlation, then drift is measured with local correlations every two
minutes and fitted linearly. Result per file: master_time = file_time*(1+slope) + offset.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import scipy.signal as ss

from . import media
from .config import Project


def _gcc(a: np.ndarray, b: np.ndarray) -> tuple[int, float]:
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    A = np.fft.rfft(a, n); B = np.fft.rfft(b, n)
    R = A * np.conj(B); R /= np.abs(R) + 1e-9
    c = np.fft.irfft(R, n); k = int(np.argmax(c)); peak = c[k] / (np.std(c) + 1e-12)
    if k > n // 2:
        k -= n
    return k, float(peak)


def run(proj: Project, log=print) -> dict:
    out = proj.work_dir / "sync.json"
    if out.exists():
        return json.loads(out.read_text())
    aud = proj.work_dir / "audio"; aud.mkdir(exist_ok=True)
    m = proj.master
    if len(m.files) != 1:
        raise ValueError("the master-audio camera must be a single file")
    m16 = media.extract_audio(m.files[0], aud / "master16.wav")
    _, M16 = media.read_wav(m16)
    M4 = ss.resample_poly(M16, 1, 4)
    res: dict = {"master": {"file": m.files[0], "duration": media.probe(m.files[0])["duration"]}, "files": {}}
    for cam in proj.cameras:
        for f in cam.files:
            if cam.master_audio:
                res["files"][f] = {"camera": cam.id, "offset": 0.0, "slope": 0.0, "duration": media.probe(f)["duration"], "residual_ms": 0.0}
                continue
            log(f"sync: {Path(f).name}")
            b16 = media.extract_audio(f, aud / (Path(f).stem + "_16.wav"))
            _, B16 = media.read_wav(b16)
            lag, peak = _gcc(M4, ss.resample_poly(B16, 1, 4))
            off = lag / 4000.0
            pts = []
            for t in np.arange(30, len(B16) / 16000 - 40, 120):
                seg = B16[int(t * 16000):int((t + 20) * 16000)]
                m0 = int((t + off - 2) * 16000)
                ref = M16[max(m0, 0):m0 + 24 * 16000]
                if len(ref) < len(seg) + 16000:
                    continue
                c = ss.correlate(ref, seg, "valid", method="fft"); k = int(np.argmax(np.abs(c)))
                sharp = np.abs(c[k]) / (np.median(np.abs(c)) + 1e-9)
                pts.append((t, (max(m0, 0) + k) / 16000.0 - t, sharp))
            pts = np.array(pts) if pts else np.zeros((0, 3))
            good = pts[pts[:, 2] > 8] if len(pts) else pts
            if len(good) >= 3:
                slope, offset = np.polyfit(good[:, 0], good[:, 1], 1)
                resid = float(np.abs(good[:, 1] - np.polyval([slope, offset], good[:, 0])).max() * 1000)
            else:
                slope, offset, resid = 0.0, off, float("nan")
            if peak < 6:
                log(f"  warning: weak sync peak ({peak:.1f}) for {Path(f).name} - check that it overlaps the master recording")
            res["files"][f] = {"camera": cam.id, "offset": float(offset), "slope": float(slope),
                               "duration": media.probe(f)["duration"], "residual_ms": resid, "peak": peak}
            log(f"  offset {offset:.3f}s, drift {slope*1800*1000:.1f} ms/30min, worst residual {resid:.1f} ms")
    out.write_text(json.dumps(res, indent=1))
    return res


def file_at(sync: dict, cam_id: str, t0: float, t1: float, margin: float = 0.5) -> tuple[str, float] | None:
    """Which file of a camera covers master interval [t0,t1], and the file-time at t0."""
    for f, s in sync["files"].items():
        if s["camera"] != cam_id:
            continue
        a = s["offset"] + margin
        b = s["offset"] + s["duration"] * (1 + s["slope"]) - margin
        if t0 >= a and t1 <= b:
            return f, (t0 - s["offset"]) / (1 + s["slope"])
    return None
