import csv
import hashlib
import io
import uuid
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import OperationalError
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import accessible_plant_ids, require_plant_access
from app.api.routes.plants import get_plant_or_404
from app.core.config import settings
from app.data_import.jobs import enqueue_import, latest_job
from app.data_import.reader import (
    TabularReadError,
    detect_layout,
    inspect_tabular_file,
    suggest_mapping,
    suggest_timestamp_column,
)
from app.models import (
    AssetStatus,
    DataQualityIssue,
    DataQualityIssuePublic,
    DataQualityIssuesPublic,
    DataQualityIssueSummaryItem,
    DataQualityIssueSummaryPublic,
    DataQualityIssueType,
    DataQualitySeverity,
    Device,
    ImportBatch,
    ImportBatchesPublic,
    ImportBatchPublic,
    ImportBatchStatus,
    ImportFileFormat,
    ImportJobPublic,
    ImportMappingInput,
    ImportPreviewPublic,
    ImportTagOptionPublic,
    Message,
    ProductionLine,
    Tag,
    get_datetime_utc,
)

router = APIRouter(prefix="/imports", tags=["data-imports"])


def _batch_to_public(batch: ImportBatch) -> ImportBatchPublic:
    return ImportBatchPublic(
        **batch.model_dump(),
        source_file_available=bool(
            batch.storage_key and Path(batch.storage_key).is_file()
        ),
    )


def _get_batch_or_404(session: SessionDep, batch_id: uuid.UUID) -> ImportBatch:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    return batch


def _safe_source_path(batch: ImportBatch) -> Path:
    if not batch.storage_key:
        raise HTTPException(status_code=404, detail="导入源文件已删除")
    storage_root = settings.IMPORT_STORAGE_DIR.resolve()
    source_path = Path(batch.storage_key).resolve()
    try:
        source_path.relative_to(storage_root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="导入源文件路径无效") from exc
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="导入源文件已删除")
    return source_path


async def _store_upload(upload: UploadFile, destination: Path) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with destination.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > settings.IMPORT_MAX_FILE_SIZE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            "文件超过限制："
                            f"{settings.IMPORT_MAX_FILE_SIZE_BYTES // 1024 // 1024} MB"
                        ),
                    )
                digest.update(chunk)
                target.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        try:
            destination.parent.rmdir()
        except OSError:
            pass
        raise
    finally:
        await upload.close()
    return size, digest.hexdigest()


@router.post("/preview", response_model=ImportPreviewPublic)
async def preview_import(
    session: SessionDep,
    current_user: CurrentUser,
    plant_id: uuid.UUID,
    file: UploadFile = File(...),
) -> Any:
    get_plant_or_404(session=session, plant_id=plant_id)
    require_plant_access(
        session=session, user=current_user, plant_id=plant_id, write=True
    )
    original_filename = Path(file.filename or "").name
    suffix = Path(original_filename).suffix.casefold()
    format_by_suffix = {
        ".csv": ImportFileFormat.CSV,
        ".xlsx": ImportFileFormat.XLSX,
    }
    file_format = format_by_suffix.get(suffix)
    if not original_filename or file_format is None:
        raise HTTPException(status_code=422, detail="只支持 .csv 和 .xlsx 文件")

    batch_id = uuid.uuid4()
    destination = settings.IMPORT_STORAGE_DIR / str(batch_id) / original_filename
    file_size, file_sha256 = await _store_upload(file, destination)

    duplicate = session.exec(
        select(ImportBatch)
        .where(
            ImportBatch.plant_id == plant_id,
            ImportBatch.file_sha256 == file_sha256,
            col(ImportBatch.status).in_(
                (
                    ImportBatchStatus.UPLOADED,
                    ImportBatchStatus.QUEUED,
                    ImportBatchStatus.PROCESSING,
                    ImportBatchStatus.COMPLETED,
                )
            ),
        )
        .order_by(col(ImportBatch.created_at).desc())
    ).first()
    if duplicate is not None:
        destination.unlink(missing_ok=True)
        try:
            destination.parent.rmdir()
        except OSError:
            pass
        duplicate_batch = ImportBatch(
            id=batch_id,
            plant_id=plant_id,
            created_by_id=current_user.id,
            duplicate_of_id=duplicate.id,
            original_filename=original_filename,
            file_format=file_format,
            content_type=file.content_type,
            file_size_bytes=file_size,
            file_sha256=file_sha256,
            status=ImportBatchStatus.DUPLICATE,
        )
        session.add(duplicate_batch)
        session.commit()
        session.refresh(duplicate_batch)
        preview_rows: list[dict[str, Any]] = []
        source_columns: list[str] = []
        mapping = None
        if duplicate.storage_key and Path(duplicate.storage_key).is_file():
            source_columns, preview_rows, _, _ = inspect_tabular_file(
                Path(duplicate.storage_key), duplicate.file_format
            )
            mapping = suggest_mapping(source_columns)
        return ImportPreviewPublic(
            batch=_batch_to_public(duplicate_batch),
            source_columns=source_columns,
            preview_rows=preview_rows,
            detected_layout=detect_layout(source_columns),
            suggested_timestamp_column=suggest_timestamp_column(source_columns),
            suggested_mapping=mapping,
        )

    batch = ImportBatch(
        id=batch_id,
        plant_id=plant_id,
        created_by_id=current_user.id,
        original_filename=original_filename,
        storage_key=str(destination.resolve()),
        file_format=file_format,
        content_type=file.content_type,
        file_size_bytes=file_size,
        file_sha256=file_sha256,
    )
    try:
        source_columns, preview_rows, encoding, sheet_name = inspect_tabular_file(
            destination, file_format
        )
        batch.file_encoding = encoding
        batch.sheet_name = sheet_name
        session.add(batch)
        session.commit()
        session.refresh(batch)
    except TabularReadError as exc:
        destination.unlink(missing_ok=True)
        try:
            destination.parent.rmdir()
        except OSError:
            pass
        batch.storage_key = None
        batch.status = ImportBatchStatus.FAILED
        batch.error_message = str(exc)
        batch.completed_at = get_datetime_utc()
        session.add(batch)
        session.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ImportPreviewPublic(
        batch=_batch_to_public(batch),
        source_columns=source_columns,
        preview_rows=preview_rows,
        detected_layout=detect_layout(source_columns),
        suggested_timestamp_column=suggest_timestamp_column(source_columns),
        suggested_mapping=suggest_mapping(source_columns),
    )


