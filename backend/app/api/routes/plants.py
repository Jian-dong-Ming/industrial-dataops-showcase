import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import (
    accessible_plant_ids,
    require_admin,
    require_plant_access,
)
from app.models import (
    AssetStatus,
    Plant,
    PlantCreate,
    PlantPublic,
    PlantsPublic,
    PlantUpdate,
    get_datetime_utc,
)

router = APIRouter(prefix="/plants", tags=["plants"])


def get_plant_or_404(session: SessionDep, plant_id: uuid.UUID) -> Plant:
    plant = session.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    return plant


def save_plant(session: SessionDep, plant: Plant) -> Plant:
    try:
        session.add(plant)
        session.commit()
        session.refresh(plant)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Plant code already exists"
        ) from exc
    return plant


@router.get("/", response_model=PlantsPublic)
def read_plants(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
    status: AssetStatus | None = None,
    query: str | None = None,
) -> Any:
    statement = select(Plant)
    allowed_ids = accessible_plant_ids(session=session, user=current_user)
    if allowed_ids is not None:
        statement = statement.where(col(Plant.id).in_(allowed_ids))
    if status is not None:
        statement = statement.where(Plant.status == status)
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(col(Plant.code).ilike(pattern), col(Plant.name).ilike(pattern))
        )

    count = session.exec(select(func.count()).select_from(statement.subquery())).one()
    plants = session.exec(
        statement.order_by(col(Plant.code)).offset(skip).limit(limit)
    ).all()
    return PlantsPublic(
        data=[PlantPublic.model_validate(plant) for plant in plants], count=count
    )


@router.get("/{plant_id}", response_model=PlantPublic)
def read_plant(
    *, session: SessionDep, current_user: CurrentUser, plant_id: uuid.UUID
) -> Plant:
    plant = get_plant_or_404(session, plant_id)
    require_plant_access(session=session, user=current_user, plant_id=plant.id)
    return plant


@router.post("/", response_model=PlantPublic)
def create_plant(
    *, session: SessionDep, current_user: CurrentUser, plant_in: PlantCreate
) -> Plant:
    require_admin(current_user)
    return save_plant(session, Plant.model_validate(plant_in))


@router.patch("/{plant_id}", response_model=PlantPublic)
def update_plant(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    plant_id: uuid.UUID,
    plant_in: PlantUpdate,
) -> Plant:
    require_admin(current_user)
    plant = get_plant_or_404(session, plant_id)
    plant.sqlmodel_update(plant_in.model_dump(exclude_unset=True))
    plant.updated_at = get_datetime_utc()
    return save_plant(session, plant)


@router.post("/{plant_id}/disable", response_model=PlantPublic)
def disable_plant(
    *, session: SessionDep, current_user: CurrentUser, plant_id: uuid.UUID
) -> Plant:
    require_admin(current_user)
    plant = get_plant_or_404(session, plant_id)
    plant.status = AssetStatus.INACTIVE
    plant.updated_at = get_datetime_utc()
    return save_plant(session, plant)
