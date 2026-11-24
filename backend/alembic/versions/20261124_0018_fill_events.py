"""fill events: what VaultX Invuller filled in (item id and host only)

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-17 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0018'
down_revision: str | None = '0017'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'vault_fill_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('cipher_id', sa.UUID(), nullable=False),
        sa.Column('host', sa.String(length=261), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('device_id', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['vault_accounts.user_id'],
            name=op.f('fk_vault_fill_events_user_id_vault_accounts'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_vault_fill_events')),
    )
    op.create_index('ix_vault_fill_events_user_at', 'vault_fill_events', ['user_id', 'occurred_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_vault_fill_events_user_at', table_name='vault_fill_events')
    op.drop_table('vault_fill_events')
