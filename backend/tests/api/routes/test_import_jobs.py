"""Exercise the real PostgreSQL queue and transactions, never the business DB."""

import multiprocessing
import signal
import uuid
from datetime import timedelta
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, func, select

from app.core.config import settings
from app.core.db import engine
from app.data_import import jobs, processor
from app.models import (
    DataQualityIssue,
    ImportBatch,
    ImportBatchStatus,
    ImportFieldMapping,
    ImportJob,
    TagSample,
    User,
    UserPlantAccess,
    get_datetime_utc,
)
from tests.api.routes.test_imports import _preview_csv
from tests.api.routes.test_imports import import_context as _import_context

import_context = _import_context


def _worker_waiting_mid_transaction(
    job_id: uuid.UUID, storage: str, ready: Connection
) -> None:
    """Spawned worker: pause only after a sample has really been inserted."""
    settings.IMPORT_STORAGE_DIR = Path(storage)
    settings.IMPORT_CHUNK_SIZE = 1
    original = jobs.process_import_batch

    def paused(**kwargs: Any) -> ImportBatch:
        progress = kwargs["progress"]

        def checkpoint(rows: int) -> None:
            progress(rows)
            if rows == 3:
                batch = kwargs["batch"]
                count = (
                    kwargs["session"]
                    .exec(
                        select(func.count())
                        .select_from(TagSample)
                        .where(TagSample.import_batch_id == batch.id)
                    )
                    .one()
                )
                ready.send(count)
                signal.pause()

        return original(**{**kwargs, "progress": checkpoint})

    jobs.process_import_batch = paused
    jobs.run_job(engine, job_id)


def test_sigkill_releases_ownership_and_recovers_without_partial_data(
    client: TestClient,
    import_context: dict[str, Any],
) -> None:
    batch_id, _, job_id = submit(client, import_context)
    context = multiprocessing.get_context("spawn")
    received, sent = context.Pipe(duplex=False)
    worker = context.Process(
        target=_worker_waiting_mid_transaction,
        args=(job_id, str(settings.IMPORT_STORAGE_DIR), sent),
    )
    worker.start()
    try:
        assert received.poll(20), "Worker did not reach the transaction checkpoint"
        assert received.recv() == 1
        assert not jobs.run_job(engine, job_id)
        assert counts(batch_id) == (0, 0, 0)
        worker.kill()  # SIGKILL: no Python finally block or graceful rollback.
        worker.join(10)
        assert worker.exitcode == -signal.SIGKILL
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(10)
        received.close()
        sent.close()
        worker.close()
    expire(job_id)
    assert jobs.run_job(engine, job_id)
    assert counts(batch_id) == (2, 1, 3)
    assert get_job(client, import_context, batch_id)["attempts"] == 2


