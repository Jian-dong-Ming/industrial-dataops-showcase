"""Add latest-value query index and readable sample view.

Revision ID: c8f3e2a1d4b7
Revises: b4a7c1d9e203
Create Date: 2026-08-20
"""

from alembic import op

revision = "c8f3e2a1d4b7"
down_revision = "b4a7c1d9e203"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX ix_tag_sample_task_tag_latest
        ON tag_sample (task_id, tag_id, source_timestamp DESC, id DESC)
        """
    )
    op.execute(
        """
        CREATE VIEW v_tag_sample_readable AS
        SELECT
            sample.id AS sample_id,
            sample.task_id,
            task.name AS task_name,
            plant.id AS plant_id,
            plant.code AS plant_code,
            plant.name AS plant_name,
            production_line.id AS production_line_id,
            production_line.code AS production_line_code,
            production_line.name AS production_line_name,
            device.id AS device_id,
            device.code AS device_code,
            device.name AS device_name,
            sample.tag_id,
            tag.code AS tag_code,
            tag.name AS tag_name,
            tag.data_type,
            tag.unit,
            tag.sampling_interval_ms,
            sample.value,
            sample.numeric_value,
            sample.source_timestamp,
            sample.server_timestamp,
            sample.received_at,
            sample.status_code,
            sample.is_good
        FROM tag_sample AS sample
        JOIN acquisition_task AS task ON task.id = sample.task_id
        JOIN tag ON tag.id = sample.tag_id
        JOIN device ON device.id = tag.device_id
        JOIN production_line ON production_line.id = device.production_line_id
        JOIN plant ON plant.id = production_line.plant_id
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_tag_sample_readable")
    op.execute("DROP INDEX IF EXISTS ix_tag_sample_task_tag_latest")
