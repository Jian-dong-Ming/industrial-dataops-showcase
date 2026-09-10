from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session


def test_health_check(client: TestClient) -> None:
    response = client.get("/api/v1/utils/health-check/")

    assert response.status_code == 200
    assert response.json() is True


def test_health_check_reports_database_failure(client: TestClient) -> None:
    with patch.object(Session, "exec", side_effect=SQLAlchemyError("unavailable")):
        response = client.get("/api/v1/utils/health-check/")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
