"""lawyers.estado_diario_last_read_on: watermark of the estado-diario probe

The probe used to ask only for the previous business day, so a cycle skipped by
the freshness guard lost that day's notifications for good. It now asks for the
business days since the last successful read of each lawyer, which has to be
remembered somewhere.

One nullable DATE on ``lawyers``: one watermark per lawyer, no new table, no
backfill (NULL = never read, the probe falls back to a single day). Adding a
nullable column without a default is metadata-only on Postgres.

Revision ID: 069
Revises: 068
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "069"
down_revision: Union[str, None] = "068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "lawyers", sa.Column("estado_diario_last_read_on", sa.Date(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("lawyers", "estado_diario_last_read_on")
