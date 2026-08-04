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
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]