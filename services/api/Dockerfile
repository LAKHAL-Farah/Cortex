# Base images: run scripts/pin-base-images.sh to pin FROM lines by digest;
# Dependabot (docker ecosystem) then keeps the digests current.
# ---- builder ----
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder
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
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
LABEL org.opencontainers.image.source="https://github.com/LAKHAL-Farah/Cortex"
# `apt-get upgrade` first: the digest-pinned base can lag Debian security fixes (git pulls in
# libcurl3t64-gnutls, which Trivy flagged HIGH: CVE-2026-8286 / CVE-2026-8927, fixed in
# 8.14.1-2+deb13u7). Re-run scripts/pin-base-images.sh / let Dependabot bump the digest too.
RUN apt-get update \
    && apt-get -y --no-install-recommends upgrade \
    && apt-get install -y --no-install-recommends openssh-client git \
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