import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import UserCreate, UserRole
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string


def unique_code(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8].upper()}"


def create_observer(*, client: TestClient, db: Session) -> tuple[str, dict[str, str]]:
    email = random_email()
    password = random_lower_string()
    user = crud.create_user(
        session=db,
        user_create=UserCreate(
            email=email,
            password=password,
            role=UserRole.OBSERVER,
        ),
    )
    return str(user.id), user_authentication_headers(
        client=client,
        email=email,
        password=password,
    )


def test_dashboard_summary_is_scoped_to_authorized_plants(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    observer_id, observer_headers = create_observer(client=client, db=db)
    plant_response = client.post(
        f"{settings.API_V1_STR}/plants/",
        headers=superuser_token_headers,
        json={
            "code": unique_code("DASH_PLANT"),
            "name": "Dashboard Test Plant",
            "location": "Test Zone",
            "status": "active",
        },
    )
    assert plant_response.status_code == 200
    plant_id = plant_response.json()["id"]
    access_response = client.put(
        f"{settings.API_V1_STR}/users/{observer_id}/plant-access",
        headers=superuser_token_headers,
        json={"plant_ids": [plant_id]},
    )
    assert access_response.status_code == 200

    line_response = client.post(
        f"{settings.API_V1_STR}/production-lines/",
        headers=superuser_token_headers,
        json={
            "plant_id": plant_id,
            "code": unique_code("DASH_LINE"),
            "name": "Dashboard Test Line",
            "process_type": "continuous",
            "status": "active",
        },
    )
    assert line_response.status_code == 200
    device_response = client.post(
        f"{settings.API_V1_STR}/devices/",
        headers=superuser_token_headers,
        json={
            "production_line_id": line_response.json()["id"],
            "code": unique_code("DASH_DEVICE"),
            "name": "Dashboard Test Device",
            "device_type": "simulator",
            "status": "active",
        },
    )
    assert device_response.status_code == 200
    tag_response = client.post(
        f"{settings.API_V1_STR}/tags/",
        headers=superuser_token_headers,
        json={
            "device_id": device_response.json()["id"],
            "code": unique_code("DASH_TAG"),
            "name": "Dashboard Test Tag",
            "data_type": "float",
            "unit": "degC",
            "sampling_interval_ms": 1000,
            "is_enabled": True,
        },
    )
    assert tag_response.status_code == 200

    summary_response = client.get(
        f"{settings.API_V1_STR}/dashboard/summary",
        headers=observer_headers,
    )
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["plant_count"] == 1
    assert summary["production_line_count"] == 1
    assert summary["device_count"] == 1
    assert summary["tag_count"] == 1
    assert summary["enabled_tag_count"] == 1
    assert summary["acquisition_task_count"] == 0
    assert summary["sample_count"] == 0
    assert summary["import_batch_count"] == 0
    assert summary["quality_issue_count"] == 0
    assert summary["generated_at"]


def test_dashboard_summary_requires_authentication(client: TestClient) -> None:
    response = client.get(f"{settings.API_V1_STR}/dashboard/summary")
    assert response.status_code == 401
