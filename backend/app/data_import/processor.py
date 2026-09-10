import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.core.config import settings
from app.data_import.reader import iter_tabular_rows
from app.models import (
    AssetStatus,
    DataQualityIssue,
    DataQualityIssueType,
    DataQualitySeverity,
    Device,
    ImportBatch,
    ImportBatchStatus,
    ImportFieldMapping,
    ImportLayout,
    ImportMappingInput,
    ProductionLine,
    SampleSourceType,
    Tag,
    TagDataType,
    TagSample,
    get_datetime_utc,
)


@dataclass(frozen=True)
class CandidateSample:
    row_number: int
    tag: Tag
    value: Any
    numeric_value: float | None
    source_timestamp: datetime
    status_code: str
    is_good: bool


def _raw_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:500]


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text_value = str(value).strip()
        if text_value.endswith("Z"):
            text_value = f"{text_value[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text_value)
        except ValueError as exc:
            raise ValueError("时间必须是 ISO 8601 或常见日期时间格式") from exc
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(settings.IMPORT_DEFAULT_TIMEZONE))
        except ZoneInfoNotFoundError as exc:
            raise RuntimeError("配置的默认时区不可用") from exc
    return parsed.astimezone(UTC)


def _parse_value(value: Any, data_type: TagDataType) -> tuple[Any, float | None]:
    if data_type == TagDataType.STRING:
        normalized = str(value).strip()
        return normalized, None
    if data_type == TagDataType.BOOLEAN:
        if isinstance(value, bool):
            return value, float(value)
        normalized = str(value).strip().casefold()
        if normalized in {"1", "true", "yes", "是", "开"}:
            return True, 1.0
        if normalized in {"0", "false", "no", "否", "关"}:
            return False, 0.0
        raise ValueError("布尔值只能使用 true/false、1/0、是/否或开/关")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("数值格式无效") from exc
    if not math.isfinite(number):
        raise ValueError("数值不能是 NaN 或无穷大")
    if data_type == TagDataType.INTEGER:
        if not number.is_integer():
            raise ValueError("整数测点不能导入小数")
        integer = int(number)
        return integer, float(integer)
    return number, number


def _quality(value: Any) -> tuple[str, bool]:
    if _is_missing(value):
        return "Good", True
    status_code = str(value).strip()[:64]
    normalized = status_code.casefold()
    is_good = normalized in {"good", "ok", "192", "0xc0", "true"}
    return status_code, is_good


def _save_mapping(
    *, session: Session, batch: ImportBatch, mapping: ImportMappingInput
) -> None:
    session.exec(
        delete(ImportFieldMapping).where(col(ImportFieldMapping.batch_id) == batch.id)
    )
    values: list[tuple[str, str]] = [("timestamp_column", mapping.timestamp_column)]
    if mapping.quality_column:
        values.append(("quality_column", mapping.quality_column))
    if mapping.layout == ImportLayout.LONG:
        if mapping.tag_code_column:
            values.append(("tag_code_column", mapping.tag_code_column))
        if mapping.value_column:
            values.append(("value_column", mapping.value_column))
        if mapping.device_code_column:
            values.append(("device_code_column", mapping.device_code_column))
    else:
        values.extend(
            (f"wide_value_{index:03d}", item.source_column)
            for index, item in enumerate(mapping.wide_columns, start=1)
        )
    session.add_all(
        [
            ImportFieldMapping(
                batch_id=batch.id,
                source_column=source_column,
                target_field=target_field,
            )
            for target_field, source_column in values
        ]
    )
    batch.mapping_config = mapping.model_dump(mode="json")
    session.add(batch)


