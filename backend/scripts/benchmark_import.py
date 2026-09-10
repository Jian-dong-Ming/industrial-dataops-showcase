import argparse
import csv
import hashlib
import json
import platform
import resource
import tracemalloc
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

from sqlmodel import Session, func, select

from app.core.config import settings
from app.core.db import engine
from app.data_import.jobs import enqueue_import, latest_job, run_job
from app.data_import.processor import process_import_batch
from app.models import (
    Device,
    ImportBatch,
    ImportFileFormat,
    ImportMappingInput,
    Plant,
    ProductionLine,
    Tag,
    TagSample,
    User,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark CSV import validation")
    parser.add_argument("--rows", type=int, required=True, choices=(10_000, 100_000))
    parser.add_argument("--mode", choices=("sync", "queued"), default="sync")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-tracemalloc", action="store_true")
    args = parser.parse_args()
    database_name = settings.DATABASE_URL.path.lstrip("/")
    if not database_name.endswith("_test") or database_name in {"app", "postgres"}:
        raise SystemExit("Benchmark refuses to run against a non-test database")
    if args.output and args.output.exists():
        raise SystemExit("Refusing to overwrite a benchmark report")

    run_id = uuid.uuid4().hex[:8].upper()
    output_dir = settings.IMPORT_STORAGE_DIR / "benchmarks" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = output_dir / f"import-{args.rows}.csv"
    started = datetime(2030, 1, 1, tzinfo=UTC) + timedelta(days=int(run_id, 16) % 3000)
    with source_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(["timestamp", "device_code", "tag_code", "value", "quality"])
        for index in range(args.rows):
            writer.writerow(
                [
                    (started + timedelta(seconds=index)).isoformat(),
                    f"DEVICE_{run_id}",
                    f"TAG_{run_id}",
                    500 + (index % 500) / 10,
                    "Good",
                ]
            )

    try:
        with Session(engine) as session:
            user = User(
                email=f"benchmark-{run_id.casefold()}@example.com",
                hashed_password="benchmark-only",
                is_superuser=True,
            )
            plant = Plant(code=f"P_{run_id}", name="Benchmark Plant")
            session.add_all([user, plant])
            session.flush()
            line = ProductionLine(
                plant_id=plant.id,
                code=f"L_{run_id}",
                name="Benchmark Line",
                process_type="continuous",
            )
            session.add(line)
            session.flush()
            device = Device(
                production_line_id=line.id,
                code=f"DEVICE_{run_id}",
                name="Benchmark Device",
                device_type="simulator",
            )
            session.add(device)
            session.flush()
            tag = Tag(
                device_id=device.id,
                code=f"TAG_{run_id}",
                name="Benchmark Tag",
                min_value=0,
                max_value=2000,
            )
            session.add(tag)
            batch = ImportBatch(
                plant_id=plant.id,
                created_by_id=user.id,
                original_filename=source_path.name,
                storage_key=str(source_path.resolve()),
                file_format=ImportFileFormat.CSV,
                content_type="text/csv",
                file_size_bytes=source_path.stat().st_size,
                file_sha256=_sha256(source_path),
                file_encoding="utf-8-sig",
            )
            session.add(batch)
            session.commit()
            session.refresh(batch)

            if not args.no_tracemalloc:
                tracemalloc.start()
            before = perf_counter()
            mapping = ImportMappingInput(
                timestamp_column="timestamp",
                device_code_column="device_code",
                tag_code_column="tag_code",
                value_column="value",
                quality_column="quality",
            )
            submission_seconds = None
            if args.mode == "sync":
                result = process_import_batch(
                    session=session, batch=batch, mapping=mapping
                )
            else:
                enqueue_import(session, batch.id, user, mapping)
                submission_seconds = perf_counter() - before
                job = latest_job(session, batch.id)
                assert job is not None
                assert run_job(engine, job.id)
                session.refresh(batch)
                result = batch
            elapsed = perf_counter() - before
            peak_bytes = None
            if not args.no_tracemalloc:
                _, peak_bytes = tracemalloc.get_traced_memory()
                tracemalloc.stop()
            actual_rows = session.exec(
                select(func.count())
                .select_from(TagSample)
                .where(TagSample.import_batch_id == batch.id)
            ).one()
            assert result.status == "completed" and actual_rows == args.rows
            report = {
                "mode": args.mode,
                "scope": "single import; queue mode invokes real worker immediately without polling delay; not HTTP latency or concurrent throughput",
                "tracemalloc_enabled": not args.no_tracemalloc,
                "python": platform.python_version(),
                "submission_seconds": round(submission_seconds, 4)
                if submission_seconds is not None
                else None,
                "actual_persisted_rows": actual_rows,
                "process_lifetime_max_rss_mib": round(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2
                ),
                "rows": result.total_rows,
                "accepted_rows": result.accepted_rows,
                "elapsed_seconds": round(elapsed, 3),
                "rows_per_second": round(result.accepted_rows / elapsed),
                "python_peak_memory_mib": round(peak_bytes / 1024 / 1024, 2)
                if peak_bytes is not None
                else None,
                "source_size_mib": round(source_path.stat().st_size / 1024 / 1024, 2),
                "chunk_size": settings.IMPORT_CHUNK_SIZE,
            }
            if args.output:
                with args.output.open("x", encoding="utf-8") as report_file:
                    json.dump(report, report_file, ensure_ascii=False, indent=2)
            print(json.dumps(report, ensure_ascii=False))  # noqa: T201
    finally:
        source_path.unlink(missing_ok=True)
        try:
            output_dir.rmdir()
            output_dir.parent.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    main()
