"""Run isolated tests with installed dependencies; never sync or download packages."""

import os
import subprocess
import sys

from sqlalchemy.engine import make_url

from app.core.config import settings


def main() -> None:
    original = make_url(str(settings.DATABASE_URL))
    env = {
        **os.environ,
        "ADMIN_DATABASE_URL": original.render_as_string(hide_password=False),
        "TEST_DATABASE_URL": original.set(database="app_test").render_as_string(
            hide_password=False
        ),
    }
    subprocess.run(
        [sys.executable, "scripts/prepare_test_database.py"], env=env, check=True
    )
    env["DATABASE_URL"] = env["TEST_DATABASE_URL"]
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "-m",
            "pytest",
            "tests/",
            "-q",
            "--maxfail=1",
            "--tb=short",
        ],
        env=env,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "report", "--fail-under=90"],
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
