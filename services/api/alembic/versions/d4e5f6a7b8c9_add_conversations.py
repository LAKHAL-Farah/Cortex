"""add conversations and conversation_messages tables

Revision ID: d4e5f6a7b8c9
Revises: c1d9a2f4e6b8
Create Date: 2026-08-13 00:00:00.000000

Backs cross-device Copilot chat history: the knowledge chat endpoint
(adr-0005) is stateless per request, so up to now "remembering old
conversations" only existed client-side (browser localStorage). These two
tables let it persist server-side instead, scoped by an anonymous per-browser
`client_id` (see app.security.get_client_id) rather than a real user account,
since Cortex has no login system yet.
"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'c1d9a2f4e6b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'conversations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('client_id', sa.String(length=128), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False, server_default='New conversation'),
        sa.Column('category', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_conversations_client_id'), 'conversations', ['client_id'], unique=False)

    op.create_table(
        'conversation_messages',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('conversation_id', sa.UUID(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('sources', sa.JSON(), nullable=True),
        sa.Column('errored', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.CheckConstraint("role IN ('user','assistant')", name='ck_conversation_messages_role_allowed'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'position', name='uq_conversation_message_position'),
    )
    op.create_index(
        op.f('ix_conversation_messages_conversation_id'), 'conversation_messages', ['conversation_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_conversation_messages_conversation_id'), table_name='conversation_messages')
    op.drop_table('conversation_messages')
    op.drop_index(op.f('ix_conversations_client_id'), table_name='conversations')
    op.drop_table('conversations')
