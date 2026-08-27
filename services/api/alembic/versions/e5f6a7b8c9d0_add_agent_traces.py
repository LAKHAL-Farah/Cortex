"""add agent_traces table

Revision ID: e5f6a7b8c9d0
Revises: c2d3e4f5a6b7
Create Date: 2026-08-26 00:00:00.000000

v0.7 (adr-0009, observability & eval): one append-only row per
POST /api/v1/agents/orchestrate turn, keyed by the trace_id minted before
the LangGraph run (see routers/agents.py) -- backs
GET /api/v1/agents/trace/{trace_id} and the 6.3 cost/latency rollup.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5f6a7b8c9d0'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'agent_traces',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_query', sa.Text(), nullable=False),
        sa.Column('intent', sa.String(length=32), nullable=True),
        sa.Column('target_agent', sa.String(length=32), nullable=True),
        sa.Column('critic_verdict_status', sa.String(length=16), nullable=True),
        sa.Column('degraded', sa.Boolean(), nullable=False),
        sa.Column('steps', sa.JSON(), nullable=False),
        sa.Column('final_answer', sa.Text(), nullable=False),
        sa.Column('duration_ms', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_agent_traces_critic_verdict_status'), 'agent_traces', ['critic_verdict_status'], unique=False
    )
    op.create_index(op.f('ix_agent_traces_created_at'), 'agent_traces', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_agent_traces_created_at'), table_name='agent_traces')
    op.drop_index(op.f('ix_agent_traces_critic_verdict_status'), table_name='agent_traces')
    op.drop_table('agent_traces')
