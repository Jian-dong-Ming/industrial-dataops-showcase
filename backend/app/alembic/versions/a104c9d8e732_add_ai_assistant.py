"""Plant-scoped knowledge and privacy-minimized assistant audit.

Revision ID: a104c9d8e732
Revises: f3b8c7d2a104
"""
import sqlalchemy as sa
from alembic import op

revision = "a104c9d8e732"
down_revision = "f3b8c7d2a104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_document",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plant_id", sa.Uuid(), sa.ForeignKey("plant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("chunks", sa.JSON(), nullable=False),
        sa.Column("embedding_model", sa.String(200)),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_knowledge_document_plant", "knowledge_document", ["plant_id"])
    op.create_table(
        "assistant_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("plant_id", sa.Uuid(), sa.ForeignKey("plant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("question_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("retrieval_mode", sa.String(30), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("tool_names", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_run_user_created", "assistant_run", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("assistant_run")
    op.drop_table("knowledge_document")
