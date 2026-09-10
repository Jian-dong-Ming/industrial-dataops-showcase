import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import UserCreate, UserRole
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string


def unique_code(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8].upper()}"


def create_role_headers(
    *, client: TestClient, db: Session, role: UserRole
) -> tuple[uuid.UUID, dict[str, str]]:
    email = random_email()
    password = random_lower_string()
    user = crud.create_user(
        session=db,
        user_create=UserCreate(email=email, password=password, role=role),
    )
    headers = user_authentication_headers(client=client, email=email, password=password)
    return user.id, headers


def create_plant(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    payload = {
        "code": unique_code("PLANT"),
        "name": "Synthetic Plant",
        "location": "Test Zone",
        "status": "active",
    }
    response = client.post(
        f"{settings.API_V1_STR}/plants/", headers=headers, json=payload
    )
    assert response.status_code == 200, response.text
    return response.json()


def assign_plant(
    client: TestClient,
    admin_headers: dict[str, str],
    user_id: uuid.UUID,
    plant_id: str,
) -> None:
    response = client.put(
        f"{settings.API_V1_STR}/users/{user_id}/plant-access",
        headers=admin_headers,
        json={"plant_ids": [plant_id]},
    )
    assert response.status_code == 200, response.text


@pytest.fixture()
def domain_context(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> dict[str, Any]:
    engineer_id, engineer_headers = create_role_headers(
        client=client, db=db, role=UserRole.ENGINEER
    )
    observer_id, observer_headers = create_role_headers(
        client=client, db=db, role=UserRole.OBSERVER
    )
    unassigned_id, unassigned_headers = create_role_headers(
        client=client, db=db, role=UserRole.ENGINEER
    )
    plant = create_plant(client, superuser_token_headers)
    restricted_plant = create_plant(client, superuser_token_headers)
    assign_plant(client, superuser_token_headers, engineer_id, plant["id"])
    assign_plant(client, superuser_token_headers, observer_id, plant["id"])
    return {
        "admin_headers": superuser_token_headers,
        "engineer_id": engineer_id,
        "engineer_headers": engineer_headers,
        "observer_id": observer_id,
        "observer_headers": observer_headers,
        "unassigned_id": unassigned_id,
        "unassigned_headers": unassigned_headers,
        "plant": plant,
        "restricted_plant": restricted_plant,
    }


def create_line(
    client: TestClient,
    headers: dict[str, str],
    plant_id: str,
    code: str | None = None,
) -> dict[str, Any]:
    response = client.post(
        f"{settings.API_V1_STR}/production-lines/",
        headers=headers,
        json={
            "plant_id": plant_id,
            "code": code or unique_code("LINE"),
            "name": "Synthetic Line",
            "process_type": "continuous",
            "status": "active",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_device(
    client: TestClient, headers: dict[str, str], production_line_id: str
) -> dict[str, Any]:
    response = client.post(
        f"{settings.API_V1_STR}/devices/",
        headers=headers,
        json={
            "production_line_id": production_line_id,
            "code": unique_code("DEVICE"),
            "name": "Synthetic Furnace",
            "device_type": "furnace",
            "manufacturer": "Demo Vendor",
            "model": "SIM-01",
            "status": "active",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_tag(
    client: TestClient, headers: dict[str, str], device_id: str, code: str | None = None
) -> dict[str, Any]:
    response = client.post(
        f"{settings.API_V1_STR}/tags/",
        headers=headers,
        json={
            "device_id": device_id,
            "code": code or unique_code("TAG"),
            "name": "Synthetic Temperature",
            "data_type": "float",
            "unit": "degC",
            "min_value": 0,
            "max_value": 1500,
            "sampling_interval_ms": 1000,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_admin_can_create_full_asset_hierarchy(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    line = create_line(
        client,
        domain_context["admin_headers"],
        domain_context["restricted_plant"]["id"],
    )
    device = create_device(client, domain_context["admin_headers"], line["id"])
    tag = create_tag(client, domain_context["admin_headers"], device["id"])
    assert tag["device_id"] == device["id"]


def test_engineer_can_manage_authorized_plant_assets(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    line = create_line(
        client,
        domain_context["engineer_headers"],
        domain_context["plant"]["id"],
    )
    device = create_device(client, domain_context["engineer_headers"], line["id"])
    response = client.patch(
        f"{settings.API_V1_STR}/devices/{device['id']}",
        headers=domain_context["engineer_headers"],
        json={"name": "Updated Synthetic Furnace"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Synthetic Furnace"


def test_observer_can_read_but_cannot_write(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    response = client.get(
        f"{settings.API_V1_STR}/plants/{domain_context['plant']['id']}",
        headers=domain_context["observer_headers"],
    )
    assert response.status_code == 200

    response = client.post(
        f"{settings.API_V1_STR}/production-lines/",
        headers=domain_context["observer_headers"],
        json={
            "plant_id": domain_context["plant"]["id"],
            "code": unique_code("LINE"),
            "name": "Forbidden Line",
            "process_type": "batch",
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Engineer role required for this operation"


def test_unassigned_engineer_is_denied_object_access(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    response = client.get(
        f"{settings.API_V1_STR}/plants/{domain_context['plant']['id']}",
        headers=domain_context["unassigned_headers"],
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "No access to this plant"


def test_only_admin_can_create_or_update_plants(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/plants/",
        headers=domain_context["engineer_headers"],
        json={"code": unique_code("PLANT"), "name": "Forbidden Plant"},
    )
    assert response.status_code == 403

    response = client.patch(
        f"{settings.API_V1_STR}/plants/{domain_context['plant']['id']}",
        headers=domain_context["engineer_headers"],
        json={"name": "Forbidden Rename"},
    )
    assert response.status_code == 403


def test_hierarchical_unique_constraints_return_conflict(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    duplicate_code = unique_code("LINE")
    create_line(
        client,
        domain_context["engineer_headers"],
        domain_context["plant"]["id"],
        duplicate_code,
    )
    response = client.post(
        f"{settings.API_V1_STR}/production-lines/",
        headers=domain_context["engineer_headers"],
        json={
            "plant_id": domain_context["plant"]["id"],
            "code": duplicate_code,
            "name": "Duplicate Line",
            "process_type": "continuous",
        },
    )
    assert response.status_code == 409


def test_invalid_code_and_tag_range_are_rejected(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/plants/",
        headers=domain_context["admin_headers"],
        json={"code": "lowercase", "name": "Invalid Plant"},
    )
    assert response.status_code == 422

    line = create_line(
        client,
        domain_context["engineer_headers"],
        domain_context["plant"]["id"],
    )
    device = create_device(client, domain_context["engineer_headers"], line["id"])
    response = client.post(
        f"{settings.API_V1_STR}/tags/",
        headers=domain_context["engineer_headers"],
        json={
            "device_id": device["id"],
            "code": unique_code("TAG"),
            "name": "Invalid Range",
            "data_type": "float",
            "min_value": 10,
            "max_value": 5,
            "sampling_interval_ms": 1000,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "min_value must be less than or equal to max_value"
    )


def test_disabled_parent_rejects_new_child(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/plants/{domain_context['plant']['id']}/disable",
        headers=domain_context["admin_headers"],
    )
    assert response.status_code == 200
    assert response.json()["status"] == "inactive"

    response = client.post(
        f"{settings.API_V1_STR}/production-lines/",
        headers=domain_context["engineer_headers"],
        json={
            "plant_id": domain_context["plant"]["id"],
            "code": unique_code("LINE"),
            "name": "Blocked Line",
            "process_type": "continuous",
        },
    )
    assert response.status_code == 409


def test_list_results_are_scoped_to_assigned_plants(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    response = client.get(
        f"{settings.API_V1_STR}/plants/",
        headers=domain_context["observer_headers"],
    )
    assert response.status_code == 200
    plant_ids = {plant["id"] for plant in response.json()["data"]}
    assert domain_context["plant"]["id"] in plant_ids
    assert domain_context["restricted_plant"]["id"] not in plant_ids


def test_access_replacement_validates_plant_ids(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    response = client.put(
        f"{settings.API_V1_STR}/users/{domain_context['engineer_id']}/plant-access",
        headers=domain_context["admin_headers"],
        json={"plant_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "One or more plants not found"


def test_admin_role_is_normalized_to_superuser(client: TestClient, db: Session) -> None:
    email = random_email()
    password = random_lower_string()
    user = crud.create_user(
        session=db,
        user_create=UserCreate(email=email, password=password, role=UserRole.ADMIN),
    )
    assert user.role == UserRole.ADMIN
    assert user.is_superuser is True

    headers = user_authentication_headers(client=client, email=email, password=password)
    response = client.get(f"{settings.API_V1_STR}/users/", headers=headers)
    assert response.status_code == 200


def test_asset_detail_update_disable_and_filter_endpoints(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    headers = domain_context["engineer_headers"]
    plant_id = domain_context["plant"]["id"]
    line = create_line(client, headers, plant_id)
    device = create_device(client, headers, line["id"])
    tag = create_tag(client, headers, device["id"])

    line_list = client.get(
        f"{settings.API_V1_STR}/production-lines/",
        headers=headers,
        params={"plant_id": plant_id, "query": line["code"], "status": "active"},
    )
    assert line_list.status_code == 200
    assert line_list.json()["count"] == 1
    assert (
        client.get(
            f"{settings.API_V1_STR}/production-lines/{line['id']}", headers=headers
        ).status_code
        == 200
    )

    device_list = client.get(
        f"{settings.API_V1_STR}/devices/",
        headers=headers,
        params={
            "production_line_id": line["id"],
            "query": device["name"],
            "device_type": "furnace",
            "status": "active",
        },
    )
    assert device_list.status_code == 200
    assert device_list.json()["count"] == 1
    assert (
        client.get(
            f"{settings.API_V1_STR}/devices/{device['id']}", headers=headers
        ).status_code
        == 200
    )

    tag_list = client.get(
        f"{settings.API_V1_STR}/tags/",
        headers=headers,
        params={
            "device_id": device["id"],
            "query": tag["code"],
            "data_type": "float",
            "is_enabled": True,
        },
    )
    assert tag_list.status_code == 200
    assert tag_list.json()["count"] == 1
    assert (
        client.get(
            f"{settings.API_V1_STR}/tags/{tag['id']}", headers=headers
        ).status_code
        == 200
    )

    tag_update = client.patch(
        f"{settings.API_V1_STR}/tags/{tag['id']}",
        headers=headers,
        json={"unit": "C", "max_value": 1200},
    )
    assert tag_update.status_code == 200
    assert tag_update.json()["max_value"] == 1200
    assert (
        client.post(
            f"{settings.API_V1_STR}/tags/{tag['id']}/disable", headers=headers
        ).json()["is_enabled"]
        is False
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/devices/{device['id']}/disable", headers=headers
        ).json()["status"]
        == "inactive"
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/production-lines/{line['id']}/disable",
            headers=headers,
        ).json()["status"]
        == "inactive"
    )


def test_admin_can_update_plant_and_duplicate_plant_is_conflict(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    plant = domain_context["restricted_plant"]
    response = client.patch(
        f"{settings.API_V1_STR}/plants/{plant['id']}",
        headers=domain_context["admin_headers"],
        json={"name": "Renamed Synthetic Plant"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Synthetic Plant"

    response = client.post(
        f"{settings.API_V1_STR}/plants/",
        headers=domain_context["admin_headers"],
        json={"code": plant["code"], "name": "Duplicate Plant"},
    )
    assert response.status_code == 409


def test_missing_asset_details_return_not_found(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    headers = domain_context["admin_headers"]
    missing_id = uuid.uuid4()
    assert (
        client.get(
            f"{settings.API_V1_STR}/plants/{missing_id}", headers=headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"{settings.API_V1_STR}/production-lines/{missing_id}", headers=headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"{settings.API_V1_STR}/devices/{missing_id}", headers=headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"{settings.API_V1_STR}/tags/{missing_id}", headers=headers
        ).status_code
        == 404
    )


def test_unassigned_user_cannot_access_nested_assets(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    line = create_line(
        client,
        domain_context["admin_headers"],
        domain_context["restricted_plant"]["id"],
    )
    device = create_device(client, domain_context["admin_headers"], line["id"])
    tag = create_tag(client, domain_context["admin_headers"], device["id"])
    headers = domain_context["unassigned_headers"]
    assert (
        client.get(
            f"{settings.API_V1_STR}/production-lines/{line['id']}", headers=headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{settings.API_V1_STR}/devices/{device['id']}", headers=headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{settings.API_V1_STR}/tags/{tag['id']}", headers=headers
        ).status_code
        == 403
    )


def test_nested_list_without_parent_filter_is_access_scoped(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    accessible_line = create_line(
        client,
        domain_context["engineer_headers"],
        domain_context["plant"]["id"],
    )
    restricted_line = create_line(
        client,
        domain_context["admin_headers"],
        domain_context["restricted_plant"]["id"],
    )
    accessible_device = create_device(
        client, domain_context["engineer_headers"], accessible_line["id"]
    )
    restricted_device = create_device(
        client, domain_context["admin_headers"], restricted_line["id"]
    )
    accessible_tag = create_tag(
        client, domain_context["engineer_headers"], accessible_device["id"]
    )
    restricted_tag = create_tag(
        client, domain_context["admin_headers"], restricted_device["id"]
    )

    line_ids = {
        row["id"]
        for row in client.get(
            f"{settings.API_V1_STR}/production-lines/",
            headers=domain_context["observer_headers"],
        ).json()["data"]
    }
    device_ids = {
        row["id"]
        for row in client.get(
            f"{settings.API_V1_STR}/devices/",
            headers=domain_context["observer_headers"],
        ).json()["data"]
    }
    tag_ids = {
        row["id"]
        for row in client.get(
            f"{settings.API_V1_STR}/tags/",
            headers=domain_context["observer_headers"],
        ).json()["data"]
    }
    assert accessible_line["id"] in line_ids
    assert restricted_line["id"] not in line_ids
    assert accessible_device["id"] in device_ids
    assert restricted_device["id"] not in device_ids
    assert accessible_tag["id"] in tag_ids
    assert restricted_tag["id"] not in tag_ids


def test_duplicate_device_and_tag_codes_are_conflicts(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    headers = domain_context["engineer_headers"]
    line = create_line(client, headers, domain_context["plant"]["id"])
    device = create_device(client, headers, line["id"])
    duplicate_device = client.post(
        f"{settings.API_V1_STR}/devices/",
        headers=headers,
        json={
            "production_line_id": line["id"],
            "code": device["code"],
            "name": "Duplicate Device",
            "device_type": "furnace",
        },
    )
    assert duplicate_device.status_code == 409

    tag = create_tag(client, headers, device["id"])
    duplicate_tag = client.post(
        f"{settings.API_V1_STR}/tags/",
        headers=headers,
        json={
            "device_id": device["id"],
            "code": tag["code"],
            "name": "Duplicate Tag",
            "data_type": "float",
            "sampling_interval_ms": 1000,
        },
    )
    assert duplicate_tag.status_code == 409


def test_inactive_line_and_device_reject_children(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    headers = domain_context["engineer_headers"]
    line = create_line(client, headers, domain_context["plant"]["id"])
    client.post(
        f"{settings.API_V1_STR}/production-lines/{line['id']}/disable",
        headers=headers,
    )
    blocked_device = client.post(
        f"{settings.API_V1_STR}/devices/",
        headers=headers,
        json={
            "production_line_id": line["id"],
            "code": unique_code("DEVICE"),
            "name": "Blocked Device",
            "device_type": "furnace",
        },
    )
    assert blocked_device.status_code == 409

    active_line = create_line(client, headers, domain_context["plant"]["id"])
    device = create_device(client, headers, active_line["id"])
    client.post(
        f"{settings.API_V1_STR}/devices/{device['id']}/disable", headers=headers
    )
    blocked_tag = client.post(
        f"{settings.API_V1_STR}/tags/",
        headers=headers,
        json={
            "device_id": device["id"],
            "code": unique_code("TAG"),
            "name": "Blocked Tag",
            "data_type": "float",
            "sampling_interval_ms": 1000,
        },
    )
    assert blocked_tag.status_code == 409


def test_plant_access_can_be_read_and_revoked(
    client: TestClient, domain_context: dict[str, Any]
) -> None:
    path = f"{settings.API_V1_STR}/users/{domain_context['engineer_id']}/plant-access"
    response = client.get(path, headers=domain_context["admin_headers"])
    assert response.status_code == 200
    assert domain_context["plant"]["id"] in response.json()["plant_ids"]

    response = client.put(
        path, headers=domain_context["admin_headers"], json={"plant_ids": []}
    )
    assert response.status_code == 200
    assert response.json()["plant_ids"] == []
    denied = client.get(
        f"{settings.API_V1_STR}/plants/{domain_context['plant']['id']}",
        headers=domain_context["engineer_headers"],
    )
    assert denied.status_code == 403
