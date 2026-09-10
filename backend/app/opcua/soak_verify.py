import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlmodel import Session, col, func, select

from app.core.db import engine
from app.models import (
    AcquisitionConnectionState,
    AcquisitionNode,
    AcquisitionTask,
    TagSample,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Snapshot:
    taken_at: datetime
    connection_state: str
    samples_received: int
    samples_written: int
    duplicate_count: int
    dropped_count: int
    error_count: int
    reconnect_count: int
    sample_rows: int
    heartbeat_age_seconds: float | None


def load_task(task_name: str) -> AcquisitionTask:
    with Session(engine) as session:
        task = session.exec(
            select(AcquisitionTask).where(AcquisitionTask.name == task_name)
        ).first()
        if task is None:
            raise RuntimeError(f"Acquisition task not found: {task_name}")
        session.expunge(task)
        return task


def take_snapshot(task_id: object) -> Snapshot:
    now = datetime.now(UTC)
    with Session(engine) as session:
        task = session.get(AcquisitionTask, task_id)
        if task is None:
            raise RuntimeError("Acquisition task was deleted during verification")
        sample_rows = session.exec(
            select(func.count())
            .select_from(TagSample)
            .where(TagSample.task_id == task.id)
        ).one()
        heartbeat_age = None
        if task.worker_heartbeat_at is not None:
            heartbeat = task.worker_heartbeat_at
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=UTC)
            heartbeat_age = max((now - heartbeat.astimezone(UTC)).total_seconds(), 0)
        return Snapshot(
            taken_at=now,
            connection_state=task.connection_state,
            samples_received=task.samples_received,
            samples_written=task.samples_written,
            duplicate_count=task.duplicate_count,
            dropped_count=task.dropped_count,
            error_count=task.error_count,
            reconnect_count=task.reconnect_count,
            sample_rows=sample_rows,
            heartbeat_age_seconds=heartbeat_age,
        )


def verify(
    *,
    task_name: str,
    duration_seconds: int,
    poll_seconds: int,
    expected_sample_interval_ms: int,
) -> tuple[dict[str, object], bool]:
    task = load_task(task_name)
    with Session(engine) as session:
        node_count = session.exec(
            select(func.count())
            .select_from(AcquisitionNode)
            .where(
                AcquisitionNode.task_id == task.id,
                col(AcquisitionNode.is_enabled).is_(True),
            )
        ).one()
    if node_count == 0:
        raise RuntimeError("The acquisition task has no enabled nodes")

    started_monotonic = time.monotonic()
    start = take_snapshot(task.id)
    state_polls: dict[str, int] = {}
    max_heartbeat_age = 0.0
    next_progress = started_monotonic + 300
    logger.info(
        "Starting %ss soak verification for task %s with %s nodes",
        duration_seconds,
        task_name,
        node_count,
    )
    while True:
        elapsed = time.monotonic() - started_monotonic
        if elapsed >= duration_seconds:
            break
        time.sleep(min(poll_seconds, duration_seconds - elapsed))
        snapshot = take_snapshot(task.id)
        state_polls[snapshot.connection_state] = (
            state_polls.get(snapshot.connection_state, 0) + 1
        )
        max_heartbeat_age = max(max_heartbeat_age, snapshot.heartbeat_age_seconds or 0)
        if time.monotonic() >= next_progress:
            logger.info(
                "Soak progress %.1f%%; state=%s; written=%s; errors=%s; dropped=%s",
                min(elapsed / duration_seconds * 100, 100),
                snapshot.connection_state,
                snapshot.samples_written,
                snapshot.error_count,
                snapshot.dropped_count,
            )
            next_progress += 300

    elapsed_seconds = time.monotonic() - started_monotonic
    end = take_snapshot(task.id)
    written_delta = end.samples_written - start.samples_written
    received_delta = end.samples_received - start.samples_received
    expected_samples = elapsed_seconds * 1000 / expected_sample_interval_ms * node_count
    delivery_ratio = written_delta / expected_samples if expected_samples else 0
    estimated_missing = max(round(expected_samples - written_delta), 0)
    duplicate_delta = end.duplicate_count - start.duplicate_count
    dropped_delta = end.dropped_count - start.dropped_count
    error_delta = end.error_count - start.error_count
    reconnect_delta = end.reconnect_count - start.reconnect_count
    row_delta = end.sample_rows - start.sample_rows
    non_connected_polls = sum(
        count
        for state, count in state_polls.items()
        if state != AcquisitionConnectionState.CONNECTED
    )
    passed = all(
        (
            end.connection_state == AcquisitionConnectionState.CONNECTED,
            error_delta == 0,
            dropped_delta == 0,
            duplicate_delta == 0,
            row_delta == written_delta,
            non_connected_polls == 0,
            0.95 <= delivery_ratio <= 1.05,
            max_heartbeat_age <= max(poll_seconds * 2, 15),
        )
    )
    report: dict[str, object] = {
        "passed": passed,
        "task_name": task_name,
        "task_id": str(task.id),
        "started_at": start.taken_at.isoformat(),
        "finished_at": end.taken_at.isoformat(),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "node_count": node_count,
        "expected_sample_interval_ms": expected_sample_interval_ms,
        "expected_samples_approx": round(expected_samples),
        "received_delta": received_delta,
        "written_delta": written_delta,
        "database_row_delta": row_delta,
        "database_write_rate_rows_per_second": round(
            written_delta / elapsed_seconds, 3
        ),
        "estimated_delivery_ratio": round(delivery_ratio, 6),
        "estimated_missing_samples": estimated_missing,
        "duplicate_delta": duplicate_delta,
        "dropped_delta": dropped_delta,
        "error_delta": error_delta,
        "reconnect_delta": reconnect_delta,
        "state_poll_counts": state_polls,
        "max_heartbeat_age_seconds": round(max_heartbeat_age, 3),
        "start": asdict(start),
        "end": asdict(end),
        "acceptance": {
            "final_state_connected": end.connection_state
            == AcquisitionConnectionState.CONNECTED,
            "no_new_errors": error_delta == 0,
            "no_dropped_samples": dropped_delta == 0,
            "no_duplicate_samples": duplicate_delta == 0,
            "database_rows_match_counter": row_delta == written_delta,
            "all_state_polls_connected": non_connected_polls == 0,
            "delivery_ratio_between_0.95_and_1.05": 0.95 <= delivery_ratio <= 1.05,
        },
    }
    return report, passed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify OPC UA collection stability for a fixed duration"
    )
    parser.add_argument("--task-name", default="OPC UA 模拟采集任务")
    parser.add_argument("--duration-seconds", type=int, default=7200)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--expected-sample-interval-ms", type=int, default=500)
    args = parser.parse_args()
    if args.duration_seconds < 1 or args.poll_seconds < 1:
        parser.error("duration-seconds and poll-seconds must be positive")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    report, passed = verify(
        task_name=args.task_name,
        duration_seconds=args.duration_seconds,
        poll_seconds=args.poll_seconds,
        expected_sample_interval_ms=args.expected_sample_interval_ms,
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.stdout.write("\n")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
