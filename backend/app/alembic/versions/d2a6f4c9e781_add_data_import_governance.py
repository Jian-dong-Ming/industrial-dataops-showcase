"""Add data import batches, quality issues, and file sample provenance.

Revision ID: d2a6f4c9e781
Revises: c8f3e2a1d4b7
Create Date: 2026-08-22
"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision = "d2a6f4c9e781"
down_revision = "c8f3e2a1d4b7"
branch_labels = None
depends_on = None


def _create_readable_view() -> None:
    op.execute(
        """
        CREATE VIEW v_tag_sample_readable AS
        SELECT
            sample.id AS sample_id,
            sample.source_type,
            sample.task_id,
            task.name AS task_name,
            sample.import_batch_id,
            import_batch.original_filename AS import_filename,
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
        LEFT JOIN acquisition_task AS task ON task.id = sample.task_id
        LEFT JOIN import_batch ON import_batch.id = sample.import_batch_id
        JOIN tag ON tag.id = sample.tag_id
        JOIN device ON device.id = tag.device_id
        JOIN production_line ON production_line.id = device.production_line_id
        JOIN plant ON plant.id = production_line.plant_id
        """
    )


def _create_legacy_readable_view() -> None:
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


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_tag_sample_readable")
    op.create_table(
        "import_batch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column(
            "original_filename",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "storage_key",
            sqlmodel.sql.sqltypes.AutoString(length=512),
            nullable=True,
        ),
        sa.Column("file_format", sa.String(length=4), nullable=False),
        sa.Column(
            "content_type",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "file_sha256",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "file_encoding",
            sqlmodel.sql.sqltypes.AutoString(length=30),
            nullable=True,
        ),
        sa.Column(
            "sheet_name",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("accepted_rows", sa.Integer(), nullable=False),
        sa.Column("rejected_rows", sa.Integer(), nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), nullable=False),
        sa.Column("warning_rows", sa.Integer(), nullable=False),
        sa.Column("issue_count", sa.Integer(), nullable=False),
        sa.Column("stored_issue_count", sa.Integer(), nullable=False),
        sa.Column("issues_truncated", sa.Boolean(), nullable=False),
        sa.Column(
            "error_message",
            sqlmodel.sql.sqltypes.AutoString(length=2000),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["plant_id"], ["plant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["user.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_id"], ["import_batch.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_import_batch_plant_created",
        "import_batch",
        ["plant_id", "created_at"],
    )
    op.create_index(
        "ix_import_batch_status_created",
        "import_batch",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_import_batch_plant_sha256",
        "import_batch",
        ["plant_id", "file_sha256"],
    )

    op.create_table(
        "import_field_mapping",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source_column",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "target_field",
            sqlmodel.sql.sqltypes.AutoString(length=50),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["import_batch.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id", "target_field", name="uq_import_mapping_batch_target"
        ),
    )
    op.create_index(
        "ix_import_mapping_batch", "import_field_mapping", ["batch_id"]
    )

    op.create_table(
        "data_quality_issue",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("issue_type", sa.String(length=30), nullable=False),
        sa.Column("severity", sa.String(length=7), nullable=False),
        sa.Column(
            "field_name",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column(
            "raw_value",
            sqlmodel.sql.sqltypes.AutoString(length=500),
            nullable=True,
        ),
        sa.Column(
            "message",
            sqlmodel.sql.sqltypes.AutoString(length=1000),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["import_batch.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_quality_issue_batch_row",
        "data_quality_issue",
        ["batch_id", "row_number"],
    )
    op.create_index(
        "ix_quality_issue_batch_type",
        "data_quality_issue",
        ["batch_id", "issue_type"],
    )
    op.create_index(
        "ix_quality_issue_batch_severity",
        "data_quality_issue",
        ["batch_id", "severity"],
    )

    op.alter_column("tag_sample", "task_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column(
        "tag_sample", sa.Column("import_batch_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "tag_sample",
        sa.Column(
            "source_type",
            sa.String(length=5),
            nullable=False,
            server_default="opcua",
        ),
    )
    op.create_foreign_key(
        "fk_tag_sample_import_batch",
        "tag_sample",
        "import_batch",
        ["import_batch_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_tag_sample_source_reference",
        "tag_sample",
        "(source_type = 'opcua' AND task_id IS NOT NULL AND "
        "import_batch_id IS NULL) OR "
        "(source_type = 'file' AND task_id IS NULL AND "
        "import_batch_id IS NOT NULL)",
    )
    op.create_index(
        "ix_tag_sample_import_batch",
        "tag_sample",
        ["import_batch_id", "id"],
    )
    op.create_index(
        "uq_tag_sample_import_tag_time",
        "tag_sample",
        ["tag_id", "source_timestamp"],
        unique=True,
        postgresql_where=sa.text("import_batch_id IS NOT NULL"),
    )
    _create_readable_view()


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM tag_sample WHERE import_batch_id IS NOT NULL LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade while imported tag samples exist';
            END IF;
        END $$
        """
    )
    op.execute("DROP VIEW IF EXISTS v_tag_sample_readable")
    op.drop_index("uq_tag_sample_import_tag_time", table_name="tag_sample")
    op.drop_index("ix_tag_sample_import_batch", table_name="tag_sample")
    op.drop_constraint(
        "ck_tag_sample_source_reference", "tag_sample", type_="check"
    )
    op.drop_constraint(
        "fk_tag_sample_import_batch", "tag_sample", type_="foreignkey"
    )
    op.drop_column("tag_sample", "source_type")
    op.drop_column("tag_sample", "import_batch_id")
    op.alter_column("tag_sample", "task_id", existing_type=sa.Uuid(), nullable=False)

    op.drop_index(
        "ix_quality_issue_batch_severity", table_name="data_quality_issue"
    )
    op.drop_index("ix_quality_issue_batch_type", table_name="data_quality_issue")
    op.drop_index("ix_quality_issue_batch_row", table_name="data_quality_issue")
    op.drop_table("data_quality_issue")
    op.drop_index("ix_import_mapping_batch", table_name="import_field_mapping")
    op.drop_table("import_field_mapping")
    op.drop_index("ix_import_batch_plant_sha256", table_name="import_batch")
    op.drop_index("ix_import_batch_status_created", table_name="import_batch")
    op.drop_index("ix_import_batch_plant_created", table_name="import_batch")
    op.drop_table("import_batch")
    _create_legacy_readable_view()
