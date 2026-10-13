FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
LABEL org.opencontainers.image.source="https://github.com/LAKHAL-Farah/Cortex"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py trivy_aggregate.py ./

EXPOSE 9110
CMD ["uvicorn", "collector:app", "--host", "0.0.0.0", "--port", "9110"]
