from typing import Any

from fastapi import APIRouter
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import accessible_plant_ids
from app.models import (
    AcquisitionConnectionState,
    AcquisitionDesiredState,
    AcquisitionTask,
    DashboardSummaryPublic,
    DataQualityIssue,
    Device,
    ImportBatch,
    ImportBatchStatus,
    Plant,
    ProductionLine,
    Tag,
    TagSample,
    get_datetime_utc,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _count(session: SessionDep, statement: Any) -> int:
    return session.exec(select(func.count()).select_from(statement.subquery())).one()


@router.get("/summary", response_model=DashboardSummaryPublic)
def read_summary(
    session: SessionDep,
    current_user: CurrentUser,
) -> DashboardSummaryPublic:
    allowed_ids = accessible_plant_ids(session=session, user=current_user)

    plant_statement = select(Plant.id)
    line_statement = select(ProductionLine.id)
    device_statement = select(Device.id).join(
        ProductionLine,
        col(ProductionLine.id) == col(Device.production_line_id),
    )
    tag_statement = (
        select(Tag.id)
        .join(Device, col(Device.id) == col(Tag.device_id))
        .join(
            ProductionLine,
            col(ProductionLine.id) == col(Device.production_line_id),
        )
    )
    enabled_tag_statement = tag_statement.where(col(Tag.is_enabled).is_(True))
    task_statement = select(AcquisitionTask.id)
    running_task_statement = task_statement.where(
        AcquisitionTask.desired_state == AcquisitionDesiredState.RUNNING
    )
    connected_task_statement = task_statement.where(
        AcquisitionTask.connection_state == AcquisitionConnectionState.CONNECTED
    )
    sample_statement = (
        select(TagSample.id)
        .join(Tag, col(Tag.id) == col(TagSample.tag_id))
        .join(Device, col(Device.id) == col(Tag.device_id))
        .join(
            ProductionLine,
            col(ProductionLine.id) == col(Device.production_line_id),
        )
    )
    latest_sample_statement = (
        select(func.max(TagSample.source_timestamp))
        .join(Tag, col(Tag.id) == col(TagSample.tag_id))
        .join(Device, col(Device.id) == col(Tag.device_id))
        .join(
            ProductionLine,
            col(ProductionLine.id) == col(Device.production_line_id),
        )
    )
    import_statement = select(ImportBatch.id)
    completed_import_statement = import_statement.where(
        ImportBatch.status == ImportBatchStatus.COMPLETED
    )
    issue_statement = select(DataQualityIssue.id).join(
        ImportBatch,
        col(ImportBatch.id) == col(DataQualityIssue.batch_id),
    )

    if allowed_ids is not None:
        plant_statement = plant_statement.where(col(Plant.id).in_(allowed_ids))
        line_statement = line_statement.where(
            col(ProductionLine.plant_id).in_(allowed_ids)
        )
        plant_filter = col(ProductionLine.plant_id).in_(allowed_ids)
        device_statement = device_statement.where(plant_filter)
        tag_statement = tag_statement.where(plant_filter)
        enabled_tag_statement = enabled_tag_statement.where(plant_filter)
        task_filter = col(AcquisitionTask.plant_id).in_(allowed_ids)
        task_statement = task_statement.where(task_filter)
        running_task_statement = running_task_statement.where(task_filter)
        connected_task_statement = connected_task_statement.where(task_filter)
        sample_statement = sample_statement.where(plant_filter)
        latest_sample_statement = latest_sample_statement.where(plant_filter)
        import_filter = col(ImportBatch.plant_id).in_(allowed_ids)
        import_statement = import_statement.where(import_filter)
        completed_import_statement = completed_import_statement.where(import_filter)
        issue_statement = issue_statement.where(import_filter)

    latest_sample_at = session.exec(latest_sample_statement).one()
    return DashboardSummaryPublic(
        plant_count=_count(session, plant_statement),
        production_line_count=_count(session, line_statement),
        device_count=_count(session, device_statement),
        tag_count=_count(session, tag_statement),
        enabled_tag_count=_count(session, enabled_tag_statement),
        acquisition_task_count=_count(session, task_statement),
        running_task_count=_count(session, running_task_statement),
        connected_task_count=_count(session, connected_task_statement),
        sample_count=_count(session, sample_statement),
        latest_sample_at=latest_sample_at,
        import_batch_count=_count(session, import_statement),
        completed_import_batch_count=_count(session, completed_import_statement),
        quality_issue_count=_count(session, issue_statement),
        generated_at=get_datetime_utc(),
    )
