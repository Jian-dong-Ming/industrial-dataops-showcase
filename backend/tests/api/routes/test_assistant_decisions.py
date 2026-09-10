# ruff: noqa: ARG001
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.assistant.answer_checks import (
    attach_data_cautions,
    conditional_conclusion_error,
)
from app.assistant.schemas import Evidence, GeneratedAnswer
from app.assistant.tools import execute_tool
from app.core.config import settings
from app.models import AssistantRun, Device, ProductionLine, Tag
from tests.api.routes.test_assistant import fake_provider as fake_provider
from tests.api.routes.test_assistant import scope as scope


def test_asset_totals_include_empty_lines_disabled_tags_and_capped_details(
    db: Session, scope: tuple
) -> None:
    plant, other, user, _ = scope

    def read() -> dict:
        return execute_tool(
            session=db,
            user=user,
            plant_id=plant.id,
            name="asset_overview",
            arguments="{}",
        )

    empty = read()
    assert (empty["line_count"], empty["device_count"], empty["tag_count"]) == (0, 0, 0)
    assert empty["devices"] == [] and empty["truncated"] is False
    for owner in (plant, other):
        line = ProductionLine(
            plant_id=owner.id, code="FULL", name="有设备", process_type="continuous"
        )
        empty_line = ProductionLine(
            plant_id=owner.id, code="EMPTY", name="空产线", process_type="continuous"
        )
        db.add_all([line, empty_line])
        db.flush()
        for index in range(35):
            device = Device(
                production_line_id=line.id,
                code=f"DEV_{index}",
                name=f"设备{index}",
                device_type="simulator",
            )
            db.add(device)
            db.flush()
            # One empty device, all others two tags (one disabled).
            if index:
                db.add_all(
                    [
                        Tag(
                            device_id=device.id,
                            code=f"T_{j}",
                            name=f"测点{j}",
                            is_enabled=j == 0,
                        )
                        for j in range(2)
                    ]
                )
    db.commit()
    data = read()
    assert (data["line_count"], data["device_count"], data["tag_count"]) == (2, 35, 68)
    assert data["counts_include_disabled"] is True
    assert len(data["devices"]) == 30 and data["truncated"] is True
    evidence = Evidence(id="tool:asset", kind="tool", title="asset_overview", data=data)
    answer = GeneratedAnswer(
        status="answered", answer="错误推断30台设备", citation_ids=[evidence.id]
    )
    attach_data_cautions(answer, [evidence])
    assert "2 条产线、35 台设备、68 个测点" in answer.answer
    assert "列表已截断" in answer.answer and "包含禁用资产" in answer.answer
    assert "实时采集页面" not in answer.answer
    assert "错误推断" not in answer.answer


@pytest.mark.parametrize(
    "text, blocked",
    [
        ("可以，但需重新检查权限。在未通过前不应继续提交。", True),
        ("可以。但是必须先完成审批。", True),
        ("允许，不过需要先验证质量。", True),
        ("能，但前提是校验成功。", True),
        ("当前不能提交，权限已撤销。", False),
        ("尚不能确认能否提交，需先核验权限。", False),
        ("可以查看，但不能修改数据。", False),
        ("权限仍有效且校验通过时可以提交。", False),
        ("可以。题述条件已全部满足。", False),
    ],
)
def test_narrow_conditional_wording_guard(text: str, blocked: bool) -> None:
    answer = GeneratedAnswer(status="answered", answer=text, citation_ids=["doc:x"])
    assert bool(conditional_conclusion_error(answer)) is blocked
    answer.status = "clarification"
    assert conditional_conclusion_error(answer) is None


@pytest.mark.parametrize("repairs", [True, False])
def test_condition_repair_is_bounded_and_preserves_evidence(
    client: TestClient,
    db: Session,
    scope: tuple,
    fake_provider: type,
    repairs: bool,
) -> None:
    plant, _, _, headers = scope
    document = client.post(
        f"{settings.API_V1_STR}/assistant/documents",
        headers=headers,
        params={"plant_id": str(plant.id)},
        json={
            "title": "权限与导入",
            "content": "导入开始和提交前必须检查工厂权限。权限已撤销时不得继续提交数据，需恢复授权后重新校验。",
        },
    )
    assert document.status_code == 200
    calls = 0
    ids: list[str] = []

    def callback(messages: list, tools: object) -> dict:
        nonlocal calls, ids
        calls += 1
        if calls == 1:
            ids = [
                item["id"] for item in json.loads(messages[1]["content"])["evidence"]
            ]
            assert ids
        else:
            assert tools is None
            request = json.loads(messages[-1]["content"])
            assert request["validation_error"] == "ambiguous_conditional_conclusion"
            assert set(ids) == set(request["allowed_citation_ids"])
        return {
            "content": json.dumps(
                {
                    "status": "answered",
                    "answer": "当前不能提交，工厂权限已撤销；恢复授权后仍须重新检查。"
                    if repairs and calls == 2
                    else "可以，但需重新检查权限，未通过前不应继续提交。",
                    "citation_ids": ids,
                }
            )
        }

    fake_provider.callback = staticmethod(callback)
    response = client.post(
        f"{settings.API_V1_STR}/assistant/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "权限已撤销，排队的导入还能继续提交吗？",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 200 and calls == 2
    result = response.json()
    assert "可以，但" not in result["answer"]
    assert result["status"] == ("answered" if repairs else "no_answer")
    if repairs:
        assert (
            result["answer"].startswith("当前不能提交")
            and result["citation_ids"] == ids
        )
    else:
        assert result["citation_ids"] == [] and "条件结论检查" in result["answer"]
        run = db.get(AssistantRun, uuid.UUID(result["run_id"]))
        assert run and run.error_code == "ambiguous_conditional_conclusion"
