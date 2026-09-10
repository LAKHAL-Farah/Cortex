"""scope conversations by user_id instead of client_id

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-24 12:00:00.000000

Copilot history used to be scoped by an anonymous per-browser client_id
(X-Client-Id), from before Cortex had real accounts -- see models.py's old
Conversation docstring. Now that every route already requires login
(app/auth.py), history should just follow the account instead, so it shows
up the same way on any device that account logs into, and the "copy this
code into another browser" sync flow (components/CopilotChat.tsx's old
SyncCodePanel) goes away.

There's no way to attribute an anonymous client_id's history to a specific
account after the fact, so this drops existing conversations rather than
leaving orphaned or fake-owned rows -- acceptable pre-launch, but flagging
it here since it's destructive.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c2d3e4f5a6b7'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Anonymous client_id history can't be mapped to an account -- see
    # module docstring.
    op.execute("DELETE FROM conversation_messages")
    op.execute("DELETE FROM conversations")

    op.drop_index(op.f('ix_conversations_client_id'), table_name='conversations')
    op.drop_column('conversations', 'client_id')

    op.add_column('conversations', sa.Column('user_id', sa.UUID(), nullable=False))
    op.create_index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)
    op.create_foreign_key(
        'fk_conversations_user_id_users', 'conversations', 'users', ['user_id'], ['id'], ondelete='CASCADE'
    )


def downgrade() -> None:
    op.execute("DELETE FROM conversation_messages")
    op.execute("DELETE FROM conversations")

    op.drop_constraint('fk_conversations_user_id_users', 'conversations', type_='foreignkey')
    op.drop_index(op.f('ix_conversations_user_id'), table_name='conversations')
    op.drop_column('conversations', 'user_id')

    op.add_column('conversations', sa.Column('client_id', sa.String(length=128), nullable=False))
    op.create_index(op.f('ix_conversations_client_id'), 'conversations', ['client_id'], unique=False)
