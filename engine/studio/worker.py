"""Queue worker: claims jobs from Supabase, runs the engine, streams progress, uploads deliverables.

Environment:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY   (service role: bypasses RLS - keep it on the worker only)
  ANTHROPIC_API_KEY                         (optional: AI editorial steps)
  STUDIO_DATA=/data                         (footage + per-project caches; keep it on a persistent volume)
Run:  python -m studio.worker
"""
from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time
import traceback
from pathlib import Path

import httpx
import yaml

from . import cli, config, pipeline

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
DATA = Path(os.environ.get("STUDIO_DATA", "/data"))
NAME = os.environ.get("STUDIO_WORKER_NAME", socket.gethostname())
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
http = httpx.Client(timeout=httpx.Timeout(60, read=600), headers=H)


# ------------------------------------------------------------------ PostgREST helpers
def rpc(fn, **args):
    r = http.post(f"{URL}/rest/v1/rpc/{fn}", json=args); r.raise_for_status(); return r.json()


def select(table, **eq):
    q = "&".join(f"{k}=eq.{v}" for k, v in eq.items())
    r = http.get(f"{URL}/rest/v1/{table}?select=*&{q}"); r.raise_for_status(); return r.json()


def update(table, row_id, **vals):
    r = http.patch(f"{URL}/rest/v1/{table}?id=eq.{row_id}", json=vals, headers={"Prefer": "return=minimal"}); r.raise_for_status()


def insert(table, **vals):
    r = http.post(f"{URL}/rest/v1/{table}", json=vals, headers={"Prefer": "return=minimal"}); r.raise_for_status()


# ------------------------------------------------------------------ storage helpers
def download(bucket: str, path: str, dst: Path) -> Path:
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True); tmp = dst.with_suffix(dst.suffix + ".part")
    with http.stream("GET", f"{URL}/storage/v1/object/authenticated/{bucket}/{path}") as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 22):
                f.write(chunk)
    tmp.rename(dst)
    return dst


def upload(bucket: str, path: str, src: Path, content_type: str) -> None:
    """TUS resumable upload (handles multi-GB files); 6 MB chunks as Supabase requires."""
    size = src.stat().st_size
    b64 = lambda s: base64.b64encode(s.encode()).decode()
    meta = f"bucketName {b64(bucket)},objectName {b64(path)},contentType {b64(content_type)},cacheControl {b64('3600')}"
    r = http.post(f"{URL}/storage/v1/upload/resumable", headers={"Tus-Resumable": "1.0.0", "Upload-Length": str(size),
                                                                "Upload-Metadata": meta, "x-upsert": "true"})
    r.raise_for_status(); loc = r.headers["Location"]
    off, CH = 0, 6 * 1024 * 1024
    with open(src, "rb") as f:
        while off < size:
            f.seek(off); buf = f.read(CH)
            for attempt in range(5):
                try:
                    rr = http.patch(loc, content=buf, headers={"Tus-Resumable": "1.0.0", "Upload-Offset": str(off),
                                                               "Content-Type": "application/offset+octet-stream"})
                    rr.raise_for_status(); off = int(rr.headers["Upload-Offset"]); break
                except httpx.HTTPError:
                    if attempt == 4:
                        raise
                    time.sleep(2 ** attempt)
                    off = int(http.head(loc, headers={"Tus-Resumable": "1.0.0"}).headers["Upload-Offset"])


# ------------------------------------------------------------------ job handling
def project_yaml(job, pdir: Path) -> Path:
    p = select("projects", id=job["project_id"])[0]
    bk = (select("brand_kits", id=p["brand_kit_id"]) or [{}])[0] if p.get("brand_kit_id") else {}
    people = select("people", project_id=p["id"])
    cams = sorted(select("cameras", project_id=p["id"]), key=lambda c: c["label"])
    cfg_cams = []
    for c in cams:
        files = sorted(select("files", camera_id=c["id"]), key=lambda f: f["position"])
        local = [str(download("footage", f["storage_path"], pdir / "footage" / c["label"] / Path(f["storage_path"]).name)) for f in files]
        cfg_cams.append({"id": c["label"], "files": local, "master_audio": c["master_audio"],
                         "order": c["person_order"], "frontal": c["frontal"]})
    out = p.get("outputs") or {}
    cfg = {
        "name": "".join(ch if ch.isalnum() else "_" for ch in p["name"]).strip("_") or "episode",
        "work_dir": str(pdir / "work"), "out_dir": str(pdir / "out" / job["id"]),
        "people": [{"id": x["slug"], "name": x["name"], "role": x["role"]} for x in people],
        "cameras": cfg_cams,
        "brand": {"show_name": bk.get("show_name", ""), "show_subtitle": bk.get("show_subtitle", ""),
                  "accent": bk.get("accent", "#6EC1FF"), "alert": bk.get("alert", "#FF4D4D"),
                  "episode_label": p.get("episode_label", ""), "guest_line": p.get("guest_line", ""),
                  "guest_subline": p.get("guest_subline", "")},
        "edit": {"angles": p.get("angles") or [], "removals": p.get("removals") or []},
        "outputs": {k: v for k, v in out.items() if k in config.Outputs.__dataclass_fields__},
    }
    f = pdir / "project.yaml"; f.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return f


