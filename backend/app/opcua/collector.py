import asyncio
import logging
import os
import signal
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from asyncua.client.client import Client
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.db import engine
from app.models import (
    AcquisitionConnectionState,
    AcquisitionDesiredState,
    AcquisitionNode,
    AcquisitionTask,
    TagSample,
    get_datetime_utc,
)
from app.opcua.security import validate_allowed_endpoint

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeNode:
    tag_id: uuid.UUID
    node_id: str


@dataclass(frozen=True)
class RuntimeTask:
    id: uuid.UUID
    endpoint_url: str
    publishing_interval_ms: int
    batch_size: int
    reconnect_delay_seconds: int
    max_reconnect_delay_seconds: int
    nodes: tuple[RuntimeNode, ...]


@dataclass(frozen=True)
class PendingSample:
    task_id: uuid.UUID
    tag_id: uuid.UUID
    value: Any
    numeric_value: float | None
    source_timestamp: datetime
    server_timestamp: datetime | None
    received_at: datetime
    status_code: str
    is_good: bool


def normalize_value(value: Any) -> tuple[Any, float | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return value, float(value)
    if isinstance(value, (int, float)):
        return value, float(value)
    if isinstance(value, str):
        return value, None
    if isinstance(value, (list, tuple)):
        normalized = [normalize_value(item)[0] for item in value]
        return normalized, None
    return str(value), None


def ensure_utc(value: datetime | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def load_runtime_task(task_id: uuid.UUID) -> RuntimeTask | None:
    with Session(engine) as session:
        task = session.get(AcquisitionTask, task_id)
        if task is None or task.desired_state != AcquisitionDesiredState.RUNNING:
            return None
        nodes = session.exec(
            select(AcquisitionNode).where(
                AcquisitionNode.task_id == task_id,
                col(AcquisitionNode.is_enabled).is_(True),
            )
        ).all()
        return RuntimeTask(
            id=task.id,
            endpoint_url=validate_allowed_endpoint(task.endpoint_url),
            publishing_interval_ms=task.publishing_interval_ms,
            batch_size=task.batch_size,
            reconnect_delay_seconds=task.reconnect_delay_seconds,
            max_reconnect_delay_seconds=task.max_reconnect_delay_seconds,
            nodes=tuple(
                RuntimeNode(tag_id=node.tag_id, node_id=node.node_id) for node in nodes
            ),
        )


def running_task_ids() -> set[uuid.UUID]:
    with Session(engine) as session:
        return set(
            session.exec(
                select(AcquisitionTask.id).where(
                    AcquisitionTask.desired_state == AcquisitionDesiredState.RUNNING
                )
            ).all()
        )


def update_task(task_id: uuid.UUID, **values: Any) -> None:
    with Session(engine) as session:
        task = session.exec(
            select(AcquisitionTask)
            .where(AcquisitionTask.id == task_id)
            .with_for_update()
        ).first()
        if task is None:
            return
        state = values.get("connection_state")
        if state is not None:
            # Serialize against user start/stop updates. An old runner must not
            # overwrite a newer desired state after disconnect/cleanup awaits.
            stopped = task.desired_state == AcquisitionDesiredState.STOPPED
            if stopped != (state == AcquisitionConnectionState.STOPPED):
                return
        for key, value in values.items():
            setattr(task, key, value)
        task.updated_at = get_datetime_utc()
        session.add(task)
        session.commit()


def record_connection_failure(
    task_id: uuid.UUID, exc: Exception, state: AcquisitionConnectionState
) -> None:
    with Session(engine) as session:
        task = session.exec(
            select(AcquisitionTask)
            .where(AcquisitionTask.id == task_id)
            .with_for_update()
        ).first()
        if task is None or task.desired_state != AcquisitionDesiredState.RUNNING:
            return
        now = get_datetime_utc()
        task.connection_state = state
        task.last_disconnected_at = now
        task.worker_heartbeat_at = now
        task.error_count += 1
        task.last_error = str(exc)[:2000]
        task.updated_at = now
        session.add(task)
        session.commit()


def reconcile_stopped_tasks(owned_ids: set[uuid.UUID]) -> None:
    """Repair persisted stale states after startup/late callbacks, single supervisor.

    Owned runners must finish cancellation/drain first; do not invent a heartbeat
    for tasks with no runner. Multi-collector ownership needs a separate lease.
    """
    with Session(engine) as session:
        tasks = session.exec(
            select(AcquisitionTask)
            .where(
                AcquisitionTask.desired_state == AcquisitionDesiredState.STOPPED,
                AcquisitionTask.connection_state != AcquisitionConnectionState.STOPPED,
            )
            .with_for_update()
        ).all()
        for task in tasks:
            if task.id in owned_ids:
                continue
            task.connection_state = AcquisitionConnectionState.STOPPED
            task.updated_at = get_datetime_utc()
            session.add(task)
        session.commit()


def increment_task(task_id: uuid.UUID, **increments: int) -> None:
    with Session(engine) as session:
        task = session.get(AcquisitionTask, task_id)
        if task is None:
            return
        for key, amount in increments.items():
            setattr(task, key, getattr(task, key) + amount)
        task.worker_heartbeat_at = get_datetime_utc()
        task.updated_at = get_datetime_utc()
        session.add(task)
        session.commit()


def store_batch(task_id: uuid.UUID, batch: list[PendingSample], dropped: int) -> None:
    if not batch and not dropped:
        return
    with Session(engine) as session:
        written = 0
        if batch:
            statement = (
                insert(TagSample)
                .values(
                    [
                        {
                            "task_id": item.task_id,
                            "tag_id": item.tag_id,
                            "value": item.value,
                            "numeric_value": item.numeric_value,
                            "source_timestamp": item.source_timestamp,
                            "server_timestamp": item.server_timestamp,
                            "received_at": item.received_at,
                            "status_code": item.status_code,
                            "is_good": item.is_good,
                        }
                        for item in batch
                    ]
                )
                .on_conflict_do_nothing(constraint="uq_tag_sample_source")
                .returning(col(TagSample.id))
            )
            written = len(session.exec(statement).all())

        task = session.get(AcquisitionTask, task_id)
        if task is not None:
            task.samples_received += len(batch)
            task.samples_written += written
            task.duplicate_count += len(batch) - written
            task.dropped_count += dropped
            if batch:
                task.last_sample_at = max(item.source_timestamp for item in batch)
            task.worker_heartbeat_at = get_datetime_utc()
            task.updated_at = get_datetime_utc()
            session.add(task)
        session.commit()


class DataChangeHandler:
    def __init__(
        self,
        *,
        task_id: uuid.UUID,
        node_to_tag: dict[str, uuid.UUID],
        queue: asyncio.Queue[PendingSample],
    ) -> None:
        self.task_id = task_id
        self.node_to_tag = node_to_tag
        self.queue = queue
        self.dropped = 0

    def take_dropped(self) -> int:
        dropped = self.dropped
        self.dropped = 0
        return dropped

    def datachange_notification(self, node: Any, val: Any, data: Any) -> None:
        node_id = node.nodeid.to_string()
        tag_id = self.node_to_tag.get(node_id)
        if tag_id is None:
            return
        received_at = get_datetime_utc()
        monitored_value = data.monitored_item.Value
        normalized_value, numeric_value = normalize_value(val)
        status = monitored_value.StatusCode
        sample = PendingSample(
            task_id=self.task_id,
            tag_id=tag_id,
            value=normalized_value,
            numeric_value=numeric_value,
            source_timestamp=ensure_utc(monitored_value.SourceTimestamp, received_at),
            server_timestamp=(
                ensure_utc(monitored_value.ServerTimestamp, received_at)
                if monitored_value.ServerTimestamp
                else None
            ),
            received_at=received_at,
            status_code=status.name,
            is_good=status.is_good(),
        )
        try:
            self.queue.put_nowait(sample)
        except asyncio.QueueFull:
            self.dropped += 1


class AcquisitionRunner:
    def __init__(self, task_id: uuid.UUID) -> None:
        self.task_id = task_id
        self.reconnecting = False

    async def _connection_lost(self, exc: Exception) -> None:
        self.reconnecting = True
        record_connection_failure(
            self.task_id, exc, AcquisitionConnectionState.RECONNECTING
        )

    async def _writer_loop(
        self,
        *,
        runtime: RuntimeTask,
        handler: DataChangeHandler,
        queue: asyncio.Queue[PendingSample],
        stop_event: asyncio.Event,
    ) -> None:
        batch: list[PendingSample] = []
        loop = asyncio.get_running_loop()
        flush_deadline = loop.time() + 1.0
        while not stop_event.is_set() or not queue.empty() or batch:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=max(flush_deadline - loop.time(), 0.05)
                )
                batch.append(item)
                while len(batch) < runtime.batch_size and not queue.empty():
                    batch.append(queue.get_nowait())
            except TimeoutError:
                pass

            now = loop.time()
            should_flush = len(batch) >= runtime.batch_size or (
                bool(batch)
                and (now >= flush_deadline or (stop_event.is_set() and queue.empty()))
            )
            if should_flush:
                store_batch(self.task_id, batch, handler.take_dropped())
                batch = []
                flush_deadline = now + 1.0
            elif not batch and now >= flush_deadline:
                dropped = handler.take_dropped()
                if dropped:
                    increment_task(self.task_id, dropped_count=dropped)
                flush_deadline = now + 1.0

    async def run(self) -> None:
        retry_delay = 1
        while runtime := load_runtime_task(self.task_id):
            if not runtime.nodes:
                update_task(
                    self.task_id,
                    connection_state=AcquisitionConnectionState.ERROR,
                    last_error="No enabled OPC UA nodes are configured",
                    worker_heartbeat_at=get_datetime_utc(),
                )
                return
            queue: asyncio.Queue[PendingSample] = asyncio.Queue(
                maxsize=settings.OPCUA_SAMPLE_QUEUE_SIZE
            )
            handler = DataChangeHandler(
                task_id=self.task_id,
                node_to_tag={node.node_id: node.tag_id for node in runtime.nodes},
                queue=queue,
            )
            client = Client(
                url=runtime.endpoint_url,
                timeout=settings.OPCUA_CLIENT_TIMEOUT_SECONDS,
                watchdog_intervall=1.0,
                auto_reconnect=True,
                reconnect_max_delay=runtime.max_reconnect_delay_seconds,
                reconnect_request_timeout=max(
                    float(runtime.max_reconnect_delay_seconds * 2), 10.0
                ),
            )
            client.connection_lost_callback = self._connection_lost
            stop_writer = asyncio.Event()
            writer: asyncio.Task[None] | None = None
            subscription = None
            try:
                update_task(
                    self.task_id,
                    connection_state=(
                        AcquisitionConnectionState.RECONNECTING
                        if retry_delay > runtime.reconnect_delay_seconds
                        else AcquisitionConnectionState.CONNECTING
                    ),
                    worker_heartbeat_at=get_datetime_utc(),
                )
                await client.connect()
                subscription = await client.create_subscription(
                    runtime.publishing_interval_ms, handler
                )
                await subscription.subscribe_data_change(
                    [client.get_node(node.node_id) for node in runtime.nodes]
                )
                now = get_datetime_utc()
                update_task(
                    self.task_id,
                    connection_state=AcquisitionConnectionState.CONNECTED,
                    last_connected_at=now,
                    last_error=None,
                    worker_heartbeat_at=now,
                )
                retry_delay = runtime.reconnect_delay_seconds
                writer = asyncio.create_task(
                    self._writer_loop(
                        runtime=runtime,
                        handler=handler,
                        queue=queue,
                        stop_event=stop_writer,
                    )
                )

                while load_runtime_task(self.task_id) is not None:
                    await asyncio.sleep(settings.OPCUA_WORKER_HEARTBEAT_SECONDS)
                    if writer.done():
                        # A live subscription is not evidence of successful storage.
                        await writer
                        raise RuntimeError("Sample writer stopped unexpectedly")
                    state = client.state.value
                    if state == "connected":
                        now = get_datetime_utc()
                        if self.reconnecting:
                            increment_task(self.task_id, reconnect_count=1)
                            self.reconnecting = False
                        update_task(
                            self.task_id,
                            connection_state=AcquisitionConnectionState.CONNECTED,
                            last_connected_at=now,
                            last_error=None,
                            worker_heartbeat_at=now,
                        )
                    elif state == "reconnecting":
                        update_task(
                            self.task_id,
                            connection_state=AcquisitionConnectionState.RECONNECTING,
                            worker_heartbeat_at=get_datetime_utc(),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("OPC UA acquisition task %s failed", self.task_id)
                record_connection_failure(
                    self.task_id, exc, AcquisitionConnectionState.ERROR
                )
                if load_runtime_task(self.task_id) is not None:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(
                        retry_delay * 2, runtime.max_reconnect_delay_seconds
                    )
            finally:
                if subscription is not None:
                    try:
                        await subscription.delete()
                    except Exception:
                        logger.debug("Subscription cleanup failed", exc_info=True)
                stop_writer.set()
                try:
                    if writer is not None:
                        await writer
                finally:
                    try:
                        await client.disconnect()
                    except Exception:
                        logger.debug("Client disconnect failed", exc_info=True)

        update_task(
            self.task_id,
            connection_state=AcquisitionConnectionState.STOPPED,
            worker_heartbeat_at=get_datetime_utc(),
        )


class CollectorSupervisor:
    def __init__(self) -> None:
        self.runners: dict[uuid.UUID, asyncio.Task[None]] = {}
        self.stop_event = asyncio.Event()

    async def _finish_runner(self, task_id: uuid.UUID) -> None:
        runner = self.runners.pop(task_id)
        if not runner.done():
            runner.cancel()
        try:
            await runner
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # Retrieve failed task exceptions without leaking SQL/connection details.
            logger.error(
                "Acquisition runner %s exited: %s", task_id, type(exc).__name__
            )

    async def _reconcile(self) -> None:
        desired_ids = running_task_ids()
        for task_id in list(self.runners):
            if task_id not in desired_ids or self.runners[task_id].done():
                await self._finish_runner(task_id)
                if task_id not in desired_ids:
                    update_task(
                        task_id,
                        connection_state=AcquisitionConnectionState.STOPPED,
                        worker_heartbeat_at=get_datetime_utc(),
                    )
        for task_id in desired_ids - self.runners.keys():
            self.runners[task_id] = asyncio.create_task(
                AcquisitionRunner(task_id).run()
            )
        reconcile_stopped_tasks(set(self.runners))

    async def run(self) -> None:
        logger.info("OPC UA collector supervisor started")
        database_unavailable = False
        try:
            while not self.stop_event.is_set():
                try:
                    await self._reconcile()
                    if database_unavailable:
                        logger.info("Collector database connection recovered")
                    database_unavailable = False
                except (OperationalError, InterfaceError) as exc:
                    # Container engine restarts do not wait for Compose health gates.
                    # Keep user start/stop intent in the DB and retry on the next poll.
                    if not database_unavailable:
                        logger.warning(
                            "Collector database unavailable; will retry (%s)",
                            type(exc).__name__,
                        )
                    database_unavailable = True
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(),
                        timeout=settings.OPCUA_WORKER_POLL_SECONDS,
                    )
                except TimeoutError:
                    pass
        finally:
            for runner in self.runners.values():
                runner.cancel()
            await asyncio.gather(*self.runners.values(), return_exceptions=True)
            self.runners.clear()
            logger.info("OPC UA collector supervisor stopped")


async def async_main() -> None:
    supervisor = CollectorSupervisor()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, supervisor.stop_event.set)
    await supervisor.run()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
