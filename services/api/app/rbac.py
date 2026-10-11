"""Minimal role-based access control for the remediation chain (roadmap 4.8,
adr-0017).

One place that answers "may this role do that?", so the approval service, the
execution service, the routers and the UI's `can_decide` / `can_execute`
flags cannot drift apart.

Three roles (models.User.role, CHECK-constrained in the database):

- **admin**    -- everything. The only role that may validate (approve /
                  reject) or execute a *high*-risk fix, and the only one that
                  manages accounts, nodes and settings.
- **operator** -- the on-call engineer. May validate and execute fixes rated
                  *low* or *medium* risk, read the remediation audit trail,
                  and acknowledge alerts. May not touch a high-risk fix.
- **viewer**   -- read-only. May look at a proposal and *ask for more
                  information* about it (wanting to understand a fix before
                  someone decides on it must not itself be a privilege), but
                  can never decide, execute or acknowledge.

The risk tier is the proposal's **effective** risk (the command's own rating
raised, never lowered, by the impact simulation -- services/impact_simulator.py).
Anything that is not exactly "low" or "medium" -- a missing value, a new level
somebody adds later -- is treated as high: an unknown risk must never be
*easier* to approve than a known one.
"""
from typing import Optional

ADMIN = "admin"
OPERATOR = "operator"
VIEWER = "viewer"

ALL_ROLES = (ADMIN, OPERATOR, VIEWER)

# Risk levels an operator may validate / execute. Everything else is admin-only.
OPERATOR_MAX_RISKS = frozenset({"low", "medium"})

# Roles that may ever validate or execute *something*. The per-proposal answer
# is `can_validate` below; this is the cheap "is this a decider at all" check.
VALIDATOR_ROLES = frozenset({ADMIN, OPERATOR})

# Roles allowed to read the remediation audit trail / per-incident timeline and
# to acknowledge alerts.
AUDIT_READER_ROLES = frozenset({ADMIN, OPERATOR})
ALERT_ACK_ROLES = frozenset({ADMIN, OPERATOR})


def can_validate(role: Optional[str], effective_risk: Optional[str]) -> bool:
    """May `role` approve, reject or execute a fix of this effective risk?"""
    if role == ADMIN:
        return True
    if role == OPERATOR:
        return effective_risk in OPERATOR_MAX_RISKS
    return False


def required_role_for(effective_risk: Optional[str]) -> str:
    """The lowest role that may validate a fix of this risk (for messages)."""
    return OPERATOR if effective_risk in OPERATOR_MAX_RISKS else ADMIN


def denial_message(role: Optional[str], effective_risk: Optional[str], action: str) -> str:
    """The sentence shown when `can_validate` is False. `action` is a verb
    phrase such as "approve or reject a proposed fix" / "execute an approved fix"."""
    if role == OPERATOR:
        return (
            f"This fix is rated {effective_risk or 'unknown'} risk, so only an admin can {action}. "
            "Operators can handle low and medium risk fixes. You can still ask for more information."
        )
    return f"Only an admin or an operator can {action}. You can still ask for more information."


def approver_hint(role: Optional[str], effective_risk: Optional[str]) -> Optional[str]:
    """Why this account cannot decide on this proposal, or None when it can.
    Shown next to the (hidden) buttons so a person knows who to ask."""
    if can_validate(role, effective_risk):
        return None
    if role == OPERATOR:
        return f"Rated {effective_risk or 'unknown'} risk -- only an admin can approve, reject or run it."
    if role == VIEWER:
        return "Your account is read-only. An operator (low/medium risk) or an admin can decide."
    return None