def sync_status(job_id: str, status_file: Path, stop: threading.Event):
    last = None
    while not stop.wait(5):
        try:
            d = json.loads(status_file.read_text())
            cur = (d.get("stage"), round(d.get("progress", 0), 3), len(d.get("log", [])))
            if cur != last:
                update("jobs", job_id, stage=d.get("stage"), progress=d.get("progress", 0), log=d.get("log", [])[-50:]); last = cur
        except (FileNotFoundError, json.JSONDecodeError, httpx.HTTPError):
            pass


KIND_OF = {"episode": "episode", "trailer": "trailer", "transcript": "transcript", "srt": "srt", "post_captions": "post_captions"}
CTYPE = {".mp4": "video/mp4", ".mov": "video/quicktime", ".txt": "text/plain", ".srt": "application/x-subrip", ".jpg": "image/jpeg"}


def publish(job, proj, outputs: dict):
    org = select("projects", id=job["project_id"])[0]["org_id"]
    items = []
    for k, v in outputs.items():
        if isinstance(v, list):
            items += [("part" if k == "parts" else "short", f) for f in v]
        elif v:
            items.append((KIND_OF.get(k, k), v))
    for kind, f in items:
        f = Path(f); dest = f"{org}/{job['project_id']}/{job['id']}/{f.name}"
        upload("deliverables", dest, f, CTYPE.get(f.suffix.lower(), "application/octet-stream"))
        insert("deliverables", project_id=job["project_id"], job_id=job["id"], kind=kind, title=f.stem,
               storage_path=dest, bytes=f.stat().st_size)


def handle(job):
    pdir = DATA / job["project_id"]; pdir.mkdir(parents=True, exist_ok=True)
    proj = config.load(project_yaml(job, pdir))
    params = job.get("params") or {}
    stop = threading.Event()
    t = threading.Thread(target=sync_status, args=(job["id"], proj.work_dir / "status.json", stop), daemon=True); t.start()
    try:
        if job["kind"] == "inspect":
            res = cli.inspect(proj, params.get("at"))
            outputs = {"inspect": [v["image"] for v in res.values()]}
            for img in outputs["inspect"]:
                f = Path(img); dest = f"{select('projects', id=job['project_id'])[0]['org_id']}/{job['project_id']}/{job['id']}/{f.name}"
                upload("deliverables", dest, f, "image/jpeg")
                insert("deliverables", project_id=job["project_id"], job_id=job["id"], kind="inspect", title=f.stem, storage_path=dest)
            outputs = {}
        else:
            if job["kind"] == "redo":
                pipeline.invalidate(proj, params.get("stage", "edit"))
            if job["kind"] == "preview":
                proj.limit = (float(params["start"]), float(params["end"]))
                proj.work_dir = proj.work_dir / f"preview_{int(proj.limit[0])}_{int(proj.limit[1])}"; proj.work_dir.mkdir(parents=True, exist_ok=True)
                proj.outputs.parts_max_seconds = 0; proj.name += "_preview"
            outputs = pipeline.run(proj, params.get("only"))
        publish(job, proj, outputs)
        update("jobs", job["id"], status="done", progress=1.0, finished_at="now()")
        update("projects", job["project_id"], status="review")
    except Exception as e:
        update("jobs", job["id"], status="failed", error=f"{e}\n{traceback.format_exc()[-3000:]}", finished_at="now()")
        update("projects", job["project_id"], status="failed")
    finally:
        stop.set()


def main():
    if not URL or not KEY:
        raise SystemExit("set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
    print(f"worker {NAME} polling {URL}", flush=True)
    while True:
        try:
            job = rpc("claim_job", worker_name=NAME)
        except httpx.HTTPError as e:
            print("poll failed:", e, flush=True); time.sleep(15); continue
        if job and job.get("id"):
            print(f"job {job['id']} ({job['kind']})", flush=True); handle(job)
        else:
            time.sleep(10)


if __name__ == "__main__":
    main()
