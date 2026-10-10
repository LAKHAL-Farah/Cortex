"""remediation execution states and audit events (roadmap 4.4)

Revision ID: a4c6e8f0b2d4
Revises: e7a1c3d5f9b2
Create Date: 2026-10-08 00:00:00.000000

Roadmap 4.4: an approved fix runs (OpenStack SDK / Ansible) only after an
explicit click. See docs/architecture/adr-0015-remediation-execution.md.

Widens two CHECK constraints and nothing else:

- remediation_proposals.status gains executing / executed / execution_failed;
- remediation_audit_log.event gains execution_started / executed /
  execution_failed, plus info_provided (the answer to an "ask for more
  information", which until now nothing wrote).

The append-only triggers on remediation_audit_log reject UPDATE/DELETE/TRUNCATE,
not DDL, so this does not touch existing rows or the hash chain.
"""
from alembic import op


revision = 'a4c6e8f0b2d4'
down_revision = 'e7a1c3d5f9b2'
branch_labels = None
depends_on = None

_OLD_STATUS = "status IN ('proposed','approved','rejected','info_requested')"
_NEW_STATUS = (
    "status IN ('proposed','approved','rejected','info_requested',"
    "'executing','executed','execution_failed')"
)
_OLD_EVENT = "event IN ('proposed','approved','rejected','info_requested')"
_NEW_EVENT = (
    "event IN ('proposed','approved','rejected','info_requested','info_provided',"
    "'execution_started','executed','execution_failed')"
)


def upgrade() -> None:
    op.drop_constraint('ck_remediation_proposals_status_allowed', 'remediation_proposals', type_='check')
    op.create_check_constraint('ck_remediation_proposals_status_allowed', 'remediation_proposals', _NEW_STATUS)
    op.drop_constraint('ck_remediation_audit_log_event_allowed', 'remediation_audit_log', type_='check')
    op.create_check_constraint('ck_remediation_audit_log_event_allowed', 'remediation_audit_log', _NEW_EVENT)


def downgrade() -> None:
    # Refuses (the constraint fails) if any row already uses a new value --
    # which is the right outcome: downgrading must not silently relabel history.
    op.drop_constraint('ck_remediation_audit_log_event_allowed', 'remediation_audit_log', type_='check')
    op.create_check_constraint('ck_remediation_audit_log_event_allowed', 'remediation_audit_log', _OLD_EVENT)
    op.drop_constraint('ck_remediation_proposals_status_allowed', 'remediation_proposals', type_='check')
    op.create_check_constraint('ck_remediation_proposals_status_allowed', 'remediation_proposals', _OLD_STATUS)