@router.post("/{batch_id}/process", response_model=ImportBatchPublic, status_code=202)
def process_import(
    session: SessionDep,
    current_user: CurrentUser,
    batch_id: uuid.UUID,
    mapping: ImportMappingInput,
) -> Any:
    batch = _get_batch_or_404(session, batch_id)
    require_plant_access(
        session=session, user=current_user, plant_id=batch.plant_id, write=True
    )
    try:
        processed = enqueue_import(
            session=session, batch_id=batch.id, user=current_user, mapping=mapping
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _batch_to_public(processed)


@router.get("/{batch_id}/job", response_model=ImportJobPublic | None)
def read_import_job(
    session: SessionDep, current_user: CurrentUser, batch_id: uuid.UUID
) -> Any:
    batch = _get_batch_or_404(session, batch_id)
    require_plant_access(session=session, user=current_user, plant_id=batch.plant_id)
    return latest_job(session, batch.id)


@router.get("", response_model=ImportBatchesPublic)
def read_import_batches(
    session: SessionDep,
    current_user: CurrentUser,
    plant_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 100,
) -> Any:
    statement = select(ImportBatch)
    if plant_id is not None:
        require_plant_access(session=session, user=current_user, plant_id=plant_id)
        statement = statement.where(ImportBatch.plant_id == plant_id)
    else:
        allowed = accessible_plant_ids(session=session, user=current_user)
        if allowed is not None:
            statement = statement.where(col(ImportBatch.plant_id).in_(allowed))
    count = session.exec(select(func.count()).select_from(statement.subquery())).one()
    rows = session.exec(
        statement.order_by(col(ImportBatch.created_at).desc()).offset(skip).limit(limit)
    ).all()
    return ImportBatchesPublic(
        data=[_batch_to_public(row) for row in rows], count=count
    )


@router.get("/mapping-options", response_model=list[ImportTagOptionPublic])
def read_mapping_options(
    session: SessionDep,
    current_user: CurrentUser,
    plant_id: uuid.UUID,
) -> Any:
    require_plant_access(session=session, user=current_user, plant_id=plant_id)
    rows = session.exec(
        select(Tag, Device)
        .join(Device, col(Device.id) == col(Tag.device_id))
        .join(
            ProductionLine,
            col(ProductionLine.id) == col(Device.production_line_id),
        )
        .where(
            ProductionLine.plant_id == plant_id,
            ProductionLine.status == AssetStatus.ACTIVE,
            Device.status == AssetStatus.ACTIVE,
            col(Tag.is_enabled).is_(True),
        )
        .order_by(col(Device.code), col(Tag.code))
    ).all()
    return [
        ImportTagOptionPublic(
            tag_id=tag.id,
            tag_code=tag.code,
            tag_name=tag.name,
            device_id=device.id,
            device_code=device.code,
            device_name=device.name,
        )
        for tag, device in rows
    ]


@router.get("/{batch_id}", response_model=ImportBatchPublic)
def read_import_batch(
    session: SessionDep, current_user: CurrentUser, batch_id: uuid.UUID
) -> Any:
    batch = _get_batch_or_404(session, batch_id)
    require_plant_access(session=session, user=current_user, plant_id=batch.plant_id)
    return _batch_to_public(batch)


@router.get("/{batch_id}/issues", response_model=DataQualityIssuesPublic)
def read_quality_issues(
    session: SessionDep,
    current_user: CurrentUser,
    batch_id: uuid.UUID,
    skip: int = 0,
    limit: int = 200,
) -> Any:
    batch = _get_batch_or_404(session, batch_id)
    require_plant_access(session=session, user=current_user, plant_id=batch.plant_id)
    statement = select(DataQualityIssue).where(DataQualityIssue.batch_id == batch.id)
    count = session.exec(select(func.count()).select_from(statement.subquery())).one()
    rows = session.exec(
        statement.order_by(col(DataQualityIssue.row_number), col(DataQualityIssue.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return DataQualityIssuesPublic(
        data=[DataQualityIssuePublic.model_validate(row) for row in rows], count=count
    )


@router.get("/{batch_id}/issue-summary", response_model=DataQualityIssueSummaryPublic)
def read_quality_issue_summary(
    session: SessionDep, current_user: CurrentUser, batch_id: uuid.UUID
) -> Any:
    batch = _get_batch_or_404(session, batch_id)
    require_plant_access(session=session, user=current_user, plant_id=batch.plant_id)
    rows = session.exec(
        select(
            DataQualityIssue.issue_type,
            DataQualityIssue.severity,
            func.count(col(DataQualityIssue.id)),
        )
        .where(DataQualityIssue.batch_id == batch.id)
        .group_by(DataQualityIssue.issue_type, DataQualityIssue.severity)
        .order_by(DataQualityIssue.severity, DataQualityIssue.issue_type)
    ).all()
    return DataQualityIssueSummaryPublic(
        data=[
            DataQualityIssueSummaryItem(
                issue_type=cast(DataQualityIssueType, issue_type),
                severity=cast(DataQualitySeverity, severity),
                count=count,
            )
            for issue_type, severity, count in rows
        ]
    )


@router.get("/{batch_id}/issues.csv")
def download_quality_issues(
    session: SessionDep, current_user: CurrentUser, batch_id: uuid.UUID
) -> StreamingResponse:
    batch = _get_batch_or_404(session, batch_id)
    require_plant_access(session=session, user=current_user, plant_id=batch.plant_id)
    rows = session.exec(
        select(DataQualityIssue)
        .where(DataQualityIssue.batch_id == batch.id)
        .order_by(col(DataQualityIssue.row_number), col(DataQualityIssue.id))
    ).all()
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["行号", "严重级别", "问题类型", "字段", "原始值", "说明"])
    for row in rows:
        writer.writerow(
            [
                row.row_number,
                row.severity.value,
                row.issue_type.value,
                row.field_name or "",
                row.raw_value or "",
                row.message,
            ]
        )
    headers = {
        "Content-Disposition": (
            f'attachment; filename="import-{batch.id}-quality-issues.csv"'
        )
    }
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8", headers=headers
    )


@router.delete("/{batch_id}/source-file", response_model=Message)
def delete_source_file(
    session: SessionDep, current_user: CurrentUser, batch_id: uuid.UUID
) -> Message:
    batch = _get_batch_or_404(session, batch_id)
    require_plant_access(
        session=session, user=current_user, plant_id=batch.plant_id, write=True
    )
    if batch.status in {ImportBatchStatus.QUEUED, ImportBatchStatus.PROCESSING}:
        raise HTTPException(status_code=409, detail="处理中的源文件不能删除")
    try:
        batch = session.exec(
            select(ImportBatch)
            .where(ImportBatch.id == batch_id)
            .with_for_update(nowait=True)
            .execution_options(populate_existing=True)
        ).one()
    except OperationalError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="批次正在变更，请刷新后重试"
        ) from exc
    if batch.status in {ImportBatchStatus.QUEUED, ImportBatchStatus.PROCESSING}:
        raise HTTPException(status_code=409, detail="处理中的源文件不能删除")
    source_path = _safe_source_path(batch)
    source_path.unlink()
    try:
        source_path.parent.rmdir()
    except OSError:
        pass
    batch.storage_key = None
    session.add(batch)
    session.commit()
    return Message(message="源文件已删除，批次、质量问题和已入库数据仍保留")
