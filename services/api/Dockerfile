FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# infra/ansible*/requirements.yml declares this collection (used by the node_exporter
# role's ufw task) but nothing installed it -- ansible-playbook fails module resolution
# for it at parse time even when that task is skipped by `when: ufw_enabled`.
RUN ansible-galaxy collection install 'community.general:<11.0.0'

# Pre-download the knowledge-RAG embedding model at build time (adr-0004) so
# it's baked into the image and no network call to huggingface.co is needed
# at runtime.
ENV HF_HOME=/opt/hf-cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

# Prevent any accidental runtime network calls to the HF Hub (e.g. version
# checks) -- everything needed is already cached at HF_HOME. If you change
# EMBEDDING_MODEL via env var, you must also update the RUN line above to
# match and rebuild -- these offline flags mean a mismatched model name at
# runtime will fail instead of silently downloading.
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]