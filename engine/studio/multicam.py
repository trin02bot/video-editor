"""Multicam edit decision list (EDL) on the master timeline.

Each speaker turn opens on the speaker's primary angle; long turns rotate
through alternates (two-shot, a listener reaction, the wide) at natural pauses.
Cuts land in gaps between words (sentence ends preferred). Removals cut the
timeline and always change angle across the join.
"""
from __future__ import annotations

import json
import re

import numpy as np

from . import framing, speakers
from .config import Project
from .media import FPS
from .sync import file_at


def word_gaps(segs) -> np.ndarray:
    ws = [w for s in segs for w in s["words"]]
    g = []
    for a, b in zip(ws[:-1], ws[1:]):
        if b[0] - a[1] >= 0.12:
            g.append(((a[1] + b[0]) / 2, b[0] - a[1], 1.0 if re.search(r"[.?!]$", a[2].strip()) else 0.0))
    return np.array(g) if g else np.zeros((0, 3))


def snap(gaps: np.ndarray, t: float, win: float = 0.6, prefer_sentence: bool = False) -> float:
    if len(gaps):
        m = np.abs(gaps[:, 0] - t) <= win
        if m.any():
            c = gaps[m]
            score = np.abs(c[:, 0] - t) - 0.15 * np.minimum(c[:, 1], 1.0) - (0.35 * c[:, 2] if prefer_sentence else 0)
            return float(c[np.argmin(score), 0])
    return t


def removal_ranges(proj: Project, segs) -> list[tuple[float, float]]:
    """Resolve configured removals to master-time ranges placed in silences."""
    ws = [w for s in segs for w in s["words"]]
    norm = lambda s: re.sub(r"[^a-z0-9' ]", "", s.lower()).split()
    toks = [norm(w[2])[0] if norm(w[2]) else "" for w in ws]

    def find(phrase: str, after: float = 0) -> int | None:
        q = norm(phrase)
        for i in range(len(toks) - len(q) + 1):
            if ws[i][0] >= after and toks[i:i + len(q)] == q:
                return i
        return None
    out = []
    for r in proj.edit.removals:
        if r.start is not None and r.end is not None:
            out.append((r.start, r.end)); continue
        i = find(r.phrase_start or "")
        if i is None:
            raise ValueError(f"removal: phrase not found: {r.phrase_start!r}")
        j0 = find(r.phrase_end or "", ws[i][0])
        if j0 is None:
            raise ValueError(f"removal: phrase not found after start: {r.phrase_end!r}")
        j = j0 + len(norm(r.phrase_end)) - 1
        a = (ws[i - 1][1] + ws[i][0]) / 2 if i > 0 else ws[i][0] - 0.1
        b = (ws[j][1] + ws[j + 1][0]) / 2 if j + 1 < len(ws) else ws[j][1] + 0.2
        out.append((round(a * FPS) / FPS, round(b * FPS) / FPS))
    return sorted(out)


def _shots_for(proj: Project, pid: str) -> dict[str, list[str]]:
    A = proj.edit.angles
    singles = [s for s in A if s.split(":")[1] == "single" and s.split(":")[2] == pid]
    twos = [s for s in A if s.split(":")[1] == "two" and pid in s.split(":")[2].split("+")]
    wides_front = [s for s in A if s.endswith(":wide") and pid in proj.cam(s.split(":")[0]).frontal]
    wides_any = [s for s in A if s.endswith(":wide") and pid in proj.cam(s.split(":")[0]).order]
    listen = [s for s in A if s.split(":")[1] in ("single", "two", "wide") and s not in singles + twos
              and pid not in proj.cam(s.split(":")[0]).frontal]
    prim = (singles or twos or wides_front or wides_any or A)[:1]
    alts = []
    for s in twos + listen[:2] + wides_front + wides_any + singles:
        if s not in alts and s not in prim:
            alts.append(s)
    return {"primary": prim, "alts": alts or prim}


