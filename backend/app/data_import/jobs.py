"""Durable queue; one DB connection owns a per-batch advisory lock while working.

Data and issue rows commit together. Progress commits separately and is scanned
work, NOT committed data. A killed worker loses its connection/lock and rolls
back its data transaction. A subsequent worker replays the immutable source.
"""

import hashlib
import logging
import signal
import time
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import Engine, text, update
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, col, select

from app.api.permissions import require_plant_access
from app.core.config import settings
from app.core.db import engine
from app.data_import.processor import process_import_batch
from app.models import (
    ImportBatch,
    ImportBatchStatus,
    ImportJob,
    ImportMappingInput,
    User,
    get_datetime_utc,
)

logger = logging.getLogger(__name__)


def latest_job(session: Session, batch_id: uuid.UUID) -> ImportJob | None:
    return session.exec(
        select(ImportJob)
        .where(ImportJob.batch_id == batch_id)
        .order_by(col(ImportJob.created_at).desc(), col(ImportJob.id).desc())
    ).first()


def enqueue_import(
    session: Session, batch_id: uuid.UUID, user: User, mapping: ImportMappingInput
) -> ImportBatch:
    config = mapping.model_dump(mode="json")
    current = latest_job(session, batch_id)
    if current is not None and current.status in {"queued", "running", "succeeded"}:
        if current.mapping_config != config:
            raise ValueError("任务已提交，不能更换映射；请先查看批次结果")
        batch = session.get(ImportBatch, batch_id, populate_existing=True)
        assert batch is not None
        require_plant_access(
            session=session, user=user, plant_id=batch.plant_id, write=True
        )
        return batch
    try:
        batch = session.exec(
            select(ImportBatch)
            .where(ImportBatch.id == batch_id)
            .with_for_update(nowait=True)
            .execution_options(populate_existing=True)
        ).one()
    except OperationalError as exc:
        session.rollback()
        raise ValueError("批次正在变更，请刷新后重试") from exc
    require_plant_access(
        session=session, user=user, plant_id=batch.plant_id, write=True
    )
    config = mapping.model_dump(mode="json")
    current = latest_job(session, batch_id)
    if current is not None and current.status in {"queued", "running", "succeeded"}:
        if current.mapping_config != config:
            raise ValueError("任务已提交，不能更换映射；请先查看批次结果")
        return batch
    if batch.status not in {ImportBatchStatus.UPLOADED, ImportBatchStatus.FAILED}:
        raise ValueError("当前批次状态不允许提交")
    if not batch.storage_key or not Path(batch.storage_key).is_file():
        raise ValueError("导入源文件已不存在")
    batch.status = ImportBatchStatus.QUEUED
    batch.mapping_config = config
    batch.error_message = None
    batch.completed_at = None
    session.add(batch)
    session.add(
        ImportJob(batch_id=batch.id, requested_by_id=user.id, mapping_config=config)
    )
    session.commit()
    session.refresh(batch)
    return batch


def _lock_key(batch_id: uuid.UUID) -> int:
    return int.from_bytes(
        hashlib.sha256(b"import:" + batch_id.bytes).digest()[:8], signed=True
    )


def _record(job: ImportJob, event: str, detail: str | None = None) -> None:
    job.attempt_history = [
        *job.attempt_history,
        {
            "attempt": job.attempts,
            "event": event,
            "at": get_datetime_utc().isoformat(),
            "detail": detail,
        },
    ][-30:]


def _failed(session: Session, job: ImportJob, batch: ImportBatch, message: str) -> None:
    job.status = "failed"
    job.error_message = message[:2000]
    job.completed_at = get_datetime_utc()
    _record(job, "failed", job.error_message)
    batch.status = ImportBatchStatus.FAILED
    batch.error_message = job.error_message
    batch.completed_at = job.completed_at
    session.add(job)
    session.add(batch)
    session.commit()


