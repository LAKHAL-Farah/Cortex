"""add weekly digest schedule to alert_email_settings (roadmap 4.5)

Revision ID: c5d6e7f8a9b0
Revises: a4c6e8f0b2d4
Create Date: 2026-10-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "c5d6e7f8a9b0"
down_revision = "a4c6e8f0b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alert_email_settings", sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("alert_email_settings", sa.Column("digest_weekday", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("alert_email_settings", sa.Column("digest_hour_utc", sa.Integer(), nullable=False, server_default="8"))
    op.add_column("alert_email_settings", sa.Column("digest_last_sent_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("alert_email_settings", "digest_last_sent_at")
    op.drop_column("alert_email_settings", "digest_hour_utc")
    op.drop_column("alert_email_settings", "digest_weekday")
    op.drop_column("alert_email_settings", "digest_enabled")
