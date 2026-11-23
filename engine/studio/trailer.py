"""Cinematic cold-open trailer: 8-9 lines in a tension -> advice -> emotion arc, animated keyword
typography (bold sans + elegant italic serif + one accent word), slow push-ins, branded title card,
and an original generated score ducked under the voices."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from PIL import ImageFont

from . import framing, llm, media, multicam, render, score
from .config import Project
from .media import FPS, SR, run
from .sync import file_at

FD = Path(__file__).parent / "fonts"
SANS, SANS2, SERIF = str(FD / "Inter-Black.otf"), str(FD / "Inter-ExtraBold.otf"), str(FD / "Caladea-BoldItalic.ttf")
SEMI, MED = str(FD / "Inter-SemiBold.otf"), str(FD / "Inter-Medium.otf")


def _fit(txt, font, size, maxw):
    while size > 30 and ImageFont.truetype(font, size).getlength(txt) > maxw:
        size -= 4
    return size


def _esc(s): return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")
def _hex(c): return "0x" + c.lstrip("#")


def _norm(w): return re.sub(r"[^a-z0-9']", "", w.lower())


def _fallback_bites(proj, segs, removals):
    """Without an LLM: short complete sentences spread through the episode."""
    cand = []
    for s in segs:
        d = s["end"] - s["start"]
        if 2.8 <= d <= 5.5 and len(s["text"].split()) >= 6 and not any(a <= s["start"] <= b for a, b in removals):
            cand.append(s)
    if not cand:
        return []
    pick = [cand[int(i)] for i in np.linspace(0, len(cand) - 1, min(9, len(cand)))]
    out = []
    for s in pick:
        ws = s["text"].split(); big = max(ws, key=len).strip(".,?!").upper()
        out.append({"start": s["start"], "end": s["end"], "lead": " ".join(ws[:3]).upper(), "serif": "", "big": big})
    return out


def _snap(b, words, db100):
    ws = [w for w in words if w[0] >= b["start"] - 0.35 and w[1] <= b["end"] + 0.35]
    if not ws:
        return None
    a, e = ws[0][0], ws[-1][1]
    i0, i1 = int((a - 0.3) * 100), int(a * 100)
    if i1 > i0:
        a = (i0 + int(np.argmin(db100[i0:i1]))) / 100
    j0, j1 = int(e * 100), int((e + 0.4) * 100)
    if j1 > j0:
        e = (j0 + int(np.argmin(db100[j0:j1]))) / 100
    return int(np.floor(a * FPS)), int(np.ceil(e * FPS)), ws


def build(proj: Project, sync, fc, path, segs, gains, final_audio: Path, removals, log=print) -> Path | None:
    tmp = proj.work_dir / "trailer"; tmp.mkdir(exist_ok=True)
    meta = tmp / "bites.json"
    if meta.exists():
        bites = json.loads(meta.read_text())
    else:
        bites = llm.pick_trailer(proj, llm.transcript_lines(proj, segs, path), removals) or _fallback_bites(proj, segs, removals)
        meta.write_text(json.dumps(bites, indent=1))
    if not bites:
        log("trailer: no usable lines"); return None
    words = [(w[0], w[1], w[2]) for s in segs for w in s["words"]]
    _, x16 = media.read_wav(proj.work_dir / "audio" / "master16.wav")
    n = len(x16) // 160; db100 = 10 * np.log10((x16[:n * 160].reshape(n, 160) ** 2).mean(1) + 1e-12)
    P = len(proj.people); accent, alert = _hex(proj.brand.accent), _hex(proj.brand.alert)
    clips, starts, prev_shot, t_acc = [], [], None, 0.0
    for i, b in enumerate(bites):
        sn = _snap(b, words, db100)
        if not sn:
            continue
        k0, k1, ws = sn; t0, t1 = k0 / FPS, k1 / FPS
        lab = path[int(t0 * 10):int(t1 * 10)]; lab = lab[lab < P]
        pid = proj.people[int(np.bincount(lab).argmax())].id if len(lab) else proj.people[0].id
        opts = multicam._shots_for(proj, pid)
        shot, crop = None, None
        for s in opts["primary"] + opts["alts"] + [a for a in proj.edit.angles if a.endswith(":wide")]:
            if s == prev_shot or not file_at(sync, s.split(":")[0], t0, t1):
                continue
            crop = framing.crop(proj, fc, s, t0, t1)
            if crop:
                shot = s; break
        if not shot:
            continue
        prev_shot = shot
        # text placement: column opposite the face for singles, lower third otherwise
        _, kind, ppl = framing.parse(shot)
        mode = "bottom"
        if kind == "single":
            m, _ = framing.F.median_pos(fc, shot.split(":")[0], ppl[0], t0, t1)
            W = media.probe(proj.cam(shot.split(":")[0]).files[0])["width"]
            fxc = (m[0] * W - crop[0]) / crop[2] if m is not None else 0.6
            mode = "left" if fxc >= 0.5 else "right"

        def when(text, frac):
            q = [_norm(t) for t in text.split()][:1]
            for a, _, w in ws:
                if q and _norm(w) == q[0]:
                    return max(0.0, a - t0 - 0.05)
            return frac * (t1 - t0)
        items = []
        lead, serif, big = b.get("lead", "").strip(), b.get("serif", "").strip(), b.get("big", "").strip().upper()
        col = mode in ("left", "right"); maxw = 780 if col else 1700
        y = 300 if col else (700 if serif else 760)
        if lead:
            s1 = _fit(lead.upper(), SANS2, 58, maxw); items.append((lead.upper(), SANS2, s1, "white", y, when(lead, 0.05))); y += int(s1 * 1.15)
        if col:
            if big:
                s3 = _fit(big, SANS, 170, maxw); items.append((big, SANS, s3, alert if big in ("WRONG", "NO", "NEVER") else accent, y, when(big, 0.6))); y += int(s3 * 1.1)
            if serif:
                s2 = _fit(serif, SERIF, 68, maxw); items.append((serif, SERIF, s2, "white", y + 10, when(serif, 0.4)))
        else:
            if serif:
                s2 = _fit(serif, SERIF, 66, maxw); items.append((serif, SERIF, s2, "white", y, when(serif, 0.4))); y += int(s2 * 1.0)
            if big:
                s3 = _fit(big, SANS, 165, maxw); items.append((big, SANS, s3, accent, y, when(big, 0.6)))
        cam = shot.split(":")[0]
        inp, pre = render.source_args(proj, sync, cam, k0, k1)
        g = gains.get(cam, [1, 1, 1])
        colf = "" if g == [1.0, 1.0, 1.0] else f"format=gbrp,colorchannelmixer=rr={g[0]:.3f}:gg={g[1]:.3f}:bb={g[2]:.3f},"
        cx, cy, cw, ch = crop
        nfr = k1 - k0
        f = (f"{pre}crop={cw}:{ch}:{cx}:{cy},{colf}scale=3840:2160:flags=lanczos,"
             f"zoompan=z='1+0.07*on/{nfr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30,"
             f"eq=contrast=1.07:brightness=-0.025:saturation=0.88")
        dt = []
        for (txt, font, size, colr, yy, ta) in items:
            xe = {"left": "110", "right": "w-tw-110"}.get(mode, "(w-tw)/2")
            al = f"if(lt(t\\,{ta:.2f})\\,0\\,min(1\\,(t-{ta:.2f})/0.14))"
            dt.append(f"drawtext=fontfile={font}:text='{_esc(txt)}':fontsize={size}:fontcolor={colr}:x={xe}:y={yy}:alpha='{al}':shadowcolor=black@0.55:shadowx=0:shadowy=4")
        if not clips:
            dt.append("fade=t=in:st=0:d=0.45")
        grad = tmp / f"grad_{mode}.png"; _gradient(grad, mode)
        out = tmp / f"b{i:02d}.mp4"
        run(["ffmpeg", "-v", "error", "-y"] + inp + ["-filter_complex",
             f"[0:v]{f}[b0];movie={grad}[g];[b0][g]overlay=0:0:format=auto,{','.join(dt + ['format=yuv420p'])}[v]",
             "-map", "[v]", "-frames:v", str(nfr)] + render.ENC + [str(out)])
        clips.append((out, k0, k1)); starts.append(t_acc); t_acc += nfr / FPS
    card = title_card(proj, fc, sync, tmp)
    render.concat([c for c, _, _ in clips] + [card], tmp / "video.mp4")
    # voice
    fcx = "".join(f"[0:a]atrim=start_sample={a*SR//FPS}:end_sample={b*SR//FPS},asetpts=PTS-STARTPTS,afade=t=in:d=0.03,"
                  f"afade=t=out:st={(b-a)/FPS-0.05:.3f}:d=0.05[a{i}];" for i, (_, a, b) in enumerate(clips))
    fcx += "".join(f"[a{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=0:a=1[aout]"
    voice = tmp / "voice.wav"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(final_audio), "-filter_complex", fcx, "-map", "[aout]", "-ac", "1", "-c:a", "pcm_s16le", str(voice)])
    total = t_acc + 3.0
    mix = tmp / "mix.wav"
    if proj.outputs.trailer_music:
        music = Path(score.trailer_score(starts + [t_acc], tmp / "music.wav", title_seconds=3.0))
        lift = starts[min(len(starts) - 1, max(1, round(len(starts) * 0.78)))]
        run(["ffmpeg", "-v", "error", "-y", "-i", str(voice), "-i", str(music), "-filter_complex",
             f"[0:a]aformat=channel_layouts=stereo,apad=whole_dur={total:.3f},asplit[v1][key];"
             f"[1:a]volume='if(between(t,{lift-0.2:.2f},{t_acc-0.05:.2f}),0.7,if(gte(t,{t_acc-0.05:.2f}),0.75,1))':eval=frame[m];"
             f"[m][key]sidechaincompress=threshold=0.03:ratio=3:attack=90:release=350:makeup=1[md];"
             f"[v1][md]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-16:TP=-1.5:LRA=11[a]",
             "-map", "[a]", "-ar", str(SR), "-c:a", "pcm_s16le", str(mix)])
    else:
        run(["ffmpeg", "-v", "error", "-y", "-i", str(voice), "-af", f"apad=whole_dur={total:.3f},aformat=channel_layouts=stereo",
             "-c:a", "pcm_s16le", str(mix)])
    out = proj.out_dir / f"{proj.name}_TRAILER.mov"
    render.mux(tmp / "video.mp4", mix, out, aac=False)
    log(f"trailer: {len(clips)} lines, {total:.1f}s -> {out.name}")
    return out


def _gradient(path: Path, mode: str):
    if path.exists():
        return
    import cv2
    g = np.zeros((1080, 1920, 4), np.uint8)
    if mode == "bottom":
        a = np.clip((np.arange(1080) - 560) / 520, 0, 1) ** 1.3 * 200; g[:, :, 3] = a[:, None].astype(np.uint8)
    else:
        a = np.clip((1150 - np.arange(1920)) / 1150, 0, 1) ** 1.4 * 170
        if mode == "right":
            a = a[::-1]
        g[:, :, 3] = a[None, :].astype(np.uint8)
    cv2.imwrite(str(path), g)


def title_card(proj: Project, fc, sync, tmp: Path) -> Path:
    b = proj.brand; acc = _hex(b.accent)
    wide = next((a for a in proj.edit.angles if a.endswith(":wide")), f"{proj.master.id}:wide")
    t = (proj.limit[0] + proj.limit[1]) / 2 if proj.limit else sync["master"]["duration"] * 0.5
    fa = file_at(sync, wide.split(":")[0], t, t + 1) or (proj.master.files[0], t)
    bg = media.frame_grab(fa[0], fa[1], tmp / "card_bg.png", 1920)
    main = b.show_name.upper()
    sub = b.show_subtitle.upper()
    lines = [f"drawtext=fontfile={SANS}:text='{_esc(main)}':fontsize={_fit(main, SANS, 128, 1700)}:fontcolor=white:x=(w-tw)/2:y=330"]
    if sub:
        spaced = "   ".join(" ".join(word) for word in sub.split())      # "O F   P R O   A T H L E T E S"
        lines.append(f"drawtext=fontfile={SEMI}:text='{_esc(spaced)}':fontsize={_fit(spaced, SEMI, 40, 1600)}:fontcolor={acc}:x=(w-tw)/2:y=480")
    lines.append("drawbox=x=(iw-240)/2:y=560:w=240:h=4:color=white@0.8:t=fill")
    if b.episode_label:
        lines.append(f"drawtext=fontfile={SEMI}:text='{_esc(b.episode_label.upper())}':fontsize=44:fontcolor=white@0.9:x=(w-tw)/2:y=610")
    if b.guest_line:
        lines.append(f"drawtext=fontfile={SANS}:text='{_esc(b.guest_line.upper())}':fontsize={_fit(b.guest_line.upper(), SANS, 76, 1600)}:fontcolor=white:x=(w-tw)/2:y=675")
    if b.guest_subline:
        lines.append(f"drawtext=fontfile={MED}:text='{_esc(b.guest_subline)}':fontsize=40:fontcolor=white@0.85:x=(w-tw)/2:y=775")
    out = tmp / "card.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(bg), "-filter_complex",
         "[0:v]scale=1920:1080,gblur=sigma=28,eq=brightness=-0.22:saturation=0.75,drawbox=x=0:y=0:w=iw:h=ih:color=black@0.35:t=fill,"
         + ",".join(lines) + ",fade=t=in:st=0:d=0.35,fade=t=out:st=2.4:d=0.6,format=yuv420p[v]",
         "-map", "[v]", "-frames:v", "90", "-r", str(FPS)] + render.ENC + [str(out)])
    return out
