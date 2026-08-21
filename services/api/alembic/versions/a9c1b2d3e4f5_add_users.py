"""add users table

Revision ID: a9c1b2d3e4f5
Revises: f1a2b3c4d5e6
Create Date: 2026-08-20 00:00:00.000000

Real username/password accounts (app/auth.py), replacing the single shared
CORTEX_API_KEY as the thing that gates mutating/sensitive endpoints. See
models.User's docstring for what each column is for.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a9c1b2d3e4f5'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False, server_default='viewer'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("role IN ('admin','viewer')", name='ck_users_role_allowed'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_table('users')
