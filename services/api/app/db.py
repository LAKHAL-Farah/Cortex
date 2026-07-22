import os
from sqlalchemy import create_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://cortex:cortex@postgres:5432/cortex"
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)