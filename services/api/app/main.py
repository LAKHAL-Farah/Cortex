
from fastapi import FastAPI
from sqlalchemy import text
from .db import engine
from fastapi.staticfiles import StaticFiles
from .routers import nodes
from .routers import metrics
from .routers import dashboard
from .routers import logs
app = FastAPI(title="Cortex API", version="0.1.0")
app.include_router(nodes.router)
app.include_router(metrics.router)
app.include_router(dashboard.router) 
app.include_router(logs.router)
app.mount("/ui", StaticFiles(directory="app/static", html=True), name="ui")
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/health/db")
def health_db():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"db": "ok"}


