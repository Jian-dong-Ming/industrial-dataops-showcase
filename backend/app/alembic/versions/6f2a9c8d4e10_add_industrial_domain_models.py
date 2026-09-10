"""Add industrial domain models and role-based plant access.

Revision ID: 6f2a9c8d4e10
Revises: fe56fa70289e
Create Date: 2026-08-15
"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision = "6f2a9c8d4e10"
down_revision = "fe56fa70289e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "role", sa.String(length=20), server_default="observer", nullable=False
        ),
    )
    op.execute(sa.text("UPDATE \"user\" SET role = 'admin' WHERE is_superuser"))

    op.create_table(
        "plant",
        sa.Column("code", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column(
            "location", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_plant_code"),
    )
    op.create_index("ix_plant_status", "plant", ["status"], unique=False)

    op.create_table(
        "production_line",
        sa.Column("code", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column(
            "process_type", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plant_id"], ["plant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plant_id", "code", name="uq_production_line_plant_code"),
    )
    op.create_index(
        "ix_production_line_plant_status",
        "production_line",
        ["plant_id", "status"],
        unique=False,
    )

    op.create_table(
        "device",
        sa.Column("code", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column(
            "device_type", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False
        ),
        sa.Column(
            "manufacturer", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True
        ),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("production_line_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["production_line_id"], ["production_line.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("production_line_id", "code", name="uq_device_line_code"),
    )
    op.create_index(
        "ix_device_line_status",
        "device",
        ["production_line_id", "status"],
        unique=False,
    )
    op.create_index("ix_device_type", "device", ["device_type"], unique=False)

    op.create_table(
        "tag",
        sa.Column("code", sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("data_type", sa.String(length=7), nullable=False),
        sa.Column("unit", sqlmodel.sql.sqltypes.AutoString(length=30), nullable=True),
        sa.Column("min_value", sa.Float(), nullable=True),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.Column("sampling_interval_ms", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_tag_value_range",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["device.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "code", name="uq_tag_device_code"),
    )
    op.create_index(
        "ix_tag_device_enabled", "tag", ["device_id", "is_enabled"], unique=False
    )

    op.create_table(
        "user_plant_access",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plant_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plant_id"], ["plant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "plant_id"),
    )

    op.drop_table("item")


def downgrade() -> None:
    op.create_table(
        "item",
        sa.Column(
            "title", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False
        ),
        sa.Column(
            "description", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.drop_table("user_plant_access")
    op.drop_index("ix_tag_device_enabled", table_name="tag")
    op.drop_table("tag")
    op.drop_index("ix_device_type", table_name="device")
    op.drop_index("ix_device_line_status", table_name="device")
    op.drop_table("device")
    op.drop_index("ix_production_line_plant_status", table_name="production_line")
    op.drop_table("production_line")
    op.drop_index("ix_plant_status", table_name="plant")
    op.drop_table("plant")
    op.drop_column("user", "role")
