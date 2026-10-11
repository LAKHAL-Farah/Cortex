"""Merge the FinOps and weekly digest migration branches."""

from alembic import op


revision = "d6e7f8a9b0c1"
down_revision = ("b5d7f9a1c3e5", "c5d6e7f8a9b0")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
