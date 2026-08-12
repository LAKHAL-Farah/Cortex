"""add topology_sync_runs table

Revision ID: c1d9a2f4e6b8
Revises: b7c9f1a2d3e4
Create Date: 2026-08-09 00:00:00.000000

Phase 5 (API) of the topology-graph feature: GET /api/v1/topology/health
needs to answer "is the sync healthy" from real run history rather than
guessing from a snapshot of the Neo4j graph (see routers/topology.py and
main.py's _run_periodic_recorded). This table is that history -- one
append-only row per completed pass of either OpenStack polling loop
(topology_sync.sync_topology / prometheus_health.sync_prometheus_health).
"""
from alembic import op
import sqlalchemy as sa


revision = 'c1d9a2f4e6b8'
down_revision = 'b7c9f1a2d3e4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'topology_sync_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('sync_type', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('ok','degraded','failed')", name='ck_topology_sync_runs_status_allowed'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_topology_sync_runs_sync_type'), 'topology_sync_runs', ['sync_type'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_topology_sync_runs_sync_type'), table_name='topology_sync_runs')
    op.drop_table('topology_sync_runs')
