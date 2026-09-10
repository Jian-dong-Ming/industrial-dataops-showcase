"""Capacity planning and bounded retention previews. No deletion endpoint."""

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import require_admin, require_plant_access
from app.models import (
    Device,
    Plant,
    ProductionLine,
    RetentionPreview,
    Tag,
    TagSample,
    get_datetime_utc,
)

router = APIRouter(prefix="/data-lifecycle", tags=["data-lifecycle"])
PREVIEW_ROW_LIMIT = 100_000


class CapacityPublic(BaseModel):
    measured_at: datetime
    database_bytes: int
    sample_total_bytes: int
    sample_index_bytes: int
    estimated_live_rows: int
    last_analyze: datetime | None
    estimated_bytes_per_row: float | None
    planning_rows_per_second: float
    estimated_daily_bytes: float | None
    host_free_bytes: None = None
    note: str


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plant_id: uuid.UUID
    source_type: Literal["opcua", "file"]
    keep_days: int = Field(default=7, ge=1, le=3650)


class PreviewPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    plant_id: uuid.UUID
    source_type: str
    keep_days: int
    cutoff: datetime
    high_water_id: int
    matched_rows: int
    count_is_exact: bool
    scan_limit: int
    affected_tags_in_scan: int
    oldest_in_scan: datetime | None
    newest_in_scan: datetime | None
    created_at: datetime
    action_performed: Literal["preview_only"] = "preview_only"
    note: str = "仅创建范围预览，未归档或删除任何样本/原文件。范围依据源采样时间，不是接收时间；按当次范围内最大ID限制候选，并非不可变事务快照。超过扫描上限时，行数为下界，测点数/时间范围只代表扫描部分。预览不是删除授权，实际归档和清理前必须重新校验范围及备份。"


@router.get("/capacity", response_model=CapacityPublic)
def read_capacity(
    session: SessionDep,
    current_user: CurrentUser,
    planning_rows_per_second: Annotated[
        float, Query(ge=0, le=1_000_000, allow_inf_nan=False)
    ] = 24,
) -> CapacityPublic:
    require_admin(current_user)
    # Relation identifiers are constant, never interpolated from user input.
    row = (
        session.connection()
        .execute(
            text("""
        SELECT pg_database_size(current_database()) AS database_bytes,
               pg_total_relation_size(relid) AS sample_total_bytes,
               pg_indexes_size(relid) AS sample_index_bytes,
               n_live_tup AS estimated_live_rows,
               greatest(last_analyze, last_autoanalyze) AS last_analyze
        FROM pg_stat_user_tables
        WHERE schemaname = 'public' AND relname = 'tag_sample'
    """)
        )
        .mappings()
        .one()
    )
    count = int(row["estimated_live_rows"])
    per_row = int(row["sample_total_bytes"]) / count if count > 0 else None
    return CapacityPublic(
        **dict(row),
        measured_at=get_datetime_utc(),
        estimated_bytes_per_row=per_row,
        planning_rows_per_second=planning_rows_per_second,
        estimated_daily_bytes=per_row * planning_rows_per_second * 86400
        if per_row is not None
        else None,
        note="数据库和样本占用来自PostgreSQL；活跃行数为统计估计，不是精确COUNT。每日增长按输入的规划速率推算，并非实测吞吐，未单独预测WAL/膨胀等开销。无法从容器可靠读取Windows C盘剩余空间，不将数据库占用或容器空间当作C盘可用容量。",
    )


@router.post("/previews", response_model=PreviewPublic, status_code=201)
def create_retention_preview(
    payload: PreviewInput, session: SessionDep, current_user: CurrentUser
) -> RetentionPreview:
    require_plant_access(
        session=session, user=current_user, plant_id=payload.plant_id, write=True
    )
    if session.get(Plant, payload.plant_id) is None:
        raise HTTPException(404, "工厂不存在")
    cutoff = get_datetime_utc() - timedelta(days=payload.keep_days)
    try:
        session.connection().execute(text("SET LOCAL statement_timeout = '5s'"))
        scoped = (
            select(TagSample.id, TagSample.tag_id, TagSample.source_timestamp)
            .join(Tag, col(Tag.id) == col(TagSample.tag_id))
            .join(Device, col(Device.id) == col(Tag.device_id))
            .join(
                ProductionLine, col(ProductionLine.id) == col(Device.production_line_id)
            )
            .where(
                ProductionLine.plant_id == payload.plant_id,
                TagSample.source_type == payload.source_type,
            )
        )
        scoped_rows = scoped.subquery()
        high_water = session.exec(select(func.max(scoped_rows.c.id))).one() or 0
        candidates = (
            scoped.where(
                TagSample.source_timestamp < cutoff, col(TagSample.id) <= high_water
            )
            .order_by(col(TagSample.id))
            .limit(PREVIEW_ROW_LIMIT + 1)
            .subquery()
        )
        count, tags, oldest, newest = session.exec(
            select(
                func.count(),
                func.count(func.distinct(candidates.c.tag_id)),
                func.min(candidates.c.source_timestamp),
                func.max(candidates.c.source_timestamp),
            ).select_from(candidates)
        ).one()
        # Permissions may have changed while a slower preview was executing.
        session.refresh(current_user)
        if not current_user.is_active:
            raise HTTPException(403, "账户已禁用")
        require_plant_access(
            session=session, user=current_user, plant_id=payload.plant_id, write=True
        )
        preview = RetentionPreview(
            plant_id=payload.plant_id,
            requested_by_id=current_user.id,
            source_type=payload.source_type,
            keep_days=payload.keep_days,
            cutoff=cutoff,
            high_water_id=high_water,
            matched_rows=count,
            count_is_exact=count <= PREVIEW_ROW_LIMIT,
            scan_limit=PREVIEW_ROW_LIMIT,
            affected_tags_in_scan=tags,
            oldest_in_scan=oldest,
            newest_in_scan=newest,
        )
        session.add(preview)
        session.commit()
        session.refresh(preview)
        return preview
    except DBAPIError as exc:
        session.rollback()
        if getattr(exc.orig, "sqlstate", None) == "57014":
            raise HTTPException(
                503,
                "预览超过单条查询5秒预算，未删除数据；请稍后重试或联系管理员优化查询范围",
            ) from exc
        raise


@router.get("/previews", response_model=list[PreviewPublic])
def list_retention_previews(
    plant_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> list[RetentionPreview]:
    require_plant_access(session=session, user=current_user, plant_id=plant_id)
    return list(
        session.exec(
            select(RetentionPreview)
            .where(RetentionPreview.plant_id == plant_id)
            .order_by(col(RetentionPreview.created_at).desc(), col(RetentionPreview.id))
            .limit(20)
        ).all()
    )


@router.get("/previews/{preview_id}", response_model=PreviewPublic)
def read_retention_preview(
    preview_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> RetentionPreview:
    preview = session.get(RetentionPreview, preview_id)
    if preview is None:
        raise HTTPException(404, "预览不存在")
    require_plant_access(session=session, user=current_user, plant_id=preview.plant_id)
    return preview
