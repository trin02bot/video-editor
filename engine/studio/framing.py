"""Shot framing: turns a shot type into a crop rectangle on a source camera.

Rules (learned the hard way):
  * a close-up never shows part of another person's head - it either excludes it
    completely or the shot is not used;
  * never enlarge more than ~1.75x (landscape) / ~2.1x (vertical);
  * leave lead room in the direction the person is looking (toward the others).
"""
from __future__ import annotations

import numpy as np

from . import faces as F
from . import media
from .config import Project

HEAD = 0.95   # head half-width incl. hair, in units of detector face-box width


def parse(shot: str) -> tuple[str, str, list[str]]:
    parts = shot.split(":")
    cam, kind = parts[0], parts[1]
    people = parts[2].split("+") if len(parts) > 2 else []
    return cam, kind, people


def _src(proj: Project, cam: str) -> tuple[int, int]:
    info = media.probe(proj.cam(cam).files[0])
    return info["width"], info["height"]


def _bounds(fc, proj, cam, keep: list[str], t0, t1, W) -> tuple[float, float]:
    """Pixel x-interval that excludes every other person's head on this camera."""
    L, R = 0.0, float(W)
    kx = [F.median_pos(fc, cam, p, t0, t1)[0] for p in keep]
    kx = [k[0] * W for k in kx if k is not None]
    if not kx:
        return L, R
    lo, hi = min(kx), max(kx)
    for p in proj.cam(cam).order:
        if p in keep:
            continue
        m, _ = F.median_pos(fc, cam, p, t0, t1)
        if m is None:
            continue
        ox = m[0] * W; hw = m[2] * W * HEAD
        if ox > hi:
            R = min(R, ox - hw)
        elif ox < lo:
            L = max(L, ox + hw)
    return L, R


def _lead(fc, proj, cam, pid, t0, t1) -> float:
    m, _ = F.median_pos(fc, cam, pid, t0, t1)
    others = [F.median_pos(fc, cam, p, t0, t1)[0] for p in proj.cam(cam).order if p != pid]
    others = [o[0] for o in others if o is not None]
    if m is None or not others:
        return 0.5
    return 0.42 if m[0] < np.mean(others) else 0.58


def crop(proj: Project, fc: dict, shot: str, t0: float, t1: float) -> list[int] | None:
    """16:9 crop [x, y, w, h] in source pixels, or None if the shot can't be framed cleanly here."""
    cam, kind, ppl = parse(shot)
    W, H = _src(proj, cam)
    if kind == "wide":
        return [0, 0, W, H]
    pos = [F.median_pos(fc, cam, p, t0, t1) for p in ppl]
    if any(m is None or sd > 0.03 for m, sd in pos):
        return None                      # person not found, or camera moved during the shot
    min_w = 1920 / 1.75
    if kind == "single":
        m = pos[0][0]; cx, cy, fw = m[0] * W, m[1] * H, m[2] * W
        want = float(np.clip(fw * 7.5, min_w, W))
        lead = _lead(fc, proj, cam, ppl[0], t0, t1)
    elif kind == "two":
        a, b = sorted([p[0] for p, _ in pos], key=lambda q: q[0])
        lo = a[0] * W - a[2] * W * 1.6; hi = b[0] * W + b[2] * W * 1.6
        cx = (lo + hi) / 2; cy = (a[1] + b[1]) / 2 * H
        want = float(np.clip((hi - lo) * 1.25, max(min_w, hi - lo), W)); lead = 0.5
        min_w = max(min_w, hi - lo)
    else:
        raise ValueError(f"unknown shot kind {kind}")
    L, R = _bounds(fc, proj, cam, ppl, t0, t1, W)
    w = min(want, R - L, W)
    if w < min_w:
        return None
    w = int(w) // 2 * 2; h = int(round(w * 9 / 16)) // 2 * 2
    x = cx - w * lead; x = min(max(x, L), R - w); x = int(min(max(x, 0), W - w))
    y = int(min(max(cy - h * 0.36, 0), H - h))
    return [x, y, w, h]


def vertical(proj: Project, fc: dict, cam: str, pid: str, t0: float, t1: float, zoom: float = 1.0) -> list[int]:
    """9:16 crop centred on one person (for shorts / reels)."""
    W, H = _src(proj, cam)
    m, _ = F.median_pos(fc, cam, pid, t0, t1)
    if m is None:
        m = np.array([0.5, 0.35, 0.06, 0.1])
    face_h = m[2] * W * 1.3
    ch = float(np.clip(face_h / 0.2, max(H * 0.45, 1920 / 2.1), H)) / zoom
    ch = int(ch) // 2 * 2; cw = int(ch * 9 / 16) // 2 * 2
    lead = _lead(fc, proj, cam, pid, t0, t1)
    x = int(np.clip(m[0] * W - cw * lead, 0, W - cw))
    y = int(np.clip(m[1] * H - ch * 0.32, 0, H - ch))
    return [x, y, cw, ch]


def best_camera_for(proj: Project, pid: str, t0: float, t1: float, sync: dict) -> str | None:
    """A camera that faces this person and has footage over [t0,t1]."""
    from .sync import file_at
    for c in proj.cameras:
        if pid in c.frontal and file_at(sync, c.id, t0, t1):
            return c.id
    for c in proj.cameras:
        if pid in c.order and file_at(sync, c.id, t0, t1):
            return c.id
    return None
