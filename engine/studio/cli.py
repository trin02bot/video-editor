"""Command line:  studio run|preview|inspect|find|redo  project.yaml"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, pipeline


def main(argv=None):
    ap = argparse.ArgumentParser(prog="studio", description="AI multicam podcast editor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the whole pipeline (cached stages are skipped)")
    r.add_argument("project"); r.add_argument("--only", help="comma-separated deliverables: episode,parts,transcript,trailer,shorts,post_captions")
    p = sub.add_parser("preview", help="process only a time window of the master recording (fast check of sync, framing, style)")
    p.add_argument("project"); p.add_argument("--start", type=float, required=True); p.add_argument("--end", type=float, required=True)
    p.add_argument("--only", default="episode,trailer")
    i = sub.add_parser("inspect", help="grab a frame per camera with detected faces numbered left to right (to fill in 'order'/'frontal')")
    i.add_argument("project"); i.add_argument("--at", type=float, default=None)
    f = sub.add_parser("find", help="find where a phrase is said (to set up a removal)")
    f.add_argument("project"); f.add_argument("phrase")
    d = sub.add_parser("redo", help="forget cached results from a stage onward (e.g. 'edit' after changing angles or removals)")
    d.add_argument("project"); d.add_argument("stage", choices=pipeline.STAGES)
    a = ap.parse_args(argv)
    proj = config.load(a.project)
    if a.cmd == "run":
        out = pipeline.run(proj, a.only.split(",") if a.only else None)
    elif a.cmd == "preview":
        proj.limit = (a.start, a.end)
        proj.work_dir = proj.work_dir / f"preview_{int(a.start)}_{int(a.end)}"; proj.work_dir.mkdir(parents=True, exist_ok=True)
        proj.out_dir = proj.out_dir / f"preview_{int(a.start)}_{int(a.end)}"; proj.out_dir.mkdir(parents=True, exist_ok=True)
        proj.name += "_preview"; proj.outputs.parts_max_seconds = 0
        out = pipeline.run(proj, a.only.split(","))
    elif a.cmd == "inspect":
        out = inspect(proj, a.at)
    elif a.cmd == "find":
        out = find(proj, a.phrase)
    else:
        pipeline.invalidate(proj, a.stage); out = {"invalidated_from": a.stage}
    print(json.dumps(out, indent=1))


def inspect(proj, at):
    """Annotated frame per camera: faces numbered left->right so the operator can map them to people."""
    import cv2
    import numpy as np
    from . import faces, media
    out = {}
    for cam in proj.cameras:
        f = cam.files[0]; t = at if at is not None else media.probe(f)["duration"] * 0.3
        rows = faces._detect_file(f, t, 0.15, 1280)
        img = proj.out_dir / f"inspect_{cam.id}.jpg"; media.frame_grab(f, t, img, 1280)
        im = cv2.imread(str(img)); H, W = im.shape[:2]
        rows = rows[np.argsort(rows[:, 1])] if len(rows) else rows
        for k, r in enumerate(rows):
            x0, y0 = int((r[1] - r[3] / 2) * W), int((r[2] - r[4] / 2) * H)
            cv2.rectangle(im, (x0, y0), (x0 + int(r[3] * W), y0 + int(r[4] * H)), (0, 255, 255), 2)
            cv2.putText(im, str(k + 1), (x0, max(30, y0 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 3)
        cv2.imwrite(str(img), im); out[cam.id] = {"image": str(img), "faces_left_to_right": len(rows)}
    return out


def find(proj, phrase):
    import re
    from . import sync as S, transcribe
    S.run(proj); segs = transcribe.run(proj)
    q = re.sub(r"[^a-z0-9' ]", "", phrase.lower())
    return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in segs if q in re.sub(r"[^a-z0-9' ]", "", s["text"].lower())]


if __name__ == "__main__":
    sys.exit(main())
