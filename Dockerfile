# QRESPONDER — multi-stage image. Builds a self-contained venv, then a slim runtime.
#
# Default build is SLIM: web UI + both cloud SDKs, no torch — so the image is small
# and builds fast on every arch (amd64 + arm64). Retrieval mode still works via a
# BM25 fallback; for full dense hybrid + cross-encoder rerank, either
#   docker build --build-arg EXTRAS=anthropic,openai,web,retrieval -t qresponder .
# or `pip install "qresponder[retrieval]"`.
#
#   docker build -t qresponder .
#   docker run --rm -p 127.0.0.1:8000:8000 -v qr-data:/data qresponder

# ---- builder: install into an isolated venv --------------------------------
FROM python:3.12-slim AS builder

ARG EXTRAS=anthropic,openai,web
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[${EXTRAS}]"

# ---- runtime: copy the venv into a clean image -----------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    WORKSPACES_DIR=/data/workspaces

# Non-root runtime user; /data is the persistent mount for workspaces + assets.
RUN useradd -u 10001 -m qr && mkdir -p /data && chown qr:qr /data
COPY --from=builder /opt/venv /opt/venv
USER qr
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8000

# Liveness: the /healthz probe is always open (even with QRESPONDER_AUTH_TOKEN set).
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)"

ENTRYPOINT ["qresponder"]
# Container binds 0.0.0.0 (its own interface); publish it to the HOST's loopback
# only (see docker-compose.yml). Do not expose to a network without auth + a proxy.
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
