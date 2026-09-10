# ruff: noqa: ARG001
import uuid
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, func, select

from app.api.routes import data_lifecycle as lifecycle
from app.core.config import settings
from app.models import (
    AcquisitionTask,
    Device,
    ImportBatch,
    ImportFileFormat,
    ProductionLine,
    RetentionPreview,
    SampleSourceType,
    Tag,
    TagSample,
    UserPlantAccess,
    UserRole,
    get_datetime_utc,
)
from tests.api.routes.test_assistant import scope as scope

PREFIX = f"{settings.API_V1_STR}/data-lifecycle"


@pytest.fixture
def samples(db: Session, scope: tuple) -> tuple[list[TagSample], ImportBatch]:
    plant, other, user, _ = scope
    rows = []
    batch = ImportBatch(
        plant_id=plant.id,
        created_by_id=user.id,
        original_filename="retention.csv",
        file_format=ImportFileFormat.CSV,
        file_sha256="e" * 64,
        file_size_bytes=100,
    )
    db.add(batch)
    db.commit()
    now = get_datetime_utc()
    for owner in (plant, other):
        line = ProductionLine(
            plant_id=owner.id, code="LINE", name="范围测试线", process_type="continuous"
        )
        db.add(line)
        db.flush()
        device = Device(
            production_line_id=line.id,
            code="DEV",
            name="范围测试设备",
            device_type="simulator",
        )
        db.add(device)
        db.flush()
        tag = Tag(device_id=device.id, code="TEMP", name="温度")
        task = AcquisitionTask(
            plant_id=owner.id, name="范围测试", endpoint_url="opc.tcp://simulator:4840"
        )
        db.add(tag)
        db.add(task)
        db.flush()
        for days in (30, 20, 10, 1):
            row = TagSample(
                task_id=task.id,
                tag_id=tag.id,
                source_timestamp=now - timedelta(days=days),
                received_at=now,
                value=days,
                numeric_value=float(days),
                status_code="Good",
                is_good=True,
            )
            db.add(row)
            rows.append(row)
        if owner.id == plant.id:
            row = TagSample(
                import_batch_id=batch.id,
                source_type=SampleSourceType.FILE,
                tag_id=tag.id,
                source_timestamp=now - timedelta(days=40),
                received_at=now,
                value=42,
                numeric_value=42,
                status_code="Good",
                is_good=True,
            )
            db.add(row)
            rows.append(row)
        db.commit()
    return rows, batch


def body(scope: tuple, **overrides: object) -> dict:
    return {
        "plant_id": str(scope[0].id),
        "source_type": "opcua",
        "keep_days": 7,
        **overrides,
    }


def test_preview_scope_source_time_and_no_deletion(
    client: TestClient,
    db: Session,
    scope: tuple,
    samples: tuple,
) -> None:
    plant, other, user, headers = scope
    rows, batch = samples
    snapshot = [
        (row.id, row.value, row.source_timestamp, row.received_at) for row in rows
    ]
    response = client.post(f"{PREFIX}/previews", headers=headers, json=body(scope))
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["matched_rows"] == 3
    assert result["count_is_exact"] is True
    assert result["affected_tags_in_scan"] == 1
    assert result["high_water_id"] == rows[3].id
    assert result["action_performed"] == "preview_only"
    assert "源采样时间" in result["note"]
    assert result["oldest_in_scan"] < result["newest_in_scan"] < result["cutoff"]
    record = db.get(RetentionPreview, uuid.UUID(result["id"]))
    assert record and record.requested_by_id == user.id
    file_result = client.post(
        f"{PREFIX}/previews", headers=headers, json=body(scope, source_type="file")
    )
    assert file_result.status_code == 201
    assert file_result.json()["matched_rows"] == 1
    assert file_result.json()["high_water_id"] == rows[4].id
    listing = client.get(
        f"{PREFIX}/previews", headers=headers, params={"plant_id": str(plant.id)}
    )
    assert listing.status_code == 200 and len(listing.json()) == 2
    assert (
        client.get(f"{PREFIX}/previews/{result['id']}", headers=headers).json()["id"]
        == result["id"]
    )
    assert (
        client.delete(f"{PREFIX}/previews/{result['id']}", headers=headers).status_code
        == 405
    )
    assert (
        client.get(
            f"{PREFIX}/previews", headers=headers, params={"plant_id": str(other.id)}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{PREFIX}/previews",
            headers=headers,
            json=body(scope, plant_id=str(other.id)),
        ).status_code
        == 403
    )
    db.expire_all()
    assert snapshot == [
        (row.id, row.value, row.source_timestamp, row.received_at) for row in rows
    ]
    assert db.get(ImportBatch, batch.id) is not None


