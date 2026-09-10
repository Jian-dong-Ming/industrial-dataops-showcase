import argparse
import asyncio
import socket
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from asyncua.ua.status_codes import StatusCodes
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.db import engine
from app.models import (
    AcquisitionConnectionState,
    AcquisitionDesiredState,
    AcquisitionNode,
    AcquisitionTask,
    AssetStatus,
    Device,
    Plant,
    ProductionLine,
    Tag,
    TagSample,
    UserRole,
    get_datetime_utc,
)
from app.opcua.browser import browse_variable_nodes
from app.opcua.collector import (
    AcquisitionRunner,
    CollectorSupervisor,
    DataChangeHandler,
    PendingSample,
    ensure_utc,
    increment_task,
    load_runtime_task,
    normalize_value,
    reconcile_stopped_tasks,
    record_connection_failure,
    running_task_ids,
    store_batch,
    update_task,
)
from app.opcua.demo_seed import seed_demo
from app.opcua.healthcheck import check
from app.opcua.simulator import (
    DEFAULT_ENDPOINT,
    SIGNALS,
    SyntheticSignalGenerator,
    run_simulator,
)
from tests.api.routes.test_acquisition import task_payload
from tests.api.routes.test_industrial_domain import (
    assign_plant,
    create_device,
    create_line,
    create_plant,
    create_role_headers,
    create_tag,
)


@pytest.fixture()
def runtime_acquisition_context(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> dict[str, Any]:
    engineer_id, engineer_headers = create_role_headers(
        client=client, db=db, role=UserRole.ENGINEER
    )
    _, observer_headers = create_role_headers(
        client=client, db=db, role=UserRole.OBSERVER
    )
    plant = create_plant(client, superuser_token_headers)
    assign_plant(client, superuser_token_headers, engineer_id, plant["id"])
    line = create_line(client, engineer_headers, plant["id"])
    device = create_device(client, engineer_headers, line["id"])
    tag = create_tag(client, engineer_headers, device["id"])
    return {
        "admin_headers": superuser_token_headers,
        "engineer_headers": engineer_headers,
        "observer_headers": observer_headers,
        "plant": plant,
        "tag": tag,
    }


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def simulator_args(port: int) -> argparse.Namespace:
    return argparse.Namespace(
        endpoint=DEFAULT_ENDPOINT.replace("4840", str(port)),
        interval_ms=100,
        seed=20260816,
        anomaly_mode="none",
        anomaly_after_seconds=60.0,
        anomaly_duration_seconds=30.0,
    )


async def wait_endpoint(endpoint: str, attempts: int = 50) -> None:
    for _ in range(attempts):
        try:
            await check(endpoint, 1.0)
            return
        except OSError, TimeoutError:
            await asyncio.sleep(0.1)
    raise AssertionError(f"OPC UA endpoint did not start: {endpoint}")


def task_snapshot(task_id: uuid.UUID) -> AcquisitionTask:
    with Session(engine) as session:
        task = session.get(AcquisitionTask, task_id)
        assert task is not None
        session.expunge(task)
        return task


async def wait_for_task(
    task_id: uuid.UUID, predicate: Any, attempts: int = 100
) -> AcquisitionTask:
    last_task: AcquisitionTask | None = None
    for _ in range(attempts):
        last_task = task_snapshot(task_id)
        if predicate(last_task):
            return last_task
        await asyncio.sleep(0.1)
    assert last_task is not None
    raise AssertionError(
        f"Task {task_id} did not reach the expected state; "
        f"state={last_task.connection_state}, received={last_task.samples_received}, "
        f"written={last_task.samples_written}, error={last_task.last_error}"
    )


def stop_task(task_id: uuid.UUID) -> None:
    with Session(engine) as session:
        task = session.get(AcquisitionTask, task_id)
        assert task is not None
        task.desired_state = AcquisitionDesiredState.STOPPED
        task.updated_at = get_datetime_utc()
        session.add(task)
        session.commit()


def create_runtime_task(
    *, client: TestClient, context: dict[str, Any], port: int
) -> uuid.UUID:
    payload = task_payload(context, name=f"运行测试-{uuid.uuid4().hex[:8]}")
    payload["endpoint_url"] = f"opc.tcp://localhost:{port}/industrial-dataops/server/"
    payload["publishing_interval_ms"] = 100
    payload["batch_size"] = 2
    payload["max_reconnect_delay_seconds"] = 2
    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks",
        headers=context["engineer_headers"],
        json=payload,
    )
    assert response.status_code == 200, response.text
    task_id = response.json()["id"]
    response = client.post(
        f"{settings.API_V1_STR}/acquisition/tasks/{task_id}/start",
        headers=context["engineer_headers"],
    )
    assert response.status_code == 200, response.text
    return uuid.UUID(task_id)


