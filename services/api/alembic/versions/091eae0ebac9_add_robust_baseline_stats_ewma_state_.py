"""add robust baseline stats, ewma_state, anomaly method tracking

Revision ID: 091eae0ebac9
Revises: 73a1d67453e1
Create Date: 2026-07-31 11:18:26.938920

"""
from alembic import op
import sqlalchemy as sa


revision = '091eae0ebac9'
down_revision = '73a1d67453e1'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # ... existing anomaly_flags / ewma_state blocks stay as-is ...
    op.create_index(op.f('ix_ewma_state_hostname'), 'ewma_state', ['hostname'], unique=False)

    op.create_table('baselines',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('hostname', sa.String(), nullable=False),
        sa.Column('metric_name', sa.String(), nullable=False),
        sa.Column('weekday', sa.Integer(), nullable=False),
        sa.Column('hour', sa.Integer(), nullable=False),
        sa.Column('mean', sa.Float(), nullable=False),
        sa.Column('stddev', sa.Float(), nullable=False),
        sa.Column('median', sa.Float(), nullable=False),
        sa.Column('mad', sa.Float(), nullable=False),
        sa.Column('sample_count', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('hostname', 'metric_name', 'weekday', 'hour', name='uq_baseline_slot')
    )
    op.create_index(op.f('ix_baselines_hostname'), 'baselines', ['hostname'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_index(op.f('ix_baselines_hostname'), table_name='baselines')
    op.drop_table('baselines')

    op.drop_index(op.f('ix_ewma_state_hostname'), table_name='ewma_state')
    op.drop_table('ewma_state')
    op.drop_index(op.f('ix_anomaly_flags_hostname'), table_name='anomaly_flags')
    op.drop_table('anomaly_flags')
    # ### end Alembic commands ###