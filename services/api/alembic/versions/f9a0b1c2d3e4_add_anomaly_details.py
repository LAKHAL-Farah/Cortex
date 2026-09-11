"""add structured details to anomaly records"""

from alembic import op
import sqlalchemy as sa


revision = "f9a0b1c2d3e4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("anomaly_flags", sa.Column("details", sa.JSON(), nullable=True))
    op.add_column("anomaly_events", sa.Column("details", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("anomaly_events", "details")
    op.drop_column("anomaly_flags", "details")
