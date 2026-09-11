from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.core.config import Settings, settings


def test_health_check(client: TestClient) -> None:
    response = client.get("/api/v1/utils/health-check/")

    assert response.status_code == 200
    assert response.json() is True


def test_health_check_reports_database_failure(client: TestClient) -> None:
    with patch.object(Session, "exec", side_effect=SQLAlchemyError("unavailable")):
        response = client.get("/api/v1/utils/health-check/")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_browser_test_guard_default_denies(client: TestClient) -> None:
    with patch.object(settings, "BROWSER_TEST_MODE", False):
        response = client.get("/api/v1/utils/browser-test-safety/")
    assert response.status_code == 200
    assert response.json() == {"browser_tests_allowed": False}


def test_browser_test_guard_checks_connected_database(client: TestClient) -> None:
    with patch.object(settings, "BROWSER_TEST_MODE", True):
        response = client.get("/api/v1/utils/browser-test-safety/")
        assert response.json() == {"browser_tests_allowed": True}
        with patch.object(Session, "exec") as execute:
            execute.return_value.one.return_value = "app"
            response = client.get("/api/v1/utils/browser-test-safety/")
            assert response.json() == {"browser_tests_allowed": False}


@pytest.mark.parametrize("database", ["/app", ""])
def test_browser_test_mode_rejects_business_database_configuration(
    database: str,
) -> None:
    values = settings.model_dump(exclude={"emails_enabled"})
    values.update(
        BROWSER_TEST_MODE=True,
        DATABASE_URL=f"postgresql://tester:local-safe-password@localhost:5432{database}",
    )
    with pytest.raises(ValidationError, match="requires a database"):
        Settings(**values)
