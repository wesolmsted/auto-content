"""Micro sets: the sidecar's optional `caption:` and `cutoff:` lines (DESIGN.md §7.10).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Plain ALTER TABLE, not batch mode: see 0005.
def upgrade() -> None:
    op.add_column("micro_sets", sa.Column("caption", sa.String(), nullable=True))
    op.add_column("micro_sets", sa.Column("text_cutoff_s", sa.Double(), nullable=True))


def downgrade() -> None:
    op.drop_column("micro_sets", "text_cutoff_s")
    op.drop_column("micro_sets", "caption")
