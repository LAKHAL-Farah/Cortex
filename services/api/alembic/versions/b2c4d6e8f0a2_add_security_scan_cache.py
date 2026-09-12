"""add security_scan_runs and security_finding_cache tables

Revision ID: b2c4d6e8f0a2
Revises: a3b4c5d6e7f8
Create Date: 2026-09-12 00:00:00.000000

Phase Sec-3: GET /api/v1/security/findings (and the /security,
/security/security-groups dashboard pages behind it) used to call the
Security Agent's four sub-checks live, per known node, on every request
-- these two tables let that work happen on a schedule instead
(services/security_scan_cache.py) and give GET /api/v1/security/health a
real run-history table to answer from, the same shape TopologySyncRun
already gives GET /api/v1/topology/health.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b2c4d6e8f0a2'
down_revision = 'a3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'security_scan_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('ok','degraded','failed')", name='ck_security_scan_runs_status_allowed'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'security_finding_cache',
        sa.Column('hostname', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('has_signal', sa.Boolean(), nullable=False),
        sa.Column('degraded', sa.Boolean(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('raw_data', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('hostname'),
    )


def downgrade() -> None:
    op.drop_table('security_finding_cache')
    op.drop_table('security_scan_runs')
