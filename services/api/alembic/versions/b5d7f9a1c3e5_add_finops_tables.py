"""add FinOps tables (project settings, hourly project + cloud samples)

Revision ID: b5d7f9a1c3e5
Revises: a4c6e8f0b2d4
Create Date: 2026-10-10 00:00:00.000000

Backs services/finops.py + services/finops_report.py: per-project
department/budget settings (editable from the Quotas & Budget page), and
the hourly metering rows from which showback/chargeback reports are summed.
See models.ProjectFinopsSetting / FinopsProjectSample / FinopsCloudSample.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b5d7f9a1c3e5'
down_revision = 'a4c6e8f0b2d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'project_finops_settings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('department', sa.String(), nullable=True),
        sa.Column('monthly_budget_eur', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_by', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_project_finops_settings_project_id'),
        'project_finops_settings', ['project_id'], unique=True,
    )

    op.create_table(
        'finops_project_samples',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('project_name', sa.String(), nullable=False),
        sa.Column('department', sa.String(), nullable=True),
        sa.Column('sampled_at', sa.DateTime(), nullable=False),
        sa.Column('instances', sa.Float(), nullable=False),
        sa.Column('vcpus', sa.Float(), nullable=False),
        sa.Column('ram_mb', sa.Float(), nullable=False),
        sa.Column('volumes', sa.Float(), nullable=False),
        sa.Column('gigabytes', sa.Float(), nullable=False),
        sa.Column('floating_ips', sa.Float(), nullable=False),
        sa.Column('compute_eur_month', sa.Float(), nullable=False),
        sa.Column('storage_eur_month', sa.Float(), nullable=False),
        sa.Column('network_eur_month', sa.Float(), nullable=False),
        sa.Column('platform_eur_month', sa.Float(), nullable=False),
        sa.Column('idle_eur_month', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'sampled_at', name='uq_finops_project_sample_hour'),
    )
    op.create_index(
        op.f('ix_finops_project_samples_project_id'),
        'finops_project_samples', ['project_id'], unique=False,
    )
    op.create_index(
        op.f('ix_finops_project_samples_sampled_at'),
        'finops_project_samples', ['sampled_at'], unique=False,
    )

    op.create_table(
        'finops_cloud_samples',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('sampled_at', sa.DateTime(), nullable=False),
        sa.Column('physical_vcpus', sa.Float(), nullable=True),
        sa.Column('physical_ram_mb', sa.Float(), nullable=True),
        sa.Column('storage_gb', sa.Float(), nullable=True),
        sa.Column('compute_nodes', sa.Integer(), nullable=False),
        sa.Column('cpu_allocation_ratio', sa.Float(), nullable=False),
        sa.Column('ram_allocation_ratio', sa.Float(), nullable=False),
        sa.Column('allocated_vcpus', sa.Float(), nullable=False),
        sa.Column('allocated_ram_mb', sa.Float(), nullable=False),
        sa.Column('allocated_gb', sa.Float(), nullable=False),
        sa.Column('committed_vcpus', sa.Float(), nullable=False),
        sa.Column('committed_ram_mb', sa.Float(), nullable=False),
        sa.Column('committed_gb', sa.Float(), nullable=False),
        sa.Column('unlimited_vcpus', sa.Integer(), nullable=False),
        sa.Column('unlimited_ram', sa.Integer(), nullable=False),
        sa.Column('unlimited_gb', sa.Integer(), nullable=False),
        sa.Column('monthly_cost_eur', sa.Float(), nullable=False),
        sa.Column('pool_compute_eur', sa.Float(), nullable=False),
        sa.Column('pool_storage_eur', sa.Float(), nullable=False),
        sa.Column('pool_platform_eur', sa.Float(), nullable=False),
        sa.Column('rate_vcpu_eur', sa.Float(), nullable=True),
        sa.Column('rate_ram_gb_eur', sa.Float(), nullable=True),
        sa.Column('rate_storage_gb_eur', sa.Float(), nullable=True),
        sa.Column('rate_floating_ip_eur', sa.Float(), nullable=False),
        sa.Column('allocated_eur_month', sa.Float(), nullable=False),
        sa.Column('idle_eur_month', sa.Float(), nullable=False),
        sa.Column('oversold', sa.Boolean(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_finops_cloud_samples_sampled_at'),
        'finops_cloud_samples', ['sampled_at'], unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_finops_cloud_samples_sampled_at'), table_name='finops_cloud_samples')
    op.drop_table('finops_cloud_samples')
    op.drop_index(op.f('ix_finops_project_samples_sampled_at'), table_name='finops_project_samples')
    op.drop_index(op.f('ix_finops_project_samples_project_id'), table_name='finops_project_samples')
    op.drop_table('finops_project_samples')
    op.drop_index(op.f('ix_project_finops_settings_project_id'), table_name='project_finops_settings')
    op.drop_table('project_finops_settings')