def test_signal_generator_and_value_normalization() -> None:
    normal = SyntheticSignalGenerator(
        seed=1,
        anomaly_mode="none",
        anomaly_after_seconds=0,
        anomaly_duration_seconds=0,
    )
    bad = SyntheticSignalGenerator(
        seed=1,
        anomaly_mode="bad_quality",
        anomaly_after_seconds=0,
        anomaly_duration_seconds=0,
    )
    value, status = normal.value(SIGNALS[0], 10)
    assert isinstance(value, float)
    assert status.is_good()
    _, bad_status = bad.value(SIGNALS[0], 10)
    assert bad_status.value == StatusCodes.BadSensorFailure
    assert normalize_value(True) == (True, 1.0)
    assert normalize_value([1, "x"]) == ([1, "x"], None)
    assert normalize_value(None) == (None, None)
    assert normalize_value(2) == (2, 2.0)
    assert normalize_value(2.5) == (2.5, 2.5)
    assert normalize_value("value") == ("value", None)
    assert normalize_value(object())[1] is None

    fallback = datetime(2026, 8, 22, tzinfo=UTC)
    assert ensure_utc(None, fallback) is fallback
    assert ensure_utc(datetime(2026, 8, 22), fallback).tzinfo == UTC
    assert ensure_utc(fallback, fallback) == fallback


