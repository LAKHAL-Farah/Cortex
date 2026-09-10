"""add agent_session_memory table

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-09-03 00:00:00.000000

v0.8 (efficiency & scale prep): one row per Copilot conversation holding a
compact "resolved entities" record (last node, metric, agent) -- backs
agents/state.py's session_memory/resolved_entities, the alternative to
re-sending the full raw conversation transcript into the router/agents on
every turn. See models.AgentSessionMemory's docstring.
"""
from alembic import op
import sqlalchemy as sa


revision = 'f7a8b9c0d1e2'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'agent_session_memory',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('conversation_id', sa.UUID(), nullable=False),
        sa.Column('resolved_entities', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_agent_session_memory_conversation_id'),
        'agent_session_memory', ['conversation_id'], unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_agent_session_memory_conversation_id'), table_name='agent_session_memory')
    op.drop_table('agent_session_memory')
