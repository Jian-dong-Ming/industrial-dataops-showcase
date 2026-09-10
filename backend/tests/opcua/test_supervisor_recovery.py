import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.opcua import collector


def unavailable() -> OperationalError:
    return OperationalError("SELECT", {}, ConnectionError("synthetic database outage"))


def test_supervisor_retries_database_startup_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        supervisor = collector.CollectorSupervisor()
        task_id = uuid.uuid4()
        reads = 0
        started: list[uuid.UUID] = []
        cleaned = asyncio.Event()

        def desired() -> set[uuid.UUID]:
            nonlocal reads
            reads += 1
            if reads <= 2:
                raise unavailable()
            return {task_id}

        async def runner(self: collector.AcquisitionRunner) -> None:
            started.append(self.task_id)
            supervisor.stop_event.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        monkeypatch.setattr(collector, "running_task_ids", desired)
        monkeypatch.setattr(collector, "reconcile_stopped_tasks", lambda _: None)
        monkeypatch.setattr(collector.AcquisitionRunner, "run", runner)
        monkeypatch.setattr(settings, "OPCUA_WORKER_POLL_SECONDS", 0.001)
        await asyncio.wait_for(supervisor.run(), timeout=2)
        assert reads == 3
        assert started == [task_id]
        assert cleaned.is_set()
        assert not supervisor.runners

    asyncio.run(scenario())


def test_supervisor_restarts_failed_runner_without_restarting_healthy_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        supervisor = collector.CollectorSupervisor()
        failed_id, healthy_id = uuid.uuid4(), uuid.uuid4()
        starts: dict[uuid.UUID, int] = {}
        cleaned: set[uuid.UUID] = set()

        async def runner(self: collector.AcquisitionRunner) -> None:
            starts[self.task_id] = starts.get(self.task_id, 0) + 1
            if self.task_id == failed_id and starts[self.task_id] == 1:
                raise unavailable()
            if starts.get(failed_id) == 2:
                supervisor.stop_event.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.add(self.task_id)

        monkeypatch.setattr(
            collector, "running_task_ids", lambda: {failed_id, healthy_id}
        )
        monkeypatch.setattr(collector, "reconcile_stopped_tasks", lambda _: None)
        monkeypatch.setattr(collector.AcquisitionRunner, "run", runner)
        monkeypatch.setattr(settings, "OPCUA_WORKER_POLL_SECONDS", 0.001)
        await asyncio.wait_for(supervisor.run(), timeout=2)
        assert starts == {failed_id: 2, healthy_id: 1}
        assert cleaned == {failed_id, healthy_id}

    asyncio.run(scenario())


def test_supervisor_cancellation_cleans_runners_during_database_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        supervisor = collector.CollectorSupervisor()
        outage = asyncio.Event()
        cleaned = asyncio.Event()

        async def existing_runner() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        def desired() -> set[uuid.UUID]:
            outage.set()
            raise unavailable()

        existing = asyncio.create_task(existing_runner())
        supervisor.runners[uuid.uuid4()] = existing
        monkeypatch.setattr(collector, "running_task_ids", desired)
        running = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(outage.wait(), timeout=2)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert existing.done()
        assert cleaned.is_set()
        assert not supervisor.runners

    asyncio.run(scenario())


def test_writer_failure_is_detected_and_client_disconnected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        task_id = uuid.uuid4()
        runtime = collector.RuntimeTask(
            id=task_id,
            endpoint_url="opc.tcp://localhost:4840",
            publishing_interval_ms=100,
            batch_size=2,
            reconnect_delay_seconds=1,
            max_reconnect_delay_seconds=2,
            nodes=(collector.RuntimeNode(tag_id=uuid.uuid4(), node_id="test"),),
        )
        disconnected = False
        unsubscribed = False
        failed = False

        class Subscription:
            async def subscribe_data_change(self, _: Any) -> None:
                pass

            async def delete(self) -> None:
                nonlocal unsubscribed
                unsubscribed = True

        class Client:
            def __init__(self, **_: Any) -> None:
                self.state = SimpleNamespace(value="connected")
                self.connection_lost_callback: Any = None

            async def connect(self) -> None:
                pass

            async def create_subscription(self, *_: Any) -> Subscription:
                return Subscription()

            def get_node(self, node_id: str) -> str:
                return node_id

            async def disconnect(self) -> None:
                nonlocal disconnected
                disconnected = True

        async def broken_writer(*_: Any, **__: Any) -> None:
            raise RuntimeError("synthetic storage failure")

        def record_failure(*_: Any) -> None:
            nonlocal failed
            failed = True

        monkeypatch.setattr(collector, "Client", Client)
        monkeypatch.setattr(
            collector, "load_runtime_task", lambda _: None if failed else runtime
        )
        monkeypatch.setattr(collector, "update_task", lambda *_, **__: None)
        monkeypatch.setattr(collector, "record_connection_failure", record_failure)
        monkeypatch.setattr(collector.AcquisitionRunner, "_writer_loop", broken_writer)
        monkeypatch.setattr(settings, "OPCUA_WORKER_HEARTBEAT_SECONDS", 0.001)
        with pytest.raises(RuntimeError, match="synthetic storage failure"):
            await asyncio.wait_for(
                collector.AcquisitionRunner(task_id).run(), timeout=2
            )
        assert failed and unsubscribed and disconnected

    asyncio.run(scenario())
