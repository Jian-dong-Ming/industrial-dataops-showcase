"""Add audited retention previews; no sample mutations.

Revision ID: c319f5d8b642
Revises: b208e4c7a531
"""

from alembic import op
import sqlalchemy as sa

revision = "c319f5d8b642"
down_revision = "b208e4c7a531"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "retention_preview",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plant_id", sa.Uuid(), sa.ForeignKey("plant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("requested_by_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_type", sa.String(10), nullable=False),
        sa.Column("keep_days", sa.Integer(), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("high_water_id", sa.Integer(), nullable=False),
        sa.Column("matched_rows", sa.Integer(), nullable=False),
        sa.Column("count_is_exact", sa.Boolean(), nullable=False),
        sa.Column("scan_limit", sa.Integer(), nullable=False),
        sa.Column("affected_tags_in_scan", sa.Integer(), nullable=False),
        sa.Column("oldest_in_scan", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newest_in_scan", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_retention_preview_plant_created", "retention_preview", ["plant_id", "created_at"])


def downgrade():
    op.drop_index("ix_retention_preview_plant_created", table_name="retention_preview")
    op.drop_table("retention_preview")
