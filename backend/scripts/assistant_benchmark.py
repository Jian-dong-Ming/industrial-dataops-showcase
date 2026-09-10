"""Frozen synthetic benchmark; never connect this runner to a business database.

No changes to prompts or references are made by this runner. All traces contain
only newly seeded synthetic data. Ordinary application audit logging is unchanged.
"""

import argparse
import hashlib
import json
import statistics
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import Session

from app.assistant import (
    answer_checks,
    capabilities,
    provider,
    retrieval,
    service,
    tools,
)
from app.assistant.schemas import Question
from app.assistant.tools import TOOL_MODELS, execute_tool
from app.core.config import settings
from app.core.db import engine
from app.models import (
    AcquisitionTask,
    Device,
    ImportBatch,
    ImportBatchStatus,
    ImportFileFormat,
    KnowledgeDocument,
    Plant,
    ProductionLine,
    SampleSourceType,
    Tag,
    TagSample,
    User,
    UserPlantAccess,
    UserRole,
    get_datetime_utc,
)

CORPUS = Path(__file__).resolve().parents[1] / "app/assistant/benchmark_v1.json"


def save(path: Path, value: Any) -> None:
    # Exclusive creation prevents overwriting first-run failures or cherry picking.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def seed(session: Session, corpus: dict[str, Any], external: bool) -> dict[str, Any]:
    suffix = uuid.uuid4().hex
    plant = Plant(code=f"BENCH_{suffix[:16]}", name="碧泉站（独立合成评测）")
    private = Plant(code=f"PRIVATE_{suffix[:16]}", name="相邻工厂（合成隔离）")
    user = User(
        email=f"bench-{suffix}@example.com",
        hashed_password="not-a-login",
        role=UserRole.ENGINEER,
    )
    session.add_all([plant, private, user])
    session.commit()
    session.add(UserPlantAccess(user_id=user.id, plant_id=plant.id))
    line = ProductionLine(
        plant_id=plant.id, code="BQ", name="碧泉测试线", process_type="continuous"
    )
    session.add(line)
    session.commit()
    device = Device(
        production_line_id=line.id,
        code="BQ_FILTER",
        name="碧泉过滤装置",
        device_type="simulator",
    )
    session.add(device)
    session.commit()
    tags = {}
    for code, name, unit in [
        ("BQ_INLET", "入口压力", "MPa"),
        ("BQ_OUTLET", "出口压力", "MPa"),
        ("BQ_OLD", "循环流量", "L/min"),
        ("BQ_EMPTY", "备用压力", "MPa"),
        ("BQ_TA", "出口温度", "℃"),
        ("BQ_TB", "出口温度", "℃"),
        ("BQ_VIB", "泵组振动", "mm/s"),
    ]:
        tag = Tag(device_id=device.id, code=code, name=name, unit=unit)
        tags[code] = tag
        session.add(tag)
    task = AcquisitionTask(
        plant_id=plant.id,
        name="碧泉合成快照（无设备连接）",
        endpoint_url="opc.tcp://example.invalid:4840",
    )
    session.add(task)
    batches = {}
    for key, status, age in [
        ("history", ImportBatchStatus.COMPLETED, 86400),
        ("pending", ImportBatchStatus.QUEUED, 0),
    ]:
        batch = ImportBatch(
            plant_id=plant.id,
            created_by_id=user.id,
            original_filename=f"synthetic-{key}.csv",
            file_format=ImportFileFormat.CSV,
            file_size_bytes=0,
            file_sha256="a" * 64,
            status=status,
            created_at=get_datetime_utc() - timedelta(seconds=age),
        )
        batches[key] = batch
        session.add(batch)
    session.commit()
    documents = {}
    for source in corpus["documents"]:
        chunks, model = retrieval.prepare_chunks(
            source["content"], allow_external=external
        )
        doc = KnowledgeDocument(
            plant_id=private.id if source.get("private") else plant.id,
            created_by_id=user.id,
            title=source["title"],
            content=source["content"],
            version=source["version"],
            content_sha256=hashlib.sha256(source["content"].encode()).hexdigest(),
            chunks=chunks,
            embedding_model=model,
        )
        session.add(doc)
        documents[source["id"]] = doc
    session.commit()
    samples = []
    for code, value, age, good, file_source in [
        ("BQ_INLET", 0.412, 1800, True, False),
        ("BQ_INLET", 0.442, 900, True, False),
        ("BQ_INLET", 0.472, 1, True, False),
        ("BQ_OUTLET", 0.316, 1, False, False),
        ("BQ_OLD", 14.625, 10800, True, False),
        ("BQ_INLET", 0.288, 86400, True, True),
    ]:
        sample = TagSample(
            tag_id=tags[code].id,
            value=value,
            numeric_value=value,
            source_timestamp=get_datetime_utc() - timedelta(seconds=age),
            status_code="Good" if good else "Bad",
            is_good=good,
            source_type=SampleSourceType.FILE
            if file_source
            else SampleSourceType.OPCUA,
            task_id=None if file_source else task.id,
            import_batch_id=batches["history"].id if file_source else None,
        )
        session.add(sample)
        samples.append((sample, age))
    session.commit()
    return {
        "plant": plant,
        "private": private,
        "user": user,
        "tags": tags,
        "batches": batches,
        "documents": documents,
        "samples": samples,
        "refs": {
            **{code: str(tag.id) for code, tag in tags.items()},
            **{key: str(batch.id) for key, batch in batches.items()},
        },
    }


