"""account reset: temporary password that must be changed at the first login

Revision ID: 0016_herstel
Revises: 0015
Create Date: 2026-11-23 19:00:00.000000

De revisie heet bewust niet '0016': andere openstaande werkstukken nummeren ook na 0015.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0016_herstel'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('password_temporary_until', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'password_temporary_until')
