"""Audio: speaker-aware leveling, loudness normalization and sample-exact timeline assembly."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
import scipy.signal as ss
from scipy.ndimage import maximum_filter1d, uniform_filter1d

from . import media
from .config import Project
from .media import FPS, SR, run


def master48(proj: Project) -> Path:
    return media.extract_audio(proj.master.files[0], proj.work_dir / "audio" / "master48.wav", sr=SR, fmt="pcm_f32le")


def level(proj: Project, path: np.ndarray, segs, log=print) -> Path:
    """Raise non-host speakers toward the host's level (only while they speak), clarity EQ, light denoise,
    then compress + two-pass loudness normalize. Output: float wav on the master timeline."""
    out = proj.work_dir / "audio" / "master_final.wav"
    if out.exists():
        return out
    src = master48(proj)
    if not proj.audio.level_guests:
        _loudnorm(src, out, proj.audio.loudness_lufs, proj.audio.true_peak)
        return out
    dn = proj.work_dir / "audio" / "master48_dn.wav"
    if not dn.exists():
        run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", "afftdn=nr=10:nf=-50:tn=1", "-c:a", "pcm_f32le", str(dn)])
    _, x = media.read_wav(src); _, xd = media.read_wav(dn); xd = xd[:len(x)]
    H = 480; N10 = len(x) // H                                      # 10 ms frames
    P = len(proj.people)
    lab = np.repeat(path, 10)[:N10]; lab = np.pad(lab, (0, max(0, N10 - len(lab))), constant_values=P)
    iw = np.zeros(N10, bool)
    for s in segs:
        for a, b, _ in s["words"]:
            iw[int(a * 100):min(int(np.ceil(b * 100)), N10)] = True
    iw = maximum_filter1d(iw.astype(np.uint8), 25).astype(bool)
    lv = 10 * np.log10(uniform_filter1d((x[:N10 * H].reshape(N10, H) ** 2).mean(1), 40) + 1e-12)
    hosts = [i for i, p in enumerate(proj.people) if p.role == "host"] or [0]
    hm = np.isin(lab, hosts) & iw
    target = float(np.median(lv[hm][lv[hm] > np.percentile(lv[hm], 30)])) if hm.sum() > 100 else -12.0
    g = np.zeros(N10, np.float32); guest = np.zeros(N10, bool)
    for i, p in enumerate(proj.people):
        if i in hosts:
            continue
        m = (lab == i) & iw; guest |= (lab == i)
        g[m] = np.clip(target - lv[m], 0, 18)
        if m.sum():
            log(f"audio: {p.name} median boost {np.median(g[m]):.1f} dB")
    g = uniform_filter1d(maximum_filter1d(g, 15), 30)
    gw = uniform_filter1d(guest.astype(np.float32), 20)
    A = 10 ** (3.0 / 40); w0 = 2 * np.pi * 3200 / SR; al = np.sin(w0) / 2
    b, a = [1 + al * A, -2 * np.cos(w0), 1 - al * A], [1 + al / A, -2 * np.cos(w0), 1 - al / A]
    xe = ss.lfilter(np.array(b) / a[0], np.array(a) / a[0], xd).astype(np.float32)
    xe = ss.sosfilt(ss.butter(2, 90, "hp", fs=SR, output="sos"), xe).astype(np.float32)
    tt = np.arange(N10) * H + H / 2
    gws = np.interp(np.arange(len(x)), tt, gw).astype(np.float32)
    y = x * (1 - gws) + xe * gws
    y *= 10 ** (np.interp(np.arange(len(x)), tt, g).astype(np.float32) / 20)
    pre = proj.work_dir / "audio" / "master_leveled_pre.wav"
    media.write_wav(pre, SR, y)
    _loudnorm(pre, out, proj.audio.loudness_lufs, proj.audio.true_peak,
              pre_filter="acompressor=threshold=-20dB:ratio=2.5:attack=15:release=250:makeup=1")
    for tmp in (pre, dn):                     # large intermediates; the final file is all later stages need
        tmp.unlink(missing_ok=True)
    return out


def _loudnorm(src: Path, dst: Path, I: float, tp: float, pre_filter: str = "") -> None:
    chain = (pre_filter + "," if pre_filter else "")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af", f"{chain}loudnorm=I={I}:TP={tp}:LRA=9:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    j = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S).group(0))
    meas = f"measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", f"{chain}loudnorm=I={I}:TP={tp}:LRA=9:{meas}:linear=true,aresample={SR}",
         "-c:a", "pcm_f32le", str(dst)])


def timeline(src: Path, keep: list[tuple[int, int]], out: Path, xfade: float = 0.012) -> Path:
    """Concatenate kept master-frame ranges with tiny crossfades; length = exactly the kept frames."""
    total = sum(b - a for a, b in keep)
    _, x = media.read_wav(src)
    if x.ndim > 1:
        x = x.mean(1)
    n = int(round(xfade * SR)); parts = []
    for a, b in keep:
        seg = x[a * SR // FPS:b * SR // FPS].copy()
        if len(seg) < b * SR // FPS - a * SR // FPS:
            seg = np.pad(seg, (0, b * SR // FPS - a * SR // FPS - len(seg)))
        if n and len(seg) > 2 * n:
            seg[:n] *= np.linspace(0, 1, n); seg[-n:] *= np.linspace(1, 0, n)
        parts.append(seg)
    y = np.concatenate(parts)[:total * SR // FPS]
    y = np.pad(y, (0, total * SR // FPS - len(y)))
    import scipy.io.wavfile as wf
    wf.write(str(out), SR, np.stack([y, y], 1).astype(np.float32))
    return out
