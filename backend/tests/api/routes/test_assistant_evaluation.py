import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import PostgresDsn

from app.assistant import evaluate
from app.core.config import settings


@pytest.mark.parametrize(
    "case_args,expected_count", [([], 15), (["--case-id", "N01"], 1)]
)
def test_retrieval_evaluation_produces_reproducible_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_args: list[str],
    expected_count: int,
) -> None:
    target = tmp_path / "retrieval.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate",
            "--mode",
            "retrieval",
            "--split",
            "all",
            "--limit",
            "40",
            "--output",
            str(target),
            *case_args,
        ],
    )
    evaluate.main()
    report = json.loads(target.read_text())
    assert report["case_count"] == expected_count
    assert report["task_accuracy"] is None
    assert len(report["corpus_sha256"]) == 64
    assert all(row["human_verdict"] is None for row in report["results"])
    assert "Synthetic" in report["warning"]
    with pytest.raises(SystemExit):
        evaluate.main()  # Evidence must not be silently overwritten.


@pytest.mark.parametrize(
    "extra",
    [
        ["--limit", "0"],
        ["--limit", "41"],
        ["--mode", "live"],
        ["--case-id", "UNKNOWN"],
        ["--case-id", "F01"],
    ],
)
def test_evaluation_guardrails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]
) -> None:
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(
        sys, "argv", ["evaluate", "--output", str(tmp_path / "report.json"), *extra]
    )
    with pytest.raises(SystemExit):
        evaluate.main()
    assert not (tmp_path / "report.json").exists()


def test_evaluation_refuses_business_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings, "DATABASE_URL", PostgresDsn("postgresql://localhost/app")
    )
    monkeypatch.setattr(
        sys, "argv", ["evaluate", "--output", str(tmp_path / "report.json")]
    )
    with pytest.raises(SystemExit):
        evaluate.main()


def test_live_evaluation_records_failures_without_inventing_accuracy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-placeholder")
    monkeypatch.setattr(settings, "AI_EMBEDDING_URL", "")
    waits = []
    monkeypatch.setattr(evaluate.time, "sleep", waits.append)

    def failure(**_kwargs: Any) -> Any:
        raise HTTPException(502, "provider_timeout")

    monkeypatch.setattr(evaluate, "answer_question", failure)
    target = tmp_path / "live-failure.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate",
            "--mode",
            "live",
            "--split",
            "dev",
            "--limit",
            "2",
            "--allow-billed-requests",
            "--output",
            str(target),
        ],
    )
    evaluate.main()
    report = json.loads(target.read_text())
    assert report["case_count"] == 2
    assert report["task_accuracy"] is None and report["retrieval_hit_at_5"] is None
    assert all(
        not row["request_success"] and row["http_status"] == 502
        for row in report["results"]
    )
    assert len(waits) == 1 and waits[0] >= 10


def test_live_evaluation_records_response_but_requires_human_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-placeholder")
    monkeypatch.setattr(settings, "AI_EMBEDDING_URL", "")

    class ProtocolResponse:
        def model_dump(self, *, mode: str) -> dict[str, Any]:
            assert mode == "json"
            return {
                "status": "no_answer",
                "answer": "协议测试桩，不代表真实模型效果",
                "prompt_tokens": 1,
            }

    monkeypatch.setattr(
        evaluate, "answer_question", lambda **kwargs: ProtocolResponse()
    )
    target = tmp_path / "live-response.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate",
            "--mode",
            "live",
            "--limit",
            "1",
            "--allow-billed-requests",
            "--output",
            str(target),
        ],
    )
    evaluate.main()
    report = json.loads(target.read_text())
    assert report["results"][0]["request_success"] is True
    assert report["results"][0]["human_verdict"] is None
    assert report["task_accuracy"] is None


def test_hybrid_evaluation_compares_same_corpus_without_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant import retrieval

    monkeypatch.setattr(
        settings, "AI_EMBEDDING_URL", "https://embedding.example/v1/embeddings"
    )
    monkeypatch.setattr(settings, "AI_EMBEDDING_MODEL", "test-vector")
    monkeypatch.setattr(retrieval, "embed", lambda texts: [[1.0, 0.0] for _ in texts])

    def forbid_chat(**_kwargs: Any) -> Any:
        raise AssertionError("Hybrid retrieval evaluation must not call chat")

    monkeypatch.setattr(evaluate, "answer_question", forbid_chat)
    output = tmp_path / "hybrid.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate",
            "--mode",
            "hybrid",
            "--split",
            "all",
            "--limit",
            "2",
            "--allow-billed-requests",
            "--output",
            str(output),
        ],
    )
    evaluate.main()
    report = json.loads(output.read_text())
    assert report["model"] is None and report["embedding_model"] == "test-vector"
    assert report["case_count"] == 2
    assert all(
        row["retrieval_mode"] == "hybrid" and "lexical_hit_at_5" in row
        for row in report["results"]
    )


def test_hybrid_evaluation_requires_explicit_external_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["evaluate", "--mode", "hybrid", "--output", str(tmp_path / "no.json")],
    )
    with pytest.raises(SystemExit):
        evaluate.main()
