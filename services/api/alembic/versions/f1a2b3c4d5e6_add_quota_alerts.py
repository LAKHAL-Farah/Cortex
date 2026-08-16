"""add quota_alerts table

Revision ID: f1a2b3c4d5e6
Revises: d4e5f6a7b8c9
Create Date: 2026-08-15 00:00:00.000000

Backs services/quota_budget_monitor.py: quota/budget breach alerts,
kept as their own table (and their own alert type) rather than reusing
`anomaly_flags`, since they answer a different question -- "is this
project up against a hard cap" -- and a cap can be either an OpenStack
quota ("capacity_cap") or a configured spend ceiling ("budget_cap").
See models.QuotaAlert's docstring.
"""
from alembic import op
import sqlalchemy as sa


revision = 'f1a2b3c4d5e6'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'quota_alerts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('project_name', sa.String(), nullable=False),
        sa.Column('breach_type', sa.String(), nullable=False),
        sa.Column('resource', sa.String(), nullable=False),
        sa.Column('used', sa.Float(), nullable=False),
        sa.Column('limit', sa.Float(), nullable=False),
        sa.Column('ratio', sa.Float(), nullable=False),
        sa.Column('severity', sa.String(), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('detected_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "breach_type IN ('capacity_cap','budget_cap')",
            name='ck_quota_alerts_breach_type_allowed',
        ),
        sa.CheckConstraint(
            "severity IN ('normal','warning','critical')",
            name='ck_quota_alerts_severity_allowed',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'project_id', 'breach_type', 'resource', name='uq_quota_alert_slot'
        ),
    )
    op.create_index(
        op.f('ix_quota_alerts_project_id'), 'quota_alerts', ['project_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_quota_alerts_project_id'), table_name='quota_alerts')
    op.drop_table('quota_alerts')
