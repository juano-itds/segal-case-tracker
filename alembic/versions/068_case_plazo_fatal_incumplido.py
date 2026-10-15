"""cases.plazo_fatal_incumplido: compliance axis next to the semáforo

A lost FATAL plazo (status no_cumplido) leaves the active set, so the semáforo
turns green and the case looks healthy. This flag keeps it visible without
touching the semáforo contract (its values are consumed by external systems).

NOT NULL with a server default of false: on Postgres that is a metadata-only
change, existing rows read false until the next recompute (see
scripts/recompute_excepciones_with_defense.py).

Revision ID: 068
Revises: 067
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cases",
        sa.Column(
            "plazo_fatal_incumplido",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("cases", "plazo_fatal_incumplido")