def build(proj: Project, fc: dict, path: np.ndarray, segs, sync: dict, log=print) -> list[dict]:
    out_f = proj.work_dir / "edl.json"
    if out_f.exists():
        return json.loads(out_f.read_text())
    P = len(proj.people)
    dur = sync["master"]["duration"]
    a0, a1 = proj.limit or (0.0, dur)
    gaps = word_gaps(segs)
    trn = speakers.turns(path, P, a0, a1, min_len=1.2)
    bounds = [a0] + [snap(gaps, t1) for _, t1, _ in trn[:-1]] + [a1]
    spans = []
    for k, (_, _, sp) in enumerate(trn):
        x, y = bounds[k], bounds[k + 1]
        if y - x <= 0.05:
            continue
        if spans and y - x < proj.edit.min_shot:
            spans[-1][1] = y
        else:
            spans.append([x, y, sp])
    rot = {}
    raw = []
    for x, y, sp in spans:
        pid = proj.people[sp].id
        opt = _shots_for(proj, pid)
        t, first = x, True
        while True:
            if first:
                shot = opt["primary"][0]
            else:
                i = rot.get(pid, 0); shot = opt["alts"][i % len(opt["alts"])]; rot[pid] = i + 1
                if i % 2 == 1:
                    shot = opt["primary"][0]
            mx = proj.edit.max_shot
            if y - t <= mx + proj.edit.min_shot:
                raw.append([t, y, shot, pid]); break
            c = snap(gaps, t + min(mx * 0.75, max(5.0, mx - 3)), win=2.0, prefer_sentence=True)
            if c - t < proj.edit.min_shot + 1 or y - c < proj.edit.min_shot:
                c = snap(gaps, t + mx, win=1.0)
            if c - t < proj.edit.min_shot or y - c < proj.edit.min_shot:
                raw.append([t, y, shot, pid]); break
            raw.append([t, c, shot, pid]); t = c; first = False
    # resolve framing + availability
    edl = []
    for x, y, shot, pid in raw:
        opt = _shots_for(proj, pid)
        cands = [shot] + [s for s in opt["primary"] + opt["alts"] if s != shot] + [s for s in proj.edit.angles if s.endswith(":wide")]
        for s in cands:
            cam = s.split(":")[0]
            if not file_at(sync, cam, x, y):
                continue
            cr = framing.crop(proj, fc, s, x, y)
            if cr:
                break
        else:
            s, cr = f"{proj.master.id}:wide", None
        if cr is None:
            cr = framing.crop(proj, fc, s, x, y)
        if edl and edl[-1]["shot"] == s and edl[-1]["crop"] == cr:
            edl[-1]["t1"] = y; continue
        edl.append({"t0": x, "t1": y, "shot": s, "crop": cr, "speaker": pid})
    # same shot type twice in a row with a different crop reads as a jump -> swap the later one
    for i in range(1, len(edl)):
        if edl[i]["shot"] == edl[i - 1]["shot"]:
            for alt in [s for s in proj.edit.angles if s != edl[i]["shot"]]:
                if file_at(sync, alt.split(":")[0], edl[i]["t0"], edl[i]["t1"]):
                    cr = framing.crop(proj, fc, alt, edl[i]["t0"], edl[i]["t1"])
                    if cr:
                        edl[i].update(shot=alt, crop=cr); break
    for s in edl:
        s["k0"] = int(round(s["t0"] * FPS)); s["k1"] = int(round(s["t1"] * FPS))
    edl[0]["k0"] = int(round(a0 * FPS)); edl[-1]["k1"] = int(round(a1 * FPS))
    for i in range(1, len(edl)):
        edl[i]["k0"] = edl[i - 1]["k1"]
    edl = [s for s in edl if s["k1"] > s["k0"]]
    out_f.write_text(json.dumps(edl))
    L = [(s["k1"] - s["k0"]) / FPS for s in edl]
    log(f"multicam: {len(edl)} shots, mean {np.mean(L):.1f}s, max {max(L):.1f}s")
    return edl


def apply_removals(proj: Project, fc: dict, edl: list[dict], removals, sync) -> tuple[list[dict], list[tuple[int, int]]]:
    """Cut removal ranges out of the EDL. Returns (timeline EDL, kept master-frame ranges)."""
    a0 = edl[0]["k0"]; a1 = edl[-1]["k1"]
    keep, t = [], a0
    for x, y in removals:
        kx, ky = int(round(x * FPS)), int(round(y * FPS))
        if ky <= a0 or kx >= a1:
            continue
        keep.append((t, max(t, kx))); t = ky
    keep.append((t, a1))
    keep = [(x, y) for x, y in keep if y > x]
    out = []
    for kx, ky in keep:
        for s in edl:
            x, y = max(s["k0"], kx), min(s["k1"], ky)
            if y > x:
                d = dict(s); d["k0"], d["k1"] = x, y; out.append(d)
    for i in range(1, len(out)):
        if out[i]["k0"] != out[i - 1]["k1"] and out[i]["shot"] == out[i - 1]["shot"]:
            t0, t1 = out[i]["k0"] / FPS, out[i]["k1"] / FPS
            for alt in [s for s in proj.edit.angles if s.split(":")[0] != out[i - 1]["shot"].split(":")[0]] + proj.edit.angles:
                if alt != out[i - 1]["shot"] and file_at(sync, alt.split(":")[0], t0, t1):
                    cr = framing.crop(proj, fc, alt, t0, t1)
                    if cr:
                        out[i].update(shot=alt, crop=cr); break
    return out, keep
