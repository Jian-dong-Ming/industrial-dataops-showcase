"""Allowlisted read-only business tools. Plant scope cannot be chosen by the LLM."""

import statistics
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select as sa_select
from sqlalchemy import true
from sqlmodel import Session, col, func, select

from app.api.permissions import require_plant_access
from app.assistant.schemas import StrictModel
from app.models import (
    AcquisitionTask,
    DataQualityIssue,
    Device,
    ImportBatch,
    ProductionLine,
    Tag,
    TagSample,
    User,
)


class FindTags(StrictModel):
    query: str = Field(default="", max_length=100)


class LatestValue(StrictModel):
    tag_id: uuid.UUID
    source: Literal["opcua", "file", "any"] = "opcua"


class ListBatches(StrictModel):
    limit: int = Field(default=5, ge=1, le=10)


class BatchIssues(StrictModel):
    batch_id: uuid.UUID


class TaskStatus(StrictModel):
    limit: int = Field(default=10, ge=1, le=20)


class TagTrend(StrictModel):
    tag_id: uuid.UUID
    hours: int = Field(default=1, ge=1, le=24)
    source: Literal["opcua", "file"] = "opcua"


class AssetOverview(StrictModel):
    pass


TOOL_MODELS: dict[str, type[StrictModel]] = {
    "find_tags": FindTags,
    "latest_value": LatestValue,
    "list_import_batches": ListBatches,
    "inspect_import_batch": BatchIssues,
    "acquisition_status": TaskStatus,
    "tag_trend": TagTrend,
    "asset_overview": AssetOverview,
}
DESCRIPTIONS = {
    "find_tags": "在当前授权工厂按测点名称或编码查找测点，最多返回20条。多个匹配必须请用户明确，不能猜测UUID。",
    "latest_value": "查询已确认测点的最新值、质量和过期状态。实时值默认只查opcua，历史导入需指定file。",
    "list_import_batches": "列出当前授权工厂最近的导入批次编号、状态和统计，不同批次不可混同。",
    "inspect_import_batch": "查询指定导入批次的确定性统计及最多10条错误示例，完整问题在数据治理页面查看。",
    "acquisition_status": "查询当前工厂采集任务的期望/连接状态、心跳、收写/丢弃/错误累计计数。不能据累计错误断言当前故障。",
    "tag_trend": "对已明确测点查询最近1到24小时内最多1000条记录的数值摘要、质量和首末变化。先find_tags消除歧义，不进行预测或故障诊断。",
    "asset_overview": "查询当前工厂产线、设备、测点的真实数量与各设备测点分配，最多展示30台设备。",
}

# Deterministic troubleshooting suggestions, not model-invented root causes.
ISSUE_ACTIONS = {
    "missing_required": "检查字段映射及必填单元格，补齐后重新导入。",
    "invalid_timestamp": "检查时间列、时区和日期格式，不要使用批次编号代替时间。",
    "invalid_value": "核对测点数据类型及原始单元格，去除非法文本。",
    "out_of_range": "核对单位、测点量程和原始记录；不要为通过校验而直接扩大范围。",
    "unknown_tag": "核对测点编码、所属设备和工厂；确认主数据后再导入。",
    "ambiguous_tag": "补充设备编码以区分同名或同编码测点。",
    "duplicate_in_file": "核对文件内同测点同时间的重复记录和保留规则。",
    "duplicate_existing": "核对已入库记录，避免重复导入；不要直接删除历史数据。",
    "bad_quality": "核对数据源质量码及采集状态；异常质量样本不能视为正常工艺值。",
}


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": DESCRIPTIONS[name],
                "parameters": model.model_json_schema(),
            },
        }
        for name, model in TOOL_MODELS.items()
    ]


def _batch_data(batch: ImportBatch) -> dict[str, Any]:
    return {
        "batch_id": str(batch.id),
        "filename": batch.original_filename,
        "status": batch.status.value,
        "counts_final": batch.status.value == "completed",
        "status_note": "只有completed批次的计数是本次完成结果；排队、处理中或失败的零计数不能解释为无数据或无问题。",
        "total_rows": batch.total_rows,
        "accepted_rows": batch.accepted_rows,
        "rejected_rows": batch.rejected_rows,
        "duplicate_rows": batch.duplicate_rows,
        "warning_rows": batch.warning_rows,
        "issue_count": batch.issue_count,
        "stored_issue_count": batch.stored_issue_count,
        "issues_truncated": batch.issues_truncated,
        "created_at": batch.created_at.isoformat(),
    }


