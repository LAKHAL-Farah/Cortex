"""add catalog_entries table

Revision ID: d3e4f5a6b7c8
Revises: c3d4e5f6a7b8
Create Date: 2026-09-17 00:00:00.000000

v1.0 OpenStack Expert Agent expansion (adr-0010): the feedback-loop layer
that lets a resolved incident become a new catalog entry from the UI,
without touching the hand-reviewed, process-static CATALOG Python literal
in openstack_expert_catalog.py -- see models.CatalogEntry's own docstring
for the full reasoning.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'd3e4f5a6b7c8'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'catalog_entries',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('symptom_id', sa.String(length=128), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('metric_names', sa.JSON(), nullable=False),
        sa.Column('service_binaries', sa.JSON(), nullable=False),
        sa.Column('keywords', sa.JSON(), nullable=False),
        sa.Column('what_it_means', sa.Text(), nullable=False),
        sa.Column('confirm_commands', sa.JSON(), nullable=False),
        sa.Column('remediation_commands', sa.JSON(), nullable=False),
        sa.Column('doc_ref', sa.String(length=500), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('source_hostname', sa.String(), nullable=True),
        sa.Column('source_metric_name', sa.String(), nullable=True),
        sa.Column('source_anomaly_event_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['source_anomaly_event_id'], ['anomaly_events.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('symptom_id', name='uq_catalog_entries_symptom_id'),
        sa.CheckConstraint("status IN ('draft','published','archived')", name='ck_catalog_entries_status_allowed'),
    )
    # UniqueConstraint above already creates a unique index on symptom_id --
    # no separate op.create_index needed (unlike models.py's
    # `index=True, unique=True` shorthand, which some SQLAlchemy versions
    # render as two indexes; writing the migration by hand avoids that).


def downgrade() -> None:
    op.drop_table('catalog_entries')
