"""Store the complete long-table or wide-table import mapping.

Revision ID: f3b8c7d2a104
Revises: d2a6f4c9e781
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from alembic import op

revision = "f3b8c7d2a104"
down_revision = "d2a6f4c9e781"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_batch",
        sa.Column("mapping_config", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_batch", "mapping_config")
