"""Vertical 9:16 shorts: one continuous take per clip, cuts only on speaker changes,
Mel-Robbins-style headline box + clean phrase captions, -14 LUFS."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import framing, llm, media, render, speakers
from .config import Project
from .media import FPS, SR, run

FONT_DIR = Path(__file__).parent / "fonts"
F_HEAD = str(FONT_DIR / "Inter-ExtraBold.otf")
SMALL = {'a', 'an', 'the', 'and', 'but', 'or', 'for', 'nor', 'on', 'at', 'to', 'by', 'of', 'in', 'so', 'is', 'vs'}


def titlecase(t: str) -> str:
    ws = t.split()
    return " ".join(w.lower() if i and w.lower() in SMALL else (w if any(c.isupper() for c in w[1:]) else w[:1].upper() + w[1:])
                    for i, w in enumerate(ws))


def _wrap(text, font, size, maxw):
    f = ImageFont.truetype(font, size); lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if f.getlength(t) <= maxw:
            cur = t
        else:
            lines.append(cur); cur = w
    lines.append(cur)
    return lines


def headline_png(text: str, out: Path) -> Path:
    text = titlecase(text); fsz = 78
    while len(_wrap(text, F_HEAD, fsz, 780)) > 2:
        fsz -= 4
    lines = _wrap(text, F_HEAD, fsz, 780); f = ImageFont.truetype(F_HEAD, fsz); lh = int(fsz * 1.18)
    tw = max(f.getlength(l) for l in lines); px, py = 46, 34
    W, H = int(tw + 2 * px), int(lh * len(lines) + 2 * py - fsz * 0.12)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=26, fill=(255, 255, 255, 255))
    for i, l in enumerate(lines):
        d.text(((W - f.getlength(l)) / 2, py + i * lh - fsz * 0.06), l, font=f, fill=(0, 0, 0, 255))
    im.save(out)
    return out


def _merge(ws):
    out = []
    for a, b, w in ws:
        if out and (re.match(r"^'(all|s|re|ll|ve|t|d|m)\b", w) or w.startswith("-") or (w.startswith(".") and len(w) <= 3)):
            out[-1][2] += w; out[-1][1] = b
        elif w.lower().strip(",.") in ("uh", "um") or not w.strip(" ."):
            continue
        else:
            out.append([a, b, w])
    return out


def caption_ass(words, out: Path) -> Path:
    G, g = [], []
    for a, b, w in words:
        g.append([a, b, w]); L = len(" ".join(x[2] for x in g))
        if re.search(r"[.?!]$", w) or (w.endswith(",") and len(g) >= 2) or len(g) >= 3 or L >= 16:
            G.append(g); g = []
    if g:
        G.append(g)
    ts = lambda t: "%d:%02d:%05.2f" % (t // 3600, (t % 3600) // 60, max(0, t) % 60)
    starts = [max(0, x[0][0] - 0.05) for x in G]
    for i in range(1, len(starts)):
        starts[i] = max(starts[i], starts[i - 1] + 0.1)
    ev, prev = [], 0.0
    for i, x in enumerate(G):
        s = max(starts[i], prev); nxt = starts[i + 1] if i + 1 < len(G) else None
        e = nxt - 0.02 if nxt is not None and nxt - x[-1][1] < 0.7 else x[-1][1] + 0.3
        if nxt is not None:
            e = min(e, nxt - 0.02)
        e = max(e, s + 0.06); prev = e + 0.01
        txt = re.sub(r"[.,]+$", "", " ".join(w[2] for w in x))
        ev.append(f"Dialogue: 0,{ts(s)},{ts(e)},Cap,,0,0,0,,{txt}")
    hdr = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n[V4+ Styles]\n"
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
           "Style: Cap,Inter,92,&H00FFFFFF,&H00FFFFFF,&H64000000,&H78000000,-1,0,0,0,100,100,-1,0,1,1.2,3,2,60,60,395,1\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    out.write_text(hdr + "\n".join(ev) + "\n")
    return out


def _fallback_clips(proj, path, segs, n, max_s, avoid):
    """Without an LLM: longest single-speaker stretches (guests first), sentence-aligned."""
    P = len(proj.people); a0, a1 = proj.limit or (0, segs[-1]["end"] if segs else 0)
    trn = [t for t in speakers.turns(path, P, a0, a1, 3.0) if 30 <= t[1] - t[0]]
    trn.sort(key=lambda t: (proj.people[t[2]].role == "host", -(t[1] - t[0])))
    out = []
    for t0, t1, sp in trn:
        t1 = min(t1, t0 + max_s)
        if any(not (t1 <= x or t0 >= y) for x, y in avoid + [(c["start"], c["end"]) for c in out]):
            continue
        first = next((s["text"] for s in segs if s["start"] >= t0 - 0.5), "")
        out.append({"headline": " ".join(first.split()[:6]), "start": t0, "end": t1, "why": "longest uninterrupted answer"})
        if len(out) >= n:
            break
    return out


def snap_clip(c, words, max_s):
    ws = [w for w in words if w[0] >= c["start"] - 0.4 and w[1] <= c["end"] + 0.4]
    if not ws:
        return None
    a, b = ws[0][0], ws[-1][1]
    while b - a > max_s and len(ws) > 3:
        ws = ws[:-1]; b = ws[-1][1]
    return a, b


def build(proj: Project, sync, fc, path, segs, gains, final_audio: Path, removals, log=print) -> list[dict]:
    out_dir = proj.out_dir / "shorts"; out_dir.mkdir(exist_ok=True)
    meta_f = proj.work_dir / "shorts.json"
    words = [(w[0], w[1], w[2]) for s in segs for w in s["words"]]
    if meta_f.exists():
        clips = json.loads(meta_f.read_text())
    else:
        lines = llm.transcript_lines(proj, segs, path)
        clips = llm.pick_shorts(proj, lines, proj.outputs.shorts, proj.outputs.shorts_max_seconds, removals) \
            or _fallback_clips(proj, path, segs, proj.outputs.shorts, proj.outputs.shorts_max_seconds, removals)
        meta_f.write_text(json.dumps(clips, indent=1))
    P = len(proj.people); results = []
    for n, c in enumerate(clips, 1):
        sn = snap_clip(c, words, proj.outputs.shorts_max_seconds)
        if not sn:
            continue
        a, b = sn; a = round((a - 0.1) * FPS) / FPS; b = round((b + 0.3) * FPS) / FPS
        tmp = proj.work_dir / f"short{n:02d}"; tmp.mkdir(exist_ok=True)
        edl = []
        for t0, t1, sp in speakers.turns(path, P, a, b, 1.5):
            pid = proj.people[sp].id
            cam = framing.best_camera_for(proj, pid, t0, t1, sync) or proj.master.id
            edl.append({"k0": int(round(t0 * FPS)), "k1": int(round(t1 * FPS)), "shot": f"{cam}:vertical:{pid}",
                        "crop": framing.vertical(proj, fc, cam, pid, t0, t1)})
        for i in range(1, len(edl)):
            edl[i]["k0"] = edl[i - 1]["k1"]
        files = render.render_segments(proj, sync, gains, edl, tmp / "seg", 1080, 1920, "eq=contrast=1.05:saturation=0.95,")
        v = render.concat(files, tmp / "v.mp4")
        nf = sum(e["k1"] - e["k0"] for e in edl)
        aw = tmp / "a.wav"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(final_audio), "-af",
             f"atrim=start_sample={edl[0]['k0']*SR//FPS}:end_sample={edl[-1]['k1']*SR//FPS},asetpts=PTS-STARTPTS,"
             f"afade=t=in:d=0.03,afade=t=out:st={nf/FPS-0.06:.3f}:d=0.06,loudnorm=I=-14:TP=-1:LRA=9,aresample={SR},apad,atrim=end={nf/FPS:.4f}",
             "-ac", "2", "-c:a", "pcm_s16le", str(aw)])
        cw = _merge([[x - edl[0]["k0"] / FPS, y - edl[0]["k0"] / FPS, w] for x, y, w in words if a <= (x + y) / 2 < b])
        caption_ass(cw, tmp / "caps.ass"); headline_png(c["headline"], tmp / "head.png")
        slug = re.sub(r"[^A-Za-z0-9]+", "_", c["headline"]).strip("_")[:40]
        f = out_dir / f"Short{n:02d}_{slug}.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(v), "-i", str(aw), "-i", str(tmp / "head.png"), "-filter_complex",
             f"[0:v][2:v]overlay=(W-w)/2:1075,ass={tmp/'caps.ass'}:fontsdir={FONT_DIR}[v]", "-map", "[v]", "-map", "1:a",
             "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", str(f)])
        text = " ".join(w[2] for w in cw)
        results.append({"n": n, "file": str(f), "headline": titlecase(c["headline"]), "start": a, "end": b, "seconds": round(nf / FPS, 1), "text": text})
        log(f"short {n}: {nf/FPS:.0f}s  {c['headline']}")
    return results