def test_collector_storage_handler_and_supervisor_helpers(
    client: TestClient,
    db: Session,
    runtime_acquisition_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = create_runtime_task(
        client=client,
        context=runtime_acquisition_context,
        port=free_port(),
    )
    assert task_id in running_task_ids()
    tag_id = uuid.UUID(runtime_acquisition_context["tag"]["id"])
    timestamp = datetime(2026, 8, 22, 8, 0, tzinfo=UTC)
    sample = PendingSample(
        task_id=task_id,
        tag_id=tag_id,
        value=123.5,
        numeric_value=123.5,
        source_timestamp=timestamp,
        server_timestamp=None,
        received_at=timestamp,
        status_code="Good",
        is_good=True,
    )
    store_batch(task_id, [], 0)
    store_batch(task_id, [sample], 0)
    store_batch(task_id, [sample], 2)
    increment_task(task_id, error_count=1)
    update_task(task_id, last_error="synthetic helper test")
    missing_id = uuid.uuid4()
    increment_task(missing_id, error_count=1)
    update_task(missing_id, last_error="ignored")
    db.expire_all()
    task = db.get(AcquisitionTask, task_id)
    assert task is not None
    assert task.samples_received == 2
    assert task.samples_written == 1
    assert task.duplicate_count == 1
    assert task.dropped_count == 2
    assert task.error_count == 1
    assert task.last_error == "synthetic helper test"

    queue: asyncio.Queue[PendingSample] = asyncio.Queue(maxsize=1)
    handler = DataChangeHandler(
        task_id=task_id,
        node_to_tag={"known": tag_id},
        queue=queue,
    )
    unknown_node = SimpleNamespace(nodeid=SimpleNamespace(to_string=lambda: "unknown"))
    handler.datachange_notification(unknown_node, 1.0, SimpleNamespace())
    known_node = SimpleNamespace(nodeid=SimpleNamespace(to_string=lambda: "known"))
    monitored_value = SimpleNamespace(
        StatusCode=SimpleNamespace(name="Good", is_good=lambda: True),
        SourceTimestamp=None,
        ServerTimestamp=None,
    )
    data = SimpleNamespace(monitored_item=SimpleNamespace(Value=monitored_value))
    handler.datachange_notification(known_node, 1.0, data)
    handler.datachange_notification(known_node, 2.0, data)
    assert queue.qsize() == 1
    assert handler.take_dropped() == 1
    assert handler.take_dropped() == 0

    async def supervisor_scenario() -> None:
        supervisor = CollectorSupervisor()
        calls = 0

        def desired_ids() -> set[uuid.UUID]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {missing_id}
            supervisor.stop_event.set()
            return set()

        async def wait_until_cancelled(_: AcquisitionRunner) -> None:
            await asyncio.Event().wait()

        state_updates: list[uuid.UUID] = []
        monkeypatch.setattr(
            "app.opcua.collector.running_task_ids",
            desired_ids,
        )
        monkeypatch.setattr(AcquisitionRunner, "run", wait_until_cancelled)
        monkeypatch.setattr(
            "app.opcua.collector.update_task",
            lambda changed_id, **_: state_updates.append(changed_id),
        )
        monkeypatch.setattr(settings, "OPCUA_WORKER_POLL_SECONDS", 0.01)
        await supervisor.run()
        assert state_updates == [missing_id]
        assert supervisor.runners == {}

    asyncio.run(supervisor_scenario())


def test_runner_rejects_task_without_enabled_nodes(
    client: TestClient,
    db: Session,
    runtime_acquisition_context: dict[str, Any],
) -> None:
    task_id = create_runtime_task(
        client=client,
        context=runtime_acquisition_context,
        port=free_port(),
    )
    nodes = db.exec(
        select(AcquisitionNode).where(AcquisitionNode.task_id == task_id)
    ).all()
    assert nodes
    for node in nodes:
        node.is_enabled = False
        db.add(node)
    db.commit()

    asyncio.run(AcquisitionRunner(task_id).run())
    task = task_snapshot(task_id)
    assert task.connection_state == "error"
    assert task.last_error == "No enabled OPC UA nodes are configured"


def test_runner_records_initial_connection_failure(
    client: TestClient,
    runtime_acquisition_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = create_runtime_task(
        client=client,
        context=runtime_acquisition_context,
        port=free_port(),
    )
    runtime = load_runtime_task(task_id)
    assert runtime is not None
    calls = 0

    def runtime_once(_: uuid.UUID) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            return runtime
        # Match real load_runtime_task: None means the task was stopped/deleted.
        update_task(task_id, desired_state=AcquisitionDesiredState.STOPPED)
        return None

    class FailingClient:
        def __init__(self, **_: Any) -> None:
            self.connection_lost_callback: Any = None

        async def connect(self) -> None:
            raise ConnectionError("synthetic connect failure")

        async def disconnect(self) -> None:
            return None

    monkeypatch.setattr("app.opcua.collector.load_runtime_task", runtime_once)
    monkeypatch.setattr("app.opcua.collector.Client", FailingClient)
    asyncio.run(AcquisitionRunner(task_id).run())

    task = task_snapshot(task_id)
    assert task.connection_state == "stopped"
    assert task.error_count == 1
    assert task.last_error == "synthetic connect failure"


def test_late_disconnect_cannot_overwrite_stop_and_start(
    client: TestClient,
    db: Session,
    runtime_acquisition_context: dict[str, Any],
) -> None:
    task_id = create_runtime_task(
        client=client, context=runtime_acquisition_context, port=free_port()
    )
    update_task(task_id, desired_state=AcquisitionDesiredState.STOPPED)
    update_task(task_id, connection_state=AcquisitionConnectionState.STOPPED)
    before = task_snapshot(task_id)
    asyncio.run(
        AcquisitionRunner(task_id)._connection_lost(ConnectionError("late callback"))
    )
    record_connection_failure(
        task_id, ConnectionError("late error"), AcquisitionConnectionState.ERROR
    )
    update_task(task_id, connection_state=AcquisitionConnectionState.CONNECTED)
    after = task_snapshot(task_id)
    assert (
        after.connection_state == "stopped" and after.error_count == before.error_count
    )
    assert after.worker_heartbeat_at == before.worker_heartbeat_at
    # Simulate a legacy persisted stale state, not a user request to reconnect.
    db.expire_all()
    task = db.get(AcquisitionTask, task_id)
    assert task
    task.connection_state = AcquisitionConnectionState.RECONNECTING
    db.add(task)
    db.commit()
    reconcile_stopped_tasks({task_id})
    assert task_snapshot(task_id).connection_state == "reconnecting"  # drain first
    reconcile_stopped_tasks(set())
    assert task_snapshot(task_id).connection_state == "stopped"
    assert task_snapshot(task_id).worker_heartbeat_at == before.worker_heartbeat_at
    update_task(task_id, desired_state=AcquisitionDesiredState.RUNNING)
    update_task(task_id, connection_state=AcquisitionConnectionState.CONNECTING)
    update_task(task_id, connection_state=AcquisitionConnectionState.STOPPED)
    assert (
        task_snapshot(task_id).connection_state == "connecting"
    )  # stale cleanup ignored
    asyncio.run(
        AcquisitionRunner(task_id)._connection_lost(ConnectionError("real loss"))
    )
    assert task_snapshot(task_id).connection_state == "reconnecting"
    assert task_snapshot(task_id).error_count == before.error_count + 1
    reconcile_stopped_tasks(set())
    assert (
        task_snapshot(task_id).connection_state == "reconnecting"
    )  # desired still running
    record_connection_failure(
        uuid.uuid4(), ConnectionError("missing"), AcquisitionConnectionState.ERROR
    )
    update_task(task_id, desired_state=AcquisitionDesiredState.STOPPED)
    reconcile_stopped_tasks(set())


def test_demo_seed_preserves_disabled_demo_assets() -> None:
    created = seed_demo(start=False)
    plant_id = uuid.UUID(str(created["plant_id"]))
    task_id = uuid.UUID(str(created["task_id"]))

    with Session(engine) as session:
        plant = session.get(Plant, plant_id)
        task = session.get(AcquisitionTask, task_id)
        assert plant is not None
        assert task is not None
        line = session.exec(
            select(ProductionLine).where(ProductionLine.plant_id == plant.id)
        ).first()
        assert line is not None
        device = session.exec(
            select(Device).where(Device.production_line_id == line.id)
        ).first()
        assert device is not None
        tag = session.exec(select(Tag).where(Tag.device_id == device.id)).first()
        assert tag is not None
        node = session.exec(
            select(AcquisitionNode).where(
                AcquisitionNode.task_id == task.id,
                AcquisitionNode.tag_id == tag.id,
            )
        ).first()
        assert node is not None
        plant.status = AssetStatus.INACTIVE
        line.status = AssetStatus.INACTIVE
        device.status = AssetStatus.INACTIVE
        tag.is_enabled = False
        node.is_enabled = False
        session.add_all([plant, line, device, tag, node])
        session.commit()

    reconciled = seed_demo(start=False)
    assert reconciled["plant_id"] == str(plant_id)
    assert reconciled["task_id"] == str(task_id)

    with Session(engine) as session:
        plant = session.get(Plant, plant_id)
        assert plant is not None
        assert plant.status == AssetStatus.INACTIVE
        lines = session.exec(
            select(ProductionLine).where(ProductionLine.plant_id == plant.id)
        ).all()
        devices = session.exec(
            select(Device).where(
                col(Device.production_line_id).in_([line.id for line in lines])
            )
        ).all()
        tags = session.exec(
            select(Tag).where(col(Tag.device_id).in_([device.id for device in devices]))
        ).all()
        nodes = session.exec(
            select(AcquisitionNode).where(AcquisitionNode.task_id == task_id)
        ).all()
        assert len(lines) == 2
        assert len(devices) == 6
        assert len(tags) == 24
        assert len(nodes) == 12
        assert sum(line.status == AssetStatus.INACTIVE for line in lines) == 1
        assert sum(device.status == AssetStatus.INACTIVE for device in devices) == 1
        assert sum(not tag.is_enabled for tag in tags) == 1
        assert sum(not node.is_enabled for node in nodes) == 1
        assert len({tag.id for tag in tags}) == 24


def test_simulator_browse_and_collection_pipeline(
    client: TestClient,
    runtime_acquisition_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = free_port()
    task_id = create_runtime_task(
        client=client, context=runtime_acquisition_context, port=port
    )
    monkeypatch.setattr(settings, "OPCUA_WORKER_HEARTBEAT_SECONDS", 0.2)

    async def scenario() -> None:
        stop_server = asyncio.Event()
        server_task = asyncio.create_task(
            run_simulator(simulator_args(port), stop_event=stop_server)
        )
        runner: asyncio.Task[None] | None = None
        endpoint = f"opc.tcp://localhost:{port}/industrial-dataops/server/"
        try:
            await wait_endpoint(endpoint)
            nodes, truncated = await browse_variable_nodes(
                endpoint_url=endpoint,
                root_node_id="i=85",
                max_depth=5,
                max_nodes=200,
            )
            assert not truncated
            assert "ns=2;s=Line1.Furnace01.Temperature" in {
                node.node_id for node in nodes
            }

            runner = asyncio.create_task(AcquisitionRunner(task_id).run())
            collected = await wait_for_task(
                task_id, lambda task: task.samples_written >= 3
            )
            assert collected.connection_state == "connected"
            stop_task(task_id)
            await asyncio.wait_for(runner, timeout=10)
        finally:
            stop_task(task_id)
            if runner is not None and not runner.done():
                await asyncio.wait_for(runner, timeout=10)
            stop_server.set()
            await asyncio.wait_for(server_task, timeout=10)

    asyncio.run(scenario())
    with Session(engine) as session:
        samples = session.exec(
            select(TagSample).where(TagSample.task_id == task_id)
        ).all()
        assert len(samples) >= 3
        assert all(sample.status_code == "Good" for sample in samples)


def test_collector_reconnects_after_simulator_restart(
    client: TestClient,
    runtime_acquisition_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = free_port()
    task_id = create_runtime_task(
        client=client, context=runtime_acquisition_context, port=port
    )
    monkeypatch.setattr(settings, "OPCUA_WORKER_HEARTBEAT_SECONDS", 0.2)

    async def scenario() -> None:
        first_stop = asyncio.Event()
        first_server = asyncio.create_task(
            run_simulator(simulator_args(port), stop_event=first_stop)
        )
        endpoint = f"opc.tcp://localhost:{port}/industrial-dataops/server/"
        await wait_endpoint(endpoint)
        runner = asyncio.create_task(AcquisitionRunner(task_id).run())
        try:
            initial = await wait_for_task(
                task_id, lambda task: task.samples_written >= 3
            )
            initial_count = initial.samples_written

            first_stop.set()
            await asyncio.wait_for(first_server, timeout=10)
            await wait_for_task(
                task_id,
                lambda task: task.connection_state in {"reconnecting", "error"},
            )

            second_stop = asyncio.Event()
            second_server = asyncio.create_task(
                run_simulator(simulator_args(port), stop_event=second_stop)
            )
            try:
                await wait_endpoint(endpoint)
                recovered = await wait_for_task(
                    task_id,
                    lambda task: (
                        task.reconnect_count >= 1
                        and task.samples_written > initial_count
                    ),
                    attempts=200,
                )
                assert recovered.connection_state == "connected"
            finally:
                second_stop.set()
                await asyncio.wait_for(second_server, timeout=10)
        finally:
            stop_task(task_id)
            await asyncio.wait_for(runner, timeout=10)

    asyncio.run(scenario())
