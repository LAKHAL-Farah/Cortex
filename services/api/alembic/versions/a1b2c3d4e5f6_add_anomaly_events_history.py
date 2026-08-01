"""add anomaly_events history table

Revision ID: a1b2c3d4e5f6
Revises: 091eae0ebac9
Create Date: 2026-08-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '091eae0ebac9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('anomaly_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('hostname', sa.String(), nullable=False),
        sa.Column('metric_name', sa.String(), nullable=False),
        sa.Column('current_value', sa.Float(), nullable=False),
        sa.Column('z_score', sa.Float(), nullable=False),
        sa.Column('severity', sa.String(), nullable=False),
        sa.Column('method', sa.String(), nullable=False, server_default='robust_zscore'),
        sa.Column('baseline_n', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_anomaly_events_hostname'), 'anomaly_events', ['hostname'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_anomaly_events_hostname'), table_name='anomaly_events')
    op.drop_table('anomaly_events')
