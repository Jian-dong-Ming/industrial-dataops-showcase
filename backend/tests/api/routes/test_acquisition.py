import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.models import (
    AcquisitionTask,
    OpcUaBrowseNodePublic,
    TagSample,
    UserRole,
)
from tests.api.routes.test_industrial_domain import (
    assign_plant,
    create_device,
    create_line,
    create_plant,
    create_role_headers,
    create_tag,
)


@pytest.fixture()
def acquisition_context(
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
    _, unassigned_headers = create_role_headers(
        client=client, db=db, role=UserRole.ENGINEER
    )
    plant = create_plant(client, superuser_token_headers)
    restricted_plant = create_plant(client, superuser_token_headers)
    assign_plant(client, superuser_token_headers, engineer_id, plant["id"])
    assign_plant(client, superuser_token_headers, observer_id, plant["id"])
    line = create_line(client, engineer_headers, plant["id"])
    device = create_device(client, engineer_headers, line["id"])
    tag = create_tag(client, engineer_headers, device["id"])
    return {
        "admin_headers": superuser_token_headers,
        "engineer_headers": engineer_headers,
        "observer_headers": observer_headers,
        "unassigned_headers": unassigned_headers,
        "plant": plant,
        "restricted_plant": restricted_plant,
        "device": device,
        "tag": tag,
    }


def task_payload(
    context: dict[str, Any], *, name: str = "合成采集任务"
) -> dict[str, Any]:
    return {
        "plant_id": context["plant"]["id"],
        "name": name,
        "endpoint_url": ("opc.tcp://opcua-simulator:4840/industrial-dataops/server/"),
        "publishing_interval_ms": 500,
        "batch_size": 20,
        "reconnect_delay_seconds": 1,
        "max_reconnect_delay_seconds": 10,
        "nodes": [
            {
                "tag_id": context["tag"]["id"],
                "node_id": "ns=2;s=Line1.Furnace01.Temperature",
                "is_enabled": True,
            }
        ],
    }


def create_task(
    client: TestClient, context: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any]:
    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks",
        headers=headers or context["engineer_headers"],
        json=task_payload(context, name=f"任务-{uuid.uuid4().hex[:8]}"),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_engineer_can_create_read_update_start_and_stop_task(
    client: TestClient, acquisition_context: dict[str, Any]
) -> None:
    task = create_task(client, acquisition_context)
    assert task["connection_state"] == "stopped"
    assert task["nodes"][0]["tag_code"] == acquisition_context["tag"]["code"]

    response = client.patch(
        f"{settings.API_V1_STR}/acquisition/tasks/{task['id']}",
        headers=acquisition_context["engineer_headers"],
        json={"batch_size": 50},
    )
    assert response.status_code == 200
    assert response.json()["batch_size"] == 50

    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks/{task['id']}/start",
        headers=acquisition_context["engineer_headers"],
    )
    assert response.status_code == 200
    assert response.json()["desired_state"] == "running"

    response = client.patch(
        f"{settings.API_V1_STR}/acquisition/tasks/{task['id']}",
        headers=acquisition_context["engineer_headers"],
        json={"batch_size": 100},
    )
    assert response.status_code == 409

    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks/{task['id']}/stop",
        headers=acquisition_context["engineer_headers"],
    )
    assert response.status_code == 200
    assert response.json()["desired_state"] == "stopped"
    assert response.json()["connection_state"] == "stopped"

    response = client.post(
        f"{settings.API_V1_STR}/devices/{acquisition_context['device']['id']}/disable",
        headers=acquisition_context["engineer_headers"],
    )
    assert response.status_code == 200

    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks/{task['id']}/start",
        headers=acquisition_context["engineer_headers"],
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Mapped tag asset hierarchy is inactive"


def test_task_permissions_and_list_scope(
    client: TestClient, acquisition_context: dict[str, Any]
) -> None:
    task = create_task(client, acquisition_context)
    response = client.get(
        f"{settings.API_V1_STR}/acquisition/tasks/{task['id']}",
        headers=acquisition_context["observer_headers"],
    )
    assert response.status_code == 200

    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks/{task['id']}/start",
        headers=acquisition_context["observer_headers"],
    )
    assert response.status_code == 403

    response = client.get(
        f"{settings.API_V1_STR}/acquisition/tasks/{task['id']}",
        headers=acquisition_context["unassigned_headers"],
    )
    assert response.status_code == 403

    response = client.get(
        f"{settings.API_V1_STR}/acquisition/tasks",
        headers=acquisition_context["observer_headers"],
    )
    assert response.status_code == 200
    assert {row["id"] for row in response.json()["data"]} == {task["id"]}


