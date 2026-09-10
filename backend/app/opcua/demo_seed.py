"""Add missing demo objects; preserve edits, disabled assets and stopped tasks."""

import argparse
import json
import sys

from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from app.core.db import engine
from app.models import (
    AcquisitionDesiredState,
    AcquisitionNode,
    AcquisitionTask,
    AssetStatus,
    Device,
    Plant,
    ProductionLine,
    Tag,
    TagDataType,
    get_datetime_utc,
)
from app.opcua.catalog import DEMO_POINTS

ENDPOINT_URL = "opc.tcp://opcua-simulator:4840/industrial-dataops/server/"


def get_or_create[T](session: Session, statement: SelectOfScalar[T], model: T) -> T:
    existing = session.exec(statement).first()
    if existing is not None:
        return existing
    session.add(model)
    session.flush()
    return model


def seed_demo(*, start: bool) -> dict[str, object]:
    with Session(engine) as session:
        plant = get_or_create(
            session,
            select(Plant).where(Plant.code == "OPC_DEMO"),
            Plant(
                code="OPC_DEMO", name="OPC UA 教学工厂", location="本地 Docker 模拟环境"
            ),
        )
        tasks: list[AcquisitionTask] = []
        for number in (1, 2):
            line = get_or_create(
                session,
                select(ProductionLine).where(
                    ProductionLine.plant_id == plant.id,
                    ProductionLine.code == f"SYN_LINE_{number}",
                ),
                ProductionLine(
                    plant_id=plant.id,
                    code=f"SYN_LINE_{number}",
                    name=f"合成数据{number}号线",
                    process_type="OPC UA 教学模拟，非真实工艺",
                ),
            )
            task_name = (
                "OPC UA 模拟采集任务" if number == 1 else "OPC UA 二号线模拟采集任务"
            )
            task = get_or_create(
                session,
                select(AcquisitionTask).where(
                    AcquisitionTask.plant_id == plant.id,
                    AcquisitionTask.name == task_name,
                ),
                AcquisitionTask(
                    plant_id=plant.id,
                    name=task_name,
                    endpoint_url=ENDPOINT_URL,
                    publishing_interval_ms=1000,
                    batch_size=24,
                    reconnect_delay_seconds=1,
                    max_reconnect_delay_seconds=10,
                ),
            )
            for point in (p for p in DEMO_POINTS if p.line == number):
                device = get_or_create(
                    session,
                    select(Device).where(
                        Device.production_line_id == line.id,
                        Device.code == point.device_code,
                    ),
                    Device(
                        production_line_id=line.id,
                        code=point.device_code,
                        name=point.device_name,
                        device_type="教学模拟设备",
                        manufacturer="内置模拟器",
                    ),
                )
                tag = get_or_create(
                    session,
                    select(Tag).where(
                        Tag.device_id == device.id, Tag.code == point.code
                    ),
                    Tag(
                        device_id=device.id,
                        code=point.code,
                        name=point.name,
                        data_type=TagDataType.FLOAT,
                        unit=point.unit,
                        min_value=point.minimum,
                        max_value=point.maximum,
                        sampling_interval_ms=1000,
                    ),
                )
                get_or_create(
                    session,
                    select(AcquisitionNode).where(
                        AcquisitionNode.task_id == task.id,
                        AcquisitionNode.tag_id == tag.id,
                    ),
                    AcquisitionNode(
                        task_id=task.id,
                        tag_id=tag.id,
                        node_id=f"ns=2;s={point.node_id}",
                    ),
                )
            if start:
                if (
                    plant.status != AssetStatus.ACTIVE
                    or line.status != AssetStatus.ACTIVE
                ):
                    raise ValueError(
                        "请先在资产页面明确启用工厂和产线；初始化不会覆盖禁用状态"
                    )
                task.desired_state = AcquisitionDesiredState.RUNNING
                task.updated_at = get_datetime_utc()
                session.add(task)
            tasks.append(task)
        session.commit()
        return {
            "plant_id": str(plant.id),
            "task_id": str(tasks[0].id),
            "task_name": tasks[0].name,
            "endpoint_url": tasks[0].endpoint_url,
            "tag_count": len(DEMO_POINTS),
            "desired_state": tasks[0].desired_state,
            "task_ids": [str(task.id) for task in tasks],
            "note": "两条线、每线三台设备、每设备四个测点；保留既有配置与启停状态。",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start", action="store_true", help="Explicitly start both demo tasks"
    )
    args = parser.parse_args()
    sys.stdout.write(
        json.dumps(seed_demo(start=args.start), ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
