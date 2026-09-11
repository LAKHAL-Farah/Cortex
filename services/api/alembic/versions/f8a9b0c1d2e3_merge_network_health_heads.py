"""Merge the agent and network-health migration branches."""

from alembic import op


revision = "f8a9b0c1d2e3"
down_revision = ("f7a8b9c0d1e2", "1b2c3d4e5f6a")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
