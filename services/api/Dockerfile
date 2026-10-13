# Base images: run scripts/pin-base-images.sh to pin FROM lines by digest;
# Dependabot (docker ecosystem) then keeps the digests current.
# ---- builder ----
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS builder
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# CPU-only torch FIRST, so sentence-transformers sees it as already satisfied
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# setuptools 80.10.2 fixes CVE-2025-47273 and vendors a patched wheel; do NOT go
# to >=82 (drops pkg_resources, which some OpenStack libs still import).
RUN pip install --no-cache-dir "setuptools==80.10.2"
RUN ansible-galaxy collection install 'community.general:<11.0.0' -p /opt/ansible-collections
ENV HF_HOME=/opt/hf-cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

# ---- runtime ----
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/LAKHAL-Farah/Cortex"
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 10001 cortex
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf-cache /opt/hf-cache
COPY --from=builder /opt/ansible-collections /opt/ansible-collections
ENV PATH="/opt/venv/bin:$PATH" HF_HOME=/opt/hf-cache HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 ANSIBLE_COLLECTIONS_PATH=/opt/ansible-collections \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini entrypoint.sh ./
RUN chmod +x entrypoint.sh && chown -R cortex /app
# Numeric UID so Kubernetes `runAsNonRoot: true` can verify it.
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]