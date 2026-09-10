import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import accessible_plant_ids, require_plant_access
from app.api.routes.production_lines import get_production_line_or_404
from app.models import (
    AssetStatus,
    Device,
    DeviceCreate,
    DevicePublic,
    DevicesPublic,
    DeviceUpdate,
    ProductionLine,
    get_datetime_utc,
)

router = APIRouter(prefix="/devices", tags=["devices"])


def get_device_or_404(session: SessionDep, device_id: uuid.UUID) -> Device:
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def get_device_plant_id(session: SessionDep, device: Device) -> uuid.UUID:
    line = get_production_line_or_404(session, device.production_line_id)
    return line.plant_id


def save_device(session: SessionDep, device: Device) -> Device:
    try:
        session.add(device)
        session.commit()
        session.refresh(device)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Device code already exists on this production line"
        ) from exc
    return device


@router.get("/", response_model=DevicesPublic)
def read_devices(
    session: SessionDep,
    current_user: CurrentUser,
    production_line_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    status: AssetStatus | None = None,
    device_type: str | None = None,
    query: str | None = None,
) -> Any:
    statement = select(Device)
    if production_line_id is not None:
        line = get_production_line_or_404(session, production_line_id)
        require_plant_access(session=session, user=current_user, plant_id=line.plant_id)
        statement = statement.where(Device.production_line_id == production_line_id)
    else:
        allowed_ids = accessible_plant_ids(session=session, user=current_user)
        if allowed_ids is not None:
            statement = statement.join(ProductionLine).where(
                col(ProductionLine.plant_id).in_(allowed_ids)
            )
    if status is not None:
        statement = statement.where(Device.status == status)
    if device_type:
        statement = statement.where(Device.device_type == device_type)
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(col(Device.code).ilike(pattern), col(Device.name).ilike(pattern))
        )

    count = session.exec(select(func.count()).select_from(statement.subquery())).one()
    rows = session.exec(
        statement.order_by(col(Device.code)).offset(skip).limit(limit)
    ).all()
    return DevicesPublic(
        data=[DevicePublic.model_validate(row) for row in rows], count=count
    )


@router.get("/{device_id}", response_model=DevicePublic)
def read_device(
    *, session: SessionDep, current_user: CurrentUser, device_id: uuid.UUID
) -> Device:
    device = get_device_or_404(session, device_id)
    plant_id = get_device_plant_id(session, device)
    require_plant_access(session=session, user=current_user, plant_id=plant_id)
    return device


@router.post("/", response_model=DevicePublic)
def create_device(
    *, session: SessionDep, current_user: CurrentUser, device_in: DeviceCreate
) -> Device:
    line = get_production_line_or_404(session, device_in.production_line_id)
    require_plant_access(
        session=session, user=current_user, plant_id=line.plant_id, write=True
    )
    if line.status == AssetStatus.INACTIVE:
        raise HTTPException(
            status_code=409, detail="Cannot add a device to an inactive production line"
        )
    return save_device(session, Device.model_validate(device_in))


@router.patch("/{device_id}", response_model=DevicePublic)
def update_device(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    device_id: uuid.UUID,
    device_in: DeviceUpdate,
) -> Device:
    device = get_device_or_404(session, device_id)
    plant_id = get_device_plant_id(session, device)
    require_plant_access(
        session=session, user=current_user, plant_id=plant_id, write=True
    )
    device.sqlmodel_update(device_in.model_dump(exclude_unset=True))
    device.updated_at = get_datetime_utc()
    return save_device(session, device)


@router.post("/{device_id}/disable", response_model=DevicePublic)
def disable_device(
    *, session: SessionDep, current_user: CurrentUser, device_id: uuid.UUID
) -> Device:
    device = get_device_or_404(session, device_id)
    plant_id = get_device_plant_id(session, device)
    require_plant_access(
        session=session, user=current_user, plant_id=plant_id, write=True
    )
    device.status = AssetStatus.INACTIVE
    device.updated_at = get_datetime_utc()
    return save_device(session, device)
