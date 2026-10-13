FROM python:3.14-slim
LABEL org.opencontainers.image.source="https://github.com/LAKHAL-Farah/Cortex"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py trivy_aggregate.py ./

EXPOSE 9110
CMD ["uvicorn", "collector:app", "--host", "0.0.0.0", "--port", "9110"]