def retrieval_metrics(titles: list[str], required: list[str]) -> dict[str, Any]:
    unique = list(dict.fromkeys(titles))[:5]
    hits = set(unique) & set(required)
    return {
        "recall_at_5": len(hits) / len(set(required)) if required else None,
        "all_sources_at_5": set(required) <= set(unique) if required else None,
        "reciprocal_rank": next(
            (1 / i for i, title in enumerate(unique, 1) if title in required), 0.0
        )
        if required
        else None,
        "returned_on_unanswerable": bool(unique) if not required else None,
        "titles": unique,
    }


def compare_retrieval(
    session: Session, fixture: dict[str, Any], case: dict[str, Any], external: bool
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "session": session,
        "user": fixture["user"],
        "plant_id": fixture["plant"].id,
        "query": case["question"],
    }
    expected = [fixture["documents"][key].title for key in case["documents"]]
    rows: dict[str, Any] = {}
    started = time.monotonic()
    lexical, mode = retrieval.retrieve(**args, strategy="lexical")
    rows["lexical"] = {
        **retrieval_metrics([item.title for item in lexical], expected),
        "mode": mode,
        "ranking_ms": (time.monotonic() - started) * 1000,
    }
    if external:
        started = time.monotonic()
        try:
            vector = provider.embed([case["question"]])
        except provider.ProviderError as exc:
            rows["embedding_error"] = exc.code
            return rows
        rows["shared_query_embedding_ms"] = (time.monotonic() - started) * 1000
        # Exactly the same query vector/corpus for both arms, no paid duplicate.
        with patch.object(retrieval, "embed", return_value=vector):
            strategies: list[tuple[str, Literal["vector", "auto"]]] = [
                ("vector", "vector"),
                ("hybrid", "auto"),
            ]
            for label, strategy in strategies:
                started = time.monotonic()
                evidence, mode = retrieval.retrieve(
                    **args, allow_external=True, strategy=strategy
                )
                rows[label] = {
                    **retrieval_metrics([item.title for item in evidence], expected),
                    "mode": mode,
                    "ranking_ms": (time.monotonic() - started) * 1000,
                }
    return rows


