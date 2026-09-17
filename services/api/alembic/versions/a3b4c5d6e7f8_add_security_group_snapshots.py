"""add security_group_snapshots table

Revision ID: a3b4c5d6e7f8
Revises: f9a0b1c2d3e4
Create Date: 2026-09-11 00:00:00.000000

Phase Sec-1 (Security agent roadmap): today's `_check_sec_group_diff`
(agents/nodes/security.py) only answers "does this look risky right now"
against a static baseline (services/security_audit.py's `_risk_reason`).
This table is the missing memory that lets it also answer "did this
change since we last looked" -- one append-only row per
(hostname, security_group_id) captured on each periodic pass of
security_snapshot_builder.capture_security_group_snapshots().
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'a3b4c5d6e7f8'
down_revision = 'f9a0b1c2d3e4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'security_group_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('hostname', sa.String(), nullable=False),
        sa.Column('security_group_id', sa.String(), nullable=False),
        sa.Column('security_group_name', sa.String(), nullable=True),
        sa.Column('rules', sa.JSON(), nullable=False),
        sa.Column('captured_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_security_group_snapshots_hostname'),
        'security_group_snapshots', ['hostname'], unique=False,
    )
    op.create_index(
        op.f('ix_security_group_snapshots_security_group_id'),
        'security_group_snapshots', ['security_group_id'], unique=False,
    )
    op.create_index(
        op.f('ix_security_group_snapshots_captured_at'),
        'security_group_snapshots', ['captured_at'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_security_group_snapshots_captured_at'), table_name='security_group_snapshots')
    op.drop_index(op.f('ix_security_group_snapshots_security_group_id'), table_name='security_group_snapshots')
    op.drop_index(op.f('ix_security_group_snapshots_hostname'), table_name='security_group_snapshots')
    op.drop_table('security_group_snapshots')
