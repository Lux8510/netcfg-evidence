"""diagnosis imports

Revision ID: 9d1f2c3a4b5c
Revises: 8f2c1a9d4e70
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "9d1f2c3a4b5c"
down_revision = "8f2c1a9d4e70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_imports",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("device_id", sa.String(length=36),
                  sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bundle_sha256", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("submitted_by", sa.String(length=80), nullable=False),
        sa.Column("diagnosis_status", sa.String(length=40), nullable=False),
        sa.Column("diagnosis_conclusion", sa.Text(), nullable=False),
        sa.Column("external_commands", sa.JSON(), nullable=False),
        sa.Column("config_version_id", sa.String(length=36),
                  sa.ForeignKey("config_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_run_id", sa.String(length=36), nullable=False),
        sa.Column("finding_id", sa.String(length=36),
                  sa.ForeignKey("findings.id", ondelete="SET NULL"), nullable=True),
        sa.Column("change_request_id", sa.String(length=36),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("no_draft_reason", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('draft','no_draft')", name="ck_diagnosis_import_status"),
        sa.UniqueConstraint("device_id", "bundle_sha256", name="uq_diagnosis_import_bundle"),
        sa.UniqueConstraint("device_id", "trace_id", name="uq_diagnosis_import_trace"),
    )
    op.create_index("ix_diagnosis_imports_device_id", "diagnosis_imports", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_diagnosis_imports_device_id", table_name="diagnosis_imports")
    op.drop_table("diagnosis_imports")
