"""diagnosis import uniqueness per environment/topology

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from __future__ import annotations

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("diagnosis_imports") as batch:
        batch.drop_constraint("uq_diagnosis_import_bundle", type_="unique")
        batch.drop_constraint("uq_diagnosis_import_trace", type_="unique")
        batch.create_unique_constraint(
            "uq_diagnosis_import_bundle",
            ["device_id", "bundle_sha256", "environment", "topology"])
        batch.create_unique_constraint(
            "uq_diagnosis_import_trace",
            ["device_id", "trace_id", "environment", "topology"])


def downgrade() -> None:
    with op.batch_alter_table("diagnosis_imports") as batch:
        batch.drop_constraint("uq_diagnosis_import_bundle", type_="unique")
        batch.drop_constraint("uq_diagnosis_import_trace", type_="unique")
        batch.create_unique_constraint(
            "uq_diagnosis_import_bundle", ["device_id", "bundle_sha256"])
        batch.create_unique_constraint(
            "uq_diagnosis_import_trace", ["device_id", "trace_id"])
