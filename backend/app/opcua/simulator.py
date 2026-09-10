import argparse
import asyncio
import logging
import math
import os
import random
import signal
from dataclasses import dataclass
from datetime import UTC

from asyncua import ua
from asyncua.common.node import Node
from asyncua.server.server import Server
from asyncua.ua.attribute_ids import AttributeIds
from asyncua.ua.status_codes import StatusCodes

from app.opcua.catalog import DEMO_POINTS

logger = logging.getLogger(__name__)

NAMESPACE_URI = "urn:industrial-dataops:synthetic"
DEFAULT_ENDPOINT = "opc.tcp://0.0.0.0:4840/industrial-dataops/server/"


@dataclass(frozen=True)
class SignalDefinition:
    node_id: str
    display_name: str
    base: float
    amplitude: float
    period_seconds: float
    drift_per_hour: float
    noise_sigma: float


SIGNALS = tuple(
    SignalDefinition(
        point.node_id,
        point.name,
        point.base,
        point.amplitude,
        point.period,
        0.0,
        point.noise,
    )
    for point in DEMO_POINTS
)


class SyntheticSignalGenerator:
    def __init__(
        self,
        *,
        seed: int,
        anomaly_mode: str,
        anomaly_after_seconds: float,
        anomaly_duration_seconds: float,
    ) -> None:
        self.random = random.Random(seed)
        self.anomaly_mode = anomaly_mode
        self.anomaly_after_seconds = anomaly_after_seconds
        self.anomaly_duration_seconds = anomaly_duration_seconds
        self.last_values: dict[str, float] = {}

    def anomaly_active(self, elapsed_seconds: float) -> bool:
        if self.anomaly_mode == "none":
            return False
        if elapsed_seconds < self.anomaly_after_seconds:
            return False
        if self.anomaly_duration_seconds <= 0:
            return True
        return elapsed_seconds <= (
            self.anomaly_after_seconds + self.anomaly_duration_seconds
        )

    def value(
        self, definition: SignalDefinition, elapsed_seconds: float
    ) -> tuple[float, ua.StatusCode]:
        periodic = definition.amplitude * math.sin(
            2 * math.pi * elapsed_seconds / definition.period_seconds
        )
        drift = definition.drift_per_hour * elapsed_seconds / 3600
        noise = self.random.gauss(0, definition.noise_sigma)
        value = definition.base + periodic + drift + noise
        status = ua.StatusCode(ua.UInt32(StatusCodes.Good))

        if self.anomaly_active(elapsed_seconds):
            match self.anomaly_mode:
                case "spike":
                    value += definition.amplitude * 8
                case "noise":
                    value += self.random.gauss(0, definition.noise_sigma * 20)
                case "flatline":
                    value = self.last_values.get(definition.node_id, value)
                case "bad_quality":
                    status = ua.StatusCode(ua.UInt32(StatusCodes.BadSensorFailure))

        value = round(value, 6)
        self.last_values[definition.node_id] = value
        return value, status


async def run_simulator(
    args: argparse.Namespace, stop_event: asyncio.Event | None = None
) -> None:
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    server = Server()
    await server.init()
    server.set_endpoint(args.endpoint)
    server.set_server_name("Industrial DataOps Synthetic OPC UA Server")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    namespace_index = await server.register_namespace(NAMESPACE_URI)

    plant = await server.nodes.objects.add_object(
        ua.NodeId.from_string(f"ns={namespace_index};s=IndustrialDataOps"),
        "IndustrialDataOps",
    )
    process = await plant.add_object(
        ua.NodeId.from_string(f"ns={namespace_index};s=Line1"), "SyntheticLine1"
    )
    processes = {"Line1": process}
    processes["Line2"] = await plant.add_object(
        ua.NodeId.from_string(f"ns={namespace_index};s=Line2"), "SyntheticLine2"
    )
    devices: dict[str, Node] = {}
    nodes: dict[str, Node] = {}
    for definition in SIGNALS:
        line_key, device_key, _ = definition.node_id.split(".")
        device_path = f"{line_key}.{device_key}"
        if device_path not in devices:
            devices[device_path] = await processes[line_key].add_object(
                ua.NodeId.from_string(f"ns={namespace_index};s={device_path}"),
                device_key,
            )
        node = await devices[device_path].add_variable(
            ua.NodeId.from_string(f"ns={namespace_index};s={definition.node_id}"),
            definition.display_name,
            definition.base,
            ua.VariantType.Double,
        )
        nodes[definition.node_id] = node

    anomaly_node = await process.add_variable(
        ua.NodeId.from_string(f"ns={namespace_index};s=Simulator.AnomalyMode"),
        "异常模式",
        args.anomaly_mode,
        ua.VariantType.String,
    )
    interval_node = await process.add_variable(
        ua.NodeId.from_string(f"ns={namespace_index};s=Simulator.IntervalMs"),
        "采样间隔毫秒",
        args.interval_ms,
        ua.VariantType.Int32,
    )

    owns_stop_event = stop_event is None
    stop_event = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    if owns_stop_event:
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_name, stop_event.set)

    generator = SyntheticSignalGenerator(
        seed=args.seed,
        anomaly_mode=args.anomaly_mode,
        anomaly_after_seconds=args.anomaly_after_seconds,
        anomaly_duration_seconds=args.anomaly_duration_seconds,
    )
    started = loop.time()
    logger.info("Synthetic OPC UA server listening on %s", args.endpoint)
    logger.info(
        "Namespace index %s; anomaly mode %s", namespace_index, args.anomaly_mode
    )

    async with server:
        while not stop_event.is_set():
            elapsed = loop.time() - started
            now = ua.DateTime.now(UTC)
            for definition in SIGNALS:
                value, status = generator.value(definition, elapsed)
                data_value = ua.DataValue(
                    Value=ua.Variant(value, ua.VariantType.Double),
                    StatusCode=status,
                    SourceTimestamp=now,
                    ServerTimestamp=now,
                )
                await nodes[definition.node_id].write_attribute(
                    AttributeIds.Value, data_value
                )
            await anomaly_node.write_value(args.anomaly_mode)
            await interval_node.write_value(args.interval_ms, ua.VariantType.Int32)
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=args.interval_ms / 1000
                )
            except TimeoutError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the synthetic OPC UA server")
    parser.add_argument(
        "--endpoint", default=os.getenv("OPCUA_SIMULATOR_ENDPOINT", DEFAULT_ENDPOINT)
    )
    parser.add_argument(
        "--interval-ms",
        type=int,
        default=int(os.getenv("OPCUA_SIMULATOR_INTERVAL_MS", "500")),
    )
    parser.add_argument(
        "--seed", type=int, default=int(os.getenv("OPCUA_SIMULATOR_SEED", "20260816"))
    )
    parser.add_argument(
        "--anomaly-mode",
        choices=("none", "spike", "noise", "flatline", "bad_quality"),
        default=os.getenv("OPCUA_SIMULATOR_ANOMALY_MODE", "none"),
    )
    parser.add_argument(
        "--anomaly-after-seconds",
        type=float,
        default=float(os.getenv("OPCUA_SIMULATOR_ANOMALY_AFTER_SECONDS", "60")),
    )
    parser.add_argument(
        "--anomaly-duration-seconds",
        type=float,
        default=float(os.getenv("OPCUA_SIMULATOR_ANOMALY_DURATION_SECONDS", "30")),
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    args = build_parser().parse_args()
    if args.interval_ms < 100 or args.interval_ms > 60_000:
        raise SystemExit("interval-ms must be between 100 and 60000")
    asyncio.run(run_simulator(args))


if __name__ == "__main__":
    main()
