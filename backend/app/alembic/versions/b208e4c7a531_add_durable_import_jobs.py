"""Persist import jobs independently from the all-or-nothing data transaction."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b208e4c7a531"
down_revision = "a104c9d8e732"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("import_batch.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mapping_config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("processed_rows", sa.Integer(), nullable=False),
        sa.Column("attempt_history", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.String(2000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_import_job_status",
        ),
    )
    op.create_index(
        "ix_import_job_status_heartbeat", "import_job", ["status", "heartbeat_at"]
    )
    op.create_index(
        "ix_import_job_batch_created", "import_job", ["batch_id", "created_at"]
    )
    op.create_index(
        "uq_import_job_active_batch",
        "import_job",
        ["batch_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    # Explicit operator downgrade only. Preserve normal deployment history.
    op.drop_table("import_job")
