"""Synthetic evaluation. Must use an isolated *_test database, never business data.

Retrieval mode is free/local; live mode is explicitly opt-in and bounded.
Answers require manual semantic review: schema/citation validity != correctness.
"""

import argparse
import hashlib
import json
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session

from app.assistant.provider import embedding_enabled
from app.assistant.retrieval import prepare_chunks, retrieve
from app.assistant.schemas import Question
from app.assistant.service import answer_question
from app.core.config import settings
from app.core.db import engine
from app.models import (
    KnowledgeDocument,
    Plant,
    User,
    UserPlantAccess,
    UserRole,
    get_datetime_utc,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=["retrieval", "hybrid", "live"], default="retrieval"
    )
    parser.add_argument("--split", choices=["dev", "heldout", "all"], default="heldout")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Select known case IDs for targeted regression; repeatable",
    )
    parser.add_argument("--allow-billed-requests", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (settings.DATABASE_URL.path or "").rstrip("/").endswith("_test"):
        parser.error("Refusing evaluation outside an isolated *_test database")
    if not 1 <= args.limit <= 40:
        parser.error("--limit must be between 1 and 40")
    if args.output.exists():
        parser.error("Output exists; choose a new filename to preserve evidence")
    if args.mode == "live" and (
        not args.allow_billed_requests or not settings.DEEPSEEK_API_KEY
    ):
        parser.error("Live mode requires a configured key and --allow-billed-requests")
    if args.mode == "hybrid" and (
        not args.allow_billed_requests or not embedding_enabled()
    ):
        parser.error(
            "Hybrid mode requires an embedding endpoint and --allow-billed-requests"
        )
    corpus_path = Path(__file__).with_name("eval_cases.json")
    corpus = json.loads(corpus_path.read_text())
    if set(args.case_id) - {
        case["id"] for case in corpus["cases"] if case["kind"] != "fault"
    }:
        parser.error("Unknown or protocol-only case ID")
    results: list[dict[str, Any]] = []
    with Session(engine) as session:
        suffix = uuid.uuid4().hex[:10]
        plant = Plant(code=f"EVAL_{suffix.upper()}", name="AI模拟评测专用工厂")
        user = User(
            email=f"eval-{suffix}@example.com",
            hashed_password="not-a-login-account",
            role=UserRole.ENGINEER,
        )
        session.add(plant)
        session.add(user)
        session.commit()
        session.add(UserPlantAccess(user_id=user.id, plant_id=plant.id))
        for source in corpus["documents"]:
            chunks, embedding_model = prepare_chunks(
                source["content"], allow_external=args.mode != "retrieval"
            )
            session.add(
                KnowledgeDocument(
                    plant_id=plant.id,
                    created_by_id=user.id,
                    title=source["title"],
                    content=source["content"],
                    chunks=chunks,
                    embedding_model=embedding_model,
                    content_sha256=hashlib.sha256(
                        source["content"].encode()
                    ).hexdigest(),
                )
            )
        session.commit()
        cases = [
            case
            for case in corpus["cases"]
            if case["kind"] != "fault"
            and (not args.case_id or case["id"] in args.case_id)
            and (args.split == "all" or case["split"] == args.split)
        ]
        if args.mode in {"retrieval", "hybrid"}:
            cases = [case for case in cases if "document" in case]
        for index, case in enumerate(cases[: args.limit]):
            started = time.monotonic()
            result: dict[str, Any] = {
                "case_id": case["id"],
                "question": case["question"],
                "expected": case["expected"],
                "human_verdict": None,
            }
            if args.mode in {"retrieval", "hybrid"}:
                if args.mode == "hybrid":
                    baseline, _ = retrieve(
                        session=session,
                        user=user,
                        plant_id=plant.id,
                        query=case["question"],
                    )
                    result["lexical_titles"] = [hit.title for hit in baseline]
                    result["lexical_hit_at_5"] = any(
                        hit.title == case["document"] for hit in baseline
                    )
                evidence, mode = retrieve(
                    session=session,
                    user=user,
                    plant_id=plant.id,
                    query=case["question"],
                    allow_external=args.mode == "hybrid",
                )
                result.update(
                    {
                        "retrieval_mode": mode,
                        "titles": [hit.title for hit in evidence],
                        "hit_at_5": any(
                            hit.title == case["document"] for hit in evidence
                        ),
                    }
                )
            else:
                try:
                    answer = answer_question(
                        session=session,
                        user=user,
                        request=Question(
                            plant_id=plant.id,
                            question=case["question"],
                            allow_external_processing=True,
                        ),
                    )
                    result["response"] = answer.model_dump(mode="json")
                    result["request_success"] = True
                except HTTPException as exc:
                    result.update(
                        {
                            "request_success": False,
                            "http_status": exc.status_code,
                            "error": exc.detail,
                        }
                    )
            result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            results.append(result)
            # Respect the same application quota; do not raise limits for benchmarks.
            if args.mode == "live" and index < len(cases[: args.limit]) - 1:
                time.sleep(60 / settings.AI_REQUESTS_PER_MINUTE + 1)
    report = {
        "created_at": get_datetime_utc().isoformat(),
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "mode": args.mode,
        "split": args.split,
        "selected_case_ids": args.case_id,
        "model": settings.DEEPSEEK_MODEL if args.mode == "live" else None,
        "embedding_model": settings.AI_EMBEDDING_MODEL
        if args.mode != "retrieval" and embedding_enabled()
        else None,
        "case_count": len(results),
        "min_semantic_score": settings.AI_MIN_SEMANTIC_SCORE,
        "task_accuracy": None,
        "warning": "Synthetic corpus only. Protocol faults run in pytest. Successful HTTP/schema/citation checks are NOT answer accuracy.",
        "median_elapsed_ms": statistics.median(row["elapsed_ms"] for row in results)
        if results
        else None,
        "retrieval_hit_at_5": sum(row.get("hit_at_5", False) for row in results)
        / len(results)
        if results and args.mode in {"retrieval", "hybrid"}
        else None,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(  # noqa: T201
        f"Saved {len(results)} {args.mode} cases to {args.output}; task accuracy requires human review."
    )


if __name__ == "__main__":
    main()
