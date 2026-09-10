"""Paired old/new query benchmark on a dedicated, bounded synthetic dataset.

No business database, provider calls or performance claims without a report.
Both queries run against identical rows; cold-cache latency is not measured.
"""

import argparse
import hashlib
import json
import random
import statistics
import uuid
from pathlib import Path
from time import perf_counter

from sqlalchemy import text
from sqlmodel import Session, col, func, select

from app.core.config import settings
from app.core.db import engine
from app.models import (
    AcquisitionNode,
    AcquisitionTask,
    Device,
    Plant,
    ProductionLine,
    Tag,
    TagSample,
)
from app.opcua.queries import latest_task_samples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tags", type=int, default=32)
    parser.add_argument("--samples-per-tag", type=int, default=6250)
    args = parser.parse_args()
    if settings.DATABASE_URL.path != "/app_perf_test":
        parser.error("Only the dedicated app_perf_test database is allowed")
    if (
        args.output.exists()
        or not 1 <= args.tags <= 200
        or not 1 <= args.samples_per_tag <= 10000
        or args.tags * args.samples_per_tag > 500000
    ):
        parser.error("New report required; at most 500,000 synthetic rows")
    with Session(engine) as session:
        suffix = uuid.uuid4().hex[:8].upper()
        plant = Plant(code=f"PERF_{suffix}", name="性能验收合成工厂")
        session.add(plant)
        session.flush()
        line = ProductionLine(
            plant_id=plant.id, code="LINE", name="性能验收线", process_type="synthetic"
        )
        task = AcquisitionTask(
            plant_id=plant.id,
            name="性能验收（停止状态）",
            endpoint_url="opc.tcp://example.invalid:4840",
        )
        session.add_all([line, task])
        session.flush()
        device = Device(
            production_line_id=line.id,
            code="DEVICE",
            name="性能合成设备",
            device_type="synthetic",
        )
        session.add(device)
        session.flush()
        for index in range(args.tags):
            tag = Tag(
                device_id=device.id, code=f"PERF_{index:03}", name=f"合成测点{index}"
            )
            session.add(tag)
            session.flush()
            session.add(
                AcquisitionNode(
                    task_id=task.id, tag_id=tag.id, node_id=f"ns=2;s=Perf{index}"
                )
            )
            session.execute(
                text("""INSERT INTO tag_sample
                (task_id,tag_id,source_type,value,numeric_value,source_timestamp,received_at,status_code,is_good)
                SELECT :task,:tag,'opcua',to_json(500.0+g%100),500.0+g%100,
                TIMESTAMPTZ '2026-01-01T00:00:00Z' + g * INTERVAL '1 second',
                TIMESTAMPTZ '2026-01-01T00:00:00Z' + g * INTERVAL '1 second','Good',true
                FROM generate_series(1,:n) g"""),
                {"task": task.id, "tag": tag.id, "n": args.samples_per_tag},
            )
        session.commit()
        session.execute(text("ANALYZE tag_sample"))
        ranked = (
            select(
                col(TagSample.id).label("sample_id"),
                func.row_number()
                .over(
                    partition_by=col(TagSample.tag_id),
                    order_by=(
                        col(TagSample.source_timestamp).desc(),
                        col(TagSample.id).desc(),
                    ),
                )
                .label("sample_rank"),
            )
            .where(TagSample.task_id == task.id)
            .subquery()
        )
        old = select(TagSample).where(
            col(TagSample.id).in_(
                select(ranked.c.sample_id).where(ranked.c.sample_rank == 1)
            )
        )
        new = latest_task_samples(task.id)
        statements = {"baseline_window_rank": old, "indexed_per_tag": new}
        times: dict[str, list[float]] = {name: [] for name in statements}
        expected = None
        for name in statements:  # Equal warm-up, excluded from recorded samples.
            session.exec(statements[name]).all()
        schedule = list(statements) * 20
        random.Random(20260908).shuffle(schedule)
        for name in schedule:
            session.expire_all()
            start = perf_counter()
            values = session.exec(statements[name]).all()
            elapsed = (perf_counter() - start) * 1000
            result = sorted(
                (str(v.tag_id), v.id, v.numeric_value, v.source_timestamp.isoformat())
                for v in values
            )
            if expected is None:
                expected = result
            assert result == expected and len(result) == args.tags
            times[name].append(elapsed)
        report = {
            "scope": "warm-cache database query plus ORM materialization; not HTTP QPS or production SLA",
            "tags": args.tags,
            "samples": args.tags * args.samples_per_tag,
            "repeats_each": 20,
            "results_equal": True,
            "result_sha256": hashlib.sha256(json.dumps(expected).encode()).hexdigest(),
            "postgres": session.execute(text("SELECT version()")).scalar_one(),
            "timings_ms": {
                name: {
                    "p50": statistics.median(values),
                    "p95": sorted(values)[18],
                    "raw": values,
                }
                for name, values in times.items()
            },
        }
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(  # noqa: T201
            json.dumps(
                {key: value for key, value in report.items() if key != "timings_ms"},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
