# ============================================================
# Stage 1: Build frontend (Vue + Vite)
# ============================================================
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ .
RUN npm run build

# ============================================================
# Stage 2: Production image
# ============================================================
FROM python:3.12-slim

# ── No .pyc files; flush logs immediately so `docker logs` is live ──
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── Optional proxy args (pass --build-arg if behind a firewall) ──
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""

# ── Optional CPU-only torch (default: CPU, pass "cuXXX" for GPU) ──
ARG TORCH_INDEX_URL="https://download.pytorch.org/whl/cpu"

# ── System dependencies (no proxy — apt can't reach debian mirrors through it) ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Proxy (set after apt-get so it only affects pip + ChromaDB pre-build + runtime) ──
#     ffmpeg.org can be reached directly from China, SSL fails through the proxy
ENV HTTP_PROXY=$HTTP_PROXY \
    HTTPS_PROXY=$HTTPS_PROXY \
    NO_PROXY=ffmpeg.org
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ENV PIP_INDEX_URL=$PIP_INDEX_URL \
    PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST \
    PIP_EXTRA_INDEX_URL=$TORCH_INDEX_URL

# ── Python dependencies ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──
COPY backend/ backend/
COPY --from=frontend-builder /build/dist/ frontend/dist/

# ── Pre-build vector database (BGE model + ChromaDB) ──
# NOTE: Skipped — ffmpeg.org SSL handshake fails during Docker build.
#       Vector DB will be built on first runtime access.
# RUN python <<EOF
# import sys, os
# sys.path.insert(0, 'backend')
# os.environ['DB_DIR']='backend/data/chroma_db'
# os.environ['COLLECTION_NAME']='ffmpeg_docs'
# os.environ['BGE_MODEL_NAME']='BAAI/bge-small-zh-v1.5'
# os.environ['BGE_CACHE_DIR']='backend/data/bge_small'
# os.environ['DOC_URL']='https://ffmpeg.org/ffmpeg-all.html'
# from app.db_search import _ensure_vector_db
# _ensure_vector_db()
# EOF

# ── Runtime defaults (override via -e) ──
ENV DB_DIR=backend/data/chroma_db \
    COLLECTION_NAME=ffmpeg_docs \
    BGE_MODEL_NAME=BAAI/bge-small-zh-v1.5 \
    BGE_CACHE_DIR=backend/data/bge_small \
    DOC_URL=https://ffmpeg.org/ffmpeg-all.html

# ── Non-root user (security hardening) ──
RUN mkdir -p backend/upload backend/download backend/data \
    && groupadd -r app \
    && useradd -r -g app -d /app app \
    && chown -R app:app /app
USER app

EXPOSE 8000

# Startup is slow (~40-80s: imports + BGE model + first-run ChromaDB build),
# hence the long start-period
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)"]

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