def run_job(bind: Engine, job_id: uuid.UUID) -> bool:
    with Session(bind) as lookup:
        found = lookup.get(ImportJob, job_id)
        if found is None or found.status not in {"queued", "running"}:
            return False
        batch_id = found.batch_id
    key = _lock_key(batch_id)
    with bind.connect() as connection:
        locked = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        ).scalar()
        connection.commit()
        if not locked:
            return False
        try:
            with Session(connection) as session:
                job = session.exec(
                    select(ImportJob).where(ImportJob.id == job_id).with_for_update()
                ).one()
                if job.status not in {"queued", "running"}:
                    return False
                now = get_datetime_utc()
                if (
                    job.status == "running"
                    and job.heartbeat_at
                    and job.heartbeat_at
                    > now - timedelta(seconds=settings.IMPORT_JOB_RECOVERY_SECONDS)
                ):
                    return False
                batch = session.exec(
                    select(ImportBatch)
                    .where(ImportBatch.id == batch_id)
                    .with_for_update()
                ).one()
                if batch.status == ImportBatchStatus.COMPLETED:
                    # Data committed, but worker died before acknowledging the job.
                    job.status = "succeeded"
                    job.processed_rows = batch.total_rows
                    job.completed_at = batch.completed_at
                    _record(job, "reconciled_committed_batch")
                    session.add(job)
                    session.commit()
                    return True
                if job.attempts >= settings.IMPORT_JOB_MAX_ATTEMPTS:
                    _failed(
                        session,
                        job,
                        batch,
                        "中断恢复次数已达上限，请检查原因后手动重试",
                    )
                    return True
                if job.attempts:
                    _record(
                        job,
                        "previous_worker_interrupted",
                        "未提交数据已回滚，从源文件重新处理",
                    )
                job.attempts += 1
                job.status = "running"
                job.heartbeat_at = now
                job.processed_rows = 0
                job.error_message = None
                _record(job, "started")
                batch.status = ImportBatchStatus.PROCESSING
                session.add(job)
                session.add(batch)
                session.commit()
                attempt = job.attempts
                last_progress = 0.0

                def progress(rows: int) -> None:
                    nonlocal last_progress
                    if time.monotonic() - last_progress < 2:
                        return
                    with bind.begin() as progress_connection:
                        result = progress_connection.execute(
                            update(ImportJob)
                            .where(
                                col(ImportJob.id) == job_id,
                                col(ImportJob.attempts) == attempt,
                                col(ImportJob.status) == "running",
                            )
                            .values(
                                processed_rows=rows, heartbeat_at=get_datetime_utc()
                            )
                        )
                        if result.rowcount != 1:
                            raise RuntimeError("任务执行权已改变")
                    last_progress = time.monotonic()

                def check_permission() -> None:
                    user = session.get(
                        User, job.requested_by_id, populate_existing=True
                    )
                    if user is None or not user.is_active:
                        raise ValueError("提交账号已停用或不存在")
                    require_plant_access(
                        session=session, user=user, plant_id=batch.plant_id, write=True
                    )

                try:
                    check_permission()
                    source = Path(batch.storage_key or "").resolve()
                    if (
                        not source.is_relative_to(settings.IMPORT_STORAGE_DIR.resolve())
                        or not source.is_file()
                    ):
                        raise ValueError("源文件不存在或不在导入目录内")
                    with source.open("rb") as handle:
                        digest = hashlib.file_digest(handle, "sha256").hexdigest()
                    if digest != batch.file_sha256:
                        raise ValueError("源文件内容已改变，拒绝使用与预览不同的数据")
                    result = process_import_batch(
                        session=session,
                        batch=batch,
                        mapping=ImportMappingInput.model_validate(job.mapping_config),
                        progress=progress,
                        before_commit=check_permission,
                        record_failure=False,
                        claimed=True,
                    )
                    # Reload after the separate progress connection updated the job.
                    session.refresh(job)
                    job.status = "succeeded"
                    job.processed_rows = result.total_rows
                    job.heartbeat_at = get_datetime_utc()
                    job.completed_at = result.completed_at
                    _record(job, "succeeded")
                    session.add(job)
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    if connection.invalidated:
                        # Never acknowledge through a replacement connection that
                        # no longer owns the advisory lock. Leave recovery to poller.
                        raise
                    session.refresh(batch)
                    session.refresh(job)
                    if batch.status == ImportBatchStatus.COMPLETED:
                        raise  # Acknowledgement failed; next worker reconciles it.
                    message = (
                        str(exc)
                        if isinstance(exc, ValueError)
                        else (
                            "提交账号已无该工厂的写入权限"
                            if isinstance(exc, HTTPException)
                            else "处理异常，未提交的数据已回滚；请按批次ID检查服务日志"
                        )
                    )
                    _failed(session, job, batch, message)
                    logger.warning(
                        "Import job %s failed (%s)", job_id, type(exc).__name__
                    )
                return True
        finally:
            # Session-level locks must never be returned to the connection pool.
            if not connection.invalidated:
                connection.rollback()
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                )
                connection.commit()


def run_next_job(bind: Engine = engine) -> bool:
    cutoff = get_datetime_utc() - timedelta(
        seconds=settings.IMPORT_JOB_RECOVERY_SECONDS
    )
    with Session(bind) as session:
        ids = session.exec(
            select(ImportJob.id)
            .where(
                (col(ImportJob.status) == "queued")
                | (
                    (col(ImportJob.status) == "running")
                    & (
                        col(ImportJob.heartbeat_at).is_(None)
                        | (col(ImportJob.heartbeat_at) <= cutoff)
                    )
                ),
            )
            .order_by(col(ImportJob.created_at))
            .limit(20)
        ).all()
    return any(run_job(bind, job_id) for job_id in ids)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    def stop(_signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    while True:
        try:
            if not run_next_job():
                time.sleep(1)
        except Exception as exc:
            logger.warning("Import poll failed (%s); retrying", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
