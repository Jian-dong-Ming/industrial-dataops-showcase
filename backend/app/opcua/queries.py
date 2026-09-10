"""Latest-per-mapped-tag query: bounded index probes, not ranking all history."""

import uuid

from sqlmodel import col, select
from sqlmodel.sql.expression import SelectOfScalar

from app.models import AcquisitionNode, TagSample


def latest_task_samples(task_id: uuid.UUID) -> SelectOfScalar[TagSample]:
    latest_id = (
        select(TagSample.id)
        .where(TagSample.task_id == task_id, TagSample.tag_id == AcquisitionNode.tag_id)
        .order_by(col(TagSample.source_timestamp).desc(), col(TagSample.id).desc())
        .limit(1)
        .correlate(AcquisitionNode)
        .scalar_subquery()
    )
    mapped_ids = (
        select(latest_id)
        .select_from(AcquisitionNode)
        .where(
            AcquisitionNode.task_id == task_id,
            col(AcquisitionNode.is_enabled).is_(True),
        )
    )
    return select(TagSample).where(col(TagSample.id).in_(mapped_ids))
