FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
LABEL org.opencontainers.image.source="https://github.com/LAKHAL-Farah/Cortex"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py trivy_aggregate.py ./

EXPOSE 9110
CMD ["uvicorn", "collector:app", "--host", "0.0.0.0", "--port", "9110"]
