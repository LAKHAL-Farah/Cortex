"""anomaly detection sensitivity settings

Revision ID: e8b2c4d6f0a1
Revises: d7a1b3c5e7f9
Create Date: 2026-10-11 00:00:00.000000

Adds `detection_settings` (one row: the sensitivity preset and the custom
thresholds) and `anomaly_flags.consecutive_anomalous_ticks` (the counter
behind the "must stay abnormal for N minutes" option). No row is seeded: an
absent row means the balanced preset, which is exactly the thresholds the
detector has always used, so deploying changes nothing until an admin does.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e8b2c4d6f0a1'
down_revision = 'd7a1b3c5e7f9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'anomaly_flags',
        sa.Column('consecutive_anomalous_ticks', sa.Integer(), server_default='0', nullable=False),
    )
    op.create_table(
        'detection_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('preset', sa.String(length=16), server_default='balanced', nullable=False),
        sa.Column('z_medium', sa.Float(), server_default='2', nullable=False),
        sa.Column('z_high', sa.Float(), server_default='3', nullable=False),
        sa.Column('z_critical', sa.Float(), server_default='4', nullable=False),
        sa.Column('ssh_medium', sa.Integer(), server_default='5', nullable=False),
        sa.Column('ssh_high', sa.Integer(), server_default='15', nullable=False),
        sa.Column('ssh_critical', sa.Integer(), server_default='30', nullable=False),
        sa.Column('persist_minutes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('id = 1', name='ck_detection_settings_singleton'),
        sa.CheckConstraint("preset IN ('low','balanced','high','custom')", name='ck_detection_settings_preset'),
    )


def downgrade() -> None:
    op.drop_table('detection_settings')
    op.drop_column('anomaly_flags', 'consecutive_anomalous_ticks')
