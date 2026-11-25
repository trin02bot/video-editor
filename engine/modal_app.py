"""Serverless GPU worker on Modal (https://modal.com). Pay only while an episode is processing.

How it works:
  * `dispatch` runs every minute on a tiny CPU container and asks Supabase whether any jobs are queued.
  * If there are, it starts `process` on a GPU machine, which claims and runs jobs until the queue is
    empty and then shuts down. Nothing runs (or bills) while the queue is empty.
  * Footage, stage caches and downloaded models live on Modal Volumes, so a "redo" after changing
    angles or removals reuses earlier work instead of re-downloading 4K files.

One-time setup (from this `engine/` folder):
  pip install modal
  modal setup                                   # signs this computer in to your Modal account
  modal secret create studio-secrets \
      SUPABASE_URL=https://<project>.supabase.co \
      SUPABASE_SERVICE_ROLE_KEY=<service role key> \
      ANTHROPIC_API_KEY=<optional>
  modal deploy modal_app.py

(The secret can also be created in the Modal dashboard: Secrets -> Create -> Custom, name `studio-secrets`.)
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import modal

HERE = Path(__file__).parent
app = modal.App("video-editor-studio")

DEPS = [
    "numpy>=1.26", "scipy>=1.11", "scikit-learn>=1.3", "librosa>=0.10", "opencv-python-headless>=4.8",
    "mediapipe>=0.10.14", "faster-whisper>=1.0", "pillow>=10", "pyyaml>=6", "anthropic>=0.70", "httpx>=0.27",
]

# CUDA 12 + cuDNN 9 (what faster-whisper / CTranslate2 need on the GPU); Ubuntu 24.04 ships ffmpeg 6.
gpu_image = (
    modal.Image.from_registry("nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04", add_python="3.11")
    .apt_install("ffmpeg", "libgl1", "libegl1", "libgles2", "libglib2.0-0")
    .pip_install(*DEPS)
    .env({"STUDIO_CUDA": "1", "STUDIO_MODELS": "/models", "STUDIO_DATA": "/data", "STUDIO_WORKER_NAME": "modal-gpu"})
    # add_local_dir (not add_local_python_source) so the bundled fonts ship with the code
    .add_local_dir(HERE / "studio", "/root/studio")
)
light_image = modal.Image.debian_slim(python_version="3.11").pip_install("httpx>=0.27")

secrets = [modal.Secret.from_name("studio-secrets")]
data = modal.Volume.from_name("studio-data", create_if_missing=True)
models = modal.Volume.from_name("studio-models", create_if_missing=True)
state = modal.Dict.from_name("studio-dispatch", create_if_missing=True)

SPAWN_COOLDOWN = 4 * 60   # a GPU container needs a minute or two to boot and claim its job


@app.function(
    image=gpu_image,
    gpu="L4",                 # transcription runs on the GPU; tracking and x264 encoding use the CPUs
    cpu=8.0,
    memory=32768,
    ephemeral_disk=500 * 1024,
    timeout=8 * 60 * 60,
    volumes={"/data": data, "/models": models},
    secrets=secrets,
    max_containers=2,
)
def process():
    from studio import worker

    def persist():
        data.commit(); models.commit()

    n = worker.run(exit_when_idle=True, after_job=persist)
    print(f"processed {n} job(s); queue empty, shutting down")


@app.function(image=light_image, secrets=secrets, schedule=modal.Period(minutes=1), timeout=60)
def dispatch():
    import httpx

    url = os.environ["SUPABASE_URL"].rstrip("/"); key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = httpx.get(f"{url}/rest/v1/jobs?select=id&status=eq.queued&limit=20",
                  headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=20)
    r.raise_for_status()
    queued = len(r.json())
    if not queued:
        return
    if time.time() - state.get("last_spawn", 0) < SPAWN_COOLDOWN:
        return
    state["last_spawn"] = time.time()
    process.spawn()
    print(f"{queued} queued job(s): started a GPU worker")


@app.local_entrypoint()
def main():
    """`modal run modal_app.py` processes whatever is queued right now, without waiting for the schedule."""
    process.remote()
