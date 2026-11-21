"""config version historical flag

Revision ID: b2c3d4e5f6a7
Revises: 9d1f2c3a4b5c
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "9d1f2c3a4b5c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("config_versions") as batch:
        batch.add_column(sa.Column("historical", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_index("ix_config_versions_historical", ["historical"])


def downgrade() -> None:
    with op.batch_alter_table("config_versions") as batch:
        batch.drop_index("ix_config_versions_historical")
        batch.drop_column("historical")
