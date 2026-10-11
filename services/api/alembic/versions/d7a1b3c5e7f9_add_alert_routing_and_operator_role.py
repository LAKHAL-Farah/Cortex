"""alert routing + escalation, operator role, diagnose step in the audit chain

Revision ID: d7a1b3c5e7f9
Revises: d6e7f8a9b0c1
Create Date: 2026-10-10 00:00:00.000000

Roadmap 4.7 (alerts route by severity, with escalation) and 4.8 (minimal RBAC
+ end-to-end audit). See docs/architecture/adr-0017-alert-routing-and-rbac.md.

Four changes, none of which rewrites an existing row:

- `anomaly_events` gains `acknowledged_at` / `acknowledged_by` (stops the
  escalation clock);
- `alert_notifications` is new: one row per (episode, channel, kind, severity),
  doubling as the email outbox;
- `users.role` accepts 'operator';
- `remediation_audit_log.event` accepts 'diagnosed'.

The append-only triggers on remediation_audit_log reject UPDATE/DELETE/
TRUNCATE, not DDL, so widening its CHECK touches neither existing rows nor the
hash chain.
"""
from alembic import op
import sqlalchemy as sa


revision = 'd7a1b3c5e7f9'
down_revision = 'd6e7f8a9b0c1'
branch_labels = None
depends_on = None

_OLD_ROLE = "role IN ('admin','viewer')"
_NEW_ROLE = "role IN ('admin','operator','viewer')"
_OLD_EVENT = (
    "event IN ('proposed','approved','rejected','info_requested','info_provided',"
    "'execution_started','executed','execution_failed')"
)
_NEW_EVENT = (
    "event IN ('diagnosed','proposed','approved','rejected','info_requested','info_provided',"
    "'execution_started','executed','execution_failed')"
)


def upgrade() -> None:
    op.add_column('anomaly_events', sa.Column('acknowledged_at', sa.DateTime(), nullable=True))
    op.add_column('anomaly_events', sa.Column('acknowledged_by', sa.String(length=64), nullable=True))

    op.create_table(
        'alert_notifications',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('recipient', sa.String(length=320), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['event_id'], ['anomaly_events.id'],
            name='fk_alert_notifications_event_id_anomaly_events', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'channel', 'kind', 'severity', name='uq_alert_notifications_route'),
        sa.CheckConstraint("channel IN ('dashboard','email')", name='ck_alert_notifications_channel'),
        sa.CheckConstraint("kind IN ('initial','severity_upgrade','escalation')", name='ck_alert_notifications_kind'),
        sa.CheckConstraint(
            "status IN ('delivered','pending','sent','failed','skipped')", name='ck_alert_notifications_status'
        ),
    )
    op.create_index(op.f('ix_alert_notifications_event_id'), 'alert_notifications', ['event_id'])
    # The dispatcher's only hot query: pending email rows that are due.
    op.create_index('ix_alert_notifications_due', 'alert_notifications', ['status', 'next_attempt_at'])

    op.drop_constraint('ck_users_role_allowed', 'users', type_='check')
    op.create_check_constraint('ck_users_role_allowed', 'users', _NEW_ROLE)

    op.drop_constraint('ck_remediation_audit_log_event_allowed', 'remediation_audit_log', type_='check')
    op.create_check_constraint('ck_remediation_audit_log_event_allowed', 'remediation_audit_log', _NEW_EVENT)


def downgrade() -> None:
    # The two CHECK re-creations below fail if a row already uses the newer
    # value (an 'operator' account, a 'diagnosed' entry) -- the right outcome:
    # a downgrade must not silently relabel accounts or rewrite history.
    op.drop_constraint('ck_remediation_audit_log_event_allowed', 'remediation_audit_log', type_='check')
    op.create_check_constraint('ck_remediation_audit_log_event_allowed', 'remediation_audit_log', _OLD_EVENT)
    op.drop_constraint('ck_users_role_allowed', 'users', type_='check')
    op.create_check_constraint('ck_users_role_allowed', 'users', _OLD_ROLE)

    op.drop_index('ix_alert_notifications_due', table_name='alert_notifications')
    op.drop_index(op.f('ix_alert_notifications_event_id'), table_name='alert_notifications')
    op.drop_table('alert_notifications')
    op.drop_column('anomaly_events', 'acknowledged_by')
    op.drop_column('anomaly_events', 'acknowledged_at')
