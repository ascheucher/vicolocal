# Vicolocal dashboard - small, internet-only (the browser does the camera WebRTC, not this).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 VICO_PORT=8080 VICO_TOKEN_FILE=/data/.vico-token.json

WORKDIR /app
# ffmpeg is used only to remux recorded HLS clips to mp4 for download.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY vico_server.py vico_signaling.py vico_sdp.py vico_api.py ./
COPY static ./static
RUN mkdir -p /data

EXPOSE 8080
# Provide credentials at runtime: mount .vico.env to /app/.vico.env (ro), or set VICO_ENV_FILE.
CMD ["python", "vico_server.py"]
