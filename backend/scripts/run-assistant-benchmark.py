"""Prepare a separate benchmark database without deleting any existing data."""

import os
import subprocess
import sys

import psycopg
from sqlalchemy.engine import make_url

from app.core.config import settings

url = make_url(
    str(settings.DATABASE_URL).replace("postgresql+psycopg://", "postgresql://")
)
target = "app_ai_independent_test"
with psycopg.connect(
    url.set(database="postgres").render_as_string(hide_password=False), autocommit=True
) as connection:
    if not connection.execute(
        "SELECT 1 FROM pg_database WHERE datname=%s", (target,)
    ).fetchone():
        connection.execute("CREATE DATABASE app_ai_independent_test")
env = {
    **os.environ,
    "DATABASE_URL": url.set(database=target).render_as_string(hide_password=False),
}
subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True
)
subprocess.run(
    [sys.executable, "scripts/assistant_benchmark.py", *sys.argv[1:]],
    env=env,
    check=True,
)
