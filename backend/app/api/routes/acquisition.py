import uuid
from datetime import datetime
from typing import Any

from asyncua.ua.uaerrors import UaError
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import accessible_plant_ids, require_plant_access
from app.api.routes.plants import get_plant_or_404
from app.api.routes.tags import get_tag_or_404, get_tag_plant_id
from app.models import (
    AcquisitionConnectionState,
    AcquisitionDesiredState,
    AcquisitionNode,
    AcquisitionNodeInput,
    AcquisitionNodePublic,
    AcquisitionTask,
    AcquisitionTaskCreate,
    AcquisitionTaskPublic,
    AcquisitionTasksPublic,
    AcquisitionTaskUpdate,
    AssetStatus,
    Device,
    LatestTagValuePublic,
    LatestTagValuesPublic,
    OpcUaBrowseRequest,
    OpcUaBrowseResult,
    ProductionLine,
    Tag,
    TagSample,
    TagSamplePublic,
    TagSamplesPublic,
    get_datetime_utc,
)
from app.opcua.browser import browse_variable_nodes
from app.opcua.queries import latest_task_samples
from app.opcua.security import validate_allowed_endpoint

router = APIRouter(prefix="/acquisition", tags=["acquisition"])


def get_task_or_404(session: SessionDep, task_id: uuid.UUID) -> AcquisitionTask:
    task = session.get(AcquisitionTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Acquisition task not found")
    return task


def validate_intervals(
    *, reconnect_delay_seconds: int, max_reconnect_delay_seconds: int
) -> None:
    if reconnect_delay_seconds > max_reconnect_delay_seconds:
        raise HTTPException(
            status_code=422,
            detail=(
                "reconnect_delay_seconds must be less than or equal to "
                "max_reconnect_delay_seconds"
            ),
        )


def validate_nodes(
    *,
    session: SessionDep,
    plant_id: uuid.UUID,
    nodes: list[AcquisitionNodeInput],
) -> list[AcquisitionNodeInput]:
    tag_ids = [node.tag_id for node in nodes]
    normalized_node_ids = [node.node_id.strip() for node in nodes]
    if len(set(tag_ids)) != len(tag_ids):
        raise HTTPException(status_code=422, detail="A tag can only be mapped once")
    if len(set(normalized_node_ids)) != len(normalized_node_ids):
        raise HTTPException(status_code=422, detail="A NodeId can only be mapped once")
    if any(not node_id for node_id in normalized_node_ids):
        raise HTTPException(status_code=422, detail="NodeId must not be blank")

    rows = session.exec(
        select(Tag, Device, ProductionLine)
        .join(Device, col(Device.id) == col(Tag.device_id))
        .join(
            ProductionLine,
            col(ProductionLine.id) == col(Device.production_line_id),
        )
        .where(col(Tag.id).in_(tag_ids))
    ).all()
    if len(rows) != len(tag_ids):
        raise HTTPException(status_code=404, detail="One or more tags not found")
    for tag, device, production_line in rows:
        if production_line.plant_id != plant_id:
            raise HTTPException(
                status_code=422,
                detail="Every mapped tag must belong to the task plant",
            )
        if not tag.is_enabled:
            raise HTTPException(status_code=409, detail=f"Tag {tag.code} is disabled")
        if (
            device.status == AssetStatus.INACTIVE
            or production_line.status == AssetStatus.INACTIVE
        ):
            raise HTTPException(
                status_code=409,
                detail="Mapped tag device or production line is inactive",
            )
    return [
        AcquisitionNodeInput(
            tag_id=node.tag_id,
            node_id=node.node_id.strip(),
            is_enabled=node.is_enabled,
        )
        for node in nodes
    ]


def task_to_public(
    *, session: SessionDep, task: AcquisitionTask
) -> AcquisitionTaskPublic:
    rows = session.exec(
        select(AcquisitionNode, Tag)
        .join(Tag, col(Tag.id) == col(AcquisitionNode.tag_id))
        .where(AcquisitionNode.task_id == task.id)
        .order_by(col(Tag.code))
    ).all()
    public = AcquisitionTaskPublic.model_validate(task)
    public.nodes = [
        AcquisitionNodePublic(
            id=node.id,
            task_id=node.task_id,
            tag_id=node.tag_id,
            node_id=node.node_id,
            is_enabled=node.is_enabled,
            tag_code=tag.code,
            tag_name=tag.name,
            unit=tag.unit,
        )
        for node, tag in rows
    ]
    return public


def save_task(
    *,
    session: SessionDep,
    task: AcquisitionTask,
    nodes: list[AcquisitionNodeInput] | None = None,
) -> AcquisitionTaskPublic:
    try:
        session.add(task)
        session.flush()
        if nodes is not None:
            session.exec(
                delete(AcquisitionNode).where(col(AcquisitionNode.task_id) == task.id)
            )
            session.flush()
            session.add_all(
                [
                    AcquisitionNode(
                        task_id=task.id,
                        tag_id=node.tag_id,
                        node_id=node.node_id,
                        is_enabled=node.is_enabled,
                    )
                    for node in nodes
                ]
            )
        session.commit()
        session.refresh(task)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Task name, tag mapping, or NodeId mapping conflicts with an "
                "existing acquisition task"
            ),
        ) from exc
    return task_to_public(session=session, task=task)


