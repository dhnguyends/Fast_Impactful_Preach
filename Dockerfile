# FastImpact Preach — server image (CPU: Whisper int8 + libx264). Works on any Linux VPS.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg (with libass for subtitles) + colour emoji font for the emoji pops
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-noto-color-emoji curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY deploy/requirements-server.txt .
RUN pip install -r requirements-server.txt

COPY fip ./fip
COPY app ./app
COPY static ./static
# Montserrat (SIL Open Font License) for the captions and end card
COPY assets/fonts ./assets/fonts
# default end-card logo (EEIG); a logo uploaded in the app is stored in /data/brand instead
COPY assets/brand ./assets/brand

RUN useradd --create-home --uid 1000 fip \
 && mkdir -p /data \
 && chown -R fip:fip /data /app/assets
USER fip

ENV FIP_DATA_DIR=/data \
    FIP_ENV_FILE=/data/.env \
    FIP_SERVER_MODE=1 \
    WHISPER_DEVICE=cpu \
    HF_HOME=/data/hf-cache

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s CMD curl -fs http://127.0.0.1:8000/login.html >/dev/null || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
