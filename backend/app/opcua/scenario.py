"""Versioned offline teaching replay. Not a physics model or live DB samples."""

import csv
import hashlib
import io
import math
import random
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from pydantic import BaseModel

from app.opcua.catalog import DEMO_POINTS, DemoPoint

VERSION = "cooling-loop-v1"
START = datetime(2026, 1, 15, tzinfo=UTC)
SEED = 20260911


class ScenarioPoint(BaseModel):
    code: str
    name: str
    line: int
    device: str
    unit: str
    minimum: float
    maximum: float
    baseline: float
    purpose: str


class ScenarioFrame(BaseModel):
    elapsed_seconds: int
    timestamp: datetime
    phase: str
    values: dict[str, float]
    bad_quality_codes: list[str]


class TeachingScenario(BaseModel):
    version: str
    title: str
    source: str
    warning: str
    seed: int
    interval_seconds: int
    csv_sha256: str
    points: list[ScenarioPoint]
    frames: list[ScenarioFrame]


PURPOSE = {
    "FURNACE_TEMP": "热状态观察；本回放不注入炉温故障。",
    "FURNACE_PRESSURE": "旧测点名为炉压；此处仅作独立压力信号，不代表真实炉膛压力或安全阈值。",
    "FURNACE_POWER": "观察加热输入；没有建立能量守恒或控制闭环模型。",
    "FURNACE_EXHAUST": "排烟温度观察，与炉温不重复；不据此判断燃烧效率。",
    "MOTOR_SPEED": "转动状态；与电流、振动、轴承温度分别观察。",
    "MOTOR_VIBRATION": "机械状态信号；本回放没有轴承故障诊断结论。",
    "MOTOR_CURRENT": "电气负载信号；保持基线波动作为非扰动通道。",
    "MOTOR_BEARING_TEMP": "热状态信号；不与电机振动合并为单一健康评分。",
    "PUMP_FLOW": "一号线在60–120秒注入流量下降，90–100秒另注入坏质量。",
    "PUMP_PRESSURE": "一号线与流量扰动同步注入压力上升；该关联来自脚本，不证明堵塞因果。",
    "PUMP_INLET_TEMP": "冷却入口参考量，保持基线波动，用于与出口温度比较。",
    "PUMP_OUTLET_TEMP": "一号线出口温度随脚本扰动升高；不代表真实换热器模型。",
}


def disturbance(seconds: int) -> float:
    if seconds < 60:
        return 0.0
    if seconds < 80:
        return (seconds - 60) / 20
    if seconds < 120:
        return 1.0
    return max(0.0, 1 - (seconds - 120) / 40)


def make_frames() -> list[ScenarioFrame]:
    random_source = random.Random(SEED)
    frames = []
    for seconds in range(0, 181, 2):
        values = {}
        for point in DEMO_POINTS:
            value = (
                point.base
                + point.amplitude * math.sin(2 * math.pi * seconds / point.period)
                + random_source.gauss(0, point.noise)
            )
            # Independent, deliberately injected effects, not inferred causality.
            if point.line == 1:
                value += {
                    "PUMP_FLOW": -20,
                    "PUMP_PRESSURE": 0.2,
                    "PUMP_OUTLET_TEMP": 8,
                }.get(point.code, 0) * disturbance(seconds)
            values[point.code] = round(value, 6)
        frames.append(
            ScenarioFrame(
                elapsed_seconds=seconds,
                timestamp=START + timedelta(seconds=seconds),
                phase="正常基线"
                if seconds < 60
                else "注入扰动"
                if seconds < 120
                else "恢复观察",
                values=values,
                bad_quality_codes=["PUMP_FLOW"] if 90 <= seconds < 100 else [],
            )
        )
    return frames


def csv_content(frames: list[ScenarioFrame]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["timestamp", "tag_code", "value", "quality_code"])
    for frame in frames:
        for code, value in frame.values.items():
            writer.writerow(
                [
                    frame.timestamp.isoformat(),
                    code,
                    value,
                    "BadSensorFailure" if code in frame.bad_quality_codes else "Good",
                ]
            )
    return stream.getvalue()


def point_purpose(point: DemoPoint) -> str:
    if point.line == 1:
        return PURPOSE[point.code]
    if point.code.startswith("L2_PUMP"):
        return "二号线冷却回路保持基线波动，作为对照，不注入一号线的扰动或坏质量。"
    return "二号线对照通道，无扰动注入。" + PURPOSE[point.code.removeprefix("L2_")]


@lru_cache(maxsize=1)
def teaching_scenario() -> TeachingScenario:
    frames = make_frames()
    return TeachingScenario(
        version=VERSION,
        title="热处理与公辅系统：冷却回路扰动回放",
        source="固定种子的离线合成数据；不读取、写入或刷新业务库",
        warning="不是企业生产数据、物理仿真或诊断算法。量程与变化幅度仅供演示，禁止作为现场报警或控制标准。实时状态请查看实时采集页面。",
        seed=SEED,
        interval_seconds=2,
        csv_sha256=hashlib.sha256(csv_content(frames).encode()).hexdigest(),
        points=[
            ScenarioPoint(
                code=p.code,
                name=p.name,
                line=p.line,
                device=p.device_name,
                unit=p.unit,
                minimum=p.minimum,
                maximum=p.maximum,
                baseline=p.base,
                purpose=point_purpose(p),
            )
            for p in sorted(
                DEMO_POINTS,
                key=lambda point: (point.line, point.device_code, point.code),
            )
        ],
        frames=frames,
    )
