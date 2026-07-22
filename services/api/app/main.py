from fastapi import FastAPI
from sqlalchemy import text
from .db import engine

app = FastAPI(title="Cortex API", version="0.1.0")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/health/db")
def health_db():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"db": "ok"}