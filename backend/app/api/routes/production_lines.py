import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import accessible_plant_ids, require_plant_access
from app.api.routes.plants import get_plant_or_404
from app.models import (
    AssetStatus,
    ProductionLine,
    ProductionLineCreate,
    ProductionLinePublic,
    ProductionLinesPublic,
    ProductionLineUpdate,
    get_datetime_utc,
)

router = APIRouter(prefix="/production-lines", tags=["production-lines"])


def get_production_line_or_404(
    session: SessionDep, production_line_id: uuid.UUID
) -> ProductionLine:
    production_line = session.get(ProductionLine, production_line_id)
    if production_line is None:
        raise HTTPException(status_code=404, detail="Production line not found")
    return production_line


def save_production_line(
    session: SessionDep, production_line: ProductionLine
) -> ProductionLine:
    try:
        session.add(production_line)
        session.commit()
        session.refresh(production_line)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Production line code already exists in this plant",
        ) from exc
    return production_line


@router.get("/", response_model=ProductionLinesPublic)
def read_production_lines(
    session: SessionDep,
    current_user: CurrentUser,
    plant_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    status: AssetStatus | None = None,
    query: str | None = None,
) -> Any:
    statement = select(ProductionLine)
    if plant_id is not None:
        get_plant_or_404(session, plant_id)
        require_plant_access(session=session, user=current_user, plant_id=plant_id)
        statement = statement.where(ProductionLine.plant_id == plant_id)
    else:
        allowed_ids = accessible_plant_ids(session=session, user=current_user)
        if allowed_ids is not None:
            statement = statement.where(col(ProductionLine.plant_id).in_(allowed_ids))
    if status is not None:
        statement = statement.where(ProductionLine.status == status)
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(
                col(ProductionLine.code).ilike(pattern),
                col(ProductionLine.name).ilike(pattern),
            )
        )

    count = session.exec(select(func.count()).select_from(statement.subquery())).one()
    rows = session.exec(
        statement.order_by(col(ProductionLine.code)).offset(skip).limit(limit)
    ).all()
    return ProductionLinesPublic(
        data=[ProductionLinePublic.model_validate(row) for row in rows], count=count
    )


@router.get("/{production_line_id}", response_model=ProductionLinePublic)
def read_production_line(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    production_line_id: uuid.UUID,
) -> ProductionLine:
    line = get_production_line_or_404(session, production_line_id)
    require_plant_access(session=session, user=current_user, plant_id=line.plant_id)
    return line


@router.post("/", response_model=ProductionLinePublic)
def create_production_line(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    production_line_in: ProductionLineCreate,
) -> ProductionLine:
    plant = get_plant_or_404(session, production_line_in.plant_id)
    require_plant_access(
        session=session, user=current_user, plant_id=plant.id, write=True
    )
    if plant.status == AssetStatus.INACTIVE:
        raise HTTPException(
            status_code=409, detail="Cannot add a production line to an inactive plant"
        )
    return save_production_line(
        session, ProductionLine.model_validate(production_line_in)
    )


@router.patch("/{production_line_id}", response_model=ProductionLinePublic)
def update_production_line(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    production_line_id: uuid.UUID,
    production_line_in: ProductionLineUpdate,
) -> ProductionLine:
    line = get_production_line_or_404(session, production_line_id)
    require_plant_access(
        session=session, user=current_user, plant_id=line.plant_id, write=True
    )
    line.sqlmodel_update(production_line_in.model_dump(exclude_unset=True))
    line.updated_at = get_datetime_utc()
    return save_production_line(session, line)


@router.post("/{production_line_id}/disable", response_model=ProductionLinePublic)
def disable_production_line(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    production_line_id: uuid.UUID,
) -> ProductionLine:
    line = get_production_line_or_404(session, production_line_id)
    require_plant_access(
        session=session, user=current_user, plant_id=line.plant_id, write=True
    )
    line.status = AssetStatus.INACTIVE
    line.updated_at = get_datetime_utc()
    return save_production_line(session, line)