def execute_tool(
    *, session: Session, user: User, plant_id: uuid.UUID, name: str, arguments: str
) -> dict[str, Any]:
    require_plant_access(session=session, user=user, plant_id=plant_id)
    model = TOOL_MODELS.get(name)
    if model is None:
        raise ValueError("tool_not_allowed")
    params = model.model_validate_json(arguments)
    if isinstance(params, AssetOverview):
        details = (
            select(Device.id, Device.name, ProductionLine.name, func.count(col(Tag.id)))
            .select_from(Device)
            .join(
                ProductionLine, col(Device.production_line_id) == col(ProductionLine.id)
            )
            .outerjoin(Tag, col(Tag.device_id) == col(Device.id))
            .where(ProductionLine.plant_id == plant_id)
            .group_by(col(Device.id), col(Device.name), col(ProductionLine.name))
            .order_by(col(Device.id))
            .limit(31)
        ).subquery()
        totals = (
            select(
                func.count(func.distinct(ProductionLine.id)).label("line_count"),
                func.count(func.distinct(Device.id)).label("device_count"),
                func.count(col(Tag.id)).label("tag_count"),
            )
            .select_from(ProductionLine)
            .outerjoin(Device, col(Device.production_line_id) == col(ProductionLine.id))
            .outerjoin(Tag, col(Tag.device_id) == col(Device.id))
            .where(ProductionLine.plant_id == plant_id)
        ).subquery()
        # One statement: totals and the capped detail share the same MVCC snapshot.
        # Outer join retains the totals row for plants without devices.
        overview_result = (
            session.connection()
            .execute(
                sa_select(totals, details)
                .select_from(totals)
                .outerjoin(details, true())
                .order_by(details.c.id)
            )
            .all()
        )
        line_count, device_count, tag_count = overview_result[0][:3]
        overview_rows = [row[3:] for row in overview_result if row[3] is not None]
        return {
            "line_count": line_count,
            "device_count": device_count,
            "tag_count": tag_count,
            "counts_include_disabled": True,
            "devices": [
                {
                    "device_id": str(key),
                    "device": device,
                    "line": line,
                    "tag_count": count,
                }
                for key, device, line, count in overview_rows[:30]
            ],
            "truncated": len(overview_rows) > 30,
            "note": "总数由数据库独立聚合，包含禁用资产，不代表在线状态；总数与明细来自同条查询，设备明细最多30条，不能用明细长度代替总数。",
        }
    if isinstance(params, TaskStatus):
        tasks = session.exec(
            select(AcquisitionTask)
            .where(AcquisitionTask.plant_id == plant_id)
            .order_by(col(AcquisitionTask.name), col(AcquisitionTask.id))
            .limit(params.limit + 1)
        ).all()
        return {
            "tasks": [
                {
                    "task_id": str(task.id),
                    "name": task.name,
                    "desired_state": task.desired_state.value,
                    "connection_state": task.connection_state.value,
                    "heartbeat_at": task.worker_heartbeat_at.isoformat()
                    if task.worker_heartbeat_at
                    else None,
                    "last_sample_at": task.last_sample_at.isoformat()
                    if task.last_sample_at
                    else None,
                    "received": task.samples_received,
                    "written": task.samples_written,
                    "dropped": task.dropped_count,
                    "duplicates": task.duplicate_count,
                    "errors": task.error_count,
                    "reconnects": task.reconnect_count,
                }
                for task in tasks[: params.limit]
            ],
            "truncated": len(tasks) > params.limit,
            "note": "计数为累计值，不是本小时计数；连接状态是记录值，应结合心跳与最后采样时间判断，不能仅凭running认定数据新鲜。",
        }
    if isinstance(params, TagTrend):
        tag = session.exec(
            select(Tag)
            .join(Device, col(Tag.device_id) == col(Device.id))
            .join(
                ProductionLine, col(Device.production_line_id) == col(ProductionLine.id)
            )
            .where(Tag.id == params.tag_id, ProductionLine.plant_id == plant_id)
        ).first()
        if tag is None:
            raise HTTPException(404, "测点不存在或不在当前授权范围")
        end = datetime.now(UTC)
        start = end - timedelta(hours=params.hours)
        samples = session.exec(
            select(TagSample)
            .where(
                TagSample.tag_id == tag.id,
                TagSample.source_type == params.source,
                TagSample.source_timestamp >= start,
                TagSample.source_timestamp <= end,
            )
            .order_by(col(TagSample.source_timestamp).desc(), col(TagSample.id).desc())
            .limit(1001)
        ).all()
        selected = list(reversed(samples[:1000]))
        values = [
            row.numeric_value
            for row in selected
            if row.is_good and row.numeric_value is not None
        ]
        return {
            "tag_id": str(tag.id),
            "code": tag.code,
            "name": tag.name,
            "unit": tag.unit,
            "enabled": tag.is_enabled,
            "source": params.source,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "sample_count": len(selected),
            "truncated": len(samples) > 1000,
            "bad_quality_count": sum(not row.is_good for row in selected),
            "good_numeric_count": len(values),
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
            "mean": statistics.fmean(values) if values else None,
            "first_to_last_change": values[-1] - values[0] if len(values) > 1 else None,
            "first_timestamp": selected[0].source_timestamp.isoformat()
            if selected
            else None,
            "last_timestamp": selected[-1].source_timestamp.isoformat()
            if selected
            else None,
            "note": "仅最近至多1000条，截断时不是整个时间窗口统计。数值统计只含Good数值样本；首末差不等于趋势斜率或异常诊断。文件来源不是实时数据，无样本不能解释为零。",
        }
    if isinstance(params, FindTags):
        statement = (
            select(Tag, Device)
            .join(Device, col(Tag.device_id) == col(Device.id))
            .join(
                ProductionLine, col(Device.production_line_id) == col(ProductionLine.id)
            )
            .where(ProductionLine.plant_id == plant_id)
        )
        if params.query:
            statement = statement.where(
                col(Tag.name).contains(params.query, autoescape=True)
                | col(Tag.code).contains(params.query, autoescape=True)
                | col(Device.name).contains(params.query, autoescape=True)
            )
        rows = session.exec(
            statement.order_by(col(Tag.code), col(Tag.id)).limit(21)
        ).all()
        return {
            "tags": [
                {
                    "tag_id": str(tag.id),
                    "code": tag.code,
                    "name": tag.name,
                    "device": device.name,
                    "unit": tag.unit,
                    "enabled": tag.is_enabled,
                }
                for tag, device in rows[:20]
            ],
            "truncated": len(rows) > 20,
            "instruction": "多个匹配时请用户明确设备/测点，不要自行选择。",
        }
    if isinstance(params, LatestValue):
        tag = session.exec(
            select(Tag)
            .join(Device, col(Tag.device_id) == col(Device.id))
            .join(
                ProductionLine, col(Device.production_line_id) == col(ProductionLine.id)
            )
            .where(Tag.id == params.tag_id, ProductionLine.plant_id == plant_id)
        ).first()
        if tag is None:
            raise HTTPException(404, "测点不存在或不在当前授权范围")
        sample_statement = select(TagSample).where(TagSample.tag_id == tag.id)
        if params.source != "any":
            sample_statement = sample_statement.where(
                TagSample.source_type == params.source
            )
        sample = session.exec(
            sample_statement.order_by(
                col(TagSample.source_timestamp).desc(), col(TagSample.id).desc()
            ).limit(1)
        ).first()
        result: dict[str, Any] = {
            "tag_id": str(tag.id),
            "code": tag.code,
            "name": tag.name,
            "unit": tag.unit,
            "enabled": tag.is_enabled,
            "requested_source": params.source,
            "stale_after_seconds": max(60, 3 * tag.sampling_interval_ms / 1000),
            "freshness_rule": "本项目演示规则，不是生产报警标准；Good也不代表工艺合格。",
            "sample": None,
        }
        if sample:
            timestamp = (
                sample.source_timestamp.replace(tzinfo=UTC)
                if sample.source_timestamp.tzinfo is None
                else sample.source_timestamp
            )
            age = (datetime.now(UTC) - timestamp).total_seconds()
            result["sample"] = {
                "sample_id": sample.id,
                "value": sample.value,
                "source": sample.source_type.value,
                "source_timestamp": timestamp.isoformat(),
                "is_good": sample.is_good,
                "status_code": sample.status_code,
                "age_seconds": round(age, 1),
                "stale": age > max(60, 3 * tag.sampling_interval_ms / 1000),
                "future_timestamp": age < -5,
            }
        return result
    if isinstance(params, ListBatches):
        batches = session.exec(
            select(ImportBatch)
            .where(ImportBatch.plant_id == plant_id)
            .order_by(col(ImportBatch.created_at).desc(), col(ImportBatch.id))
            .limit(params.limit)
        ).all()
        return {
            "batches": [_batch_data(batch) for batch in batches],
            "limit": params.limit,
        }
    if isinstance(params, BatchIssues):
        batch = session.exec(
            select(ImportBatch).where(
                ImportBatch.id == params.batch_id, ImportBatch.plant_id == plant_id
            )
        ).first()
        if batch is None:
            raise HTTPException(404, "批次不存在或不在当前授权范围")
        groups = session.exec(
            select(DataQualityIssue.issue_type, func.count(col(DataQualityIssue.id)))
            .where(DataQualityIssue.batch_id == batch.id)
            .group_by(DataQualityIssue.issue_type)
        ).all()
        issue_rows = session.exec(
            select(DataQualityIssue)
            .where(DataQualityIssue.batch_id == batch.id)
            .order_by(col(DataQualityIssue.row_number), col(DataQualityIssue.id))
            .limit(10)
        ).all()
        return {
            **_batch_data(batch),
            "stored_issue_summary": [
                {
                    "type": str(kind),
                    "count": count,
                    "suggestion": ISSUE_ACTIONS[str(kind)],
                }
                for kind, count in sorted(groups, key=lambda row: str(row[0]))
            ],
            "examples": [
                {
                    "row": row.row_number,
                    "type": row.issue_type.value,
                    "field": row.field_name,
                    "message": row.message,
                }
                for row in issue_rows
            ],
            "note": "问题摘要基于已保存问题；截断时不是全量分布。示例最多10条。问题条数不等于失败行数；重复行属于拒绝行的子集，警告行可已入库，不要相加当成总行数。建议是排查步骤，不是已确认根因或已执行修复。",
        }
    raise ValueError("tool_not_allowed")
