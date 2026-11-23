"""Face tracking per camera: who is where in frame, and how much each mouth moves.

Detection: MediaPipe BlazeFace full-range (works on small, distant faces).
Landmarks: MediaPipe FaceLandmarker run on an enlarged crop around each face
(the landmarker alone only finds near-camera faces).
Identity: detections are matched to the camera's left-to-right person order.

Output per camera (npz, 10 samples/sec on the master timeline):
  pos_<pid>   (N,4) normalized cx, cy, w, h   (nan when not seen)
  mouth_<pid> (N,)  inner-lip gap / face height (nan when no landmarks)
"""
from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path

import numpy as np

from . import transcribe
from .config import Camera, Project

RATE = 10
URLS = {
    "blaze_full.tflite": "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_full_range/float16/latest/blaze_face_full_range.tflite",
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}


def _models() -> dict[str, str]:
    d = transcribe.MODELS / "mediapipe"; d.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, url in URLS.items():
        p = d / name
        if not p.exists():
            urllib.request.urlretrieve(url, p)
        out[name] = str(p)
    return out


def _detect_file(path: str, t_start: float, t_len: float | None, analysis_w: int) -> np.ndarray:
    """rows: [file_time, cx, cy, w, h, mouth, score] for every detected face."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision
    from . import media
    m = _models()
    det = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
        base_options=mpp.BaseOptions(model_asset_path=m["blaze_full.tflite"]), min_detection_confidence=0.45))
    lmk = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mpp.BaseOptions(model_asset_path=m["face_landmarker.task"]), num_faces=1,
        min_face_detection_confidence=0.3, min_face_presence_confidence=0.3))
    info = media.probe(path)
    W = analysis_w; H = int(round(W * info["height"] / info["width"] / 2)) * 2
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{t_start:.3f}"] + (["-t", f"{t_len:.3f}"] if t_len else []) + \
          ["-i", path, "-vf", f"fps={RATE},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=W * H * 3 * 4)
    rows = []; i = 0; C = 192
    while True:
        buf = p.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        for d in det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=fr)).detections:
            bb = d.bounding_box; cx = bb.origin_x + bb.width / 2; cy = bb.origin_y + bb.height / 2; s = bb.width * 1.1
            x0, y0, x1, y1 = int(cx - s), int(cy - s), int(cx + s), int(cy + s)
            pad = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
            sx0, sy0, sx1, sy1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
            if sx1 <= sx0 or sy1 <= sy0:
                continue
            pad[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = fr[sy0:sy1, sx0:sx1]
            q = lmk.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(cv2.resize(pad, (C, C)))))
            mouth = np.nan
            if q.face_landmarks:
                P = np.array([[t.x, t.y] for t in q.face_landmarks[0]])
                mouth = np.linalg.norm(P[13] - P[14]) / max(P[:, 1].max() - P[:, 1].min(), 1e-6)
            rows.append([t_start + i / RATE, cx / W, cy / H, bb.width / W, bb.height / H, mouth, d.categories[0].score])
        i += 1
    p.wait()
    return np.array(rows, np.float32).reshape(-1, 7)


def _assign(rows: np.ndarray, cam: Camera, N: int, to_master) -> dict[str, np.ndarray]:
    """Match detections to people by left-to-right order, with position tracking for partial frames."""
    k = len(cam.order)
    pos = {p: np.full((N, 4), np.nan, np.float32) for p in cam.order}
    mouth = {p: np.full(N, np.nan, np.float32) for p in cam.order}
    if not len(rows):
        return {**{f"pos_{p}": v for p, v in pos.items()}, **{f"mouth_{p}": v for p, v in mouth.items()}}
    idx = np.round(to_master(rows[:, 0]) * RATE).astype(int)
    frames: dict[int, np.ndarray] = {}
    for j in np.unique(idx):
        if 0 <= j < N:
            f = rows[idx == j]
            f = f[f[:, 2] < 0.85]                       # ignore detections near the bottom (hands, table)
            frames[j] = f[np.argsort(-f[:, 6])][:k]
    # pass 1: frames with all k faces -> direct left-to-right assignment
    track = {p: np.full(N, np.nan) for p in cam.order}
    for j, f in frames.items():
        if len(f) == k:
            f = f[np.argsort(f[:, 1])]
            for p, r in zip(cam.order, f):
                track[p][j] = r[1]
    # smooth expected x per person (interpolate gaps)
    xs = np.arange(N)
    for p in cam.order:
        ok = ~np.isnan(track[p])
        track[p] = np.interp(xs, xs[ok], track[p][ok]) if ok.sum() >= 2 else np.full(N, (cam.order.index(p) + 0.5) / k)
    # pass 2: assign every detection to the nearest expected position (greedy, unique)
    for j, f in frames.items():
        used = set()
        for r in f[np.argsort(-f[:, 6])]:
            d = sorted(((abs(r[1] - track[p][j]), p) for p in cam.order if p not in used))
            if not d or d[0][0] > 0.12:
                continue
            p = d[0][1]; used.add(p)
            pos[p][j] = r[1:5]; mouth[p][j] = r[5]
    return {**{f"pos_{p}": v for p, v in pos.items()}, **{f"mouth_{p}": v for p, v in mouth.items()}}


def run(proj: Project, sync: dict, log=print) -> dict[str, dict]:
    N = int(sync["master"]["duration"] * RATE) + 1
    out = {}
    for cam in proj.cameras:
        f_npz = proj.work_dir / f"faces_{cam.id}.npz"
        if not f_npz.exists():
            allrows = []
            for f in cam.files:
                s = sync["files"][f]
                ts, tl = 0.0, None
                if proj.limit:                            # only analyse the part of this file inside the window
                    a = (proj.limit[0] - s["offset"]) / (1 + s["slope"]); b = (proj.limit[1] - s["offset"]) / (1 + s["slope"])
                    a, b = max(0.0, a), min(s["duration"], b)
                    if b <= a:
                        continue
                    ts, tl = a, b - a
                log(f"faces: camera {cam.id} {Path(f).name}")
                rows = _detect_file(f, ts, tl, 1280 if _is_4k(f) else 960)
                rows[:, 0] = rows[:, 0] * (1 + s["slope"]) + s["offset"]          # file time -> master time
                allrows.append(rows)
            rows = np.vstack(allrows) if allrows else np.zeros((0, 7), np.float32)
            np.savez(f_npz, **_assign(rows, cam, N, lambda t: t))   # rows already on master time
        out[cam.id] = dict(np.load(f_npz))
    return out


def _is_4k(path: str) -> bool:
    from . import media
    return media.probe(path)["width"] >= 2560


def activity(mouth: np.ndarray) -> np.ndarray:
    """Talking score: rolling std of mouth opening, robust-normalized per person (nan where unseen)."""
    from scipy.ndimage import uniform_filter1d
    N = len(mouth); T = np.arange(N); ok = ~np.isnan(mouth)
    if ok.sum() < 10:
        return np.full(N, np.nan)
    fill = np.interp(T, T[ok], mouth[ok])
    mu = uniform_filter1d(fill, 7); sd = np.sqrt(np.maximum(uniform_filter1d(fill ** 2, 7) - mu ** 2, 0))
    sd[uniform_filter1d(ok.astype(float), 7) < 0.4] = np.nan
    q = np.nanpercentile(sd, [20, 90])
    return (sd - q[0]) / (q[1] - q[0] + 1e-9)


def median_pos(faces: dict, cam: str, pid: str, t0: float, t1: float) -> tuple[np.ndarray | None, float]:
    """Median normalized (cx,cy,w,h) of a person on a camera over [t0,t1], widening the window if unseen."""
    P = faces[cam].get(f"pos_{pid}")
    if P is None:
        return None, 1.0
    i0, i1 = int(t0 * RATE), int(t1 * RATE)
    for pad in (0, 20, 60, 150, 400, 1200):
        seg = P[max(i0 - pad, 0):i1 + pad]; ok = ~np.isnan(seg[:, 0])
        if ok.sum() >= 5:
            q = seg[ok]
            return np.median(q, 0), (float(np.std(q[:, 0])) if pad == 0 else 0.0)
    return None, 1.0