def process_import_batch(
    *,
    session: Session,
    batch: ImportBatch,
    mapping: ImportMappingInput,
    progress: Callable[[int], None] | None = None,
    before_commit: Callable[[], None] | None = None,
    record_failure: bool = True,
    claimed: bool = False,
) -> ImportBatch:
    if not batch.storage_key:
        raise ValueError("导入源文件已不存在")
    source_path = Path(batch.storage_key)
    if not source_path.is_file():
        raise ValueError("导入源文件已不存在")
    if batch.status not in {
        ImportBatchStatus.UPLOADED,
        ImportBatchStatus.FAILED,
    } and not (claimed and batch.status == ImportBatchStatus.PROCESSING):
        raise ValueError("当前批次状态不允许处理")

    batch.status = ImportBatchStatus.PROCESSING
    batch.started_at = get_datetime_utc()
    batch.completed_at = None
    batch.error_message = None
    session.add(batch)
    session.commit()

    try:
        _save_mapping(session=session, batch=batch, mapping=mapping)
        session.exec(
            delete(DataQualityIssue).where(col(DataQualityIssue.batch_id) == batch.id)
        )

        tag_rows = session.exec(
            select(Tag, Device)
            .join(Device, col(Device.id) == col(Tag.device_id))
            .join(
                ProductionLine,
                col(ProductionLine.id) == col(Device.production_line_id),
            )
            .where(ProductionLine.plant_id == batch.plant_id)
            .where(
                ProductionLine.status == AssetStatus.ACTIVE,
                Device.status == AssetStatus.ACTIVE,
                col(Tag.is_enabled).is_(True),
            )
        ).all()
        by_device_and_tag = {
            (device.code.casefold(), tag.code.casefold()): tag
            for tag, device in tag_rows
        }
        by_tag: dict[str, list[Tag]] = {}
        for tag, _device in tag_rows:
            by_tag.setdefault(tag.code.casefold(), []).append(tag)

        total_rows = 0
        rejected_rows = 0
        duplicate_rows = 0
        warning_row_numbers: set[int] = set()
        issue_count = 0
        stored_issue_count = 0
        seen_in_file: set[tuple[uuid.UUID, datetime]] = set()
        candidates: list[CandidateSample] = []
        accepted_rows = 0

        def add_issue(
            *,
            row_number: int,
            issue_type: DataQualityIssueType,
            severity: DataQualitySeverity,
            field_name: str | None,
            raw_value: Any,
            message: str,
        ) -> None:
            nonlocal issue_count, stored_issue_count
            issue_count += 1
            if severity == DataQualitySeverity.WARNING:
                warning_row_numbers.add(row_number)
            if stored_issue_count >= settings.IMPORT_MAX_ISSUES:
                return
            session.add(
                DataQualityIssue(
                    batch_id=batch.id,
                    row_number=row_number,
                    issue_type=issue_type,
                    severity=severity,
                    field_name=field_name,
                    raw_value=_raw_text(raw_value),
                    message=message,
                )
            )
            stored_issue_count += 1

        def persist_candidates(chunk: list[CandidateSample]) -> None:
            nonlocal accepted_rows, rejected_rows, duplicate_rows
            if not chunk:
                return
            tag_ids = {item.tag.id for item in chunk}
            earliest = min(item.source_timestamp for item in chunk)
            latest = max(item.source_timestamp for item in chunk)
            existing = set(
                session.exec(
                    select(TagSample.tag_id, TagSample.source_timestamp).where(
                        col(TagSample.import_batch_id).is_not(None),
                        col(TagSample.tag_id).in_(tag_ids),
                        TagSample.source_timestamp >= earliest,
                        TagSample.source_timestamp <= latest,
                    )
                ).all()
            )
            new_samples: list[CandidateSample] = []
            for item in chunk:
                if (item.tag.id, item.source_timestamp) in existing:
                    rejected_rows += 1
                    duplicate_rows += 1
                    add_issue(
                        row_number=item.row_number,
                        issue_type=DataQualityIssueType.DUPLICATE_EXISTING,
                        severity=DataQualitySeverity.ERROR,
                        field_name=mapping.timestamp_column,
                        raw_value=item.source_timestamp.isoformat(),
                        message="数据库中已存在同一测点和时间戳的文件数据",
                    )
                else:
                    new_samples.append(item)

            if not new_samples:
                return
            statement = (
                insert(TagSample)
                .on_conflict_do_nothing(
                    index_elements=["tag_id", "source_timestamp"],
                    index_where=col(TagSample.import_batch_id).is_not(None),
                )
                .returning(col(TagSample.id))
            )
            # Parameter-list execution lets SQLAlchemy batch values without
            # building a fresh expression/bind-parameter tree for every cell.
            # Keep the same conflict target and returned IDs for exact counts.
            parameters = [
                {
                    "task_id": None,
                    "import_batch_id": batch.id,
                    "source_type": SampleSourceType.FILE,
                    "tag_id": item.tag.id,
                    "value": item.value,
                    "numeric_value": item.numeric_value,
                    "source_timestamp": item.source_timestamp,
                    "server_timestamp": None,
                    "received_at": get_datetime_utc(),
                    "status_code": item.status_code,
                    "is_good": item.is_good,
                }
                for item in new_samples
            ]
            written = len(session.exec(statement, params=parameters).all())
            accepted_rows += written
            if written < len(new_samples):
                rejected_rows += len(new_samples) - written
                duplicate_rows += len(new_samples) - written

        if mapping.layout == ImportLayout.LONG:
            required_columns = {
                mapping.timestamp_column,
                str(mapping.tag_code_column),
                str(mapping.value_column),
            }
        else:
            required_columns = {
                mapping.timestamp_column,
                *(item.source_column for item in mapping.wide_columns),
            }
        optional_columns = {
            column
            for column in (mapping.device_code_column, mapping.quality_column)
            if column
        }
        first_row_columns: set[str] | None = None

        for row_number, row in iter_tabular_rows(
            source_path, batch.file_format, batch.file_encoding
        ):
            measurement_count = (
                1 if mapping.layout == ImportLayout.LONG else len(mapping.wide_columns)
            )
            total_rows += measurement_count
            if progress is not None:
                progress(total_rows)
            if first_row_columns is None:
                first_row_columns = set(row)
                missing_columns = (
                    required_columns | optional_columns
                ) - first_row_columns
                if missing_columns:
                    raise ValueError(
                        "映射列不存在：" + "、".join(sorted(missing_columns))
                    )

            timestamp_raw = row.get(mapping.timestamp_column)
            if _is_missing(timestamp_raw):
                rejected_rows += measurement_count
                add_issue(
                    row_number=row_number,
                    issue_type=DataQualityIssueType.MISSING_REQUIRED,
                    severity=DataQualitySeverity.ERROR,
                    field_name=mapping.timestamp_column,
                    raw_value=timestamp_raw,
                    message="数据时间不能为空",
                )
                continue
            try:
                source_timestamp = _parse_timestamp(timestamp_raw)
            except ValueError as exc:
                rejected_rows += measurement_count
                add_issue(
                    row_number=row_number,
                    issue_type=DataQualityIssueType.INVALID_TIMESTAMP,
                    severity=DataQualitySeverity.ERROR,
                    field_name=mapping.timestamp_column,
                    raw_value=timestamp_raw,
                    message=str(exc),
                )
                continue

            quality_raw = (
                row.get(mapping.quality_column) if mapping.quality_column else None
            )
            status_code, is_good = _quality(quality_raw)
            if not is_good:
                add_issue(
                    row_number=row_number,
                    issue_type=DataQualityIssueType.BAD_QUALITY,
                    severity=DataQualitySeverity.WARNING,
                    field_name=mapping.quality_column,
                    raw_value=quality_raw,
                    message="质量码不是 Good；保留原始值并标记为非良好数据",
                )

            if mapping.layout == ImportLayout.LONG:
                tag_column = str(mapping.tag_code_column)
                value_column = str(mapping.value_column)
                measurements = [
                    (
                        value_column,
                        tag_column,
                        row.get(tag_column),
                        row.get(mapping.device_code_column)
                        if mapping.device_code_column
                        else None,
                        row.get(value_column),
                    )
                ]
            else:
                measurements = [
                    (
                        item.source_column,
                        item.source_column,
                        item.tag_code,
                        item.device_code,
                        row.get(item.source_column),
                    )
                    for item in mapping.wide_columns
                ]

            for (
                value_field,
                tag_field,
                tag_code_raw,
                device_code_raw,
                value_raw,
            ) in measurements:
                if _is_missing(tag_code_raw) or _is_missing(value_raw):
                    rejected_rows += 1
                    add_issue(
                        row_number=row_number,
                        issue_type=DataQualityIssueType.MISSING_REQUIRED,
                        severity=DataQualitySeverity.ERROR,
                        field_name=value_field,
                        raw_value=value_raw,
                        message="测点编码和值均不能为空",
                    )
                    continue

                tag_code = str(tag_code_raw).strip().casefold()
                if not _is_missing(device_code_raw):
                    matched_tag = by_device_and_tag.get(
                        (str(device_code_raw).strip().casefold(), tag_code)
                    )
                    if matched_tag is None:
                        rejected_rows += 1
                        add_issue(
                            row_number=row_number,
                            issue_type=DataQualityIssueType.UNKNOWN_TAG,
                            severity=DataQualitySeverity.ERROR,
                            field_name=tag_field,
                            raw_value=tag_code_raw,
                            message="工厂内不存在该启用设备与测点组合",
                        )
                        continue
                    tag = matched_tag
                else:
                    matches = by_tag.get(tag_code, [])
                    if not matches:
                        rejected_rows += 1
                        add_issue(
                            row_number=row_number,
                            issue_type=DataQualityIssueType.UNKNOWN_TAG,
                            severity=DataQualitySeverity.ERROR,
                            field_name=tag_field,
                            raw_value=tag_code_raw,
                            message="工厂内不存在该启用测点编码",
                        )
                        continue
                    if len(matches) > 1:
                        rejected_rows += 1
                        add_issue(
                            row_number=row_number,
                            issue_type=DataQualityIssueType.AMBIGUOUS_TAG,
                            severity=DataQualitySeverity.ERROR,
                            field_name=tag_field,
                            raw_value=tag_code_raw,
                            message="测点编码不唯一，请指定设备编码",
                        )
                        continue
                    tag = matches[0]

                try:
                    value, numeric_value = _parse_value(value_raw, tag.data_type)
                except ValueError as exc:
                    rejected_rows += 1
                    add_issue(
                        row_number=row_number,
                        issue_type=DataQualityIssueType.INVALID_VALUE,
                        severity=DataQualitySeverity.ERROR,
                        field_name=value_field,
                        raw_value=value_raw,
                        message=str(exc),
                    )
                    continue
                if numeric_value is not None and (
                    (tag.min_value is not None and numeric_value < tag.min_value)
                    or (tag.max_value is not None and numeric_value > tag.max_value)
                ):
                    rejected_rows += 1
                    add_issue(
                        row_number=row_number,
                        issue_type=DataQualityIssueType.OUT_OF_RANGE,
                        severity=DataQualitySeverity.ERROR,
                        field_name=value_field,
                        raw_value=value_raw,
                        message=f"数值超出测点范围 [{tag.min_value}, {tag.max_value}]",
                    )
                    continue

                key = (tag.id, source_timestamp)
                if key in seen_in_file:
                    rejected_rows += 1
                    duplicate_rows += 1
                    add_issue(
                        row_number=row_number,
                        issue_type=DataQualityIssueType.DUPLICATE_IN_FILE,
                        severity=DataQualitySeverity.ERROR,
                        field_name=value_field,
                        raw_value=timestamp_raw,
                        message="文件内同一测点和时间戳重复",
                    )
                    continue
                seen_in_file.add(key)

                candidates.append(
                    CandidateSample(
                        row_number=row_number,
                        tag=tag,
                        value=value,
                        numeric_value=numeric_value,
                        source_timestamp=source_timestamp,
                        status_code=status_code,
                        is_good=is_good,
                    )
                )
                if len(candidates) >= settings.IMPORT_CHUNK_SIZE:
                    persist_candidates(candidates)
                    candidates.clear()

        persist_candidates(candidates)

        if before_commit is not None:
            before_commit()

        batch.total_rows = total_rows
        batch.accepted_rows = accepted_rows
        batch.rejected_rows = rejected_rows
        batch.duplicate_rows = duplicate_rows
        batch.warning_rows = len(warning_row_numbers)
        batch.issue_count = issue_count
        batch.stored_issue_count = stored_issue_count
        batch.issues_truncated = issue_count > stored_issue_count
        batch.status = ImportBatchStatus.COMPLETED
        batch.completed_at = get_datetime_utc()
        session.add(batch)
        session.commit()
        session.refresh(batch)
        return batch
    except Exception as exc:
        session.rollback()
        if not record_failure:
            raise
        failed = session.get(ImportBatch, batch.id)
        if failed is not None:
            failed.status = ImportBatchStatus.FAILED
            failed.error_message = str(exc)[:2000]
            failed.completed_at = get_datetime_utc()
            session.add(failed)
            session.commit()
        raise
