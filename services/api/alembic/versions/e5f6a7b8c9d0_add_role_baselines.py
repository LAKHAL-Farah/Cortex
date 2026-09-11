"""add role_baselines table

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-20 00:00:00.000000

Story 3.8: contextual anomaly detection by node role. Adds a baseline
table grouped by (role, metric_name, weekday, hour) instead of hostname,
so anomaly_detector.score_current_value() can fall back to "what's normal
for this role at this hour" before falling all the way through to the
role/host-agnostic EWMA estimate.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'role_baselines',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('metric_name', sa.String(), nullable=False),
        sa.Column('weekday', sa.Integer(), nullable=False),
        sa.Column('hour', sa.Integer(), nullable=False),
        sa.Column('mean', sa.Float(), nullable=False),
        sa.Column('stddev', sa.Float(), nullable=False),
        sa.Column('median', sa.Float(), nullable=False),
        sa.Column('mad', sa.Float(), nullable=False),
        sa.Column('sample_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('distinct_hosts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('role', 'metric_name', 'weekday', 'hour', name='uq_role_baseline_slot'),
    )
    op.create_index(op.f('ix_role_baselines_role'), 'role_baselines', ['role'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_role_baselines_role'), table_name='role_baselines')
    op.drop_table('role_baselines')
