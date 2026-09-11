from fastapi import APIRouter

from app.api.routes import (
    acquisition,
    assistant,
    dashboard,
    data_lifecycle,
    demo,
    devices,
    imports,
    login,
    plants,
    private,
    production_lines,
    tags,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(dashboard.router)
api_router.include_router(demo.router)
api_router.include_router(data_lifecycle.router)
api_router.include_router(plants.router)
api_router.include_router(production_lines.router)
api_router.include_router(devices.router)
api_router.include_router(tags.router)
api_router.include_router(acquisition.router)
api_router.include_router(imports.router)
api_router.include_router(assistant.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