def test_scan_cap_and_empty_scope(
    client: TestClient,
    scope: tuple,
    samples: tuple,
    monkeypatch: pytest.MonkeyPatch,
    superuser_token_headers: dict[str, str],
) -> None:
    monkeypatch.setattr(lifecycle, "PREVIEW_ROW_LIMIT", 2)
    response = client.post(f"{PREFIX}/previews", headers=scope[3], json=body(scope))
    assert response.status_code == 201
    assert response.json()["matched_rows"] == 3
    assert response.json()["count_is_exact"] is False
    assert response.json()["scan_limit"] == 2
    empty = client.post(
        f"{PREFIX}/previews",
        headers=superuser_token_headers,
        json=body(scope, plant_id=str(scope[1].id), source_type="file"),
    )
    assert empty.status_code == 201
    assert empty.json()["matched_rows"] == 0 and empty.json()["high_water_id"] == 0
    assert empty.json()["oldest_in_scan"] is None
    assert (
        client.get(
            f"{PREFIX}/previews/{empty.json()['id']}", headers=scope[3]
        ).status_code
        == 403
    )


def test_observer_and_validation(
    client: TestClient,
    db: Session,
    scope: tuple,
    superuser_token_headers: dict[str, str],
) -> None:
    headers = scope[3]
    for invalid in (
        {"source_type": "all"},
        {"keep_days": 0},
        {"keep_days": 3651},
        {"delete": True},
    ):
        assert (
            client.post(
                f"{PREFIX}/previews", headers=headers, json=body(scope, **invalid)
            ).status_code
            == 422
        )
    assert (
        client.get(f"{PREFIX}/previews/{uuid.uuid4()}", headers=headers).status_code
        == 404
    )
    assert (
        client.post(
            f"{PREFIX}/previews",
            headers=superuser_token_headers,
            json=body(scope, plant_id=str(uuid.uuid4())),
        ).status_code
        == 404
    )
    scope[2].role = UserRole.OBSERVER
    db.add(scope[2])
    db.commit()
    assert (
        client.post(f"{PREFIX}/previews", headers=headers, json=body(scope)).status_code
        == 403
    )
    assert (
        client.get(
            f"{PREFIX}/previews", headers=headers, params={"plant_id": str(scope[0].id)}
        ).status_code
        == 200
    )
    assert client.get(f"{PREFIX}/capacity", headers=headers).status_code == 403


def test_capacity_actual_statistics_and_unknown_estimate(
    client: TestClient,
    scope: tuple,
    superuser_token_headers: dict[str, str],
) -> None:
    assert client.get(f"{PREFIX}/capacity", headers=scope[3]).status_code == 403
    response = client.get(
        f"{PREFIX}/capacity",
        headers=superuser_token_headers,
        params={"planning_rows_per_second": 0},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["database_bytes"] > 0
    assert result["sample_total_bytes"] >= result["sample_index_bytes"] >= 0
    assert result["host_free_bytes"] is None
    assert result["estimated_daily_bytes"] in (None, 0)
    for invalid in ("NaN", "inf", "-1", "1000001"):
        assert (
            client.get(
                f"{PREFIX}/capacity",
                headers=superuser_token_headers,
                params={"planning_rows_per_second": invalid},
            ).status_code
            == 422
        )
    fake = MagicMock()
    fake.connection.return_value.execute.return_value.mappings.return_value.one.return_value = {
        "database_bytes": 8192,
        "sample_total_bytes": 4096,
        "sample_index_bytes": 1024,
        "estimated_live_rows": 0,
        "last_analyze": None,
    }
    admin = MagicMock(is_superuser=True)
    unknown = lifecycle.read_capacity(
        session=fake, current_user=admin, planning_rows_per_second=24
    )
    assert (
        unknown.estimated_bytes_per_row is None
        and unknown.estimated_daily_bytes is None
    )


def test_permission_rechecked_before_audit_commit(
    client: TestClient,
    db: Session,
    scope: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = lifecycle.require_plant_access
    calls = 0

    def revoke_before_second_check(**kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            grant = db.exec(
                select(UserPlantAccess).where(UserPlantAccess.user_id == scope[2].id)
            ).one()
            db.delete(grant)
            db.commit()
        original(**kwargs)

    monkeypatch.setattr(lifecycle, "require_plant_access", revoke_before_second_check)
    assert (
        client.post(
            f"{PREFIX}/previews", headers=scope[3], json=body(scope)
        ).status_code
        == 403
    )
    assert calls == 2
    assert (
        db.exec(
            select(func.count())
            .select_from(RetentionPreview)
            .where(RetentionPreview.plant_id == scope[0].id)
        ).one()
        == 0
    )


def test_timeout_rolls_back_without_audit(
    client: TestClient,
    db: Session,
    scope: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class QueryCanceled(Exception):
        sqlstate = "57014"

    original = Connection.execute

    def fail_preview(
        connection: Connection, statement: object, *args: object, **kwargs: object
    ) -> object:
        if "SET LOCAL statement_timeout" in str(statement):
            raise DBAPIError("preview", None, QueryCanceled())
        return original(connection, statement, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", fail_preview)
    response = client.post(f"{PREFIX}/previews", headers=scope[3], json=body(scope))
    assert response.status_code == 503
    assert "未删除" in response.json()["detail"]
    assert (
        db.exec(
            select(func.count())
            .select_from(RetentionPreview)
            .where(RetentionPreview.plant_id == scope[0].id)
        ).one()
        == 0
    )