def score_structure(
    case: dict[str, Any],
    response: dict[str, Any],
    calls: list[dict[str, Any]],
    refs: dict[str, str],
    document_titles: list[str],
) -> dict[str, Any]:
    cursor = 0
    matches = []
    for requirement in case.get("tools", []):
        expected = {
            key: refs[value[1:]]
            if isinstance(value, str) and value.startswith("$")
            else value
            for key, value in requirement["args"].items()
        }
        found = next(
            (
                i
                for i in range(cursor, len(calls))
                if calls[i]["name"] == requirement["name"]
                and calls[i].get("success")
                and all(
                    calls[i].get("args", {}).get(key) == value
                    for key, value in expected.items()
                )
            ),
            None,
        )
        matches.append(found is not None)
        if found is not None:
            cursor = found + 1
    ids = set(response.get("citation_ids", []))
    evidence = response.get("evidence", [])
    cited_titles = {
        item["title"]
        for item in evidence
        if item["id"] in ids and item["kind"] == "document"
    }
    text = response.get("answer", "")
    # Visible response includes the evidence panel, not merely generated text.
    serialized = json.dumps(response, ensure_ascii=False)
    checks = {
        "status_allowed": response.get("status") in case["statuses"],
        "required_tools_and_parameters_in_order": all(matches),
        "forbidden_tools_absent": not any(
            call["name"] in case.get("forbidden_tools", []) for call in calls
        ),
        "no_tool_errors": all(call.get("success") for call in calls),
        "citation_ids_valid": ids <= {item["id"] for item in evidence}
        and (response.get("status") != "answered" or bool(ids)),
        "required_documents_cited": set(document_titles) <= cited_titles,
        "reference_terms_present": all(
            any(term in text for term in group) for group in case.get("terms", [])
        ),
        "private_canaries_absent": "CANARY_73921" not in serialized
        and "PRIVATE_482" not in serialized,
    }
    return {
        "checks": checks,
        "structural_pass": all(checks.values()),
        "human_task_verdict": None,
        "human_notes": None,
        "warning": "Structural/substring checks do not establish semantic task correctness.",
    }