@router.get("/tasks", response_model=AcquisitionTasksPublic)
def read_tasks(
    session: SessionDep,
    current_user: CurrentUser,
    plant_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 100,
) -> Any:
    statement = select(AcquisitionTask)
    if plant_id is not None:
        require_plant_access(session=session, user=current_user, plant_id=plant_id)
        statement = statement.where(AcquisitionTask.plant_id == plant_id)
    else:
        allowed_ids = accessible_plant_ids(session=session, user=current_user)
        if allowed_ids is not None:
            statement = statement.where(col(AcquisitionTask.plant_id).in_(allowed_ids))
    count = session.exec(select(func.count()).select_from(statement.subquery())).one()
    tasks = session.exec(
        statement.order_by(col(AcquisitionTask.created_at).desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return AcquisitionTasksPublic(
        data=[task_to_public(session=session, task=task) for task in tasks],
        count=count,
    )


@router.get("/tasks/{task_id}", response_model=AcquisitionTaskPublic)
def read_task(
    *, session: SessionDep, current_user: CurrentUser, task_id: uuid.UUID
) -> AcquisitionTaskPublic:
    task = get_task_or_404(session, task_id)
    require_plant_access(session=session, user=current_user, plant_id=task.plant_id)
    return task_to_public(session=session, task=task)


@router.post("/tasks", response_model=AcquisitionTaskPublic)
def create_task(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    task_in: AcquisitionTaskCreate,
) -> AcquisitionTaskPublic:
    plant = get_plant_or_404(session, task_in.plant_id)
    require_plant_access(
        session=session, user=current_user, plant_id=plant.id, write=True
    )
    if plant.status == AssetStatus.INACTIVE:
        raise HTTPException(
            status_code=409,
            detail="Cannot add an acquisition task to an inactive plant",
        )
    try:
        validate_allowed_endpoint(task_in.endpoint_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    validate_intervals(
        reconnect_delay_seconds=task_in.reconnect_delay_seconds,
        max_reconnect_delay_seconds=task_in.max_reconnect_delay_seconds,
    )
    nodes = validate_nodes(
        session=session, plant_id=task_in.plant_id, nodes=task_in.nodes
    )
    task = AcquisitionTask.model_validate(task_in.model_dump(exclude={"nodes"}))
    return save_task(session=session, task=task, nodes=nodes)


@router.patch("/tasks/{task_id}", response_model=AcquisitionTaskPublic)
def update_task(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    task_in: AcquisitionTaskUpdate,
) -> AcquisitionTaskPublic:
    task = get_task_or_404(session, task_id)
    require_plant_access(
        session=session, user=current_user, plant_id=task.plant_id, write=True
    )
    if task.desired_state == AcquisitionDesiredState.RUNNING:
        raise HTTPException(
            status_code=409, detail="Stop the acquisition task before editing it"
        )
    update_data = task_in.model_dump(exclude_unset=True, exclude={"nodes"})
    endpoint_url = update_data.get("endpoint_url", task.endpoint_url)
    try:
        validate_allowed_endpoint(endpoint_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    validate_intervals(
        reconnect_delay_seconds=update_data.get(
            "reconnect_delay_seconds", task.reconnect_delay_seconds
        ),
        max_reconnect_delay_seconds=update_data.get(
            "max_reconnect_delay_seconds", task.max_reconnect_delay_seconds
        ),
    )
    nodes = None
    if task_in.nodes is not None:
        nodes = validate_nodes(
            session=session, plant_id=task.plant_id, nodes=task_in.nodes
        )
    task.sqlmodel_update(update_data)
    task.updated_at = get_datetime_utc()
    return save_task(session=session, task=task, nodes=nodes)


@router.post("/tasks/{task_id}/start", response_model=AcquisitionTaskPublic)
def start_task(
    *, session: SessionDep, current_user: CurrentUser, task_id: uuid.UUID
) -> AcquisitionTaskPublic:
    task = get_task_or_404(session, task_id)
    plant = get_plant_or_404(session, task.plant_id)
    require_plant_access(
        session=session, user=current_user, plant_id=task.plant_id, write=True
    )
    if plant.status == AssetStatus.INACTIVE:
        raise HTTPException(status_code=409, detail="The task plant is inactive")
    mapped_rows = session.exec(
        select(AcquisitionNode, Tag, Device, ProductionLine)
        .join(Tag, col(Tag.id) == col(AcquisitionNode.tag_id))
        .join(Device, col(Device.id) == col(Tag.device_id))
        .join(
            ProductionLine,
            col(ProductionLine.id) == col(Device.production_line_id),
        )
        .where(
            AcquisitionNode.task_id == task.id,
            col(AcquisitionNode.is_enabled).is_(True),
        )
    ).all()
    if not mapped_rows:
        raise HTTPException(status_code=409, detail="No enabled nodes are configured")
    if any(
        not tag.is_enabled
        or device.status == AssetStatus.INACTIVE
        or production_line.status == AssetStatus.INACTIVE
        for _, tag, device, production_line in mapped_rows
    ):
        raise HTTPException(
            status_code=409,
            detail="Mapped tag asset hierarchy is inactive",
        )
    task.desired_state = AcquisitionDesiredState.RUNNING
    task.connection_state = AcquisitionConnectionState.CONNECTING
    task.last_error = None
    task.updated_at = get_datetime_utc()
    return save_task(session=session, task=task)


@router.post("/tasks/{task_id}/stop", response_model=AcquisitionTaskPublic)
def stop_task(
    *, session: SessionDep, current_user: CurrentUser, task_id: uuid.UUID
) -> AcquisitionTaskPublic:
    task = get_task_or_404(session, task_id)
    require_plant_access(
        session=session, user=current_user, plant_id=task.plant_id, write=True
    )
    task.desired_state = AcquisitionDesiredState.STOPPED
    task.connection_state = AcquisitionConnectionState.STOPPED
    task.last_disconnected_at = get_datetime_utc()
    task.updated_at = get_datetime_utc()
    return save_task(session=session, task=task)


@router.post("/browse", response_model=OpcUaBrowseResult)
async def browse_endpoint(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    browse_in: OpcUaBrowseRequest,
) -> OpcUaBrowseResult:
    require_plant_access(
        session=session, user=current_user, plant_id=browse_in.plant_id, write=True
    )
    try:
        nodes, truncated = await browse_variable_nodes(
            endpoint_url=browse_in.endpoint_url,
            root_node_id=browse_in.root_node_id,
            max_depth=browse_in.max_depth,
            max_nodes=browse_in.max_nodes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, TimeoutError, UaError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to browse the OPC UA endpoint",
        ) from exc
    return OpcUaBrowseResult(
        endpoint_url=browse_in.endpoint_url,
        nodes=nodes,
        truncated=truncated,
    )


@router.get("/tags/{tag_id}/samples", response_model=TagSamplesPublic)
def read_tag_samples(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    tag_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    is_good: bool | None = None,
    limit: int = 1000,
) -> TagSamplesPublic:
    tag = get_tag_or_404(session, tag_id)
    plant_id = get_tag_plant_id(session, tag)
    require_plant_access(session=session, user=current_user, plant_id=plant_id)
    if limit < 1 or limit > 5000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 5000")
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail="start must be before end")

    statement = select(TagSample).where(TagSample.tag_id == tag_id)
    if task_id is not None:
        task = get_task_or_404(session, task_id)
        require_plant_access(session=session, user=current_user, plant_id=task.plant_id)
        if task.plant_id != plant_id:
            raise HTTPException(
                status_code=422, detail="task and tag must belong to the same plant"
            )
        statement = statement.where(TagSample.task_id == task_id)
    if start is not None:
        statement = statement.where(TagSample.source_timestamp >= start)
    if end is not None:
        statement = statement.where(TagSample.source_timestamp <= end)
    if is_good is not None:
        statement = statement.where(TagSample.is_good == is_good)

    count = session.exec(select(func.count()).select_from(statement.subquery())).one()
    samples = list(
        session.exec(
            statement.order_by(
                col(TagSample.source_timestamp).desc(), col(TagSample.id).desc()
            ).limit(limit)
        ).all()
    )
    samples.reverse()
    return TagSamplesPublic(
        data=[TagSamplePublic.model_validate(sample) for sample in samples],
        count=count,
    )


@router.get(
    "/tasks/{task_id}/latest-values",
    response_model=LatestTagValuesPublic,
)
def read_latest_task_values(
    *, session: SessionDep, current_user: CurrentUser, task_id: uuid.UUID
) -> LatestTagValuesPublic:
    """Return one latest sample for every enabled node mapped to a task."""
    task = get_task_or_404(session, task_id)
    require_plant_access(session=session, user=current_user, plant_id=task.plant_id)

    node_rows = session.exec(
        select(AcquisitionNode, Tag)
        .join(Tag, col(Tag.id) == col(AcquisitionNode.tag_id))
        .where(
            AcquisitionNode.task_id == task.id,
            col(AcquisitionNode.is_enabled).is_(True),
        )
        .order_by(col(Tag.code))
    ).all()

    latest_samples = session.exec(latest_task_samples(task.id)).all()
    latest_by_tag = {sample.tag_id: sample for sample in latest_samples}

    data: list[LatestTagValuePublic] = []
    for node, tag in node_rows:
        sample = latest_by_tag.get(tag.id)
        data.append(
            LatestTagValuePublic(
                node_id=node.node_id,
                tag_id=tag.id,
                tag_code=tag.code,
                tag_name=tag.name,
                data_type=tag.data_type,
                unit=tag.unit,
                sampling_interval_ms=tag.sampling_interval_ms,
                sample_id=sample.id if sample is not None else None,
                value=sample.value if sample is not None else None,
                numeric_value=(sample.numeric_value if sample is not None else None),
                source_timestamp=(
                    sample.source_timestamp if sample is not None else None
                ),
                server_timestamp=(
                    sample.server_timestamp if sample is not None else None
                ),
                received_at=sample.received_at if sample is not None else None,
                status_code=sample.status_code if sample is not None else None,
                is_good=sample.is_good if sample is not None else None,
            )
        )
    return LatestTagValuesPublic(
        data=data,
        count=len(data),
        generated_at=get_datetime_utc(),
    )
