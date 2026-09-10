"""Add OPC UA acquisition tasks, node mappings, and samples.

Revision ID: b4a7c1d9e203
Revises: 6f2a9c8d4e10
Create Date: 2026-08-16
"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision = "b4a7c1d9e203"
down_revision = "6f2a9c8d4e10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "acquisition_task",
        sa.Column(
            "name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False
        ),
        sa.Column(
            "endpoint_url",
            sqlmodel.sql.sqltypes.AutoString(length=512),
            nullable=False,
        ),
        sa.Column("publishing_interval_ms", sa.Integer(), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("reconnect_delay_seconds", sa.Integer(), nullable=False),
        sa.Column("max_reconnect_delay_seconds", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plant_id", sa.Uuid(), nullable=False),
        sa.Column("desired_state", sa.String(length=7), nullable=False),
        sa.Column("connection_state", sa.String(length=12), nullable=False),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sample_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("reconnect_count", sa.Integer(), nullable=False),
        sa.Column("samples_received", sa.Integer(), nullable=False),
        sa.Column("samples_written", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("dropped_count", sa.Integer(), nullable=False),
        sa.Column(
            "last_error",
            sqlmodel.sql.sqltypes.AutoString(length=2000),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plant_id"], ["plant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plant_id", "name", name="uq_acquisition_task_plant_name"
        ),
    )
    op.create_index(
        "ix_acquisition_task_plant_desired",
        "acquisition_task",
        ["plant_id", "desired_state"],
        unique=False,
    )
    op.create_index(
        "ix_acquisition_task_heartbeat",
        "acquisition_task",
        ["worker_heartbeat_at"],
        unique=False,
    )

    op.create_table(
        "acquisition_node",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column(
            "node_id", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=False
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["acquisition_task.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "tag_id", name="uq_acquisition_node_task_tag"
        ),
        sa.UniqueConstraint(
            "task_id", "node_id", name="uq_acquisition_node_task_node"
        ),
        sa.UniqueConstraint("tag_id", name="uq_acquisition_node_tag"),
    )
    op.create_index(
        "ix_acquisition_node_task_enabled",
        "acquisition_node",
        ["task_id", "is_enabled"],
        unique=False,
    )

    op.create_table(
        "tag_sample",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("numeric_value", sa.Float(), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status_code",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column("is_good", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["acquisition_task.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "tag_id", "source_timestamp", name="uq_tag_sample_source"
        ),
    )
    op.create_index(
        "ix_tag_sample_tag_time",
        "tag_sample",
        ["tag_id", "source_timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_tag_sample_task_received",
        "tag_sample",
        ["task_id", "received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tag_sample_task_received", table_name="tag_sample")
    op.drop_index("ix_tag_sample_tag_time", table_name="tag_sample")
    op.drop_table("tag_sample")
    op.drop_index("ix_acquisition_node_task_enabled", table_name="acquisition_node")
    op.drop_table("acquisition_node")
    op.drop_index("ix_acquisition_task_heartbeat", table_name="acquisition_task")
    op.drop_index(
        "ix_acquisition_task_plant_desired", table_name="acquisition_task"
    )
    op.drop_table("acquisition_task")
