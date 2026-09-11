import csv
import hashlib
import io
import json
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.assistant.retrieval import retrieve, split_content
from app.core.config import settings
from app.models import (
    Device,
    KnowledgeDocument,
    Plant,
    ProductionLine,
    Tag,
    TagDataType,
    User,
    UserRole,
)
from app.opcua.catalog import DEMO_POINTS
from app.opcua.scenario import csv_content, disturbance, make_frames, teaching_scenario
from tests.utils.imports import finish_import


def test_replay_is_deterministic_bounded_and_not_live():
    first = make_frames()
    assert first == make_frames()
    assert len(first) == 91
    assert len(DEMO_POINTS) == 24
    assert {p.code for p in DEMO_POINTS} == set(first[0].values)
    assert first[-1].elapsed_seconds == 180
    assert sum(len(frame.bad_quality_codes) for frame in first) == 5
    assert [frame.elapsed_seconds for frame in first if frame.bad_quality_codes] == [
        90,
        92,
        94,
        96,
        98,
    ]
    assert [disturbance(t) for t in (0, 60, 70, 80, 120, 140, 160, 180)] == [
        0,
        0,
        0.5,
        1,
        1,
        0.5,
        0,
        0,
    ]
    disturbed = first[45]
    assert disturbed.values["PUMP_FLOW"] < 30
    assert disturbed.values["L2_PUMP_FLOW"] > 40
    assert disturbed.values["PUMP_PRESSURE"] > 0.75
    assert first[-1].values["PUMP_FLOW"] > 40
    scenario = teaching_scenario()
    assert (
        scenario.csv_sha256 == hashlib.sha256(csv_content(first).encode()).hexdigest()
    )
    assert "不读取、写入" in scenario.source
    assert "2026-01-15" in first[0].timestamp.isoformat()


def test_scenario_endpoints_require_login_and_csv_matches_json(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    for path in ("/api/v1/demo/scenario", "/api/v1/demo/scenario.csv"):
        assert client.get(path).status_code == 401
    result = client.get("/api/v1/demo/scenario", headers=superuser_token_headers)
    assert result.status_code == 200
    csv_response = client.get(
        "/api/v1/demo/scenario.csv", headers=superuser_token_headers
    )
    assert csv_response.status_code == 200
    assert (
        hashlib.sha256(csv_response.content).hexdigest() == result.json()["csv_sha256"]
    )
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(rows) == 2184
    assert sum(row["quality_code"] == "BadSensorFailure" for row in rows) == 5
    assert all(row["quality_code"] in {"Good", "BadSensorFailure"} for row in rows)


def test_downloaded_scenario_really_imports_with_five_quality_warnings(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "IMPORT_STORAGE_DIR", tmp_path / "imports")
    plant = Plant(
        code="SCENARIO_" + uuid.uuid4().hex[:8], name="Isolated scenario import"
    )
    db.add(plant)
    db.flush()
    devices = {}
    for number in (1, 2):
        line = ProductionLine(
            plant_id=plant.id,
            code=f"LINE{number}",
            name=f"Line {number}",
            process_type="Synthetic teaching",
        )
        db.add(line)
        db.flush()
        for point in (p for p in DEMO_POINTS if p.line == number):
            key = (number, point.device_code)
            if key not in devices:
                device = Device(
                    production_line_id=line.id,
                    code=point.device_code,
                    name=point.device_name,
                    device_type="simulator",
                )
                db.add(device)
                db.flush()
                devices[key] = device.id
            db.add(
                Tag(
                    device_id=devices[key],
                    code=point.code,
                    name=point.name,
                    data_type=TagDataType.FLOAT,
                    unit=point.unit,
                    min_value=point.minimum,
                    max_value=point.maximum,
                )
            )
    db.commit()
    content = csv_content(teaching_scenario().frames).encode()
    preview = client.post(
        "/api/v1/imports/preview",
        params={"plant_id": str(plant.id)},
        headers=superuser_token_headers,
        files={"file": ("synthetic-cooling-loop-v1.csv", content, "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    batch_id = preview.json()["batch"]["id"]
    mapping = preview.json()["suggested_mapping"]
    assert mapping["quality_column"] == "quality_code"
    queued = client.post(
        f"/api/v1/imports/{batch_id}/process",
        headers=superuser_token_headers,
        json=mapping,
    )
    assert queued.status_code == 202, queued.text
    result = finish_import(client, batch_id, superuser_token_headers).json()
    assert (
        result["accepted_rows"],
        result["rejected_rows"],
        result["warning_rows"],
    ) == (2184, 0, 5)


def test_scenario_knowledge_retrieval_uses_real_corpus_and_plant_permissions(
    db: Session,
):
    admin = db.exec(select(User).where(User.is_superuser == True)).first()  # noqa: E712
    assert admin is not None
    plant = Plant(
        code="CORPUS_" + uuid.uuid4().hex[:8], name="Isolated scenario corpus"
    )
    db.add(plant)
    db.flush()
    corpus_path = Path(__file__).parents[3] / "app/assistant"
    for filename in ("eval_cases.json", "manuals.json", "scenario_manuals.json"):
        for document in json.loads((corpus_path / filename).read_text())["documents"]:
            db.add(
                KnowledgeDocument(
                    plant_id=plant.id,
                    created_by_id=admin.id,
                    title=document["title"],
                    content=document["content"],
                    content_sha256=hashlib.sha256(
                        document["content"].encode()
                    ).hexdigest(),
                    chunks=split_content(document["content"]),
                )
            )
    db.commit()
    # Development regression cases, not held-out accuracy measurements.
    for question, title in (
        ("冷却回路回放什么时候进入恢复观察？", "二号线对照与恢复观察"),
        ("PUMP_FLOW在90秒为什么标记为坏质量？", "坏质量与过期数据不是工艺不合格"),
        ("合成CSV的timestamp和quality_code如何映射？", "合成CSV的导入映射与核验"),
        ("流量下降和压力升高能证明管路堵塞吗？", "流量下降与压力升高如何解释"),
        (
            "回放数据是否会自动写入实际采集数据库？",
            "演示回放、实际采集、历史文件的区别",
        ),
        ("两线六设备二十四点分别测量什么，单位是什么？", "两线六设备二十四点字典"),
    ):
        hits, mode = retrieve(
            session=db,
            user=admin,
            plant_id=plant.id,
            query=question,
            strategy="lexical",
        )
        assert mode == "lexical"
        assert any(title in hit.title for hit in hits), (
            question,
            [hit.title for hit in hits],
        )
    unassigned = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        hashed_password="not-a-login",
        role=UserRole.OBSERVER,
    )
    with pytest.raises(HTTPException) as error:
        retrieve(session=db, user=unassigned, plant_id=plant.id, query="冷却回路")
    assert error.value.status_code == 403
    hits, _ = retrieve(
        session=db,
        user=admin,
        plant_id=plant.id,
        query="zzzxxyy987654",
        strategy="lexical",
    )
    assert hits == []
