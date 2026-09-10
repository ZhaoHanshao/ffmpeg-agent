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

# ── System dependencies (no proxy — apt can't reach debian mirrors through it) ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Proxy (set after apt-get so it only affects pip + runtime) ──
#     ffmpeg.org can be reached directly from China, SSL fails through the proxy
ENV HTTP_PROXY=$HTTP_PROXY \
    HTTPS_PROXY=$HTTPS_PROXY \
    NO_PROXY=ffmpeg.org
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ENV PIP_INDEX_URL=$PIP_INDEX_URL \
    PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST

# ── Python dependencies ──
# requirements.txt 不含 torch/transformers/sentence-transformers：
# 嵌入走 onnxruntime+tokenizers（见 app/onnx_embed.py），
# 装了 transformers 反而会让 langchain_core 在导入期拉入 torch，启动慢 7 倍。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──
COPY backend/ backend/
COPY --from=frontend-builder /build/dist/ frontend/dist/

# ── Pre-built knowledge base ──
# backend/data/bge_onnx（嵌入模型）与 backend/data/chroma_db（向量库）由 .dockerignore 放行后随
# COPY backend/ 一起进镜像，无需在构建期联网抓 ffmpeg.org。
# 若需在构建期重建向量库，可运行:
# RUN python backend/build_package_db.py

# ── Runtime defaults (override via -e) ──
# BGE_CACHE_DIR 必须指向含 model.onnx 的目录；旧值 backend/data/bge_small 是
# PyTorch 权重目录，镜像里并不存在该目录，会导致嵌入模型解析失败。
ENV DB_DIR=backend/data/chroma_db \
    COLLECTION_NAME=ffmpeg_docs \
    PROBE_COLLECTION_NAME=ffprobe_docs \
    BGE_CACHE_DIR=backend/data/bge_onnx \
    DOC_URL=https://ffmpeg.org/ffmpeg-all.html \
    PROBE_DOC_URL=https://ffmpeg.org/ffprobe-all.html

# ── Non-root user (security hardening) ──
RUN mkdir -p backend/upload backend/download backend/data \
    && groupadd -r app \
    && useradd -r -g app -d /app app \
    && chown -R app:app /app
USER app

EXPOSE 8000

# Startup is slow (~40-80s: imports + BGE model + first-run ChromaDB build),
# hence the long start-period.
# 注意：/api/health 固定返回 HTTP 200，初始化失败时把原因写在 body 的 status 里，
# 所以这里必须解析 body，否则初始化失败也永远显示 healthy。
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import json,urllib.request;d=json.load(urllib.request.urlopen('http://localhost:8000/api/health',timeout=3));raise SystemExit(0 if d.get('status')=='ok' else 1)"]

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
