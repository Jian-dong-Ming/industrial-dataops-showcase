import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import accessible_plant_ids, require_plant_access
from app.api.routes.devices import get_device_or_404, get_device_plant_id
from app.models import (
    AssetStatus,
    Device,
    ProductionLine,
    Tag,
    TagCreate,
    TagDataType,
    TagPublic,
    TagsPublic,
    TagUpdate,
    get_datetime_utc,
)

router = APIRouter(prefix="/tags", tags=["tags"])


def get_tag_or_404(session: SessionDep, tag_id: uuid.UUID) -> Tag:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return tag


def get_tag_plant_id(session: SessionDep, tag: Tag) -> uuid.UUID:
    device = get_device_or_404(session, tag.device_id)
    return get_device_plant_id(session, device)


def validate_value_range(min_value: float | None, max_value: float | None) -> None:
    if min_value is not None and max_value is not None and min_value > max_value:
        raise HTTPException(
            status_code=422,
            detail="min_value must be less than or equal to max_value",
        )


def save_tag(session: SessionDep, tag: Tag) -> Tag:
    try:
        session.add(tag)
        session.commit()
        session.refresh(tag)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Tag code already exists on this device"
        ) from exc
    return tag


@router.get("/", response_model=TagsPublic)
def read_tags(
    session: SessionDep,
    current_user: CurrentUser,
    device_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    is_enabled: bool | None = None,
    data_type: TagDataType | None = None,
    query: str | None = None,
) -> Any:
    statement = select(Tag)
    if device_id is not None:
        device = get_device_or_404(session, device_id)
        plant_id = get_device_plant_id(session, device)
        require_plant_access(session=session, user=current_user, plant_id=plant_id)
        statement = statement.where(Tag.device_id == device_id)
    else:
        allowed_ids = accessible_plant_ids(session=session, user=current_user)
        if allowed_ids is not None:
            statement = (
                statement.join(Device)
                .join(ProductionLine)
                .where(col(ProductionLine.plant_id).in_(allowed_ids))
            )
    if is_enabled is not None:
        statement = statement.where(Tag.is_enabled == is_enabled)
    if data_type is not None:
        statement = statement.where(Tag.data_type == data_type)
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(col(Tag.code).ilike(pattern), col(Tag.name).ilike(pattern))
        )

    count = session.exec(select(func.count()).select_from(statement.subquery())).one()
    rows = session.exec(
        statement.order_by(col(Tag.code)).offset(skip).limit(limit)
    ).all()
    return TagsPublic(data=[TagPublic.model_validate(row) for row in rows], count=count)


@router.get("/{tag_id}", response_model=TagPublic)
def read_tag(
    *, session: SessionDep, current_user: CurrentUser, tag_id: uuid.UUID
) -> Tag:
    tag = get_tag_or_404(session, tag_id)
    plant_id = get_tag_plant_id(session, tag)
    require_plant_access(session=session, user=current_user, plant_id=plant_id)
    return tag


@router.post("/", response_model=TagPublic)
def create_tag(
    *, session: SessionDep, current_user: CurrentUser, tag_in: TagCreate
) -> Tag:
    device = get_device_or_404(session, tag_in.device_id)
    plant_id = get_device_plant_id(session, device)
    require_plant_access(
        session=session, user=current_user, plant_id=plant_id, write=True
    )
    if device.status == AssetStatus.INACTIVE:
        raise HTTPException(
            status_code=409, detail="Cannot add a tag to an inactive device"
        )
    validate_value_range(tag_in.min_value, tag_in.max_value)
    return save_tag(session, Tag.model_validate(tag_in))


@router.patch("/{tag_id}", response_model=TagPublic)
def update_tag(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    tag_id: uuid.UUID,
    tag_in: TagUpdate,
) -> Tag:
    tag = get_tag_or_404(session, tag_id)
    plant_id = get_tag_plant_id(session, tag)
    require_plant_access(
        session=session, user=current_user, plant_id=plant_id, write=True
    )
    update_data = tag_in.model_dump(exclude_unset=True)
    min_value = update_data.get("min_value", tag.min_value)
    max_value = update_data.get("max_value", tag.max_value)
    validate_value_range(min_value, max_value)
    tag.sqlmodel_update(update_data)
    tag.updated_at = get_datetime_utc()
    return save_tag(session, tag)


@router.post("/{tag_id}/disable", response_model=TagPublic)
def disable_tag(
    *, session: SessionDep, current_user: CurrentUser, tag_id: uuid.UUID
) -> Tag:
    tag = get_tag_or_404(session, tag_id)
    plant_id = get_tag_plant_id(session, tag)
    require_plant_access(
        session=session, user=current_user, plant_id=plant_id, write=True
    )
    tag.is_enabled = False
    tag.updated_at = get_datetime_utc()
    return save_tag(session, tag)
