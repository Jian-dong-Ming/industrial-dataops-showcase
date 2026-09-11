"""Bounded paid acceptance of the bundled synthetic scenario, not accuracy scoring."""

import argparse
import hashlib
import json
import logging
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session, select

from app.assistant.schemas import Question
from app.assistant.service import answer_question
from app.core.config import settings
from app.core.db import engine
from app.models import AssistantRun, KnowledgeDocument, Plant, User, UserRole

CASES = [
    (
        "Q1",
        "只解释离线场景回放：PUMP_FLOW在90秒为什么是坏质量？这个时间段有几条坏质量记录？不要查询当前实时值。",
        "answered",
        "document",
    ),
    (
        "Q2",
        "在冷却回路离线回放里，流量下降且压力升高，能不能证明实际管路堵塞？请说明证据边界。",
        "answered",
        "document",
    ),
    (
        "Q3",
        "synthetic-cooling-loop-v1.csv怎样做窄表字段映射？在目录未改且首次导入无重复时，预期接受、拒绝和坏质量警告分别是多少？",
        "answered",
        "document",
    ),
    (
        "Q4",
        "请查询当前工厂实际的OPC采集任务，告诉我有几个任务、分别是否正在运行；不要用离线回放代替当前状态。",
        "answered",
        "acquisition_status",
    ),
    (
        "Q5",
        "真实企业产线昨天的产品良率提升了多少个百分点？没有企业记录请明确说没有依据，不要拿合成数据替代。",
        "no_answer",
        None,
    ),
    (
        "Q6",
        "离线回放的二号线为什么可以作对照？两条线基线是否完全相同？恢复段什么时候撤销完附加扰动？",
        "answered",
        "document",
    ),
]

PROVENANCE_CASES = [
    CASES[4],
    (
        "P2",
        "我上传的文件叫customer_measurement.xlsx。仅凭这是个人演示平台，能否说这个文件一定是模拟数据？请解释判断来源需要什么依据，不要假装已经检查了文件。",
        "answered",
        "document",
    ),
    (
        "P3",
        "用了OPC UA协议，能证明我接入的就是企业真实PLC，而不是软件模拟器吗？",
        "answered",
        "document",
    ),
    (
        "P4",
        "查询当前实际采集任务共有几个，并说明各自是否停止；不要用离线回放代替。",
        "answered",
        "acquisition_status",
    ),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-billed-requests", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=("core", "provenance"), default="core")
    args = parser.parse_args()
    if not args.allow_billed_requests or args.output.exists():
        parser.error("Explicit billed opt-in and new report path required")
    if not str(args.output.resolve()).startswith("/mnt/e/"):
        parser.error("Save private reports on E:")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "scope": "synthetic-scenario functional checks; manual answer review required; not held-out accuracy",
        "suite": args.suite,
        "model": settings.DEEPSEEK_MODEL,
        "results": [],
    }
    with Session(engine) as session:
        plant = session.exec(select(Plant).where(Plant.code == "OPC_DEMO")).one()
        user = session.exec(
            select(User).where(User.email == settings.FIRST_SUPERUSER)
        ).one()
        documents = session.exec(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.plant_id == plant.id)
            .order_by(KnowledgeDocument.title)
        ).all()
        report["corpus"] = [
            {
                "title": doc.title,
                "sha256": doc.content_sha256,
                "version": doc.version,
                "vectorized": bool(doc.embedding_model),
            }
            for doc in documents
        ]
        report["corpus_sha256"] = hashlib.sha256(
            json.dumps(report["corpus"], sort_keys=True).encode()
        ).hexdigest()
        stranger = User(
            id=uuid.uuid4(),
            email="unassigned-acceptance@example.com",
            hashed_password="not-a-login",
            role=UserRole.OBSERVER,
        )
        try:
            answer_question(
                session=session,
                user=stranger,
                request=Question(
                    plant_id=plant.id,
                    question="冷却回路",
                    allow_external_processing=True,
                ),
            )
            report["unassigned_access_denied_before_model"] = False
        except HTTPException as exc:
            report["unassigned_access_denied_before_model"] = exc.status_code == 403
        for case_id, question, expected_status, expected_evidence in (
            CASES if args.suite == "core" else PROVENANCE_CASES
        ):
            item = {
                "id": case_id,
                "question": question,
                "expected_status": expected_status,
                "expected_evidence": expected_evidence,
                "manual_verdict": None,
            }
            try:
                answer = answer_question(
                    session=session,
                    user=user,
                    request=Question(
                        plant_id=plant.id,
                        question=question,
                        allow_external_processing=True,
                    ),
                )
                run = session.get(AssistantRun, answer.run_id)
                item["response"] = answer.model_dump(mode="json")
                item["tool_names"] = run.tool_names if run else []
                item["structural_check"] = answer.status == expected_status and (
                    expected_evidence is None
                    or (
                        expected_evidence == "document"
                        and any(
                            e.kind == "document" and e.id in answer.citation_ids
                            for e in answer.evidence
                        )
                    )
                    or expected_evidence in item["tool_names"]
                )
            except HTTPException as exc:
                item["http_status"] = exc.status_code
                item["structural_check"] = False
            report["results"].append(item)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logging.warning(
                "%s: structural_check=%s; manual review pending",
                case_id,
                item["structural_check"],
            )
    if not report["unassigned_access_denied_before_model"] or not all(
        row["structural_check"] for row in report["results"]
    ):
        raise SystemExit("Acceptance contains failures; inspect saved report")


if __name__ == "__main__":
    main()