def test_task_validation_rejects_unsafe_endpoint_and_duplicate_mapping(
    client: TestClient, acquisition_context: dict[str, Any]
) -> None:
    payload = task_payload(acquisition_context)
    payload["endpoint_url"] = "opc.tcp://example.com:4840/server/"
    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks",
        headers=acquisition_context["engineer_headers"],
        json=payload,
    )
    assert response.status_code == 422
    assert "not allowed" in response.json()["detail"]

    payload = task_payload(acquisition_context)
    payload["nodes"].append(payload["nodes"][0].copy())
    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks",
        headers=acquisition_context["engineer_headers"],
        json=payload,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "A tag can only be mapped once"

    payload = task_payload(acquisition_context)
    payload["reconnect_delay_seconds"] = 20
    payload["max_reconnect_delay_seconds"] = 10
    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks",
        headers=acquisition_context["engineer_headers"],
        json=payload,
    )
    assert response.status_code == 422


def test_browse_requires_write_access_and_returns_variables(
    client: TestClient,
    acquisition_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_browse(**_: Any) -> tuple[list[OpcUaBrowseNodePublic], bool]:
        return (
            [
                OpcUaBrowseNodePublic(
                    node_id="ns=2;s=Temperature",
                    browse_name="Temperature",
                    display_name="炉温",
                    data_type="Double",
                )
            ],
            False,
        )

    monkeypatch.setattr("app.api.routes.acquisition.browse_variable_nodes", fake_browse)
    payload = {
        "plant_id": acquisition_context["plant"]["id"],
        "endpoint_url": "opc.tcp://opcua-simulator:4840/server/",
    }
    response = client.post(
        f"{settings.API_V1_STR}/acquisition/browse",
        headers=acquisition_context["engineer_headers"],
        json=payload,
    )
    assert response.status_code == 200
    assert response.json()["nodes"][0]["display_name"] == "炉温"

    response = client.post(
        f"{settings.API_V1_STR}/acquisition/browse",
        headers=acquisition_context["observer_headers"],
        json=payload,
    )
    assert response.status_code == 403


def test_sample_query_is_ordered_and_plant_scoped(
    client: TestClient,
    db: Session,
    acquisition_context: dict[str, Any],
) -> None:
    task = create_task(client, acquisition_context)
    task_id = uuid.UUID(task["id"])
    tag_id = uuid.UUID(acquisition_context["tag"]["id"])
    now = datetime.now(UTC)
    db.add_all(
        [
            TagSample(
                task_id=task_id,
                tag_id=tag_id,
                value=value,
                numeric_value=float(value),
                source_timestamp=now + timedelta(seconds=index),
                server_timestamp=now + timedelta(seconds=index),
                status_code="Good",
                is_good=True,
            )
            for index, value in enumerate((10, 20, 30))
        ]
    )
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/acquisition/tags/{tag_id}/samples",
        headers=acquisition_context["observer_headers"],
        params={"limit": 2},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert [row["value"] for row in response.json()["data"]] == [20, 30]

    response = client.get(
        f"{settings.API_V1_STR}/acquisition/tags/{tag_id}/samples",
        headers=acquisition_context["unassigned_headers"],
    )
    assert response.status_code == 403

    stored_task = db.get(AcquisitionTask, task_id)
    assert stored_task is not None


def test_sample_query_filters_task_without_mixing_sources(
    client: TestClient,
    db: Session,
    acquisition_context: dict[str, Any],
) -> None:
    context = acquisition_context
    first = create_task(client, context)
    # A tag can move to a new task while its old task's history is retained.
    replacement = create_tag(
        client, context["engineer_headers"], context["device"]["id"]
    )
    remapped = client.patch(
        f"{settings.API_V1_STR}/acquisition/tasks/{first['id']}",
        headers=context["engineer_headers"],
        json={
            "nodes": [{"tag_id": replacement["id"], "node_id": "ns=2;s=replacement"}]
        },
    )
    assert remapped.status_code == 200
    second = create_task(client, context)
    tag_id = uuid.UUID(context["tag"]["id"])
    now = datetime.now(UTC)
    for task, value in ((first, 10), (second, 999)):
        db.add(
            TagSample(
                task_id=uuid.UUID(task["id"]),
                tag_id=tag_id,
                value=value,
                numeric_value=float(value),
                source_timestamp=now,
                server_timestamp=now,
                status_code="Good",
                is_good=True,
            )
        )
    db.commit()
    url = f"{settings.API_V1_STR}/acquisition/tags/{tag_id}/samples"
    response = client.get(
        url, headers=context["observer_headers"], params={"task_id": first["id"]}
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert [row["value"] for row in response.json()["data"]] == [10]
    assert (
        client.get(
            url, headers=context["unassigned_headers"], params={"task_id": first["id"]}
        ).status_code
        == 403
    )
    assert (
        client.get(
            url,
            headers=context["observer_headers"],
            params={"task_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )

    # Access to the tag does not confer access to a task in another factory.
    restricted = AcquisitionTask(
        name="restricted",
        plant_id=uuid.UUID(context["restricted_plant"]["id"]),
        endpoint_url="opc.tcp://opcua-simulator:4840/",
    )
    db.add(restricted)
    db.commit()
    assert (
        client.get(
            url,
            headers=context["observer_headers"],
            params={"task_id": str(restricted.id)},
        ).status_code
        == 403
    )
    assert (
        client.get(
            url,
            headers=context["admin_headers"],
            params={"task_id": str(restricted.id)},
        ).status_code
        == 422
    )


def test_latest_values_returns_readable_metadata_and_empty_tags(
    client: TestClient,
    db: Session,
    acquisition_context: dict[str, Any],
) -> None:
    second_tag = create_tag(
        client,
        acquisition_context["engineer_headers"],
        acquisition_context["device"]["id"],
        code=f"PRESSURE_{uuid.uuid4().hex[:8].upper()}",
    )
    payload = task_payload(acquisition_context, name=f"任务-{uuid.uuid4().hex[:8]}")
    payload["nodes"].append(
        {
            "tag_id": second_tag["id"],
            "node_id": "ns=2;s=Line1.Furnace01.Pressure",
            "is_enabled": True,
        }
    )
    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks",
        headers=acquisition_context["engineer_headers"],
        json=payload,
    )
    assert response.status_code == 200, response.text
    task = response.json()

    task_id = uuid.UUID(task["id"])
    first_tag_id = uuid.UUID(acquisition_context["tag"]["id"])
    now = datetime.now(UTC)
    db.add_all(
        [
            TagSample(
                task_id=task_id,
                tag_id=first_tag_id,
                value=value,
                numeric_value=float(value),
                source_timestamp=timestamp,
                server_timestamp=timestamp,
                status_code="Good",
                is_good=True,
            )
            for value, timestamp in (
                (30, now + timedelta(seconds=30)),
                (10, now + timedelta(seconds=10)),
            )
        ]
    )
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/acquisition/tasks/{task_id}/latest-values",
        headers=acquisition_context["observer_headers"],
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["count"] == 2
    by_tag_id = {row["tag_id"]: row for row in result["data"]}

    latest = by_tag_id[str(first_tag_id)]
    assert latest["tag_code"] == acquisition_context["tag"]["code"]
    assert latest["tag_name"] == acquisition_context["tag"]["name"]
    assert latest["unit"] == "degC"
    assert latest["sampling_interval_ms"] == 1000
    assert latest["value"] == 30
    assert latest["numeric_value"] == 30
    assert latest["is_good"] is True

    empty = by_tag_id[second_tag["id"]]
    assert empty["tag_code"] == second_tag["code"]
    assert empty["sample_id"] is None
    assert empty["source_timestamp"] is None
    assert empty["is_good"] is None

    response = client.get(
        f"{settings.API_V1_STR}/acquisition/tasks/{task_id}/latest-values",
        headers=acquisition_context["unassigned_headers"],
    )
    assert response.status_code == 403
