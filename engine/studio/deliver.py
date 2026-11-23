"""Text deliverables on the final (post-removal) timeline: transcript, SRT captions."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .config import Project
from .media import FPS


def map_time(t: float, keep: list[tuple[int, int]]) -> float | None:
    """Master time -> final timeline time (None if inside a removed range)."""
    acc = 0.0
    for a, b in keep:
        if a / FPS <= t < b / FPS:
            return acc + t - a / FPS
        acc += (b - a) / FPS
    return None


def timeline_words(segs, keep, path, P, rate=10) -> list[tuple[float, float, str, int]]:
    """Words on the final timeline, labeled with smoothed speaker turns (no word-by-word flicker)."""
    from . import speakers
    if not segs:
        return []
    turns = speakers.turns(path, P, segs[0]["start"], segs[-1]["end"] + 1, min_len=2.0)
    starts = np.array([t[0] for t in turns])

    def who(t):
        i = int(np.searchsorted(starts, t, side="right")) - 1
        return turns[max(i, 0)][2]
    out = []
    for s in segs:
        for a, b, w in s["words"]:
            m = map_time((a + b) / 2, keep)
            if m is None:
                continue
            ta = map_time(a, keep); tb = map_time(b, keep)
            out.append((ta if ta is not None else m, tb if tb is not None else m, w.strip(), who((a + b) / 2)))
    return out


def _clean(t: str) -> str:
    t = re.sub(r"\s+([,.?!])", r"\1", t)
    return re.sub(r" '(all|s|re|ll|ve|t|d|m)\b", r"'\1", t).replace(" -", "-")


def transcript(proj: Project, words, out: Path) -> Path:
    def ts(s): s = int(s); return f"{s//3600}:{s%3600//60:02d}:{s%60:02d}" if s >= 3600 else f"{s//60:02d}:{s%60:02d}"
    lines = [proj.brand.show_name.upper(), f"{proj.brand.episode_label} · {proj.brand.guest_line}".strip(" ·"), "",
             "Auto-generated transcript of the final edit. Speaker labels are automatic.", ""]
    cur = None; buf = []; start = 0.0
    for i, (a, b, w, sp) in enumerate(words):
        brk = cur is None or sp != cur or (a - words[i - 1][1] > 2.0 and re.search(r"[.?!]$", words[i - 1][2])) \
            or (len(buf) > 110 and re.search(r"[.?!]$", words[i - 1][2]))
        if brk and buf:
            lines += [f"[{ts(start)}] {proj.people[cur].name}: {_clean(' '.join(buf))}", ""]; buf = []
        if brk:
            cur, start = sp, a
        buf.append(w)
    if buf:
        lines += [f"[{ts(start)}] {proj.people[cur].name}: {_clean(' '.join(buf))}", ""]
    out.write_text("\n".join(lines))
    return out


def srt(words, out: Path, offset: float = 0.0) -> Path:
    def st(s): s += offset; return f"{int(s//3600):02d}:{int(s%3600//60):02d}:{s%60:06.3f}".replace(".", ",")
    cues, cw = [], []
    for i, (a, b, w, _) in enumerate(words):
        cw.append((a, b, w)); txt = " ".join(x[2] for x in cw)
        gap = i + 1 < len(words) and words[i + 1][0] - b > 0.8
        if len(txt) > 70 or (re.search(r"[.?!,]$", w) and len(txt) > 40) or gap or cw[-1][1] - cw[0][0] > 5.5 or i == len(words) - 1:
            cues.append((cw[0][0], cw[-1][1], _clean(txt))); cw = []
    out_l = []
    for n, (a, b, txt) in enumerate(cues, 1):
        if len(txt) > 42:
            ws = txt.split(); k = min(range(1, len(ws)), key=lambda k: abs(len(" ".join(ws[:k])) - len(" ".join(ws[k:]))))
            txt = " ".join(ws[:k]) + "\n" + " ".join(ws[k:])
        nb = cues[n][0] if n < len(cues) else b + 0.5
        out_l += [str(n), f"{st(a)} --> {st(min(b + 0.3, nb - 0.02))}", txt, ""]
    out.write_text("\n".join(out_l))
    return out
