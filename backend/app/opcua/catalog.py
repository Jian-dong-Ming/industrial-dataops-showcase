"""Single source of truth for 24 synthetic teaching signals, not plant standards."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoPoint:
    line: int
    device_code: str
    device_name: str
    code: str
    name: str
    node_id: str
    unit: str
    minimum: float
    maximum: float
    base: float
    amplitude: float
    period: float
    noise: float


def build_catalog() -> tuple[DemoPoint, ...]:
    # Each device has four distinct operational measurements; no duplicate aliases.
    specs = (
        (
            "FURNACE_01",
            "加热炉",
            "Furnace01",
            "FURNACE_TEMP",
            "炉温",
            "Temperature",
            "℃",
            700,
            1000,
            850,
            12,
            60,
            0.6,
        ),
        (
            "FURNACE_01",
            "加热炉",
            "Furnace01",
            "FURNACE_PRESSURE",
            "炉压",
            "Pressure",
            "MPa",
            0,
            2,
            1.2,
            0.08,
            30,
            0.005,
        ),
        (
            "MOTOR_01",
            "驱动电机",
            "Motor01",
            "MOTOR_SPEED",
            "电机转速",
            "Speed",
            "rpm",
            0,
            3000,
            1450,
            20,
            20,
            1.5,
        ),
        (
            "MOTOR_01",
            "驱动电机",
            "Motor01",
            "MOTOR_VIBRATION",
            "电机振动",
            "Vibration",
            "mm/s",
            0,
            20,
            2.5,
            0.3,
            15,
            0.02,
        ),
        (
            "FURNACE_01",
            "加热炉",
            "Furnace01",
            "FURNACE_POWER",
            "加热功率",
            "Power",
            "kW",
            0,
            500,
            240,
            10,
            60,
            0.5,
        ),
        (
            "FURNACE_01",
            "加热炉",
            "Furnace01",
            "FURNACE_EXHAUST",
            "排烟温度",
            "Exhaust",
            "℃",
            0,
            500,
            180,
            8,
            80,
            0.4,
        ),
        (
            "MOTOR_01",
            "驱动电机",
            "Motor01",
            "MOTOR_CURRENT",
            "电机电流",
            "Current",
            "A",
            0,
            100,
            32,
            2,
            20,
            0.1,
        ),
        (
            "MOTOR_01",
            "驱动电机",
            "Motor01",
            "MOTOR_BEARING_TEMP",
            "轴承温度",
            "BearingTemperature",
            "℃",
            0,
            120,
            55,
            3,
            120,
            0.2,
        ),
        (
            "PUMP_01",
            "冷却水泵",
            "Pump01",
            "PUMP_FLOW",
            "冷却水流量",
            "Flow",
            "m³/h",
            0,
            100,
            45,
            2,
            40,
            0.1,
        ),
        (
            "PUMP_01",
            "冷却水泵",
            "Pump01",
            "PUMP_PRESSURE",
            "冷却水压力",
            "Pressure",
            "MPa",
            0,
            1.6,
            0.6,
            0.03,
            40,
            0.002,
        ),
        (
            "PUMP_01",
            "冷却水泵",
            "Pump01",
            "PUMP_INLET_TEMP",
            "冷却水入口温度",
            "InletTemperature",
            "℃",
            0,
            80,
            25,
            1,
            120,
            0.05,
        ),
        (
            "PUMP_01",
            "冷却水泵",
            "Pump01",
            "PUMP_OUTLET_TEMP",
            "冷却水出口温度",
            "OutletTemperature",
            "℃",
            0,
            100,
            35,
            1.5,
            120,
            0.05,
        ),
    )
    return tuple(
        DemoPoint(
            line,
            device,
            f"{line}号线{name}",
            ("" if line == 1 else "L2_") + code,
            title,
            f"Line{line}.{node_device}.{node}",
            unit,
            minimum,
            maximum,
            base if line == 1 else base * 0.98,
            amplitude,
            period,
            noise,
        )
        for line in (1, 2)
        for device, name, node_device, code, title, node, unit, minimum, maximum, base, amplitude, period, noise in specs
    )


DEMO_POINTS = build_catalog()
