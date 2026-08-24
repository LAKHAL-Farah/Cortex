"""add agent_used/raw_data to conversation_messages

Revision ID: b1c2d3e4f5a6
Revises: a9c1b2d3e4f5
Create Date: 2026-08-24 00:00:00.000000

Copilot now answers through the agent orchestrator (monitoring/prediction/
rag, see app/agents/) instead of only the knowledge-chat RAG path. So a
saved turn can render the right panel again on reload (components/
CopilotAgentPanels.tsx), it needs to remember which agent answered and the
raw payload that agent returned -- see models.ConversationMessage's
docstring.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b1c2d3e4f5a6'
down_revision = 'a9c1b2d3e4f5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('conversation_messages', sa.Column('agent_used', sa.String(length=32), nullable=True))
    op.add_column('conversation_messages', sa.Column('raw_data', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('conversation_messages', 'raw_data')
    op.drop_column('conversation_messages', 'agent_used')
