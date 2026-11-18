"""Micro sets: the sidecar's optional `mirror:` line (DESIGN.md §7.10).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("micro_sets") as batch_op:
        batch_op.add_column(
            sa.Column("mirror_allowed", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("micro_sets") as batch_op:
        batch_op.drop_column("mirror_allowed")