def live_case(
    session: Session, fixture: dict[str, Any], case: dict[str, Any]
) -> dict[str, Any]:
    # Refresh synthetic timestamps before every request. Never touch business data.
    now = get_datetime_utc()
    for sample, age in fixture["samples"]:
        sample.source_timestamp = now - timedelta(seconds=age)
        session.add(sample)
    session.commit()
    for sample, _ in fixture["samples"]:
        session.refresh(sample)
    before = [sample.model_dump(mode="json") for sample, _ in fixture["samples"]]
    calls: list[dict[str, Any]] = []

    def observed(**kwargs: Any) -> Any:
        entry: dict[str, Any] = {"name": kwargs["name"], "success": False}
        calls.append(entry)
        model = TOOL_MODELS.get(kwargs["name"])
        if model:
            entry["args"] = model.model_validate_json(kwargs["arguments"]).model_dump(
                mode="json"
            )
        result = execute_tool(**kwargs)
        entry["success"] = True
        return result

    result: dict[str, Any] = {"fixture_time": now.isoformat()}
    try:
        with patch.object(service, "execute_tool", side_effect=observed):
            answer = service.answer_question(
                session=session,
                user=fixture["user"],
                request=Question(
                    plant_id=fixture["plant"].id,
                    question=case["question"],
                    allow_external_processing=True,
                ),
            )
        response = answer.model_dump(mode="json")
        result.update(
            {
                "request_success": True,
                "response": response,
                **score_structure(
                    case,
                    response,
                    calls,
                    fixture["refs"],
                    [
                        fixture["documents"][key].title
                        for key in case.get("documents", [])
                    ],
                ),
            }
        )
    except HTTPException as exc:
        result.update(
            {
                "request_success": False,
                "http_status": exc.status_code,
                "structural_pass": False,
                "human_task_verdict": None,
            }
        )
    for sample, _ in fixture["samples"]:
        session.refresh(sample)
    after = [sample.model_dump(mode="json") for sample, _ in fixture["samples"]]
    result.update({"calls": calls, "sample_snapshot_unchanged": before == after})
    result["structural_pass"] = bool(result["structural_pass"] and before == after)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=["lexical", "retrieval", "live"], default="lexical"
    )
    parser.add_argument("--split", choices=["dev", "holdout"], default="dev")
    parser.add_argument(
        "--evaluation-role", choices=["first_run", "regression"], default="regression"
    )
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--allow-billed-requests", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        engine.url.database != "app_ai_independent_test"
        or (settings.DATABASE_URL.path or "").strip("/") != "app_ai_independent_test"
    ):
        parser.error(
            "Only app_ai_independent_test is allowed; runner never deletes databases"
        )
    if not 1 <= args.limit <= 30:
        parser.error("limit must be 1..30")
    external = args.mode != "lexical"
    if external and (
        not args.allow_billed_requests
        or not provider.embedding_enabled()
        or (args.mode == "live" and not settings.DEEPSEEK_API_KEY)
    ):
        parser.error(
            "External modes need explicit billed consent and configured services"
        )
    corpus_bytes = CORPUS.read_bytes()
    corpus = json.loads(corpus_bytes)
    cases = [
        case
        for case in corpus["cases"]
        if case["split"] == args.split
        and (args.mode == "live" or case["kind"] == "document")
    ][: args.limit]
    args.output.mkdir(parents=True, exist_ok=False)
    save(
        args.output / "plan.json",
        {
            "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
            "case_ids": [case["id"] for case in cases],
            "mode": args.mode,
            "split": args.split,
            "evaluation_role": args.evaluation_role,
            "rubric": corpus["rubric"],
            "model": settings.DEEPSEEK_MODEL,
            "embedding_model": settings.AI_EMBEDDING_MODEL if external else None,
            "semantic_cutoff": settings.AI_MIN_SEMANTIC_SCORE,
            "code_hashes": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [
                    Path(__file__),
                    Path(retrieval.__file__),
                    Path(service.__file__),
                    Path(tools.__file__),
                    Path(answer_checks.__file__),
                    Path(capabilities.__file__),
                ]
            },
        },
    )
    usage: list[dict[str, Any]] = []
    original_post = provider.post_json

    def metered(
        url: str, key: str, payload: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "kind": "chat" if "messages" in payload else "embedding",
            "success": False,
            "usage": None,
        }
        usage.append(entry)
        try:
            body = original_post(url, key, payload, timeout=timeout)
            entry.update({"success": True, "usage": body.get("usage")})
            return body
        finally:
            # No credentials, URLs, input text or raw provider bodies in usage logs.
            save(args.output / f"usage-{len(usage):04d}.json", entry)

    results = []
    with (
        patch.object(provider, "post_json", side_effect=metered),
        Session(engine) as session,
    ):
        fixture = seed(session, corpus, external)
        for index, case in enumerate(cases):
            save(
                args.output / f"{case['id']}-started.json",
                {
                    "question": case["question"],
                    "reference": case["reference"],
                    "started_at": get_datetime_utc().isoformat(),
                },
            )
            started = time.monotonic()
            result: dict[str, Any] = {
                "case_id": case["id"],
                "question": case["question"],
                "reference": case["reference"],
                "human_task_verdict": None,
            }
            try:
                if args.mode == "live":
                    result.update(live_case(session, fixture, case))
                else:
                    result["retrieval"] = compare_retrieval(
                        session, fixture, case, external
                    )
            except Exception as exc:
                session.rollback()
                result.update(
                    {
                        "request_success": False,
                        "error_type": type(exc).__name__,
                        "structural_pass": False,
                    }
                )
            result["wall_ms"] = (time.monotonic() - started) * 1000
            save(args.output / f"{case['id']}.json", result)
            results.append(result)
            print(  # noqa: T201
                f"Completed {case['id']} ({index + 1}/{len(cases)}); semantic review still required",
                flush=True,
            )  # noqa: T201
            if args.mode == "live" and index + 1 < len(cases):
                time.sleep(60 / settings.AI_REQUESTS_PER_MINUTE + 1)
    save(
        args.output / "summary.json",
        {
            "planned": len(cases),
            "completed": len(results),
            "structural_pass_count": sum(
                row.get("structural_pass", False) for row in results
            )
            if args.mode == "live"
            else None,
            "task_accuracy": None,
            "evaluation_role": args.evaluation_role,
            "median_wall_ms": statistics.median(row["wall_ms"] for row in results)
            if results
            else None,
            "provider_logical_requests": len(usage),
            "currency_cost": None,
            "warning": "Synthetic evidence only; observed holdout questions are regression data, not independent evaluation after tuning. Provider usage may omit failed attempts and embedding tokens; no invented zero cost or semantic accuracy. Per-request latency excludes queue/rate-limit waiting.",
        },
    )


if __name__ == "__main__":
    main()
