import uuid

from fastapi.testclient import TestClient

from app.core.db import engine
from app.data_import.jobs import run_job


def finish_import(client: TestClient, batch_id: str, headers: dict[str, str]):
    """Run the real worker synchronously in the guarded test DB, then poll API."""
    response = client.get(f"/api/v1/imports/{batch_id}/job", headers=headers)
    assert response.status_code == 200
    assert run_job(engine, uuid.UUID(response.json()["id"]))
    return client.get(f"/api/v1/imports/{batch_id}", headers=headers)
