# AI Lecture Search backend.
#
# Build from the repo root (the backend serves ../web at /app, so web/ is in the context):
#   docker buildx build --platform linux/amd64 -f backend/Dockerfile -t aic-backend:test --load .
#
# Layout in the image mirrors the repo, because app/main.py finds web/ as parents[2] / "web":
#   /srv/backend/app, /srv/backend/db, /srv/web

# ---------------------------------------------------------------- build stage
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hf

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# CPU-only torch first. Installed from the CPU index, it already satisfies sentence-transformers,
# so the next step does not pull the CUDA wheels (about 2 GB more). Same version as development.
RUN pip install "torch==2.15.0" --index-url https://download.pytorch.org/whl/cpu

# requirements-ml.txt includes requirements.txt with `-r`, so both go in side by side.
COPY backend/requirements.txt backend/requirements-ml.txt /tmp/req/
RUN pip install -r /tmp/req/requirements-ml.txt

# The embedding model, downloaded now so the running container never needs the network for it.
# EMBEDDING_MODEL must match backend/app/config.py.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

# ---------------------------------------------------------------- final stage
FROM python:3.12-slim

# Offline at runtime: the model is already in HF_HOME, and nothing may reach Hugging Face.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/hf /opt/hf

WORKDIR /srv/backend
COPY backend/app /srv/backend/app
COPY backend/db /srv/backend/db
COPY web /srv/web

# Non-root, and everything above stays owned by root and read-only to it. The app writes no files:
# the one scratch place for libraries (temp files) is /tmp, which the compose file can mount as a
# tmpfs together with a read-only root filesystem.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000

# python's urllib, because the slim image has no curl. The Host header is the first ALLOWED_HOSTS
# entry: the app refuses any other Host (TrustedHostMiddleware), and in production that list holds
# the public name, not 127.0.0.1. Any non-2xx answer or error makes the check fail.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; h = os.environ.get('ALLOWED_HOSTS', 'localhost').split(',')[0].strip(); u.urlopen(u.Request('http://127.0.0.1:8000/health', headers={'Host': h}), timeout=4)"]

# --proxy-headers with --forwarded-allow-ips=* makes uvicorn trust X-Forwarded-Proto/-For from
# any peer, so behind Caddy the request scheme is https, which the Origin check's own-origin rule
# (app/origin.py) needs for same-origin POSTs from the site. That is safe only because port 8000
# is never published: only Caddy, on the internal Docker network, can reach it. Do not publish it.
# One worker: the embedding model lives in memory once (2 GB host). No --reload.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
