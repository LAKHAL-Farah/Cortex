"""add remediation_proposals and remediation_audit_log (roadmap 4.3)

Revision ID: e7a1c3d5f9b2
Revises: d3e4f5a6b7c8
Create Date: 2026-10-08 00:00:00.000000

Roadmap 4.3: approve / reject / ask-for-more-info on a proposed fix, every
decision logged. See docs/architecture/adr-0014-remediation-approval.md.

`remediation_audit_log` is append-only. A trigger rejects UPDATE, DELETE and
TRUNCATE so the guarantee holds even for code that forgets the rule; the
hash chain (prev_hash/entry_hash) covers the case the trigger cannot -- a
superuser or a restored backup editing rows -- by making that visible to
`verify_chain`. For the same reason `actor_user_id` is a plain column, not a
foreign key: ON DELETE SET NULL would be an UPDATE of an audit row.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e7a1c3d5f9b2'
down_revision = 'd3e4f5a6b7c8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'remediation_proposals',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('trace_id', sa.UUID(), nullable=True),
        sa.Column('proposal_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('proposal', sa.JSON(), nullable=False),
        sa.Column('requested_by_user_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(
            ['trace_id'], ['agent_traces.id'],
            name='fk_remediation_proposals_trace_id_agent_traces', ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trace_id', 'proposal_id', name='uq_remediation_proposals_trace_proposal'),
        sa.CheckConstraint(
            "status IN ('proposed','approved','rejected','info_requested')",
            name='ck_remediation_proposals_status_allowed',
        ),
    )
    op.create_index(op.f('ix_remediation_proposals_trace_id'), 'remediation_proposals', ['trace_id'])
    op.create_index(op.f('ix_remediation_proposals_proposal_id'), 'remediation_proposals', ['proposal_id'])
    op.create_index(op.f('ix_remediation_proposals_status'), 'remediation_proposals', ['status'])
    op.create_index(op.f('ix_remediation_proposals_created_at'), 'remediation_proposals', ['created_at'])

    op.create_table(
        'remediation_audit_log',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('proposal_row_id', sa.UUID(), nullable=False),
        sa.Column('proposal_id', sa.String(length=64), nullable=False),
        sa.Column('event', sa.String(length=20), nullable=False),
        sa.Column('from_status', sa.String(length=20), nullable=True),
        sa.Column('to_status', sa.String(length=20), nullable=False),
        sa.Column('actor_user_id', sa.UUID(), nullable=True),
        sa.Column('actor_username', sa.String(length=64), nullable=True),
        sa.Column('actor_role', sa.String(length=20), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('prev_hash', sa.String(length=64), nullable=False),
        sa.Column('entry_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['proposal_row_id'], ['remediation_proposals.id'],
            name='fk_remediation_audit_log_proposal_row_id',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entry_hash', name='uq_remediation_audit_log_entry_hash'),
        sa.CheckConstraint(
            "event IN ('proposed','approved','rejected','info_requested')",
            name='ck_remediation_audit_log_event_allowed',
        ),
    )
    op.create_index(op.f('ix_remediation_audit_log_proposal_row_id'), 'remediation_audit_log', ['proposal_row_id'])
    op.create_index(op.f('ix_remediation_audit_log_proposal_id'), 'remediation_audit_log', ['proposal_id'])
    op.create_index(op.f('ix_remediation_audit_log_event'), 'remediation_audit_log', ['event'])
    op.create_index(op.f('ix_remediation_audit_log_actor_user_id'), 'remediation_audit_log', ['actor_user_id'])
    op.create_index(op.f('ix_remediation_audit_log_created_at'), 'remediation_audit_log', ['created_at'])

    op.execute(
        """
        CREATE FUNCTION remediation_audit_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'remediation_audit_log is append-only (% not allowed)', TG_OP
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_remediation_audit_log_no_update_delete
        BEFORE UPDATE OR DELETE ON remediation_audit_log
        FOR EACH ROW EXECUTE FUNCTION remediation_audit_log_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_remediation_audit_log_no_truncate
        BEFORE TRUNCATE ON remediation_audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION remediation_audit_log_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_remediation_audit_log_no_truncate ON remediation_audit_log")
    op.execute("DROP TRIGGER IF EXISTS trg_remediation_audit_log_no_update_delete ON remediation_audit_log")
    op.execute("DROP FUNCTION IF EXISTS remediation_audit_log_append_only()")
    op.drop_table('remediation_audit_log')
    op.drop_table('remediation_proposals')
