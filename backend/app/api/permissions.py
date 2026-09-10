import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models import User, UserPlantAccess, UserRole


def is_admin(user: User) -> bool:
    return user.is_superuser or user.role == UserRole.ADMIN


def require_admin(user: User) -> None:
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required",
        )


def require_plant_access(
    *, session: Session, user: User, plant_id: uuid.UUID, write: bool = False
) -> None:
    if is_admin(user):
        return
    if write and user.role != UserRole.ENGINEER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Engineer role required for this operation",
        )
    # Refresh grants even during a long-lived request (e.g. external AI calls).
    access = session.get(UserPlantAccess, (user.id, plant_id), populate_existing=True)
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to this plant",
        )


def accessible_plant_ids(*, session: Session, user: User) -> list[uuid.UUID] | None:
    if is_admin(user):
        return None
    statement = select(UserPlantAccess.plant_id).where(
        UserPlantAccess.user_id == user.id
    )
    return list(session.exec(statement).all())
