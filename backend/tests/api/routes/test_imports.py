import io
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlmodel import Session, func, select

from app.core.config import settings
from app.data_import.processor import process_import_batch
from app.models import (
    ImportBatch,
    ImportFieldMapping,
    ImportMappingInput,
    Tag,
    TagDataType,
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
from tests.utils.imports import finish_import


@pytest.fixture()
def import_context(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    monkeypatch.setattr(settings, "IMPORT_STORAGE_DIR", tmp_path / "imports")
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
    assign_plant(client, superuser_token_headers, engineer_id, plant["id"])
    assign_plant(client, superuser_token_headers, observer_id, plant["id"])
    line = create_line(client, engineer_headers, plant["id"])
    device = create_device(client, engineer_headers, line["id"])
    tag = create_tag(client, engineer_headers, device["id"], code="TEMP")
    return {
        "plant": plant,
        "device": device,
        "tag": tag,
        "engineer_headers": engineer_headers,
        "observer_headers": observer_headers,
        "unassigned_headers": unassigned_headers,
    }


def _preview_csv(client: TestClient, context: dict[str, Any], content: str) -> Any:
    return client.post(
        f"{settings.API_V1_STR}/imports/preview",
        params={"plant_id": context["plant"]["id"]},
        headers=context["engineer_headers"],
        files={"file": ("history.csv", content.encode("utf-8-sig"), "text/csv")},
    )


def test_bulk_insert_preserves_mixed_types_nulls_quality_and_provenance(
    client: TestClient,
    db: Session,
    import_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "IMPORT_CHUNK_SIZE", 2)
    headers = import_context["engineer_headers"]
    for code, kind in (
        ("COUNT", TagDataType.INTEGER),
        ("SWITCH", TagDataType.BOOLEAN),
        ("LABEL", TagDataType.STRING),
    ):
        created = create_tag(client, headers, import_context["device"]["id"], code=code)
        tag = db.get(Tag, uuid.UUID(created["id"]))
        assert tag is not None
        tag.data_type = kind
        db.add(tag)
    db.commit()
    preview = _preview_csv(
        client,
        import_context,
        "timestamp,tag_code,value,quality\n"
        "2026-08-22T10:00:00+08:00,TEMP,12.5,Good\n"
        "2026-08-22T10:00:00+08:00,LABEL,正常,Bad\n"
        "2026-08-22T10:00:00+08:00,COUNT,7,Good\n"
        "2026-08-22T10:00:00+08:00,SWITCH,false,Good\n"
        "2026-08-22T10:00:00+08:00,TEMP,99,Good\n",
    ).json()
    batch_id = preview["batch"]["id"]
    response = client.post(
        f"{settings.API_V1_STR}/imports/{batch_id}/process",
        headers=headers,
        json=preview["suggested_mapping"],
    )
    assert response.status_code == 202
    result = finish_import(client, batch_id, headers).json()
    assert (
        result["accepted_rows"],
        result["duplicate_rows"],
        result["warning_rows"],
    ) == (4, 1, 1)
    rows = db.exec(
        select(TagSample, Tag.code)
        .join(Tag, Tag.id == TagSample.tag_id)
        .where(TagSample.import_batch_id == uuid.UUID(batch_id))
        .order_by(Tag.code)
    ).all()
    assert [
        (code, sample.value, sample.numeric_value, sample.is_good)
        for sample, code in rows
    ] == [
        ("COUNT", 7, 7.0, True),
        ("LABEL", "正常", None, False),
        ("SWITCH", False, 0.0, True),
        ("TEMP", 12.5, 12.5, True),
    ]
    for sample, _ in rows:
        assert sample.task_id is None and sample.server_timestamp is None
        assert sample.source_type == "file" and sample.import_batch_id == uuid.UUID(
            batch_id
        )
        assert sample.source_timestamp.isoformat() == "2026-08-22T02:00:00+00:00"


def test_csv_import_validates_rows_tracks_provenance_and_exports_issues(
    client: TestClient, db: Session, import_context: dict[str, Any]
) -> None:
    device_code = import_context["device"]["code"]
    csv_content = "\n".join(
        [
            "时间,设备编码,测点编码,值,质量码",
            f"2026-08-22 10:00:00,{device_code},TEMP,100,Good",
            f"2026-08-22 10:00:01,{device_code},TEMP,101,Bad",
            f"2026-08-22 10:00:02,{device_code},TEMP,2000,Good",
            f"2026-08-22 10:00:03,{device_code},UNKNOWN,102,Good",
            f"2026-08-22 10:00:00,{device_code},TEMP,103,Good",
        ]
    )
    response = _preview_csv(client, import_context, csv_content)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["source_columns"] == ["时间", "设备编码", "测点编码", "值", "质量码"]
    assert preview["suggested_mapping"] == {
        "layout": "long",
        "timestamp_column": "时间",
        "tag_code_column": "测点编码",
        "value_column": "值",
        "device_code_column": "设备编码",
        "quality_column": "质量码",
        "wide_columns": [],
    }
    batch_id = preview["batch"]["id"]
    assert preview["batch"]["source_file_available"] is True

    response = client.post(
        f"{settings.API_V1_STR}/imports/{batch_id}/process",
        headers=import_context["engineer_headers"],
        json=preview["suggested_mapping"],
    )
    assert response.status_code == 202, response.text
    response = finish_import(client, batch_id, import_context["engineer_headers"])
    batch = response.json()
    assert batch["status"] == "completed"
    assert batch["total_rows"] == 5
    assert batch["accepted_rows"] == 2
    assert batch["rejected_rows"] == 3
    assert batch["duplicate_rows"] == 1
    assert batch["warning_rows"] == 1
    assert batch["issue_count"] == 4

    response = client.get(
        f"{settings.API_V1_STR}/imports/{batch_id}/issue-summary",
        headers=import_context["observer_headers"],
    )
    assert response.status_code == 200
    assert sum(item["count"] for item in response.json()["data"]) == 4

    response = client.get(
        f"{settings.API_V1_STR}/imports/{batch_id}/issues.csv",
        headers=import_context["observer_headers"],
    )
    assert response.status_code == 200
    assert "测点编码" in response.content.decode("utf-8-sig")

    readable = db.execute(
        text(
            "SELECT source_type, import_filename, tag_code, numeric_value "
            "FROM v_tag_sample_readable "
            "WHERE import_batch_id = :batch_id ORDER BY source_timestamp"
        ),
        {"batch_id": batch_id},
    ).all()
    assert readable == [
        ("file", "history.csv", "TEMP", 100.0),
        ("file", "history.csv", "TEMP", 101.0),
    ]

    response = _preview_csv(client, import_context, csv_content)
    assert response.status_code == 200
    duplicate = response.json()["batch"]
    assert duplicate["status"] == "duplicate"
    assert duplicate["duplicate_of_id"] == batch_id
    assert duplicate["source_file_available"] is False


def test_xlsx_preview_and_permissions(
    client: TestClient, import_context: dict[str, Any]
) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "历史数据"
    worksheet.append(["timestamp", "tag_code", "value"])
    worksheet.append(["2026-08-22T11:00:00+08:00", "TEMP", 120.5])
    content = io.BytesIO()
    workbook.save(content)
    workbook.close()

    response = client.post(
        f"{settings.API_V1_STR}/imports/preview",
        params={"plant_id": import_context["plant"]["id"]},
        headers=import_context["observer_headers"],
        files={
            "file": (
                "history.xlsx",
                content.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 403

    response = client.post(
        f"{settings.API_V1_STR}/imports/preview",
        params={"plant_id": import_context["plant"]["id"]},
        headers=import_context["engineer_headers"],
        files={
            "file": (
                "history.xlsx",
                content.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["batch"]["sheet_name"] == "历史数据"
    assert preview["suggested_mapping"]["timestamp_column"] == "timestamp"

    response = client.get(
        f"{settings.API_V1_STR}/imports/{preview['batch']['id']}",
        headers=import_context["unassigned_headers"],
    )
    assert response.status_code == 403


def test_source_file_can_be_removed_without_deleting_results(
    client: TestClient, import_context: dict[str, Any]
) -> None:
    content = "timestamp,tag_code,value\n2026-08-22T12:00:00+08:00,TEMP,130"
    preview = _preview_csv(client, import_context, content).json()
    batch_id = preview["batch"]["id"]
    response = client.post(
        f"{settings.API_V1_STR}/imports/{batch_id}/process",
        headers=import_context["engineer_headers"],
        json=preview["suggested_mapping"],
    )
    assert response.status_code == 202
    assert (
        client.delete(
            f"{settings.API_V1_STR}/imports/{batch_id}/source-file",
            headers=import_context["engineer_headers"],
        ).status_code
        == 409
    )
    finish_import(client, batch_id, import_context["engineer_headers"])

    response = client.delete(
        f"{settings.API_V1_STR}/imports/{batch_id}/source-file",
        headers=import_context["engineer_headers"],
    )
    assert response.status_code == 200
    response = client.get(
        f"{settings.API_V1_STR}/imports/{batch_id}",
        headers=import_context["observer_headers"],
    )
    assert response.json()["source_file_available"] is False
    assert response.json()["accepted_rows"] == 1


def test_missing_mapping_column_marks_batch_failed(
    client: TestClient, db: Session, import_context: dict[str, Any]
) -> None:
    content = "timestamp,tag_code,value\n2026-08-22T13:00:00+08:00,TEMP,140"
    preview = _preview_csv(client, import_context, content).json()
    response = client.post(
        f"{settings.API_V1_STR}/imports/{preview['batch']['id']}/process",
        headers=import_context["engineer_headers"],
        json={
            **preview["suggested_mapping"],
            "value_column": "missing_value_column",
        },
    )
    assert response.status_code == 202
    finish_import(client, preview["batch"]["id"], import_context["engineer_headers"])
    db.expire_all()
    batch = db.get(ImportBatch, preview["batch"]["id"])
    assert batch is not None
    assert batch.status.value == "failed"
    assert "映射列不存在" in (batch.error_message or "")


def test_unexpected_midstream_failure_rolls_back_all_samples(
    client: TestClient,
    db: Session,
    import_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = "timestamp,tag_code,value\n2026-08-22T14:00:00+08:00,TEMP,150"
    preview = _preview_csv(client, import_context, content).json()
    batch = db.get(ImportBatch, preview["batch"]["id"])
    assert batch is not None

    def failing_rows(*_: Any, **__: Any) -> Any:
        yield (
            2,
            {
                "timestamp": "2026-08-22T14:00:00+08:00",
                "tag_code": "TEMP",
                "value": "150",
            },
        )
        raise RuntimeError("synthetic midstream failure")

    monkeypatch.setattr(
        "app.data_import.processor.iter_tabular_rows",
        failing_rows,
    )
    monkeypatch.setattr(settings, "IMPORT_CHUNK_SIZE", 1)
    with pytest.raises(RuntimeError, match="synthetic midstream failure"):
        process_import_batch(
            session=db,
            batch=batch,
            mapping=ImportMappingInput(**preview["suggested_mapping"]),
        )
    db.expire_all()
    failed = db.get(ImportBatch, batch.id)
    assert failed is not None
    assert failed.status.value == "failed"
    sample_count = db.exec(
        select(func.count())
        .select_from(TagSample)
        .where(TagSample.import_batch_id == batch.id)
    ).one()
    assert sample_count == 0


def test_wide_table_maps_measurement_columns_to_enabled_tags(
    client: TestClient, db: Session, import_context: dict[str, Any]
) -> None:
    second_tag = create_tag(
        client,
        import_context["engineer_headers"],
        import_context["device"]["id"],
        code="PRESSURE",
    )
    content = "\n".join(
        [
            "timestamp,temperature_column,pressure_column,batch_no",
            "2026-08-22T16:00:00+08:00,100,20,B001",
            "2026-08-22T16:00:01+08:00,101,21,B001",
        ]
    )
    preview = _preview_csv(client, import_context, content).json()
    assert preview["detected_layout"] == "wide"
    assert preview["suggested_timestamp_column"] == "timestamp"
    assert preview["suggested_mapping"] is None

    options_response = client.get(
        f"{settings.API_V1_STR}/imports/mapping-options",
        params={"plant_id": import_context["plant"]["id"]},
        headers=import_context["engineer_headers"],
    )
    assert options_response.status_code == 200
    assert {item["tag_code"] for item in options_response.json()} == {
        "TEMP",
        "PRESSURE",
    }

    mapping = {
        "layout": "wide",
        "timestamp_column": "timestamp",
        "quality_column": None,
        "wide_columns": [
            {
                "source_column": "temperature_column",
                "tag_code": "TEMP",
                "device_code": import_context["device"]["code"],
            },
            {
                "source_column": "pressure_column",
                "tag_code": second_tag["code"],
                "device_code": import_context["device"]["code"],
            },
        ],
    }
    response = client.post(
        f"{settings.API_V1_STR}/imports/{preview['batch']['id']}/process",
        headers=import_context["engineer_headers"],
        json=mapping,
    )
    assert response.status_code == 202, response.text
    response = finish_import(
        client, preview["batch"]["id"], import_context["engineer_headers"]
    )
    batch = response.json()
    assert batch["status"] == "completed"
    assert batch["total_rows"] == 4
    assert batch["accepted_rows"] == 4
    assert batch["rejected_rows"] == 0
    assert batch["mapping_config"]["layout"] == "wide"
    assert len(batch["mapping_config"]["wide_columns"]) == 2

    field_mappings = db.exec(
        select(ImportFieldMapping).where(
            ImportFieldMapping.batch_id == preview["batch"]["id"]
        )
    ).all()
    assert {item.source_column for item in field_mappings} == {
        "timestamp",
        "temperature_column",
        "pressure_column",
    }
