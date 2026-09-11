"""add manual-resolution state and notes to anomaly alerts

Revision ID: 0a1b2c3d4e5f
Revises: f6a7b8c9d0e1
Create Date: 2026-08-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0a1b2c3d4e5f"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("anomaly_flags", sa.Column("manually_resolved_at", sa.DateTime(), nullable=True))
    op.add_column("anomaly_flags", sa.Column("resolution_note", sa.Text(), nullable=True))
    op.add_column("anomaly_events", sa.Column("resolution_type", sa.String(), nullable=True))
    op.add_column("anomaly_events", sa.Column("resolution_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("anomaly_events", "resolution_note")
    op.drop_column("anomaly_events", "resolution_type")
    op.drop_column("anomaly_flags", "resolution_note")
    op.drop_column("anomaly_flags", "manually_resolved_at")
