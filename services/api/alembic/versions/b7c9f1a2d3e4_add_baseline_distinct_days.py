"""add distinct_days to baselines

Revision ID: b7c9f1a2d3e4
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04 00:00:00.000000

Tracks how many distinct calendar days actually contributed a sample to a
(hostname, metric_name, weekday, hour) slot, separately from sample_count
(raw point count). See anomaly_detector.MIN_BASELINE_DAYS for why this
matters: a slot can clear MIN_BASELINE_SAMPLES from a single hour's worth
of 5-minute-step points -- all from one real occurrence of that
(weekday, hour) -- which is not enough data to trust a median/MAD spread.

Backfilled to 1 for any existing rows (sample_count > 0) so already-computed
slots don't get treated as having zero real-day coverage; the next
compute_baselines() pass overwrites this with the real count anyway.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c9f1a2d3e4'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('baselines', sa.Column('distinct_days', sa.Integer(), nullable=False, server_default='0'))
    op.execute("UPDATE baselines SET distinct_days = 1 WHERE sample_count > 0")


def downgrade() -> None:
    op.drop_column('baselines', 'distinct_days')
