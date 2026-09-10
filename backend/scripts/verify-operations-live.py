"""Opt-in paid smoke checks, not an independent accuracy benchmark."""

import argparse
import json
from pathlib import Path

from sqlmodel import Session, select

from app.assistant.schemas import Question
from app.assistant.service import answer_question
from app.core.config import settings
from app.core.db import engine
from app.models import AssistantRun, Plant, User


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-billed-requests", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_billed_requests or args.output.exists():
        parser.error("Explicit paid opt-in and a new report path are required")
    cases = [
        ("当前工厂各产线设备的测点如何分配？请查实际资产。", "asset_overview"),
        ("请查当前工厂采集任务的连接状态、心跳和累计丢弃数。", "acquisition_status"),
        (
            "请查询L2_PUMP_FLOW最近1小时的均值和最大值，没有数据请明确说明。",
            "tag_trend",
        ),
        ("文件里每列是一个变量，没有测点编码列，我该如何做导入映射？", "document"),
    ]
    results = []
    with Session(engine) as session:
        plant = session.exec(select(Plant).where(Plant.code == "OPC_DEMO")).one()
        user = session.exec(
            select(User).where(User.email == settings.FIRST_SUPERUSER)
        ).one()
        for question, expected in cases:
            answer = answer_question(
                session=session,
                user=user,
                request=Question(
                    plant_id=plant.id, question=question, allow_external_processing=True
                ),
            )
            run = session.get(AssistantRun, answer.run_id)
            assert run is not None
            checked = (
                any(
                    item.kind == "document" and item.id in answer.citation_ids
                    for item in answer.evidence
                )
                if expected == "document"
                else expected in run.tool_names
            )
            results.append(
                {
                    "question": question,
                    "expected": expected,
                    "expected_evidence_observed": checked,
                    "tool_names": run.tool_names,
                    "error_code": run.error_code,
                    "response": answer.model_dump(mode="json"),
                }
            )
    args.output.write_text(
        json.dumps(
            {
                "scope": "four functional smoke questions; not accuracy or SLA",
                "cases": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if not all(
        item["expected_evidence_observed"] and item["response"]["status"] == "answered"
        for item in results
    ):
        raise SystemExit("One or more smoke checks failed; inspect the saved report")


if __name__ == "__main__":
    main()
