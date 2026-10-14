"""case listing rows (experiment: old values of every listing cell)

Adds one nullable TEXT column to ``cases``: the canonical JSON of the listing
rows behind ``listing_fingerprint``. The change log needs the OLD value of all
seven listing cells, and migration 066 only stored two of them. Written only
when the fingerprint changes. If the experiment fails, drop the column.

No backfill: NULL means "old rows unknown" and the log prints ``?`` for them.
Nullable column without default is metadata-only on Postgres.

Revision ID: 067
Revises: 066
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "067"
down_revision: Union[str, None] = "066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("listing_rows_raw", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "listing_rows_raw")
