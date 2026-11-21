"""device mappings and import environment

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_mappings",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("environment", sa.String(length=80), nullable=False),
        sa.Column("topology", sa.String(length=80), nullable=False),
        sa.Column("node", sa.String(length=80), nullable=False),
        sa.Column("device_id", sa.String(length=36),
                  sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_by", sa.String(length=80), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_mappings_device_id", "device_mappings", ["device_id"])
    op.create_index("one_active_mapping", "device_mappings",
                    ["environment", "topology", "node"], unique=True,
                    sqlite_where=sa.text("active = 1"), postgresql_where=sa.text("active"))
    with op.batch_alter_table("diagnosis_imports") as batch:
        batch.add_column(sa.Column("environment", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("topology", sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("diagnosis_imports") as batch:
        batch.drop_column("topology")
        batch.drop_column("environment")
    op.drop_index("one_active_mapping", table_name="device_mappings")
    op.drop_index("ix_device_mappings_device_id", table_name="device_mappings")
    op.drop_table("device_mappings")
