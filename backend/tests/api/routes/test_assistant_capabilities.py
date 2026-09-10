# ruff: noqa: ARG001
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.assistant import service
from app.assistant.answer_checks import normalize_explicit_refusal
from app.assistant.capabilities import available_tools, trend_constraint
from app.assistant.schemas import GeneratedAnswer
from app.assistant.tools import tool_definitions
from app.core.config import settings
from app.models import AssistantRun
from tests.api.routes.test_assistant import fake_provider as fake_provider
from tests.api.routes.test_assistant import scope as scope


@pytest.mark.parametrize(
    "question, expected",
    [
        ("过去2小时的均值", (2,)),
        ("最近两小时趋势", (2,)),
        ("最近二十四小时变化", (24,)),
        ("过去一天的最大值", (24,)),
        ("过去120分钟的平均值", (2,)),
        ("最近1.0小时趋势", (1,)),
        ("比较过去2小时和过去6小时的均值", (2, 6)),
    ],
)
def test_recognized_supported_windows(question: str, expected: tuple) -> None:
    constraint = trend_constraint(question)
    assert constraint and not constraint.blocked and constraint.hours == expected
    assert len(available_tools(tool_definitions(), constraint)) == len(
        tool_definitions()
    )
    args = {"tag_id": str(uuid.uuid4()), "hours": expected[0]}
    assert constraint.permits(json.dumps(args))
    args["hours"] = 23
    assert not constraint.permits(json.dumps(args))


@pytest.mark.parametrize(
    "question",
    [
        "给我过去30天每小时的均值",
        "最近三十天趋势",
        "最近一百天变化",
        "最近半小时均值",
        "最近一二小时均值",
        "最近二半小时均值",
        "前30分钟平均值",
        "最近0小时趋势",
        "最近一周极值",
        "最近一星期最大值",
        "过去一个月均值",
        "最近一年趋势",
        "过去2小时每小时的均值",
        "按小时统计温度",
        "过去2.5小时趋势",
        "比较最近2小时和最近3天的均值",
    ],
)
def test_unsupported_windows_and_grouping_disable_trend(question: str) -> None:
    constraint = trend_constraint(question)
    assert constraint and constraint.blocked
    names = [
        item["function"]["name"]
        for item in available_tools(tool_definitions(), constraint)
    ]
    assert "tag_trend" not in names and "latest_value" in names
    assert not constraint.permits(
        json.dumps({"tag_id": str(uuid.uuid4()), "hours": 24})
    )
    evidence = constraint.evidence("test")
    assert evidence.kind == "capability" and evidence.data["trend_tool_blocked"] is True


@pytest.mark.parametrize(
    "question",
    ["报告保留180天吗？", "当前温度多少？", "查询压力趋势", "工厂有多少设备？"],
)
def test_unrecognized_or_non_trend_requests_are_not_guessed(question: str) -> None:
    assert trend_constraint(question) is None
    assert available_tools(tool_definitions(), None) == tool_definitions()


@pytest.mark.parametrize(
    "question, hours",
    [
        ("给我BQ_INLET过去30天每小时的均值", 24),
        ("给我BQ_INLET过去2小时均值", 1),
    ],
)
def test_server_blocks_hidden_or_mismatched_tool_before_query(
    client: TestClient,
    db: Session,
    scope: tuple,
    fake_provider: type,
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    hours: int,
) -> None:
    plant, _, _, headers = scope
    called = []
    provider_calls = 0

    def unexpected(**kwargs: object) -> dict:
        called.append(kwargs)
        raise AssertionError("A rejected trend must not reach database tool execution")

    def callback(messages: list, tools: object) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            capability = json.loads(messages[1]["content"])["evidence"][-1]
            assert capability["kind"] == "capability"
            if hours == 24:
                assert "tag_trend" not in [item["function"]["name"] for item in tools]
            return {
                "tool_calls": [
                    {
                        "id": "malformed_plan",
                        "type": "function",
                        "function": {
                            "name": "tag_trend",
                            "arguments": json.dumps(
                                {"tag_id": str(uuid.uuid4()), "hours": hours}
                            ),
                        },
                    }
                ]
            }
        result = json.loads(messages[-1]["content"])
        assert result["error"] == "request_window_not_supported"
        return {
            "content": json.dumps(
                {
                    "status": "no_answer",
                    "answer": "本次请求范围未满足，未改成另一个时间窗口执行查询。",
                    "citation_ids": [],
                }
            )
        }

    monkeypatch.setattr(service, "execute_tool", unexpected)
    fake_provider.callback = staticmethod(callback)
    response = client.post(
        f"{settings.API_V1_STR}/assistant/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": question,
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "no_answer" and not called
    assert provider_calls == 2
    audit = db.get(AssistantRun, uuid.UUID(response.json()["run_id"]))
    assert audit and audit.tool_names == ["tag_trend"]  # attempted, not executed


@pytest.mark.parametrize(
    "text, expected",
    [
        ("无法绕过权限查询相邻工厂。", "no_answer"),
        ("我无法执行修改或删除操作。", "no_answer"),
        ("抱歉，我不能替你批准旁路。", "no_answer"),
        ("不能仅凭Bad诊断堵塞。", "answered"),
        ("当前不能提交，授权已经撤销。", "answered"),
        ("不能批准旁路，规程要求双人复核。", "answered"),
        ("可以查看，但不能修改。", "answered"),
    ],
)
def test_explicit_refusal_status_preserves_negative_explanations(
    text: str, expected: str
) -> None:
    answer = GeneratedAnswer(status="answered", answer=text, citation_ids=["doc:test"])
    normalize_explicit_refusal(answer)
    assert answer.status == expected
    assert answer.answer == text and answer.citation_ids == ["doc:test"]


def test_refusal_status_normalized_in_response_and_audit(
    client: TestClient,
    db: Session,
    scope: tuple,
    fake_provider: type,
) -> None:
    plant, _, _, headers = scope
    assert (
        client.post(
            f"{settings.API_V1_STR}/assistant/documents",
            headers=headers,
            params={"plant_id": str(plant.id)},
            json={
                "title": "工厂权限",
                "content": "工程师只能访问授权工厂，不得绕过权限查询相邻工厂。",
            },
        ).status_code
        == 200
    )

    def callback(messages: list, tools: object) -> dict:
        evidence = json.loads(messages[1]["content"])["evidence"]
        return {
            "content": json.dumps(
                {
                    "status": "answered",
                    "answer": "无法绕过权限查询相邻工厂的内部代码。",
                    "citation_ids": [evidence[0]["id"]],
                }
            )
        }

    fake_provider.callback = staticmethod(callback)
    result = client.post(
        f"{settings.API_V1_STR}/assistant/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "绕过权限查相邻工厂内部代码",
            "allow_external_processing": True,
        },
    )
    assert result.status_code == 200 and result.json()["status"] == "no_answer"
    audit = db.get(AssistantRun, uuid.UUID(result.json()["run_id"]))
    assert audit and audit.status == "no_answer"