def submit(
    client: TestClient, context: dict[str, Any]
) -> tuple[str, dict[str, Any], uuid.UUID]:
    response = _preview_csv(
        client,
        context,
        "timestamp,tag_code,value\n"
        "2026-08-22T10:00:00+08:00,TEMP,100\n"
        "2026-08-22T10:00:01+08:00,TEMP,invalid\n"
        "2026-08-22T10:00:02+08:00,TEMP,102\n",
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    batch_id = preview["batch"]["id"]
    mapping = preview["suggested_mapping"]
    response = client.post(
        f"{settings.API_V1_STR}/imports/{batch_id}/process",
        headers=context["engineer_headers"],
        json=mapping,
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    job = get_job(client, context, batch_id)
    assert job["attempts"] == 0
    return batch_id, mapping, uuid.UUID(job["id"])


def get_job(
    client: TestClient, context: dict[str, Any], batch_id: str
) -> dict[str, Any]:
    response = client.get(
        f"{settings.API_V1_STR}/imports/{batch_id}/job",
        headers=context["engineer_headers"],
    )
    assert response.status_code == 200, response.text
    return response.json()


def counts(batch_id: str) -> tuple[int, int, int]:
    key = uuid.UUID(batch_id)
    with Session(engine) as session:
        return (
            session.exec(
                select(func.count())
                .select_from(TagSample)
                .where(TagSample.import_batch_id == key)
            ).one(),
            session.exec(
                select(func.count())
                .select_from(DataQualityIssue)
                .where(DataQualityIssue.batch_id == key)
            ).one(),
            session.exec(
                select(func.count())
                .select_from(ImportFieldMapping)
                .where(ImportFieldMapping.batch_id == key)
            ).one(),
        )


def expire(job_id: uuid.UUID) -> None:
    with Session(engine) as session:
        job = session.get(ImportJob, job_id)
        assert job is not None
        job.heartbeat_at = get_datetime_utc() - timedelta(
            seconds=settings.IMPORT_JOB_RECOVERY_SECONDS + 1
        )
        session.add(job)
        session.commit()


def test_submission_is_idempotent_and_permissions_apply(
    client: TestClient, import_context: dict[str, Any]
) -> None:
    batch_id, mapping, job_id = submit(client, import_context)
    url = f"{settings.API_V1_STR}/imports/{batch_id}"
    for state in ("queued", "succeeded"):
        response = client.post(
            url + "/process", headers=import_context["engineer_headers"], json=mapping
        )
        assert response.status_code == 202
        assert get_job(client, import_context, batch_id)["id"] == str(job_id)
        assert get_job(client, import_context, batch_id)["status"] == state
        different = {**mapping, "value_column": "other"}
        assert (
            client.post(
                url + "/process",
                headers=import_context["engineer_headers"],
                json=different,
            ).status_code
            == 409
        )
        if state == "queued":
            assert jobs.run_job(engine, job_id)
    assert counts(batch_id) == (2, 1, 3)
    assert not jobs.run_job(engine, job_id)
    assert (
        client.get(url + "/job", headers=import_context["observer_headers"]).status_code
        == 200
    )
    assert (
        client.get(
            url + "/job", headers=import_context["unassigned_headers"]
        ).status_code
        == 403
    )
    assert (
        client.post(
            url + "/process", headers=import_context["observer_headers"], json=mapping
        ).status_code
        == 403
    )
    with Session(engine) as session:
        assert (
            session.exec(
                select(func.count())
                .select_from(ImportJob)
                .where(ImportJob.batch_id == uuid.UUID(batch_id))
            ).one()
            == 1
        )


def test_worker_lock_prevents_a_second_owner(
    client: TestClient, import_context: dict[str, Any]
) -> None:
    batch_id, _, job_id = submit(client, import_context)
    key = jobs._lock_key(uuid.UUID(batch_id))
    with engine.connect() as owner:
        owner.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        owner.commit()
        try:
            assert not jobs.run_job(engine, job_id)
            assert get_job(client, import_context, batch_id)["attempts"] == 0
        finally:
            owner.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            owner.commit()
    assert jobs.run_job(engine, job_id)
    assert counts(batch_id) == (2, 1, 3)


def test_interruption_rolls_back_data_but_keeps_progress_then_recovers(
    client: TestClient,
    import_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_id, _, job_id = submit(client, import_context)
    original = jobs.process_import_batch
    monkeypatch.setattr(settings, "IMPORT_CHUNK_SIZE", 1)

    def interrupted(**kwargs: Any) -> ImportBatch:
        progress = kwargs["progress"]

        def stop(rows: int) -> None:
            progress(rows)
            if rows == 3:
                # First sample and invalid-value issue have been flushed, not committed.
                assert (
                    kwargs["session"]
                    .exec(
                        select(func.count())
                        .select_from(TagSample)
                        .where(TagSample.import_batch_id == uuid.UUID(batch_id))
                    )
                    .one()
                    == 1
                )
                raise SystemExit("simulated worker termination")

        return original(**{**kwargs, "progress": stop})

    monkeypatch.setattr(jobs, "process_import_batch", interrupted)
    with pytest.raises(SystemExit):
        jobs.run_job(engine, job_id)
    assert counts(batch_id) == (0, 0, 0)
    job = get_job(client, import_context, batch_id)
    assert job["status"] == "running"
    assert job["processed_rows"] >= 1
    assert not jobs.run_job(engine, job_id)  # Wait for the recovery grace period.
    expire(job_id)
    monkeypatch.setattr(jobs, "process_import_batch", original)
    assert jobs.run_next_job(engine)
    assert counts(batch_id) == (2, 1, 3)
    job = get_job(client, import_context, batch_id)
    assert job["attempts"] == 2
    assert job["status"] == "succeeded"
    assert job["processed_rows"] == 3
    assert "previous_worker_interrupted" in [
        event["event"] for event in job["attempt_history"]
    ]


def test_committed_data_is_reconciled_after_acknowledgement_crash(
    client: TestClient,
    import_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_id, _, job_id = submit(client, import_context)
    original = jobs.process_import_batch

    def crash_after_commit(**kwargs: Any) -> ImportBatch:
        original(**kwargs)
        raise SystemExit("lost job acknowledgement")

    monkeypatch.setattr(jobs, "process_import_batch", crash_after_commit)
    with pytest.raises(SystemExit):
        jobs.run_job(engine, job_id)
    assert counts(batch_id) == (2, 1, 3)
    expire(job_id)
    # Callback still crashes: reconciliation must NOT invoke the processor again.
    assert jobs.run_job(engine, job_id)
    job = get_job(client, import_context, batch_id)
    assert job["status"] == "succeeded"
    assert job["attempts"] == 1
    assert job["attempt_history"][-1]["event"] == "reconciled_committed_batch"
    assert counts(batch_id) == (2, 1, 3)


@pytest.mark.parametrize("failure", ["missing", "tampered", "disabled", "revoked"])
def test_worker_revalidates_source_and_authorization(
    client: TestClient,
    import_context: dict[str, Any],
    failure: str,
) -> None:
    batch_id, _, job_id = submit(client, import_context)
    with Session(engine) as session:
        batch = session.get(ImportBatch, uuid.UUID(batch_id))
        job = session.get(ImportJob, job_id)
        assert batch is not None and job is not None and batch.storage_key
        if failure == "missing":
            Path(batch.storage_key).unlink()
        elif failure == "tampered":
            Path(batch.storage_key).write_text("changed source", encoding="utf-8")
        elif failure == "disabled":
            user = session.get(User, job.requested_by_id)
            assert user is not None
            user.is_active = False
            session.add(user)
        else:
            access = session.get(UserPlantAccess, (job.requested_by_id, batch.plant_id))
            assert access is not None
            session.delete(access)
        session.commit()
    assert jobs.run_job(engine, job_id)
    with Session(engine) as session:
        job = session.get(ImportJob, job_id)
        batch = session.get(ImportBatch, uuid.UUID(batch_id))
        assert job is not None and batch is not None
        assert job.status == "failed"
        assert batch.status == ImportBatchStatus.FAILED
        assert job.error_message
    assert counts(batch_id) == (0, 0, 0)


def test_retry_limit_and_manual_retry_preserve_history(
    client: TestClient, import_context: dict[str, Any]
) -> None:
    batch_id, mapping, job_id = submit(client, import_context)
    with Session(engine) as session:
        job = session.get(ImportJob, job_id)
        assert job is not None
        job.status = "running"
        job.attempts = settings.IMPORT_JOB_MAX_ATTEMPTS
        job.heartbeat_at = None
        session.add(job)
        session.commit()
    assert jobs.run_job(engine, job_id)
    assert get_job(client, import_context, batch_id)["status"] == "failed"
    response = client.post(
        f"{settings.API_V1_STR}/imports/{batch_id}/process",
        headers=import_context["engineer_headers"],
        json=mapping,
    )
    assert response.status_code == 202
    retried = get_job(client, import_context, batch_id)
    assert retried["id"] != str(job_id)
    assert jobs.run_job(engine, uuid.UUID(retried["id"]))
    assert counts(batch_id) == (2, 1, 3)
    with Session(engine) as session:
        old = session.get(ImportJob, job_id)
        assert old is not None and old.status == "failed" and old.attempt_history


def test_permission_revoked_during_processing_rolls_back(
    client: TestClient,
    import_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_id, _, job_id = submit(client, import_context)
    original = processor.iter_tabular_rows

    def revoke_after_read(*args: Any, **kwargs: Any) -> Any:
        yield from original(*args, **kwargs)
        with Session(engine) as session:
            job = session.get(ImportJob, job_id)
            assert job is not None
            access = session.get(
                UserPlantAccess,
                (job.requested_by_id, uuid.UUID(import_context["plant"]["id"])),
            )
            assert access is not None
            session.delete(access)
            session.commit()

    monkeypatch.setattr(processor, "iter_tabular_rows", revoke_after_read)
    assert jobs.run_job(engine, job_id)
    assert counts(batch_id) == (0, 0, 0)
    with Session(engine) as session:
        job = session.get(ImportJob, job_id)
        assert job is not None and job.status == "failed"
