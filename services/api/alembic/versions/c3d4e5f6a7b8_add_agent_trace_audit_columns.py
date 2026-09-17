"""add audit-log columns (user_id, user_role, security_involved) to agent_traces

Revision ID: c3d4e5f6a7b8
Revises: b2c4d6e8f0a2
Create Date: 2026-09-16 00:00:00.000000

Phase Sec-6: backs GET /api/v1/security/audit-log -- "who asked a security
question, what role they had, and whether the answer was redacted", using
the same security_rbac.security_agent_involved /
filter_security_response_for_role logic already gating chat answers today
(see routers/agents.py, services/security_rbac.py).

`user_id` is nullable (ON DELETE SET NULL, not CASCADE -- an audit row
should outlive the account it came from) since rows written before this
migration, and any stateless/API caller with no session, have no asker to
record. `user_role` is a snapshot taken at request time, not a live join
to `users.role`, so a later promotion/demotion doesn't rewrite what
actually happened on a past turn. `security_involved` is computed once at
write time from the same raw_data-based check routers/agents.py already
runs for RBAC filtering, persisted here since raw_data itself isn't a
column on this table (see models.AgentTrace.steps).
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c4d6e8f0a2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agent_traces', sa.Column('user_id', sa.UUID(), nullable=True))
    op.add_column('agent_traces', sa.Column('user_role', sa.String(length=20), nullable=True))
    op.add_column(
        'agent_traces',
        sa.Column('security_involved', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(op.f('ix_agent_traces_user_id'), 'agent_traces', ['user_id'], unique=False)
    op.create_index(
        op.f('ix_agent_traces_security_involved'), 'agent_traces', ['security_involved'], unique=False
    )
    op.create_foreign_key(
        'fk_agent_traces_user_id_users', 'agent_traces', 'users', ['user_id'], ['id'], ondelete='SET NULL'
    )
    # server_default only exists to satisfy the NOT NULL backfill for
    # existing rows -- new inserts always pass security_involved
    # explicitly (crud.create_agent_trace), so the default is dropped
    # once the column is populated, same idiom degraded/other NOT NULL
    # booleans on this table already use.
    op.alter_column('agent_traces', 'security_involved', server_default=None)


def downgrade() -> None:
    op.drop_constraint('fk_agent_traces_user_id_users', 'agent_traces', type_='foreignkey')
    op.drop_index(op.f('ix_agent_traces_security_involved'), table_name='agent_traces')
    op.drop_index(op.f('ix_agent_traces_user_id'), table_name='agent_traces')
    op.drop_column('agent_traces', 'security_involved')
    op.drop_column('agent_traces', 'user_role')
    op.drop_column('agent_traces', 'user_id')
