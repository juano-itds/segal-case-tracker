"""case listing fingerprint (experiment: does the listing row signal change?)

Adds four nullable columns to ``cases``. They are written only by the listing
upsert and only when the row's fingerprint changes; nothing reads them in the
product. If the experiment fails, drop the columns.

``listing_estado_raw`` / ``listing_cuaderno_raw`` are String(255), the same
size as ``cases.procedure`` which already stores the ``cuaderno`` cell.

No backfill and no index: NULL means "not observed yet" and the first listing
pass baselines each case. Adding nullable columns without a default is
metadata-only on Postgres, so this is instant on the full table.

Revision ID: 066
Revises: 065
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("listing_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("cases", sa.Column("listing_fingerprint_at", sa.DateTime(), nullable=True))
    op.add_column("cases", sa.Column("listing_estado_raw", sa.String(length=255), nullable=True))
    op.add_column("cases", sa.Column("listing_cuaderno_raw", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "listing_cuaderno_raw")
    op.drop_column("cases", "listing_estado_raw")
    op.drop_column("cases", "listing_fingerprint_at")
    op.drop_column("cases", "listing_fingerprint")
