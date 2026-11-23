"""Who is speaking when (10 Hz on the master timeline).

Fuses three signals:
  * lip activity from the face tracker (on a camera where that person faces the lens),
  * a voice classifier (MFCC + pitch) trained on moments where lips clearly identify the speaker,
  * the transcript (a word being spoken = speech),
then smooths with a Viterbi pass that penalizes rapid speaker switching.
Output: labels array, values 0..P-1 = index into proj.people, P = silence.
"""
from __future__ import annotations

import json

import numpy as np
from scipy.ndimage import uniform_filter1d

from . import faces as F
from . import media
from .config import Project

R = F.RATE


def run(proj: Project, fc: dict, segs: list[dict], sync: dict, log=print) -> np.ndarray:
    out = proj.work_dir / "speakers.npy"
    if out.exists():
        return np.load(out)
    import librosa
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    N = int(sync["master"]["duration"] * R) + 1
    P = len(proj.people)
    # ---- lip activity per person (best camera)
    V = np.full((N, P), np.nan)
    for i, p in enumerate(proj.people):
        acts = [F.activity(fc[c.id][f"mouth_{p.id}"]) for c in proj.cameras if p.id in c.frontal and f"mouth_{p.id}" in fc[c.id]]
        if acts:
            V[:, i] = np.nanmax(np.vstack(acts), 0) if len(acts) > 1 else acts[0]
    # ---- speech mask from transcript words
    speech = np.zeros(N, bool)
    for s in segs:
        for a, b, _ in s["words"]:
            speech[int(a * R):min(int(np.ceil(b * R)), N)] = True
    # ---- voice features
    _, x = media.read_wav(proj.work_dir / "audio" / "master16.wav")
    hop = 160
    mf = librosa.feature.mfcc(y=x, sr=16000, n_mfcc=20, n_fft=512, hop_length=hop, n_mels=40)
    f0 = librosa.yin(x, fmin=70, fmax=400, sr=16000, frame_length=1024, hop_length=hop)
    rms = librosa.feature.rms(y=x, frame_length=512, hop_length=hop)[0]

    def pool(a):
        a = a[..., :N * 10] if a.shape[-1] >= N * 10 else np.pad(a, [(0, 0)] * (a.ndim - 1) + [(0, N * 10 - a.shape[-1])], mode="edge")
        return a.reshape(*a.shape[:-1], N, 10).mean(-1)
    M = pool(mf); E = pool(rms[None])[0]
    Fv = (rms > np.percentile(rms, 40)).astype(float); Fl = np.log(np.clip(f0, 70, 400))
    Fp = pool((Fl * Fv)[None])[0] / np.maximum(pool(Fv[None])[0], 1e-3)
    ctx = lambda a: uniform_filter1d(a, 10, axis=-1)
    X = np.vstack([ctx(M), ctx(M ** 2) - ctx(M) ** 2, ctx(Fp)[None], np.log(ctx(E) + 1e-5)[None]]).T
    # ---- seeds: lips clearly say who talks
    Vz = np.nan_to_num(V, nan=-1)
    best = np.argmax(Vz, 1); sv = np.sort(Vz, 1)
    seed = (sv[:, -1] > 0.9) & ((sv[:, -1] - sv[:, -2]) > 0.6 if P > 1 else True) & speech
    lab = np.where(seed, best, -1)
    counts = [int((lab == i).sum()) for i in range(P)]
    log(f"speakers: seed frames per person {dict(zip([p.id for p in proj.people], counts))}")
    usable = [i for i in range(P) if counts[i] >= 30]
    pa = np.full((N, P), 1.0 / P)
    if len(usable) >= 2:
        sc = StandardScaler().fit(X[speech]); clf = LogisticRegression(max_iter=2000, C=0.3, class_weight="balanced")
        m = np.isin(lab, usable)
        clf.fit(sc.transform(X[m]), lab[m])
        pr = clf.predict_proba(sc.transform(X))
        pa = np.full((N, P), 1e-3)
        for j, c in enumerate(clf.classes_):
            pa[:, c] = pr[:, j]
    # ---- fuse + viterbi
    vis = np.where(np.isnan(V) | (V < -0.5), 0, 1.2 * np.clip(V, -1, 3))
    K = P + 1
    L = np.zeros((N, K)); L[:, :P] = np.log(pa + 1e-4) + vis
    L[:, P] = np.where(speech, -6, 2.0); L[~speech, :P] -= 2.5
    sw = 8.0
    C = sw * (1 - np.eye(K)); C[P, :P] = C[:P, P] = sw * 0.25
    dp = L[0].copy(); bp = np.zeros((N, K), np.int16)
    for t in range(1, N):
        s = dp[:, None] - C
        bp[t] = np.argmax(s, 0); dp = s[bp[t], np.arange(K)] + L[t]
    path = np.zeros(N, np.int16); path[-1] = int(np.argmax(dp))
    for t in range(N - 1, 0, -1):
        path[t - 1] = bp[t, path[t]]
    np.save(out, path)
    tot = {proj.people[i].id: round(float((path == i).sum()) / R / 60, 1) for i in range(P)}
    log(f"speakers: minutes per person {tot}")
    return path


def turns(path: np.ndarray, P: int, t0: float, t1: float, min_len: float = 1.5) -> list[tuple[float, float, int]]:
    """Smoothed speaker turns inside [t0,t1): silence joins the previous speaker, short blips are absorbed."""
    i0, i1 = int(t0 * R), int(t1 * R); p = path[i0:i1].astype(int).copy()
    if not len(p):
        return []
    last = next((v for v in p if v != P), 0)
    for i, v in enumerate(p):
        if v == P:
            p[i] = last
        else:
            last = v
    runs = []; s = 0
    for i in range(1, len(p) + 1):
        if i == len(p) or p[i] != p[s]:
            runs.append([s, i, int(p[s])]); s = i
    mn = int(min_len * R); changed = True
    while changed and len(runs) > 1:
        changed = False
        for k, (a, b, v) in enumerate(runs):
            if b - a < mn:
                if k == 0:
                    runs[1][0] = a
                elif k == len(runs) - 1:
                    runs[k - 1][1] = b
                elif runs[k - 1][1] - runs[k - 1][0] >= runs[k + 1][1] - runs[k + 1][0]:
                    runs[k - 1][1] = b
                else:
                    runs[k + 1][0] = a
                runs.pop(k); changed = True; break
        merged = []
        for r in runs:
            if merged and merged[-1][2] == r[2]:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        runs = merged
    return [(t0 + a / R, t0 + b / R, v) for a, b, v in runs]
