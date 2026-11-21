"""config version uniqueness per historical flag

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""

from __future__ import annotations

from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("config_versions") as batch:
        batch.drop_constraint("uq_config_device_raw", type_="unique")
        batch.create_unique_constraint("uq_config_device_raw", ["device_id", "raw_sha256", "historical"])


def downgrade() -> None:
    with op.batch_alter_table("config_versions") as batch:
        batch.drop_constraint("uq_config_device_raw", type_="unique")
        batch.create_unique_constraint("uq_config_device_raw", ["device_id", "raw_sha256"])
