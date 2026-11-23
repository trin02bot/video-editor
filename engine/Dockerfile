# Studio engine worker image (CPU). For GPU workers, base on nvidia/cuda:12.x-cudnn-runtime-ubuntu22.04,
# install python3.11 + ffmpeg the same way, and set STUDIO_CUDA=1 (faster-whisper then runs on the GPU).
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libgl1 libegl1 libgles2 libglib2.0-0 fonts-inter \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY studio ./studio
RUN pip install --no-cache-dir .

# Models (whisper + mediapipe) download on first use into this volume.
ENV STUDIO_MODELS=/models
VOLUME ["/models", "/data"]

ENTRYPOINT ["studio"]
CMD ["--help"]
