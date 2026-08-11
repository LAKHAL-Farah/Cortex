"""add node_exporter_installed status column

Revision ID: 73a1d67453e1
Revises: 72b4f64486a0
Create Date: 2026-07-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '73a1d67453e1'
down_revision = '72b4f64486a0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'nodes',
        sa.Column('node_exporter_installed', sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('nodes', 'node_exporter_installed')