import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from app.assistant import provider, retrieval
from app.core.config import settings


@pytest.fixture
def benchmark() -> Any:
    path = Path(__file__).resolve().parents[3] / "scripts/assistant_benchmark.py"
    spec = importlib.util.spec_from_file_location("assistant_benchmark", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_benchmark_integrity_and_no_old_question_reuse(benchmark: Any) -> None:
    raw = benchmark.CORPUS.read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "647f59b52981d38788a1f8cf4ad6f70706f3dd44863668e17fc9b2b3464e3672"
    )
    corpus = json.loads(raw)
    cases = corpus["cases"]
    assert len({case["id"] for case in cases}) == len(cases) == 30
    assert sum(case["split"] == "holdout" for case in cases) == 26
    old = json.loads(benchmark.CORPUS.with_name("eval_cases.json").read_text())
    assert not {row["question"] for row in cases} & {
        row["question"] for row in old["cases"]
    }
    assert all(case["reference"] for case in cases)


def test_retrieval_metrics_count_all_required_documents(benchmark: Any) -> None:
    result = benchmark.retrieval_metrics(["wrong", "A", "A"], ["A", "B"])
    assert result["recall_at_5"] == 0.5
    assert result["reciprocal_rank"] == 0.5
    assert result["all_sources_at_5"] is False
    empty = benchmark.retrieval_metrics(["wrong"], [])
    assert empty["recall_at_5"] is None
    assert empty["returned_on_unanswerable"] is True


def test_structure_scoring_rejects_wrong_parameters_and_false_citations(
    benchmark: Any,
) -> None:
    case = {
        "statuses": ["answered"],
        "tools": [
            {"name": "find_tags", "args": {}},
            {"name": "latest_value", "args": {"tag_id": "$TAG", "source": "file"}},
        ],
        "terms": [["0.288"]],
    }
    response = {
        "status": "answered",
        "answer": "0.288",
        "citation_ids": ["t1"],
        "evidence": [{"id": "t1", "kind": "tool", "title": "latest_value"}],
    }
    calls = [
        {"name": "find_tags", "args": {}, "success": True},
        {
            "name": "latest_value",
            "args": {"tag_id": "abc", "source": "opcua"},
            "success": True,
        },
    ]
    score = benchmark.score_structure(case, response, calls, {"TAG": "abc"}, [])
    assert score["structural_pass"] is False
    calls[1]["args"]["source"] = "file"
    score = benchmark.score_structure(case, response, calls, {"TAG": "abc"}, [])
    assert score["structural_pass"] is True
    assert score["human_task_verdict"] is None
    response["citation_ids"] = ["invented"]
    assert not benchmark.score_structure(case, response, calls, {"TAG": "abc"}, ["A"])[
        "structural_pass"
    ]
    response["evidence"][0]["data"] = {"secret": "CANARY_73921"}
    assert not benchmark.score_structure(case, response, calls, {"TAG": "abc"}, [])[
        "checks"
    ]["private_canaries_absent"]


def test_three_arm_retrieval_same_vector_and_private_scope(
    benchmark: Any, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings, "AI_EMBEDDING_URL", "https://example.invalid/embeddings"
    )
    monkeypatch.setattr(settings, "AI_EMBEDDING_MODEL", "test")
    requests = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        requests.append(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(retrieval, "embed", fake_embed)
    monkeypatch.setattr(provider, "embed", fake_embed)
    corpus = json.loads(benchmark.CORPUS.read_text())
    fixture = benchmark.seed(db, corpus, True)
    case = next(row for row in corpus["cases"] if row["id"] == "R10")
    count = len(requests)
    results = benchmark.compare_retrieval(db, fixture, case, True)
    assert len(requests) == count + 1  # shared query embedding, not two paid calls
    for mode in ["lexical", "vector", "hybrid"]:
        assert mode in results
        assert "相邻工厂保密测试规程" not in results[mode]["titles"]
    assert results["vector"]["mode"] == "vector"
    assert results["hybrid"]["mode"] == "hybrid"

    def unavailable(_texts: list[str]) -> Any:
        raise provider.ProviderError("provider_timeout")

    monkeypatch.setattr(retrieval, "embed", unavailable)
    monkeypatch.setattr(provider, "embed", unavailable)
    args = {
        "session": db,
        "user": fixture["user"],
        "plant_id": fixture["plant"].id,
        "query": case["question"],
        "allow_external": True,
    }
    with pytest.raises(provider.ProviderError, match="provider_timeout"):
        retrieval.retrieve(**args, strategy="vector")
    _, mode = retrieval.retrieve(**args)
    assert mode == "lexical_embedding_unavailable"  # production behavior preserved
    _, mode = retrieval.retrieve(**args, strategy="lexical")
    assert mode == "lexical"  # explicit lexical never uses a paid service
    results = benchmark.compare_retrieval(db, fixture, case, True)
    assert results["embedding_error"] == "provider_timeout" and "vector" not in results
    monkeypatch.setattr(settings, "AI_EMBEDDING_URL", "")
    with pytest.raises(provider.ProviderError, match="embedding_required"):
        retrieval.retrieve(**args, strategy="vector")


def test_live_benchmark_uses_real_tools_and_keeps_failed_requests(
    benchmark: Any, db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    corpus = json.loads(benchmark.CORPUS.read_text())
    fixture = benchmark.seed(db, corpus, False)
    case = next(row for row in corpus["cases"] if row["id"] == "B07")

    class FakeProvider:
        prompt_tokens = 12
        completion_tokens = 8
        step = 0

        def chat(self, messages: list[dict], _tools: Any) -> dict:
            self.step += 1
            if self.step <= 2:
                name = "find_tags" if self.step == 1 else "latest_value"
                args = (
                    {"query": "BQ_INLET"}
                    if self.step == 1
                    else {"tag_id": fixture["refs"]["BQ_INLET"], "source": "file"}
                )
                return {
                    "tool_calls": [
                        {
                            "id": f"call{self.step}",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ]
                }
            evidence = json.loads(messages[-1]["content"])
            return {
                "content": json.dumps(
                    {
                        "status": "answered",
                        "answer": "历史文件值0.288",
                        "citation_ids": [evidence["id"]],
                    }
                )
            }

    monkeypatch.setattr(benchmark.service, "DeepSeekProvider", FakeProvider)
    result = benchmark.live_case(db, fixture, case)
    assert result["request_success"] and result["structural_pass"], result
    assert result["sample_snapshot_unchanged"]
    assert result["calls"][-1]["args"]["source"] == "file"
    assert result["human_task_verdict"] is None
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    result = benchmark.live_case(db, fixture, case)
    assert result["request_success"] is False and result["http_status"] == 503
    assert result["structural_pass"] is False
    path = tmp_path / "evidence.json"
    benchmark.save(path, result)
    with pytest.raises(FileExistsError):
        benchmark.save(path, result)


def test_benchmark_cli_refuses_ordinary_test_database(
    benchmark: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    monkeypatch.setattr(
        sys, "argv", ["benchmark", "--output", str(tmp_path / "report")]
    )
    with pytest.raises(SystemExit):
        benchmark.main()
    assert not (tmp_path / "report").exists()
