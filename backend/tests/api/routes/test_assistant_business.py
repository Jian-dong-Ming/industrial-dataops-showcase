"""Business acceptance with synthetic data and the real CSV processing pipeline.

Normal tests stub only the model, not database tools. The explicitly enabled live
test calls paid providers and records every result, including failures, on E:.
"""

# ruff: noqa: ARG001
import json
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.assistant.answer_checks import attach_data_cautions
from app.assistant.schemas import Evidence, GeneratedAnswer
from app.assistant.tools import execute_tool
from app.models import (
    AcquisitionTask,
    Device,
    ProductionLine,
    Tag,
    TagSample,
    get_datetime_utc,
)
from tests.api.routes.test_assistant import PREFIX
from tests.api.routes.test_assistant import fake_provider as fake_provider
from tests.api.routes.test_assistant import scope as scope
from tests.utils.imports import finish_import


@pytest.fixture
def business(
    client: TestClient,
    db: Session,
    scope: tuple,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    from app.core.config import settings

    monkeypatch.setattr(settings, "IMPORT_STORAGE_DIR", tmp_path / "imports")
    plant, other, user, headers = scope
    line = ProductionLine(
        plant_id=plant.id, code="DEMO", name="合成验收产线", process_type="continuous"
    )
    db.add(line)
    db.commit()
    device = Device(
        production_line_id=line.id,
        code="DEMO",
        name="合成验收设备",
        device_type="simulator",
    )
    db.add(device)
    db.commit()
    tags = {}
    for code, name, unit in [
        ("HEAT_TEMP", "加热温度", "℃"),
        ("COOL_TEMP", "冷却温度", "℃"),
        ("OLD_FLOW", "过期流量", "L/min"),
        ("EMPTY_PRESSURE", "无样本压力", "MPa"),
        ("PRESSURE_A", "同名压力", "MPa"),
        ("PRESSURE_B", "同名压力", "MPa"),
    ]:
        tag = Tag(
            device_id=device.id,
            code=code,
            name=name,
            unit=unit,
            min_value=0,
            max_value=1000,
        )
        db.add(tag)
        tags[code] = tag
    task = AcquisitionTask(
        plant_id=plant.id,
        name="合成快照任务（不连接设备）",
        endpoint_url="opc.tcp://example.invalid:4840",
    )
    db.add(task)
    db.commit()
    for code, value, good, age in [
        ("HEAT_TEMP", 123.45, True, 0),
        ("COOL_TEMP", 67.89, False, 0),
        ("OLD_FLOW", 8.76, True, 7200),
    ]:
        db.add(
            TagSample(
                task_id=task.id,
                tag_id=tags[code].id,
                value=value,
                numeric_value=value,
                source_timestamp=get_datetime_utc() - timedelta(seconds=age),
                status_code="Good" if good else "Bad",
                is_good=good,
            )
        )
    db.commit()
    csv = "\n".join(
        [
            "时间,测点编码,值,质量码",
            "2026-09-01T00:00:00Z,HEAT_TEMP,100,Good",
            "2026-09-01T00:00:01Z,HEAT_TEMP,101,Bad",
            "2026-09-01T00:00:02Z,HEAT_TEMP,2000,Good",
            "2026-09-01T00:00:03Z,UNKNOWN,102,Good",
            "2026-09-01T00:00:00Z,HEAT_TEMP,103,Good",
            "2026-09-01T00:00:04Z,HEAT_TEMP,hello,Good",
            "not-a-time,HEAT_TEMP,105,Good",
        ]
    )
    preview = client.post(
        "/api/v1/imports/preview",
        params={"plant_id": str(plant.id)},
        headers=headers,
        files={"file": ("synthetic-business.csv", csv.encode(), "text/csv")},
    )
    assert preview.status_code == 200
    batch_id = preview.json()["batch"]["id"]
    processed = client.post(
        f"/api/v1/imports/{batch_id}/process",
        headers=headers,
        json=preview.json()["suggested_mapping"],
    )
    assert processed.status_code == 202
    processed = finish_import(client, batch_id, headers)
    batch = processed.json()
    assert (
        batch["total_rows"],
        batch["accepted_rows"],
        batch["rejected_rows"],
        batch["issue_count"],
    ) == (7, 2, 5, 6)
    body = {
        "title": "合成复核规程",
        "content": "仅供软件验收，不是生产标准：本演示工厂的复核等待时间为70秒。质量码Bad应检查数据源，不得判为正常工艺值。",
        "allow_external_processing": os.environ.get("AI_LIVE_ACCEPTANCE") == "1",
    }
    doc = client.post(
        f"{PREFIX}/documents",
        headers=headers,
        params={"plant_id": str(plant.id)},
        json=body,
    )
    assert doc.status_code == 200
    return {
        "plant": plant,
        "other": other,
        "user": user,
        "headers": headers,
        "tags": tags,
        "batch": batch,
        "document": doc.json(),
        "document_body": body,
    }


def tool(db: Session, business: dict, name: str, **arguments: Any) -> dict:
    return execute_tool(
        session=db,
        user=business["user"],
        plant_id=business["plant"].id,
        name=name,
        arguments=json.dumps(arguments),
    )


def test_business_tools_follow_real_import_and_source_boundaries(
    db: Session, business: dict
) -> None:
    assert len(tool(db, business, "find_tags", query="同名压力")["tags"]) == 2
    latest = tool(
        db, business, "latest_value", tag_id=str(business["tags"]["HEAT_TEMP"].id)
    )
    assert latest["sample"]["value"] == 123.45 and latest["sample"]["source"] == "opcua"
    history = tool(
        db,
        business,
        "latest_value",
        tag_id=str(business["tags"]["HEAT_TEMP"].id),
        source="file",
    )
    assert history["sample"]["value"] == 101 and not history["sample"]["is_good"]
    assert (
        tool(
            db,
            business,
            "latest_value",
            tag_id=str(business["tags"]["EMPTY_PRESSURE"].id),
        )["sample"]
        is None
    )
    batch = tool(db, business, "inspect_import_batch", batch_id=business["batch"]["id"])
    assert (
        batch["accepted_rows"],
        batch["rejected_rows"],
        batch["duplicate_rows"],
        batch["warning_rows"],
    ) == (2, 5, 1, 1)
    assert sum(row["count"] for row in batch["stored_issue_summary"]) == 6
    assert len(batch["examples"]) == 6
    assert all(row["suggestion"] for row in batch["stored_issue_summary"])


@pytest.mark.parametrize(
    "code,source,expected",
    [
        ("COOL_TEMP", "opcua", "质量码异常"),
        ("OLD_FLOW", "opcua", "数据已过期"),
        ("EMPTY_PRESSURE", "opcua", "没有样本"),
        ("HEAT_TEMP", "file", "不是实时采集"),
    ],
)
def test_safety_cautions_do_not_depend_on_model(
    db: Session, business: dict, code: str, source: str, expected: str
) -> None:
    data = tool(
        db,
        business,
        "latest_value",
        tag_id=str(business["tags"][code].id),
        source=source,
    )
    answer = GeneratedAnswer(status="answered", answer="模型回答。", citation_ids=[])
    evidence = Evidence(id="tool:real:1", kind="tool", title="latest_value", data=data)
    attach_data_cautions(answer, [evidence])
    assert expected in answer.answer and answer.citation_ids == [evidence.id]


def test_nonempty_model_tool_chain_preserves_numbers(
    client: TestClient, business: dict, fake_provider: type
) -> None:
    calls = []

    def callback(messages: list, tools: Any) -> dict:
        calls.append(1)
        if len(calls) == 1:
            name, args = "find_tags", {"query": "HEAT_TEMP"}
        elif len(calls) == 2:
            found = json.loads(messages[-1]["content"])
            name, args = "latest_value", {"tag_id": found["data"]["tags"][0]["tag_id"]}
        else:
            found = json.loads(messages[-1]["content"])
            return {
                "content": json.dumps(
                    {
                        "status": "answered",
                        "answer": str(found["data"]["sample"]["value"]),
                        "citation_ids": [found["id"]],
                    }
                )
            }
        return {
            "tool_calls": [
                {
                    "id": f"call_{len(calls)}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ]
        }

    fake_provider.callback = staticmethod(callback)
    response = client.post(
        f"{PREFIX}/ask",
        headers=business["headers"],
        json={
            "plant_id": str(business["plant"].id),
            "question": "HEAT_TEMP当前值是多少",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 200 and "123.45" in response.json()["answer"]
    assert [e["title"] for e in response.json()["evidence"] if e["kind"] == "tool"] == [
        "find_tags",
        "latest_value",
    ]


def test_ambiguous_identifiers_are_rendered_from_database(
    db: Session, business: dict
) -> None:
    data = tool(db, business, "find_tags", query="同名压力")
    answer = GeneratedAnswer(
        status="clarification", answer="错误编码WRONG_CODE", citation_ids=[]
    )
    attach_data_cautions(
        answer, [Evidence(id="tool:actual", kind="tool", title="find_tags", data=data)]
    )
    assert "PRESSURE_A" in answer.answer and "PRESSURE_B" in answer.answer
    assert "WRONG_CODE" not in answer.answer


def test_batch_statistics_are_not_rewritten_by_model(
    db: Session, business: dict
) -> None:
    data = tool(db, business, "inspect_import_batch", batch_id=business["batch"]["id"])
    answer = GeneratedAnswer(
        status="answered", answer="错误：6行失败，修改量程即可", citation_ids=[]
    )
    attach_data_cautions(
        answer,
        [
            Evidence(
                id="tool:batch", kind="tool", title="inspect_import_batch", data=data
            )
        ],
    )
    assert "拒绝 5" in answer.answer and "问题条数 6" in answer.answer
    assert "6行失败" not in answer.answer and "修改量程即可" not in answer.answer
    assert "invalid_value" in answer.answer and "排查建议" in answer.answer
    data["issues_truncated"] = True
    attach_data_cautions(
        answer,
        [
            Evidence(
                id="tool:batch", kind="tool", title="inspect_import_batch", data=data
            )
        ],
    )
    assert "不是全量问题分布" in answer.answer


@pytest.mark.parametrize(
    "status", ["uploaded", "queued", "processing", "failed", "duplicate"]
)
def test_unfinished_batch_zero_counts_are_not_presented_as_final(status: str) -> None:
    answer = GeneratedAnswer(
        status="answered", answer="导入成功，没有任何质量问题", citation_ids=[]
    )
    attach_data_cautions(
        answer,
        [
            Evidence(
                id="tool:pending",
                kind="tool",
                title="inspect_import_batch",
                data={
                    "batch_id": "pending",
                    "filename": "pending.csv",
                    "status": status,
                },
            )
        ],
    )
    assert "当前不是已完成批次" in answer.answer
    assert "不能把零计数解释为" in answer.answer
    assert "导入成功，没有任何质量问题" not in answer.answer
    assert answer.citation_ids == ["tool:pending"]


def test_future_and_disabled_cautions() -> None:
    answer = GeneratedAnswer(status="answered", answer="查询结果", citation_ids=[])
    attach_data_cautions(
        answer,
        [
            Evidence(
                id="tool:future",
                kind="tool",
                title="latest_value",
                data={
                    "code": "DEMO",
                    "name": "演示测点",
                    "unit": "℃",
                    "enabled": False,
                    "sample": {
                        "value": 2,
                        "source_timestamp": "2026-09-08T00:00:00Z",
                        "status_code": "Good",
                        "is_good": True,
                        "stale": False,
                        "future_timestamp": True,
                        "source": "opcua",
                    },
                },
            )
        ],
    )
    assert "已禁用" in answer.answer and "时间超前" in answer.answer


@pytest.mark.skipif(
    os.environ.get("AI_LIVE_ACCEPTANCE") != "1",
    reason="Explicit paid business acceptance only",
)
def test_live_business_workflows(
    client: TestClient, db: Session, business: dict
) -> None:
    from app.core.config import settings

    output = Path(os.environ["AI_BUSINESS_REPORT"])
    assert not output.exists(), "Never overwrite a previous acceptance report"
    results = []
    cases = [
        (
            "T01",
            "HEAT_TEMP目前最新采集值是多少？给出单位、时间和质量。",
            "latest_value",
            ["123.45"],
            "answered",
        ),
        (
            "T02",
            "COOL_TEMP最新采集值及质量是什么，能判断工况正常吗？",
            "latest_value",
            ["67.89", "质量码异常"],
            "answered",
        ),
        (
            "T03",
            "OLD_FLOW最新值还能代表现在的流量吗？",
            "latest_value",
            ["8.76", "过期"],
            "answered",
        ),
        ("T04", "EMPTY_PRESSURE现在的压力是多少？", "latest_value", ["没有"], None),
        ("T05", "查一下同名压力的实时值。", "find_tags", [], "clarification"),
        (
            "T06",
            "HEAT_TEMP历史文件导入中的最新记录是什么？不要查询实时采集。",
            "latest_value",
            ["101", "历史"],
            "answered",
        ),
        (
            "B01",
            "最近一批导入具体有多少行成功、失败、重复和警告？失败原因及排查步骤是什么？",
            "inspect_import_batch",
            [],
            "answered",
        ),
        (
            "K01",
            "根据合成复核规程，演示工厂需要等待多少秒再复核？",
            None,
            ["70"],
            "answered",
        ),
        (
            "K02",
            "根据合成复核规程，演示工厂需要等待多少秒再复核？",
            None,
            ["95"],
            "answered",
        ),
        ("K03", "本工厂轴承型号对应的厂家保修截止日期是什么？", None, [], "no_answer"),
    ]
    selected = set(filter(None, os.environ.get("AI_BUSINESS_CASE_IDS", "").split(",")))
    assert selected <= {case[0] for case in cases}, "Unknown business case ID"
    cases = [case for case in cases if not selected or case[0] in selected]
    for index, (case_id, question, required_tool, phrases, status) in enumerate(cases):
        if index:
            time.sleep(11)
        if case_id == "K02":
            document = business["document"]
            updated = client.put(
                f"{PREFIX}/documents/{document['id']}",
                params={"plant_id": str(business["plant"].id)},
                headers=business["headers"],
                json={
                    **business["document_body"],
                    "expected_version": 1,
                    "content": "仅供软件验收，不是生产标准：本演示工厂的复核等待时间修订为95秒，以本版本为准。质量码Bad应检查数据源，不得判为正常工艺值。",
                },
            )
            assert updated.status_code == 200
        response = client.post(
            f"{PREFIX}/ask",
            headers=business["headers"],
            json={
                "plant_id": str(business["plant"].id),
                "question": question,
                "allow_external_processing": True,
            },
        )
        data = response.json()
        evidence = data.get("evidence", [])
        tool_names = [e["title"] for e in evidence if e["kind"] == "tool"]
        checks = {
            "http_ok": response.status_code == 200,
            "required_tool": not required_tool or required_tool in tool_names,
            "status": not status or data.get("status") == status,
            "key_facts": all(text in data.get("answer", "") for text in phrases),
            "citations_exist": set(data.get("citation_ids", []))
            <= {e["id"] for e in evidence},
        }
        if case_id == "T05":
            checks["no_arbitrary_latest"] = "latest_value" not in tool_names
            checks["exact_candidate_codes"] = all(
                code in data.get("answer", "") for code in ("PRESSURE_A", "PRESSURE_B")
            )
        if case_id == "B01":
            detail = next(
                (e["data"] for e in evidence if e["title"] == "inspect_import_batch"),
                {},
            )
            checks["exact_batch_counts"] = [
                detail.get(k)
                for k in ["total_rows", "accepted_rows", "rejected_rows", "issue_count"]
            ] == [7, 2, 5, 6]
        if case_id == "K02":
            checks["new_version_only"] = bool(evidence) and all(
                e["data"].get("version") == 2
                for e in evidence
                if e["kind"] == "document"
            )
        results.append(
            {
                "id": case_id,
                "question": question,
                "checks": checks,
                "passed": all(checks.values()),
                "human_verdict": None,
                "response": data,
            }
        )
        # A crash still leaves earlier results; only synthetic fixture content.
        output.write_text(
            json.dumps(
                {
                    "model": settings.DEEPSEEK_MODEL,
                    "embedding_model": settings.AI_EMBEDDING_MODEL,
                    "scope": "synthetic business acceptance; automatic checks are not semantic accuracy",
                    "cases": results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    assert all(result["passed"] for result in results), [
        (r["id"], r["checks"]) for r in results if not r["passed"]
    ]
