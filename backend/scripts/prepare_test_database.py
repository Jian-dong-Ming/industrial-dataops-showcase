"""Create a dedicated disposable PostgreSQL database for the test suite."""

import os

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def main() -> None:
    test_url_value = os.environ.get("TEST_DATABASE_URL")
    admin_url_value = os.environ.get("ADMIN_DATABASE_URL")
    if not test_url_value or not admin_url_value:
        raise RuntimeError("TEST_DATABASE_URL and ADMIN_DATABASE_URL are required")

    test_url = make_url(
        test_url_value.replace("postgresql+psycopg://", "postgresql://")
    )
    admin_url = make_url(
        admin_url_value.replace("postgresql+psycopg://", "postgresql://")
    ).set(database="postgres")
    database_name = test_url.database or ""
    if not database_name.endswith("_test") or database_name in {"app", "postgres"}:
        raise RuntimeError(
            "Refusing to prepare a non-test database; the name must end with '_test'"
        )
    if (
        test_url.host != admin_url.host
        or test_url.port != admin_url.port
        or test_url.username != admin_url.username
    ):
        raise RuntimeError("Test and admin database URLs must use the same server")

    with psycopg.connect(admin_url.render_as_string(hide_password=False)) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(database_name)
                )
            )
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )


if __name__ == "__main__":
    main()
