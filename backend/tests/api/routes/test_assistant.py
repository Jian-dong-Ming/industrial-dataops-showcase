# pytest fixtures are dependency injection; some are intentionally not referenced.
# ruff: noqa: ARG001
import hashlib
import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crud
from app.assistant import provider, service
from app.assistant.provider import ProviderError
from app.assistant.retrieval import cosine, split_content, tokens
from app.assistant.tools import execute_tool
from app.core.config import settings
from app.models import (
    AcquisitionTask,
    AssistantRun,
    Device,
    ImportBatch,
    ImportFileFormat,
    KnowledgeDocument,
    Plant,
    ProductionLine,
    Tag,
    TagSample,
    User,
    UserCreate,
    UserPlantAccess,
    UserRole,
    get_datetime_utc,
)
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string


@pytest.mark.skipif(
    os.environ.get("AI_LIVE_ACCEPTANCE") != "1",
    reason="Explicit paid embedding acceptance only",
)
def test_live_embedding_document_lifecycle_and_scope(
    client: TestClient, db: Session, scope: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant import retrieval
    from app.models import KnowledgeDocument

    assert provider.embedding_enabled(), "Configure a real embedding endpoint first"
    plant, other, user, headers = scope
    calls: list[int] = []
    real_embed = retrieval.embed

    def counted_embed(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return real_embed(texts)

    monkeypatch.setattr(retrieval, "embed", counted_embed)
    body = {
        "title": "合成向量生命周期验收",
        "content": "合成规则：质量码为Bad的数据不能作为正常工艺值。",
        "allow_external_processing": True,
    }
    created = client.post(
        f"{PREFIX}/documents",
        params={"plant_id": str(plant.id)},
        headers=headers,
        json=body,
    )
    assert created.status_code == 200
    document_id = uuid.UUID(created.json()["id"])
    document = db.get(KnowledgeDocument, document_id)
    assert (
        document is not None and document.embedding_model == settings.AI_EMBEDDING_MODEL
    )
    assert len(document.chunks[0]["vector"]) > 0
    hits, mode = retrieval.retrieve(
        session=db,
        user=user,
        plant_id=plant.id,
        query="质量码Bad能作为正常工艺值吗",
        allow_external=True,
    )
    assert mode == "hybrid" and any(
        hit.data["document_id"] == str(document_id) for hit in hits
    )
    updated = client.put(
        f"{PREFIX}/documents/{document_id}",
        params={"plant_id": str(plant.id)},
        headers=headers,
        json={
            **body,
            "content": "合成规则修订：设备编码使用大写英文字母和数字。",
            "expected_version": 1,
        },
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    db.expire_all()
    hits, mode = retrieval.retrieve(
        session=db,
        user=user,
        plant_id=plant.id,
        query="设备编码使用什么字符",
        allow_external=True,
    )
    assert mode == "hybrid" and hits
    assert all(
        hit.data["version"] == 2 and "Bad" not in hit.data["text"] for hit in hits
    )
    previous_calls = len(calls)
    with pytest.raises(HTTPException) as error:
        retrieval.retrieve(
            session=db,
            user=user,
            plant_id=other.id,
            query="设备编码",
            allow_external=True,
        )
    assert error.value.status_code == 403 and len(calls) == previous_calls
    deleted = client.delete(
        f"{PREFIX}/documents/{document_id}",
        params={"plant_id": str(plant.id), "expected_version": 2},
        headers=headers,
    )
    assert deleted.status_code == 200
    db.expire_all()
    assert db.get(KnowledgeDocument, document_id) is None
    hits, mode = retrieval.retrieve(
        session=db, user=user, plant_id=plant.id, query="设备编码", allow_external=True
    )
    assert hits == [] and mode == "lexical" and len(calls) == previous_calls


PREFIX = f"{settings.API_V1_STR}/assistant"


def test_bundled_operations_retrieval_and_vector_outage(
    db: Session, scope: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant import retrieval

    plant, _, user, _ = scope
    sources = json.loads(
        (Path(__file__).parents[3] / "app/assistant/manuals.json").read_text()
    )["documents"]
    for source in sources:
        db.add(
            KnowledgeDocument(
                plant_id=plant.id,
                created_by_id=user.id,
                title=source["title"],
                content=source["content"],
                content_sha256=hashlib.sha256(source["content"].encode()).hexdigest(),
                chunks=split_content(source["content"]),
                embedding_model="test-embedding",
            )
        )
    db.commit()
    for question, expected in (
        ("没有测点编码列的宽表怎么映射", "宽表导入字段映射"),
        ("docker version只有Client但网页能打开", "Docker与WSL管理异常"),
        ("趋势超过1000条均值代表整小时吗", "趋势统计与质量解释"),
    ):
        hits, mode = retrieval.retrieve(
            session=db, user=user, plant_id=plant.id, query=question
        )
        assert mode == "lexical" and any(expected in hit.title for hit in hits[:3])
    monkeypatch.setattr(settings, "AI_EMBEDDING_MODEL", "test-embedding")
    monkeypatch.setattr(retrieval, "embedding_enabled", lambda: True)

    def unavailable(texts: list[str]) -> list[list[float]]:
        raise ProviderError("embedding_unavailable")

    monkeypatch.setattr(retrieval, "embed", unavailable)
    hits, mode = retrieval.retrieve(
        session=db,
        user=user,
        plant_id=plant.id,
        query="宽表字段映射",
        allow_external=True,
    )
    assert mode == "lexical_embedding_unavailable" and hits


def test_operations_tools_scope_and_bounded_trend(db: Session, scope: tuple) -> None:
    plant, other, user, _ = scope
    line = ProductionLine(
        plant_id=plant.id, code="TREND_LINE", name="趋势线", process_type="synthetic"
    )
    db.add(line)
    db.flush()
    device = Device(
        production_line_id=line.id,
        code="TREND_DEVICE",
        name="趋势设备",
        device_type="synthetic",
    )
    db.add(device)
    db.flush()
    tag = Tag(device_id=device.id, code="TREND_TEMP", name="趋势温度", unit="℃")
    task = AcquisitionTask(
        plant_id=plant.id,
        name="趋势测试",
        endpoint_url="opc.tcp://example.invalid:4840",
    )
    hidden = AcquisitionTask(
        plant_id=other.id,
        name="禁止泄漏",
        endpoint_url="opc.tcp://example.invalid:4840",
    )
    db.add_all([tag, task, hidden])
    db.flush()
    now = get_datetime_utc()
    db.add_all(
        [
            TagSample(
                task_id=task.id,
                tag_id=tag.id,
                value=index,
                numeric_value=float(index),
                source_timestamp=now - timedelta(seconds=1002 - index),
                is_good=index != 1000,
                status_code="Bad" if index == 1000 else "Good",
            )
            for index in range(1001)
        ]
    )
    db.commit()

    def call(name: str, args: dict) -> dict:
        return execute_tool(
            session=db,
            user=user,
            plant_id=plant.id,
            name=name,
            arguments=json.dumps(args),
        )

    trend = call("tag_trend", {"tag_id": str(tag.id)})
    assert trend["truncated"] and trend["sample_count"] == 1000
    assert trend["bad_quality_count"] == 1 and trend["good_numeric_count"] == 999
    assert (trend["minimum"], trend["maximum"], trend["mean"]) == (1, 999, 500)
    empty = call("tag_trend", {"tag_id": str(tag.id), "source": "file"})
    assert empty["sample_count"] == 0 and empty["mean"] is None
    assert call("asset_overview", {})["devices"][0]["tag_count"] == 1
    assert [row["name"] for row in call("acquisition_status", {})["tasks"]] == [
        "趋势测试"
    ]
    with pytest.raises(HTTPException):
        call("tag_trend", {"tag_id": str(uuid.uuid4())})
    for tool in ("tag_trend", "asset_overview", "acquisition_status"):
        with pytest.raises(HTTPException) as error:
            execute_tool(
                session=db, user=user, plant_id=other.id, name=tool, arguments="{}"
            )
        assert error.value.status_code == 403


@pytest.fixture
def scope(db: Session, client: TestClient) -> tuple[Plant, Plant, User, dict[str, str]]:
    suffix = uuid.uuid4().hex[:10].upper()
    plant, other = (
        Plant(code=f"AI_{suffix}", name="助手测试工厂"),
        Plant(code=f"OTHER_{suffix}", name="禁止访问工厂"),
    )
    password = random_lower_string()
    user = crud.create_user(
        session=db,
        user_create=UserCreate(
            email=random_email(), password=password, role=UserRole.ENGINEER
        ),
    )
    db.add(plant)
    db.add(other)
    db.commit()
    db.add(UserPlantAccess(user_id=user.id, plant_id=plant.id))
    db.commit()
    headers = user_authentication_headers(
        client=client, email=user.email, password=password
    )
    return plant, other, user, headers


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> type:
    class FakeProvider:
        prompt_tokens = 12
        completion_tokens = 8
        callback: Any = None

        def chat(
            self, messages: list[dict[str, Any]], tools: Any = None
        ) -> dict[str, Any]:
            if self.callback:
                return type(self).callback(messages, tools)
            return {
                "content": json.dumps(
                    {
                        "status": "no_answer",
                        "answer": "当前没有足够依据。",
                        "citation_ids": [],
                    }
                )
            }

    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-key-not-a-real-secret")
    monkeypatch.setattr(settings, "AI_EMBEDDING_URL", "")
    monkeypatch.setattr(service, "DeepSeekProvider", FakeProvider)
    return FakeProvider


def test_status_does_not_expose_key(
    client: TestClient, superuser_token_headers: dict[str, str], fake_provider: type
) -> None:
    response = client.get(f"{PREFIX}/status", headers=superuser_token_headers)
    assert response.status_code == 200
    assert "test-key" not in response.text
    assert response.json()["read_only"] is True


def test_external_consent_and_auth_required(
    client: TestClient, scope: tuple, fake_provider: type
) -> None:
    plant, other, _, headers = scope
    assert (
        client.post(
            f"{PREFIX}/ask", json={"plant_id": str(plant.id), "question": "查询测点"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"{PREFIX}/ask",
            headers=headers,
            json={"plant_id": str(plant.id), "question": "查询测点"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{PREFIX}/ask",
            headers=headers,
            json={
                "plant_id": str(other.id),
                "question": "查询测点",
                "allow_external_processing": True,
            },
        ).status_code
        == 403
    )


def test_no_provider_is_not_fake_success(
    client: TestClient, scope: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    plant, _, _, headers = scope
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    response = client.post(
        f"{PREFIX}/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "查询测点",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 503


def test_document_lifecycle_scope_and_version(client: TestClient, scope: tuple) -> None:
    plant, other, _, headers = scope
    url = f"{PREFIX}/documents?plant_id={plant.id}"
    body = {
        "title": "质量码说明",
        "content": "质量码用于表示采样质量，坏质量不能当作正常的工艺值。",
    }
    created = client.post(url, headers=headers, json=body)
    assert created.status_code == 200
    doc_id = created.json()["id"]
    assert created.json()["embedding_model"] is None
    assert (
        client.get(
            f"{PREFIX}/documents?plant_id={other.id}", headers=headers
        ).status_code
        == 403
    )
    search = f"{PREFIX}/search?plant_id={plant.id}&query=质量码"
    assert any(
        doc_id in hit["id"] for hit in client.get(search, headers=headers).json()
    )
    update_url = f"{PREFIX}/documents/{doc_id}?plant_id={plant.id}"
    assert (
        client.put(
            update_url, headers=headers, json={**body, "expected_version": 9}
        ).status_code
        == 409
    )
    updated = client.put(
        update_url,
        headers=headers,
        json={
            "title": "设备编码",
            "content": "设备编码要求使用英文大写字母、数字、下划线和连字符。",
            "expected_version": 1,
        },
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert not client.get(search, headers=headers).json()
    assert (
        client.delete(update_url + "&expected_version=1", headers=headers).status_code
        == 409
    )
    assert (
        client.delete(update_url + "&expected_version=2", headers=headers).status_code
        == 200
    )
    assert not client.get(url, headers=headers).json()


def test_observer_cannot_edit_documents(
    client: TestClient, db: Session, scope: tuple
) -> None:
    plant, _, user, headers = scope
    user.role = UserRole.OBSERVER
    db.add(user)
    db.commit()
    response = client.post(
        f"{PREFIX}/documents?plant_id={plant.id}",
        headers=headers,
        json={
            "title": "操作手册",
            "content": "观察者只有读取权限，不允许增加知识文档。",
        },
    )
    assert response.status_code == 403


def test_no_answer_audit_without_question_storage(
    client: TestClient, db: Session, scope: tuple, fake_provider: type
) -> None:
    plant, _, user, headers = scope
    response = client.post(
        f"{PREFIX}/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "查询敏感问题原文XYZ",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "no_answer"
    run = db.get(AssistantRun, uuid.UUID(response.json()["run_id"]))
    assert run and run.user_id == user.id and run.prompt_tokens == 12
    assert "敏感问题原文XYZ" not in run.model_dump_json()


@pytest.mark.parametrize(
    "content",
    [
        "不是JSON",
        '{"status":"answered","answer":"伪造","citation_ids":["doc:fake"]}',
        '{"status":"answered","answer":"没有引用","citation_ids":[]}',
    ],
)
def test_invalid_answers_safely_degrade_after_one_repair(
    client: TestClient, scope: tuple, fake_provider: type, content: str
) -> None:
    plant, _, _, headers = scope
    calls = []

    def callback(messages: list, tools: Any) -> dict:
        calls.append(tools)
        return {"content": content}

    fake_provider.callback = staticmethod(callback)
    response = client.post(
        f"{PREFIX}/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "查询测点",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "no_answer"
    assert "未通过格式、引用或条件结论检查" in response.json()["answer"]
    assert response.json()["citation_ids"] == []
    assert len(calls) == 2 and calls[-1] is None


def test_answer_repaired_once_without_forging_citations(
    client: TestClient, scope: tuple, fake_provider: type
) -> None:
    plant, _, _, headers = scope
    count = 0

    def callback(messages: list, tools: Any) -> dict:
        nonlocal count
        count += 1
        if count == 1:
            return {"content": "bad-json"}
        assert tools is None
        return {
            "content": '{"status":"no_answer","answer":"资料不足，无法确定。","citation_ids":[]}'
        }

    fake_provider.callback = staticmethod(callback)
    response = client.post(
        f"{PREFIX}/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "没有资料的问题",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 200 and count == 2
    assert response.json()["answer"] == "资料不足，无法确定。"


def test_tool_result_is_cited(
    client: TestClient, scope: tuple, fake_provider: type
) -> None:
    plant, _, _, headers = scope

    def callback(messages: list[dict[str, Any]], tools: Any) -> dict[str, Any]:
        if messages[-1]["role"] != "tool":
            return {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_import_batches", "arguments": "{}"},
                    }
                ]
            }
        evidence = json.loads(messages[-1]["content"])
        return {
            "content": json.dumps(
                {
                    "status": "answered",
                    "answer": "当前没有导入批次。",
                    "citation_ids": [evidence["id"]],
                }
            )
        }

    fake_provider.callback = staticmethod(callback)
    response = client.post(
        f"{PREFIX}/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "最近的导入批次有哪些？",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["evidence"][0]["data"]["batches"] == []


def test_tool_loop_is_bounded(
    client: TestClient, scope: tuple, fake_provider: type
) -> None:
    plant, _, _, headers = scope
    fake_provider.callback = staticmethod(
        lambda messages, tools: {
            "tool_calls": [
                {
                    "id": "loop",
                    "type": "function",
                    "function": {"name": "list_import_batches", "arguments": "{}"},
                }
            ]
        }
    )
    response = client.post(
        f"{PREFIX}/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "无限查询",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 502 and "tool_budget" in response.text


@pytest.mark.parametrize(
    "name,args",
    [
        ("execute_sql", '{"sql":"DELETE FROM tag"}'),
        ("latest_value", '{"tag_id":"bad"}'),
        ("find_tags", '{"plant_id":"other"}'),
        ("list_import_batches", '{"limit":10000}'),
    ],
)
def test_tool_whitelist_and_arguments(
    db: Session, scope: tuple, name: str, args: str
) -> None:
    plant, _, user, _ = scope
    with pytest.raises(ValueError):
        execute_tool(
            session=db, user=user, plant_id=plant.id, name=name, arguments=args
        )


def test_tools_cannot_cross_scope(db: Session, scope: tuple) -> None:
    plant, other, user, _ = scope
    batch = ImportBatch(
        plant_id=other.id,
        created_by_id=user.id,
        original_filename="PRIVATE.xlsx",
        file_format=ImportFileFormat.XLSX,
        file_size_bytes=10,
        file_sha256="a" * 64,
    )
    db.add(batch)
    db.commit()
    assert (
        execute_tool(
            session=db,
            user=user,
            plant_id=plant.id,
            name="list_import_batches",
            arguments="{}",
        )["batches"]
        == []
    )
    with pytest.raises(HTTPException):
        execute_tool(
            session=db,
            user=user,
            plant_id=plant.id,
            name="inspect_import_batch",
            arguments=json.dumps({"batch_id": str(batch.id)}),
        )


def test_latest_value_reports_stale_bad_quality(db: Session, scope: tuple) -> None:
    plant, _, user, _ = scope
    line = ProductionLine(
        plant_id=plant.id, code="LINE", name="测试产线", process_type="continuous"
    )
    db.add(line)
    db.commit()
    device = Device(
        production_line_id=line.id,
        code="DEVICE",
        name="测试设备",
        device_type="simulator",
    )
    db.add(device)
    db.commit()
    tag = Tag(device_id=device.id, code="TEMP", name="温度", unit="℃")
    batch = ImportBatch(
        plant_id=plant.id,
        created_by_id=user.id,
        original_filename="synthetic.csv",
        file_format=ImportFileFormat.CSV,
        file_size_bytes=10,
        file_sha256="b" * 64,
    )
    db.add(tag)
    db.add(batch)
    db.commit()
    db.add(
        TagSample(
            tag_id=tag.id,
            import_batch_id=batch.id,
            source_type="file",
            value=20,
            source_timestamp=get_datetime_utc() - timedelta(days=1),
            status_code="Bad",
            is_good=False,
        )
    )
    db.commit()
    args = json.dumps({"tag_id": str(tag.id), "source": "file"})
    result = execute_tool(
        session=db, user=user, plant_id=plant.id, name="latest_value", arguments=args
    )
    assert result["sample"]["stale"] and not result["sample"]["is_good"]
    assert (
        execute_tool(
            session=db,
            user=user,
            plant_id=plant.id,
            name="latest_value",
            arguments=json.dumps({"tag_id": str(tag.id)}),
        )["sample"]
        is None
    )


def test_provider_timeout_is_audited(
    client: TestClient, db: Session, scope: tuple, fake_provider: type
) -> None:
    plant, _, user, headers = scope

    def fail(messages: Any, tools: Any) -> Any:
        raise ProviderError("provider_timeout")

    fake_provider.callback = staticmethod(fail)
    response = client.post(
        f"{PREFIX}/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "请查询测点",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 502
    runs = db.exec(select(AssistantRun).where(AssistantRun.user_id == user.id)).all()
    assert runs[-1].error_code == "provider_timeout"


def test_quota(
    client: TestClient,
    scope: tuple,
    fake_provider: type,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plant, _, _, headers = scope
    monkeypatch.setattr(settings, "AI_REQUESTS_PER_MINUTE", 1)
    payload = {
        "plant_id": str(plant.id),
        "question": "查询测点",
        "allow_external_processing": True,
    }
    assert (
        client.post(f"{PREFIX}/ask", headers=headers, json=payload).status_code == 200
    )
    assert (
        client.post(f"{PREFIX}/ask", headers=headers, json=payload).status_code == 429
    )


def test_chunk_spans_and_lexical_baseline() -> None:
    content = "质量码与测点数据" * 200
    chunks = split_content(content)
    assert all(
        content[chunk["start"] : chunk["end"]] == chunk["text"] for chunk in chunks
    )
    assert "质量" in tokens("质量码 OPCUA")
    assert cosine([1, 0], [1, 0]) == 1
    assert cosine([0, 0], [1, 1]) == 0


def test_embedding_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "AI_EMBEDDING_URL", "https://embedding.example/embeddings"
    )
    monkeypatch.setattr(
        provider,
        "post_json",
        lambda *args, **kwargs: {"data": [{"index": 0, "embedding": [float("nan")]}]},
    )
    with pytest.raises(ProviderError):
        provider.embed(["测试文档"])


def test_document_answer_has_real_versioned_citation(
    client: TestClient, scope: tuple, fake_provider: type
) -> None:
    plant, _, _, headers = scope
    created = client.post(
        f"{PREFIX}/documents?plant_id={plant.id}",
        headers=headers,
        json={
            "title": "质量码",
            "content": "质量码为Bad时，不能作为正常的工艺测量值使用。",
        },
    )
    assert created.status_code == 200

    def callback(messages: Any, tools: Any) -> dict[str, str]:
        evidence = json.loads(messages[1]["content"])["evidence"]
        return {
            "content": json.dumps(
                {
                    "status": "answered",
                    "answer": "坏质量不可作为正常工艺值。",
                    "citation_ids": [evidence[0]["id"]],
                }
            )
        }

    fake_provider.callback = staticmethod(callback)
    response = client.post(
        f"{PREFIX}/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "质量码为Bad时怎么办？",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 200
    assert ":v1:c0" in response.json()["citation_ids"][0]


def test_changed_document_during_generation_is_not_returned(
    client: TestClient, db: Session, scope: tuple, fake_provider: type
) -> None:
    from app.models import KnowledgeDocument

    plant, _, _, headers = scope
    created = client.post(
        f"{PREFIX}/documents?plant_id={plant.id}",
        headers=headers,
        json={
            "title": "质量码",
            "content": "质量码表示采集数据质量，不能忽略质量标志。",
        },
    )
    document_id = uuid.UUID(created.json()["id"])

    def callback(messages: Any, tools: Any) -> dict[str, str]:
        evidence = json.loads(messages[1]["content"])["evidence"]
        document = db.get(KnowledgeDocument, document_id)
        assert document is not None
        db.delete(document)
        db.commit()
        return {
            "content": json.dumps(
                {
                    "status": "answered",
                    "answer": "这段内容已被删除。",
                    "citation_ids": [evidence[0]["id"]],
                }
            )
        }

    fake_provider.callback = staticmethod(callback)
    response = client.post(
        f"{PREFIX}/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "质量码如何处理？",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 502 and "knowledge_changed_retry" in response.text


def test_revoked_access_during_generation_is_not_returned(
    client: TestClient, db: Session, scope: tuple, fake_provider: type
) -> None:
    plant, _, user, headers = scope

    def callback(messages: Any, tools: Any) -> dict[str, str]:
        grant = db.get(UserPlantAccess, (user.id, plant.id))
        assert grant is not None
        db.delete(grant)
        db.commit()
        return {
            "content": json.dumps(
                {
                    "status": "no_answer",
                    "answer": "不应返回给失去授权的用户。",
                    "citation_ids": [],
                }
            )
        }

    fake_provider.callback = staticmethod(callback)
    response = client.post(
        f"{PREFIX}/ask",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "question": "查询测点",
            "allow_external_processing": True,
        },
    )
    assert response.status_code == 403


def test_semantic_retrieval_is_scoped_and_distinguished(
    client: TestClient, db: Session, scope: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant import retrieval

    plant, other, user, headers = scope
    monkeypatch.setattr(
        settings, "AI_EMBEDDING_URL", "https://embedding.example/embeddings"
    )
    monkeypatch.setattr(settings, "AI_EMBEDDING_MODEL", "test-embedding")
    monkeypatch.setattr(retrieval, "embed", lambda texts: [[1.0, 0.0] for _ in texts])
    created = client.post(
        f"{PREFIX}/documents?plant_id={plant.id}",
        headers=headers,
        json={
            "title": "规则",
            "content": "采集数据需要检查来源时间以确认是否过期。",
            "allow_external_processing": True,
        },
    )
    assert created.status_code == 200
    assert created.json()["embedding_model"] == "test-embedding"
    hits, mode = retrieval.retrieve(
        session=db, user=user, plant_id=plant.id, query="新鲜程度", allow_external=True
    )
    assert mode == "hybrid" and hits
    with pytest.raises(HTTPException):
        retrieval.retrieve(
            session=db,
            user=user,
            plant_id=other.id,
            query="新鲜程度",
            allow_external=True,
        )
    monkeypatch.setattr(retrieval, "embed", lambda texts: [[0.6, 0.8] for _ in texts])
    monkeypatch.setattr(settings, "AI_MIN_SEMANTIC_SCORE", 0.65)
    hits, _ = retrieval.retrieve(
        session=db, user=user, plant_id=plant.id, query="新鲜程度", allow_external=True
    )
    assert hits == []
    monkeypatch.setattr(settings, "AI_MIN_SEMANTIC_SCORE", 0.50)
    hits, _ = retrieval.retrieve(
        session=db, user=user, plant_id=plant.id, query="新鲜程度", allow_external=True
    )
    assert hits and hits[0].data["semantic_score"] == 0.6
