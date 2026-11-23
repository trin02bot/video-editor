"""End-to-end pipeline. Each stage caches its output in work_dir, so re-runs (e.g. after adding a
removal or changing allowed angles) only redo what changed. Progress is written to status.json."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import audio, deliver, faces, llm, multicam, render, shorts, speakers, sync, trailer, transcribe
from .config import Project
from .media import FPS

STAGES = ["sync", "transcribe", "faces", "speakers", "edit", "color", "audio", "episode", "parts",
          "transcript", "trailer", "shorts", "post_captions"]


class Status:
    def __init__(self, proj: Project):
        self.f = proj.work_dir / "status.json"; self.d = {"stage": None, "progress": 0.0, "log": [], "outputs": {}, "started": time.time()}

    def stage(self, name):
        self.d["stage"] = name; self.d["progress"] = STAGES.index(name) / len(STAGES); self.save()

    def log(self, msg):
        print(msg, flush=True); self.d["log"] = (self.d["log"] + [msg])[-200:]; self.save()

    def output(self, key, val):
        self.d["outputs"][key] = val; self.save()

    def save(self):
        self.f.write_text(json.dumps(self.d, indent=1, default=str))


def invalidate(proj: Project, stage: str) -> None:
    """Drop cached results from `stage` onward."""
    names = {"edit": ["edl.json"], "audio": ["audio/master_final.wav", "audio/master_leveled_pre.wav"],
             "episode": ["episode"], "trailer": ["trailer"], "shorts": ["shorts.json"] + [f"short{i:02d}" for i in range(1, 51)],
             "speakers": ["speakers.npy"], "faces": [f"faces_{c.id}.npz" for c in proj.cameras],
             "transcribe": ["transcript.json"], "sync": ["sync.json"], "color": ["color.json"]}
    for st in STAGES[STAGES.index(stage):]:
        for n in names.get(st, []):
            p = proj.work_dir / n
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()


def run(proj: Project, only: list[str] | None = None) -> dict:
    S = Status(proj); want = lambda s: only is None or s in only
    S.stage("sync"); sy = sync.run(proj, S.log)
    S.stage("transcribe"); segs = transcribe.run(proj, S.log)
    S.stage("faces"); fc = faces.run(proj, sy, S.log)
    S.stage("speakers"); path = speakers.run(proj, fc, segs, sy, S.log)
    S.stage("edit")
    removals = multicam.removal_ranges(proj, segs)
    for a, b in removals:
        S.log(f"edit: removing {a:.2f}-{b:.2f}s ({b-a:.1f}s)")
    edl = multicam.build(proj, fc, path, segs, sy, S.log)
    edl_tl, keep = multicam.apply_removals(proj, fc, edl, removals, sy)
    (proj.work_dir / "edl_timeline.json").write_text(json.dumps({"edl": edl_tl, "keep": keep}))
    S.stage("color"); gains = render.color_match(proj, sy, S.log)
    S.stage("audio"); final_master = audio.level(proj, path, segs, S.log)
    ep_dir = proj.work_dir / "episode"; ep_dir.mkdir(exist_ok=True)
    tl_audio = audio.timeline(final_master, keep, ep_dir / "audio.wav")
    out = {}
    if want("episode") and proj.outputs.full_episode:
        S.stage("episode")
        files = render.render_segments(proj, sy, gains, edl_tl, ep_dir / "seg",
                                       progress=lambda p: (S.d.update(progress=(STAGES.index("episode") + p) / len(STAGES)), S.save()))
        video = render.concat(files, ep_dir / "video.mp4")
        f = render.mux(video, tl_audio, proj.out_dir / f"{proj.name}.mp4", title=f"{proj.brand.show_name} {proj.brand.episode_label}".strip())
        out["episode"] = str(f); S.output("episode", str(f))
        S.log(f"episode: {sum(s['k1']-s['k0'] for s in edl_tl)/FPS/60:.1f} min -> {f.name}")
        if want("parts") and proj.outputs.parts_max_seconds:
            S.stage("parts")
            pdir = proj.out_dir / "parts"; pdir.mkdir(exist_ok=True)
            out["parts"] = [str(p) for p in render.parts(proj, edl_tl, video, tl_audio, pdir, proj.outputs.parts_max_seconds)]
            S.output("parts", out["parts"])
    if want("transcript") and (proj.outputs.transcript or proj.outputs.srt):
        S.stage("transcript")
        tw = deliver.timeline_words(segs, keep, path, len(proj.people))
        if proj.outputs.transcript:
            out["transcript"] = str(deliver.transcript(proj, tw, proj.out_dir / f"{proj.name}_Transcript.txt"))
        if proj.outputs.srt:
            out["srt"] = str(deliver.srt(tw, proj.out_dir / f"{proj.name}_Captions.srt"))
        S.output("transcript", out.get("transcript"))
    if want("trailer") and proj.outputs.trailer:
        S.stage("trailer")
        t = trailer.build(proj, sy, fc, path, segs, gains, final_master, removals, S.log)
        if t:
            out["trailer"] = str(t); S.output("trailer", str(t))
    clips = []
    if want("shorts") and proj.outputs.shorts:
        S.stage("shorts")
        clips = shorts.build(proj, sy, fc, path, segs, gains, final_master, removals, S.log)
        out["shorts"] = [c["file"] for c in clips]; S.output("shorts", out["shorts"])
    if want("post_captions") and proj.outputs.post_captions and clips:
        S.stage("post_captions")
        caps = llm.post_captions(proj, clips) or [f"{c['headline']}\n\nWatch the full conversation: [link]" for c in clips]
        f = proj.out_dir / "shorts" / "Post_Captions.txt"
        f.write_text("\n\n".join(f"--- SHORT {c['n']}: {c['headline']} ---\n{cap}" for c, cap in zip(clips, caps)))
        out["post_captions"] = str(f); S.output("post_captions", str(f))
    S.d.update(stage="done", progress=1.0); S.save()
    (proj.out_dir / "manifest.json").write_text(json.dumps(out, indent=1))
    return out
